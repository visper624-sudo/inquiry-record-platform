# -*- coding: utf-8 -*-
"""模板服务：模板库同步、检索、AI 推荐打分与多模板合并大纲。"""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter

from config import SECTIONS, TEMPLATE_DIR
from core import db

_PUNCT_RE = re.compile(r"[^\u4e00-\u9fa5A-Za-z0-9]")

# 进程内模板缓存：推荐与列表都不再每次全表查询 + JSON 解析（审查报告 P5）
_CACHE: dict = {"rows": None, "ts": 0.0}
_CACHE_TTL_SEC = 30.0


def invalidate_cache() -> None:
    _CACHE.update({"rows": None, "ts": 0.0})


def _all_hydrated() -> list[dict]:
    now = time.time()
    if _CACHE["rows"] is None or now - _CACHE["ts"] > _CACHE_TTL_SEC:
        _CACHE["rows"] = [
            _hydrate(row) for row in db.rows(
                "SELECT * FROM template ORDER BY is_general DESC, template_id ASC")
        ]
        _CACHE["ts"] = now
    return _CACHE["rows"]


# ------------------------------------------------------------------ 同步
def sync_from_files() -> int:
    """把 data/templates/*.json 同步进模板表（幂等，启动时调用）。"""
    if not TEMPLATE_DIR.exists():
        return 0
    count = 0
    with db.write() as conn:
        for path in sorted(TEMPLATE_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            conn.execute(
                "INSERT INTO template (template_id, code, name, category, description, signals, keywords, "
                "is_general, sections, version, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(template_id) DO UPDATE SET code=excluded.code, name=excluded.name, "
                "category=excluded.category, description=excluded.description, signals=excluded.signals, "
                "keywords=excluded.keywords, is_general=excluded.is_general, sections=excluded.sections, "
                "version=excluded.version, updated_at=excluded.updated_at",
                (
                    data.get("template_id", path.stem),
                    data.get("code", path.stem.lower()),
                    data.get("name", path.stem),
                    data.get("category", "其他"),
                    data.get("description", ""),
                    db.json_dump(data.get("feature_signals", [])),
                    db.json_dump(data.get("weight_keywords", [])),
                    int(bool(data.get("is_general"))),
                    db.json_dump(data.get("sections", [])),
                    data.get("version", "1.0"),
                    db.now_iso(),
                ),
            )
            count += 1
    invalidate_cache()
    return count


# ------------------------------------------------------------------ 查询
def _hydrate(row: dict) -> dict:
    return {
        "template_id": row["template_id"],
        "code": row["code"],
        "name": row["name"],
        "category": row["category"],
        "description": row["description"],
        "signals": db.json_load(row["signals"], []),
        "keywords": db.json_load(row["keywords"], []),
        "is_general": bool(row["is_general"]),
        "sections": db.json_load(row["sections"], []),
        "version": row["version"],
        "question_count": sum(len(s.get("questions", [])) for s in db.json_load(row["sections"], [])),
    }


def list_templates(keyword: str = "", category: str = "") -> list[dict]:
    items = _all_hydrated()
    if keyword:
        kw = keyword.lower()
        items = [t for t in items
                 if kw in t["name"].lower() or kw in t["description"].lower()
                 or any(kw in s.lower() for s in t["signals"])]
    if category:
        items = [t for t in items if t["category"] == category]
    return list(items)


def get_template(template_id: str) -> dict | None:
    return next((t for t in _all_hydrated() if t["template_id"] == template_id), None)


def get_many(template_ids: list[str]) -> list[dict]:
    index = {t["template_id"]: t for t in _all_hydrated()}
    return [index[tid] for tid in template_ids if tid in index]


def get_many_map(template_ids: list[str]) -> dict[str, dict]:
    """批量取模板（列表页用，避免逐条查询）。"""
    index = {t["template_id"]: t for t in _all_hydrated()}
    return {tid: index[tid] for tid in template_ids if tid in index}


def categories() -> list[str]:
    return sorted({t["category"] for t in _all_hydrated()})


# ------------------------------------------------------------------ 相似度
def _bigrams(text: str) -> Counter:
    clean = _PUNCT_RE.sub("", text or "")
    if len(clean) < 2:
        return Counter()
    return Counter(clean[i:i + 2] for i in range(len(clean) - 1))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ------------------------------------------------------------------ 推荐
# 信号词命中饱和阈值：命中 8 个即视为完全命中，避免模板信号词越多越吃亏
SIGNAL_SATURATION = 8
KEYWORD_SATURATION = 4


def recommend(brief: str, category_hint: str = "", top_n: int = 3) -> list[dict]:
    """按可解释打分推荐模板：0.6 信号词命中 + 0.25 语义相似 + 0.15 手法关键词。"""
    brief = brief or ""
    brief_bigrams = _bigrams(brief)
    templates = _all_hydrated()
    scored = []
    for tpl in templates:
        if tpl["is_general"]:
            continue
        signals = tpl["signals"] or []
        hits = [s for s in signals if s and s in brief]
        signal_score = min(1.0, len(hits) / SIGNAL_SATURATION)
        corpus = " ".join(signals) + " " + " ".join(tpl["keywords"] or []) + " " + tpl["name"]
        semantic = _cosine(brief_bigrams, _bigrams(corpus))
        keywords = tpl["keywords"] or []
        kw_hits = [k for k in keywords if k and k in brief]
        keyword_score = min(1.0, len(kw_hits) / KEYWORD_SATURATION)
        base = 0.6 * signal_score + 0.25 * semantic + 0.15 * keyword_score
        bonus = 0.0
        if category_hint and (category_hint in tpl["category"] or category_hint in tpl["name"]):
            bonus = 0.08
        score = min(1.0, base + bonus)
        reasons = []
        if hits:
            reasons.append(f"命中信号词 {len(hits)} 个：" + "、".join(hits[:6]))
        if semantic >= 0.05:
            reasons.append(f"案情与模板特征词语义相似度 {round(semantic * 100)}%")
        if kw_hits:
            reasons.append("手法关键词匹配：" + "、".join(kw_hits[:4]))
        if bonus:
            reasons.append("与报案登记案由一致")
        if not reasons:
            reasons.append("按案由通用特征初步匹配，建议人工确认")
        scored.append({
            **{k: tpl[k] for k in ("template_id", "code", "name", "category", "description",
                                   "signals", "is_general", "question_count")},
            "match_score": round(score * 100, 1),
            "signal_hits": hits,
            "keyword_hits": kw_hits,
            "reason": "；".join(reasons),
            "breakdown": {
                "signal": round(signal_score, 4),
                "semantic": round(semantic, 4),
                "keyword": round(keyword_score, 4),
                "bonus": bonus,
            },
        })
    scored.sort(key=lambda x: x["match_score"], reverse=True)
    top = scored[:max(1, top_n)]
    for idx, item in enumerate(top):
        item["rank"] = idx + 1
    return top


# ------------------------------------------------------------------ 合并大纲
def _norm_question(text: str) -> str:
    return _PUNCT_RE.sub("", text or "")


def merge_outline(template_ids: list[str]) -> dict:
    """多模板合并：按章节归并、问题去重、保留来源模板标记。"""
    templates = get_many(template_ids)
    if not templates:
        return {"sections": [], "template_refs": [], "total": 0}
    merged: dict[str, list[dict]] = {s["key"]: [] for s in SECTIONS}
    seen: dict[str, set] = {s["key"]: set() for s in SECTIONS}
    for tpl in templates:
        for section in tpl["sections"]:
            key = section.get("key")
            if key not in merged:
                continue
            for question in section.get("questions", []):
                norm = _norm_question(question.get("text", ""))
                if not norm or norm in seen[key]:
                    continue
                seen[key].add(norm)
                merged[key].append({
                    "qid": question.get("qid") or db.new_id("Q", 8),
                    "text": question.get("text", ""),
                    "flow": question.get("flow"),
                    "field": question.get("field"),
                    "key_item": bool(question.get("key_item")),
                    "source_template": tpl["template_id"],
                    "source_template_name": tpl["name"],
                })
    sections = [
        {"key": s["key"], "title": s["title"], "questions": merged[s["key"]]}
        for s in SECTIONS
    ]
    return {
        "sections": sections,
        "template_refs": [{"template_id": t["template_id"], "name": t["name"],
                           "category": t["category"]} for t in templates],
        "total": sum(len(s["questions"]) for s in sections),
    }


def template_usage_stats() -> list[dict]:
    """模板使用统计：被选中次数与累计问题数（运营用）。"""
    records = db.rows("SELECT selected_templates FROM record")
    counter = Counter()
    for row in records:
        for tid in db.json_load(row["selected_templates"], []):
            counter[tid] += 1
    result = []
    for tid, count in counter.most_common():
        tpl = get_template(tid)
        if tpl:
            result.append({"template_id": tid, "name": tpl["name"], "used": count})
    return result

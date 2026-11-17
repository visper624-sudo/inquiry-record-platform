#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""问询模板数据校验脚本。

用法:
    python scripts/validate_templates.py [模板目录]

校验项:
  1. 文件命名与 JSON 合法性
  2. 顶层字段完整性、类型与长度约束
  3. 章节必须为 5 个且顺序固定
  4. qid 全局唯一、格式规范
  5. flow / field 必须存在于 data/five_flow_fields.json
  6. 问题条数下限、信号词数量、全局 template_id / code 唯一
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 统一 UTF-8 输出，避免 Windows 控制台 GBK 编码报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "templates"
FIELDS_FILE = ROOT / "data" / "five_flow_fields.json"

SECTION_ORDER = ["opening", "contact", "process", "loss", "evidence"]
SECTION_TITLES = {
    "opening": "固定开头",
    "contact": "接触引流",
    "process": "被骗经过",
    "loss": "资金损失",
    "evidence": "证据补充",
}
SECTION_MIN = {"opening": 3, "contact": 4, "process": 6, "loss": 5, "evidence": 3}
MIN_TOTAL = 22
QID_RE = re.compile(r"^T\d{2}-S[1-5]-Q\d{2}$")

# 文件 → code 对应关系（规范第 1 节）
FILE_CODES = {
    "T00_general": ("T00", "general"),
    "T01_ec_logistics_service": ("T01", "ec_logistics_service"),
    "T02_love_romance": ("T02", "love_romance"),
    "T03_fake_investment": ("T03", "fake_investment"),
    "T04_fake_credit_report": ("T04", "fake_credit_report"),
    "T05_brushing_rebate": ("T05", "brushing_rebate"),
    "T06_impersonate_police": ("T06", "impersonate_police"),
    "T07_impersonate_leader": ("T07", "impersonate_leader"),
    "T08_game_trade": ("T08", "game_trade"),
    "T09_fake_shopping": ("T09", "fake_shopping"),
    "T10_fake_loan": ("T10", "fake_loan"),
    "T11_military_police_purchase": ("T11", "military_police_purchase"),
    "T12_fake_prize": ("T12", "fake_prize"),
    "T13_flight_refund": ("T13", "flight_refund"),
    "T14_crypto_investment": ("T14", "crypto_investment"),
    "T15_cancel_membership": ("T15", "cancel_membership"),
    "T16_naked_chat_extortion": ("T16", "naked_chat_extortion"),
    "T17_elder_scam": ("T17", "elder_scam"),
    "T18_campus_loan": ("T18", "campus_loan"),
    "T19_prostitution_lure": ("T19", "prostitution_lure"),
}


def load_field_dict():
    data = json.loads(FIELDS_FILE.read_text(encoding="utf-8"))
    mapping = {}
    for flow in data["flows"]:
        mapping[flow["key"]] = {f["key"] for f in flow["fields"]}
    return mapping


def validate(directory: Path) -> tuple[list[str], dict]:
    errors: list[str] = []
    stats = {"files": 0, "questions": 0, "by_template": {}}
    field_dict = load_field_dict()

    files = sorted(directory.glob("*.json"))
    if len(files) != 20:
        errors.append(f"[目录] 期望 20 个模板文件，实际 {len(files)} 个")

    seen_ids: dict[str, str] = {}
    seen_codes: dict[str, str] = {}
    seen_qids: dict[str, str] = {}

    for path in files:
        stats["files"] += 1
        stem = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"[{path.name}] JSON 解析失败: {exc}")
            continue

        if stem not in FILE_CODES:
            errors.append(f"[{path.name}] 文件名不在规范清单内")
            continue
        expect_id, expect_code = FILE_CODES[stem]

        for key in ("template_id", "code", "name", "category", "description",
                    "feature_signals", "weight_keywords", "sections", "is_general"):
            if key not in data:
                errors.append(f"[{path.name}] 缺少字段 {key}")

        tid = data.get("template_id", "")
        code = data.get("code", "")
        if tid != expect_id:
            errors.append(f"[{path.name}] template_id={tid}，期望 {expect_id}")
        if code != expect_code:
            errors.append(f"[{path.name}] code={code}，期望 {expect_code}")
        if tid in seen_ids:
            errors.append(f"[{path.name}] template_id 重复: {tid}")
        seen_ids[tid] = path.name
        if code in seen_codes:
            errors.append(f"[{path.name}] code 重复: {code}")
        seen_codes[code] = path.name

        if not data.get("is_general") and tid == "T00":
            errors.append("[T00] is_general 必须为 true")
        if data.get("is_general") and tid != "T00":
            errors.append(f"[{path.name}] 仅 T00 允许 is_general=true")

        name = data.get("name", "")
        if not isinstance(name, str) or not (2 <= len(name) <= 24):
            errors.append(f"[{path.name}] name 长度异常")
        desc = data.get("description", "")
        if not isinstance(desc, str) or not (60 <= len(desc) <= 220):
            errors.append(f"[{path.name}] description 应为 60~220 字，实际 {len(desc)}")

        signals = data.get("feature_signals") or []
        if not (8 <= len(signals) <= 14):
            errors.append(f"[{path.name}] feature_signals 应为 8~14 个，实际 {len(signals)}")
        if any((not isinstance(s, str)) or not s.strip() for s in signals):
            errors.append(f"[{path.name}] feature_signals 含空值或非字符串")
        if len(set(signals)) != len(signals):
            errors.append(f"[{path.name}] feature_signals 存在重复项")

        keywords = data.get("weight_keywords") or []
        if not (3 <= len(keywords) <= 6):
            errors.append(f"[{path.name}] weight_keywords 应为 3~6 个，实际 {len(keywords)}")

        sections = data.get("sections") or []
        keys = [s.get("key") for s in sections]
        if keys != SECTION_ORDER:
            errors.append(f"[{path.name}] sections 必须为 {SECTION_ORDER}，实际 {keys}")
            continue

        total = 0
        for idx, section in enumerate(sections):
            skey = section["key"]
            if section.get("title") != SECTION_TITLES[skey]:
                errors.append(f"[{path.name}] 章节 {skey} 标题应为 {SECTION_TITLES[skey]}")
            questions = section.get("questions") or []
            if len(questions) < SECTION_MIN[skey]:
                errors.append(
                    f"[{path.name}] 章节 {skey} 问题数 {len(questions)} < 下限 {SECTION_MIN[skey]}"
                )
            total += len(questions)
            for q in questions:
                qid = q.get("qid", "")
                if not QID_RE.match(str(qid)):
                    errors.append(f"[{path.name}] qid 格式不合法: {qid}")
                elif not qid.startswith(f"{tid}-S{idx + 1}-"):
                    errors.append(f"[{path.name}] qid 章节序号与所在章节不一致: {qid}")
                if qid in seen_qids:
                    errors.append(f"[{path.name}] qid 重复: {qid}（另见 {seen_qids[qid]}）")
                seen_qids[qid] = path.name
                text = q.get("text", "")
                if not isinstance(text, str) or not (8 <= len(text) <= 160):
                    errors.append(f"[{path.name}] {qid} 问题文本长度异常: {len(text)}")
                if not text.strip().endswith(("？", "?", "：", ":")):
                    errors.append(f"[{path.name}] {qid} 问题文本应以问号或冒号结尾")
                if not isinstance(q.get("key_item"), bool):
                    errors.append(f"[{path.name}] {qid} key_item 必须为布尔值")
                flow = q.get("flow")
                field = q.get("field")
                if flow is None:
                    if field is not None:
                        errors.append(f"[{path.name}] {qid} flow 为 null 时 field 必须为 null")
                else:
                    if flow not in field_dict:
                        errors.append(f"[{path.name}] {qid} flow 非法: {flow}")
                    elif field is None:
                        errors.append(f"[{path.name}] {qid} flow={flow} 时 field 不能为空")
                    elif field not in field_dict[flow]:
                        errors.append(f"[{path.name}] {qid} field={field} 不属于流 {flow}")

        if total < MIN_TOTAL:
            errors.append(f"[{path.name}] 问题总数 {total} < 下限 {MIN_TOTAL}")
        stats["questions"] += total
        stats["by_template"][tid] = total

    return errors, stats


def main() -> int:
    directory = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_DIR
    if not directory.exists():
        print(f"[FATAL] 模板目录不存在: {directory}")
        return 1
    errors, stats = validate(directory)
    print("=" * 72)
    print("问询模板数据校验报告")
    print("=" * 72)
    print(f"模板文件: {stats['files']} 个    问题条目: {stats['questions']} 条")
    if stats["by_template"]:
        line = "  ".join(f"{k}:{v}" for k, v in sorted(stats["by_template"].items()))
        print(f"分布: {line}")
    print("-" * 72)
    if errors:
        for err in errors:
            print("  [FAIL]", err)
        print("-" * 72)
        print(f"结果: 不通过，共 {len(errors)} 项问题")
        return 1
    print("结果: 全部通过 [PASS]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

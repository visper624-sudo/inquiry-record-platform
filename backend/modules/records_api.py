# -*- coding: utf-8 -*-
"""笔录台账、接报建档、模板选定、三栏问询、五流与 AI 建议接口。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from config import (CASE_CATEGORIES, FLOW_LABELS, SECTIONS, SECTION_TITLE, STAGE_LABELS,
                    STAGE_NEXT, STATUS_LABELS)
from core import audit, db, security
from core.http import ApiError, StreamResponse, sse_event
from core.router import api
from services import ai_svc, docx_svc, fiveflow_svc, templates_svc

EDITABLE_FIELDS = ("case_category", "brief", "reporter_name", "reporter_phone", "reporter_id_no",
                   "report_time", "case_location", "unit", "case_no")
CASE_NO_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")


# ------------------------------------------------------------------ 通用助手
def _record_row(record_id: str) -> dict | None:
    return db.one(
        "SELECT r.*, u.name AS police_name, u.badge_no AS police_badge, o.name AS org_name "
        "FROM record r JOIN user u ON u.id = r.police_user_id JOIN org o ON o.id = r.org_id "
        "WHERE r.id = ?",
        (record_id,),
    )


def _require_record(record_id: str, user: dict, edit: bool = False) -> dict:
    record = _record_row(record_id)
    if not record:
        raise ApiError("笔录不存在或已归档", 404, "record_not_found")
    if not security.can_access(user, record):
        raise ApiError("该笔录不属于你的机构管辖范围", 403, "org_forbidden")
    if edit:
        if not security.can_edit(user):
            raise ApiError("反诈研判员为只读复核角色，不能修改问询内容", 403, "readonly_role")
        if record["stage"] in ("completed",) and user["role"] != "admin":
            raise ApiError("该笔录已完成问询并封存，如需修改请联系管理员", 409, "record_sealed")
    return record


def _record_public(record: dict, template_map: dict | None = None) -> dict:
    item = dict(record)
    item["stage_label"] = STAGE_LABELS.get(record["stage"], record["stage"])
    item["status"] = STATUS_LABELS.get(record["stage"], record["stage"])
    item["selected_templates"] = db.json_load(record["selected_templates"], [])
    item["flow_excluded"] = db.json_load(record["flow_excluded"], [])
    item["snapshot"] = db.json_load(record["snapshot"], {})
    if template_map is None:
        item["template_names"] = [t["name"] for t in
                                  templates_svc.get_many(item["selected_templates"])]
    else:
        item["template_names"] = [template_map[tid]["name"] for tid in item["selected_templates"]
                                  if tid in template_map]
    item["brief_preview"] = (record["brief"] or "")[:36] + ("…" if len(record["brief"] or "") > 36 else "")
    return item


def _qa_items_map(record_ids: list[str]) -> dict[str, list[dict]]:
    """批量取多条笔录的问答，避免列表页 N+1 查询（审查报告 P2）。"""
    if not record_ids:
        return {}
    placeholders = ",".join("?" for _ in record_ids)
    rows = db.rows(
        f"SELECT * FROM qa_item WHERE record_id IN ({placeholders}) AND is_deleted = 0 "
        f"ORDER BY record_id ASC, section ASC, sort_no ASC", record_ids)
    labels = {"template": "模板预置", "ai_suggested": "AI 建议", "manual": "民警录入",
              "imported": "历史导入"}
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        row["source_label"] = labels.get(row["source"], row["source"])
        grouped.setdefault(row["record_id"], []).append(row)
    return grouped


def _qa_items(record_id: str) -> list[dict]:
    rows = db.rows(
        "SELECT * FROM qa_item WHERE record_id = ? AND is_deleted = 0 ORDER BY section ASC, sort_no ASC",
        (record_id,),
    )
    ordered = []
    for row in rows:
        row["source_label"] = {"template": "模板预置", "ai_suggested": "AI 建议", "manual": "民警录入",
                              "imported": "历史导入"}.get(row["source"], row["source"])
        ordered.append(row)
    return ordered


def _flow_state(record: dict, items: list[dict] | None = None) -> dict:
    items = items if items is not None else _qa_items(record["id"])
    return fiveflow_svc.analyze(items, db.json_load(record["flow_excluded"], []))


def _recommendations(record: dict) -> list[dict]:
    return templates_svc.recommend(record.get("brief", ""), record.get("case_category", ""), top_n=3)


def _outline(items: list[dict]) -> list[dict]:
    sections = []
    for section in SECTIONS:
        section_items = [i for i in items if i["section"] == section["key"]]
        sections.append({
            "key": section["key"],
            "title": section["title"],
            "items": [{
                "id": i["id"],
                "question": i["question"],
                "answer": i["answer"],
                "source": i["source"],
                "source_label": i["source_label"],
                "flow": i["flow"],
                "field": i["field"],
                "updated_at": i["updated_at"],
            } for i in section_items],
            "answered": sum(1 for i in section_items if (i["answer"] or "").strip()),
            "total": len(section_items),
        })
    return sections


def _progress(items: list[dict]) -> dict:
    total = len(items)
    answered = sum(1 for i in items if (i["answer"] or "").strip())
    return {
        "total": total,
        "answered": answered,
        "percent": round(answered / total * 100, 1) if total else 0.0,
    }


def _materials(record_id: str) -> list[dict]:
    rows = db.rows(
        "SELECT m.*, u.name AS uploader_name FROM material m LEFT JOIN user u ON u.id = m.uploader_id "
        "WHERE m.record_id = ? ORDER BY m.created_at DESC",
        (record_id,),
    )
    for row in rows:
        row["size_text"] = _size_text(row["size"])
    return rows


def _size_text(size: int) -> str:
    size = int(size or 0)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _refresh_advice(record: dict, items: list[dict], flow_state: dict) -> list[dict]:
    return ai_svc.refresh_advice(record, flow_state, _recommendations(record))


def _bundle(record: dict, items: list[dict], user: dict | None = None,
            include_advice: bool = True) -> dict:
    flow_state = _flow_state(record, items)
    advice = _refresh_advice(record, items, flow_state) if include_advice else ai_svc.list_advice(record["id"])
    role = (user or {}).get("role", "police")
    return {
        "record": _record_public(record),
        "outline": _outline(items),
        "flow": flow_state,
        "advice": advice,
        "advice_stats": ai_svc.advice_stats(record["id"]),
        "materials": _materials(record["id"]),
        "progress": _progress(items),
        "llm": ai_svc.llm_status(),
        "permissions": {
            "can_edit": role in ("police", "admin"),
            "can_export": True,
        },
        "session": {"elapsed_sec": record.get("elapsed_sec", 0),
                    "scroll_anchor": db.json_load(record["snapshot"], {}).get("scroll_anchor")},
    }


# ------------------------------------------------------------------ 台账
@api("GET", r"^/api/records$")
def list_records(req):
    page = max(1, req.arg_int("page", 1))
    page_size = min(50, max(5, req.arg_int("page_size", 8)))
    keyword = req.arg("keyword")
    status = req.arg("status")
    category = req.arg("category")

    where = ["r.is_archived = 0"]
    params: list = []
    scope = req.user.get("org_scope")
    if scope is not None:
        where.append(f"r.org_id IN ({','.join('?' for _ in scope)})")
        params.extend(scope)
    if keyword:
        where.append("(r.case_no LIKE ? OR r.brief LIKE ? OR r.reporter_name LIKE ? OR r.case_category LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like, like])
    if status:
        if status == "pending":
            where.append("r.stage IN ('intake', 'templates')")
        elif status in ("inquiry", "completed"):
            where.append("r.stage = ?")
            params.append(status)
    if category:
        where.append("r.case_category = ?")
        params.append(category)

    where_sql = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM record r WHERE {where_sql}", params, 0) or 0
    rows = db.rows(
        f"SELECT r.*, u.name AS police_name, u.badge_no AS police_badge, o.name AS org_name "
        f"FROM record r JOIN user u ON u.id = r.police_user_id JOIN org o ON o.id = r.org_id "
        f"WHERE {where_sql} ORDER BY r.updated_at DESC, r.case_no DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    items = []
    record_ids = [row["id"] for row in rows]
    qa_map = _qa_items_map(record_ids)
    template_ids = sorted({tid for row in rows
                           for tid in db.json_load(row["selected_templates"], [])})
    template_map = templates_svc.get_many_map(template_ids)
    for row in rows:
        record = _record_public(row, template_map)
        flow_state = _flow_state(row, qa_map.get(row["id"], []))
        record["key_gap_count"] = len(flow_state.get("key_gaps", []))
        record["completeness"] = flow_state.get("completeness", 0)
        record["gap_preview"] = [
            f"{g['flow_name']}·{g['label']}" for g in flow_state.get("key_gaps", [])[:2]
        ]
        items.append(record)
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)$")
def get_record(req, record_id):
    record = _require_record(record_id, req.user)
    data = _record_public(record)
    data["materials"] = _materials(record_id)
    data["progress"] = _progress(_qa_items(record_id))
    return {"record": data}


@api("POST", r"^/api/records$", roles=("police", "admin"))
def create_record(req):
    security.require_permission(req.user, "can_create_record")
    brief = req.arg("brief")
    category = req.arg("case_category") or req.arg("category")
    if not brief or len(brief) < 10:
        raise ApiError("简要案情不得少于 10 个字，请完整描述案件事实")
    if category not in CASE_CATEGORIES:
        raise ApiError("请选择合法的案件类别")
    now = db.now_iso()
    record_id = db.new_id("RC", 10)
    case_no = (req.arg("case_no") or _next_case_no()).strip()
    if not CASE_NO_RE.match(case_no):
        raise ApiError("案件编号只能包含字母、数字、下划线或短横线（4~32 位）")
    if db.one("SELECT id FROM record WHERE case_no = ?", (case_no,)):
        raise ApiError("案件编号已存在，请检查后重试", 409)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO record (id, case_no, case_category, brief, reporter_name, reporter_phone, "
            "reporter_id_no, report_time, case_location, unit, org_id, police_user_id, stage, "
            "external_source, external_case_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'intake', ?, ?, ?, ?)",
            (record_id, case_no, category, brief, req.arg("reporter_name"), req.arg("reporter_phone"),
             req.arg("reporter_id_no"), req.arg("report_time") or now, req.arg("case_location"),
             req.arg("unit") or req.user.get("org_name", ""), req.user["org_id"], req.user["id"],
             req.arg("external_source"), req.arg("external_case_id"), now, now),
        )
    audit.write(req.user, "record.create", "record", record_id, {"case_no": case_no, "category": category}, req)
    return {"record": _record_public(_record_row(record_id))}


def _next_case_no() -> str:
    prefix = "JC" + datetime.now().strftime("%Y%m%d")
    count = db.scalar("SELECT COUNT(*) FROM record WHERE case_no LIKE ?", (prefix + "%",), 0) or 0
    while True:
        count += 1
        candidate = f"{prefix}{count:03d}"
        if not db.one("SELECT id FROM record WHERE case_no = ?", (candidate,)):
            return candidate


@api("PATCH", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)$", roles=("police", "admin"))
def update_record(req, record_id):
    record = _require_record(record_id, req.user, edit=True)
    assignments, params, changed = [], [], []
    for field in EDITABLE_FIELDS:
        if field in req.json:
            value = str(req.json.get(field) or "").strip()
            if field == "case_category" and value not in CASE_CATEGORIES:
                raise ApiError("案件类别不合法")
            if field == "case_no" and value and not CASE_NO_RE.match(value):
                raise ApiError("案件编号只能包含字母、数字、下划线或短横线（4~32 位）")
            if field == "brief" and len(value) < 10:
                raise ApiError("简要案情不得少于 10 个字")
            assignments.append(f"{field} = ?")
            params.append(value)
            changed.append(field)
    if not assignments:
        raise ApiError("没有需要更新的字段")
    assignments.append("updated_at = ?")
    params.extend([db.now_iso(), record_id])
    with db.write() as conn:
        conn.execute(f"UPDATE record SET {', '.join(assignments)} WHERE id = ?", params)
    audit.write(req.user, "record.update", "record", record_id, {"fields": changed}, req)
    return {"record": _record_public(_record_row(record_id))}


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/stage$", roles=("police", "admin"))
def change_stage(req, record_id):
    record = _require_record(record_id, req.user, edit=True)
    target = req.arg("stage")
    if target not in STAGE_LABELS:
        raise ApiError("阶段不合法")
    if target == record["stage"]:
        return {"stage": target, "stage_label": STAGE_LABELS[target]}
    expected = STAGE_NEXT.get(record["stage"])
    if target != expected and req.user["role"] != "admin":
        raise ApiError(f"阶段流转必须按顺序：{STAGE_LABELS.get(record['stage'])} → {STAGE_LABELS.get(expected or '')}", 409)
    if target == "inquiry" and not db.json_load(record["selected_templates"], []):
        raise ApiError("请先选定问询模板再进入问询")
    now = db.now_iso()
    with db.write() as conn:
        conn.execute("UPDATE record SET stage = ?, updated_at = ? WHERE id = ?", (target, now, record_id))
        if target == "completed":
            conn.execute("UPDATE record SET completed_at = ? WHERE id = ?", (now, record_id))
    audit.write(req.user, "record.stage", "record", record_id,
                {"from": record["stage"], "to": target}, req)
    return {"stage": target, "stage_label": STAGE_LABELS[target]}


# ------------------------------------------------------------------ 外部系统（Mock）
@api("GET", r"^/api/mock/external/cases$")
def mock_external_cases(req):
    keyword = req.arg("keyword")
    system = req.arg("system")
    sql = "SELECT * FROM external_case WHERE 1 = 1"
    params: list = []
    if system:
        sql += " AND system = ?"
        params.append(system)
    if keyword:
        sql += " AND (case_no LIKE ? OR reporter_name LIKE ? OR brief LIKE ?)"
        like = f"%{keyword}%"
        params += [like, like, like]
    sql += " ORDER BY report_time DESC LIMIT 20"
    rows = db.rows(sql, params)
    return {
        "system": system or "all",
        "items": rows,
        "systems": [{"key": k, "name": v} for k, v in
                    (("zhiyanpan", "智研判平台"), ("zhianguan", "智案管平台"))],
    }


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/sync-external$", roles=("police", "admin"))
def sync_external(req, record_id):
    record = _require_record(record_id, req.user, edit=True)
    external_id = req.arg("external_case_id")
    case = db.one("SELECT * FROM external_case WHERE id = ?", (external_id,))
    if not case:
        raise ApiError("未检索到该外部案件，请重新检索", 404)
    now = db.now_iso()
    with db.write() as conn:
        conn.execute(
            "UPDATE record SET case_category = ?, brief = ?, reporter_name = ?, reporter_phone = ?, "
            "report_time = ?, case_location = ?, unit = ?, external_source = ?, external_case_id = ?, "
            "updated_at = ? WHERE id = ?",
            (case["case_category"], case["brief"], case["reporter_name"], case["reporter_phone"],
             case["report_time"], case["location"], case["unit"], case["system"], case["id"], now, record_id),
        )
    audit.write(req.user, "record.sync_external", "record", record_id,
                {"external_case_id": external_id, "system": case["system"]}, req)
    record = _record_row(record_id)
    return {"record": _record_public(record), "recommendations": _recommendations(record)}


# ------------------------------------------------------------------ 模板推荐与选定
@api("GET", r"^/api/templates$")
def templates_list(req):
    keyword = req.arg("keyword")
    category = req.arg("category")
    items = templates_svc.list_templates(keyword, category)
    return {"items": items, "categories": templates_svc.categories(),
            "count": len(items)}


@api("POST", r"^/api/templates/recommend$")
def templates_recommend(req):
    brief = req.arg("brief")
    if not brief:
        raise ApiError("请先填写简要案情")
    recommendations = templates_svc.recommend(brief, req.arg("case_category"), top_n=3)
    return {"recommendations": recommendations}


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/templates$")
def record_templates(req, record_id):
    record = _require_record(record_id, req.user)
    recommendations = _recommendations(record)
    library = templates_svc.list_templates()
    return {
        "record": _record_public(record),
        "recommendations": recommendations,
        "library": library,
        "categories": templates_svc.categories(),
        "selected": db.json_load(record["selected_templates"], []),
        "judgement": ai_svc.type_judgement(record, recommendations),
    }


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/templates$", roles=("police", "admin"))
def select_templates(req, record_id):
    record = _require_record(record_id, req.user, edit=True)
    template_ids = req.json.get("template_ids") or []
    if isinstance(template_ids, str):
        template_ids = [t for t in template_ids.split(",") if t]
    template_ids = [str(t) for t in template_ids if t]
    if not template_ids:
        raise ApiError("请至少选定一个问询模板")
    templates = templates_svc.get_many(template_ids)
    if len(templates) != len(template_ids):
        raise ApiError("存在无效模板，请刷新后重试")
    existing = _qa_items(record_id)
    replace = bool(req.json.get("replace"))
    if existing and not replace:
        raise ApiError("该笔录已生成问询大纲，重新选择模板将覆盖现有问答，请确认后重试", 409, "outline_exists")
    merged = templates_svc.merge_outline(template_ids)
    now = db.now_iso()
    with db.write() as conn:
        if replace or existing:
            conn.execute("DELETE FROM qa_item WHERE record_id = ?", (record_id,))
        for section in merged["sections"]:
            for idx, question in enumerate(section["questions"]):
                conn.execute(
                    "INSERT INTO qa_item (id, record_id, section, question, answer, source, flow, field, "
                    "sort_no, is_deleted, created_at, updated_at) VALUES (?, ?, ?, ?, '', 'template', ?, ?, ?, 0, ?, ?)",
                    (db.new_id("QA", 12), record_id, section["key"], question["text"],
                     question["flow"], question["field"], idx, now, now),
                )
        conn.execute(
            "UPDATE record SET selected_templates = ?, stage = 'inquiry', updated_at = ? WHERE id = ?",
            (db.json_dump(template_ids), now, record_id),
        )
    audit.write(req.user, "record.template_select", "record", record_id,
                {"template_ids": template_ids, "question_count": merged["total"]}, req)
    record = _record_row(record_id)
    items = _qa_items(record_id)
    return {
        "record": _record_public(record),
        "bundle": _bundle(record, items, req.user),
        "merged": merged,
    }


# ------------------------------------------------------------------ 工作台
@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/workbench$")
def workbench(req, record_id):
    record = _require_record(record_id, req.user)
    items = _qa_items(record_id)
    data = _bundle(record, items, req.user)
    data["judgement"] = ai_svc.type_judgement(record, _recommendations(record))
    return data


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/analysis$")
def record_analysis(req, record_id):
    record = _require_record(record_id, req.user)
    items = _qa_items(record_id)
    flow_state = _flow_state(record, items)
    recommendations = _recommendations(record)
    judgement = ai_svc.type_judgement(record, recommendations)
    return {
        "record": _record_public(record),
        "flow": flow_state,
        "judgement": judgement,
        "guides": ai_svc.investigation_guides(judgement["category"]),
        "templates": templates_svc.get_many(db.json_load(record["selected_templates"], [])),
        "progress": _progress(items),
        "material_count": len(_materials(record_id)),
        "summary": docx_svc.flow_summary_text(flow_state),
        "advice_stats": ai_svc.advice_stats(record_id),
    }


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/save$", roles=("police", "admin"))
def save_progress(req, record_id):
    record = _require_record(record_id, req.user, edit=True)
    elapsed = max(0, req.arg_int("elapsed_sec", record.get("elapsed_sec", 0)))
    snapshot = db.json_load(record["snapshot"], {})
    snapshot.update({
        "saved_at": db.now_iso(),
        "scroll_anchor": req.json.get("scroll_anchor") or snapshot.get("scroll_anchor"),
        "last_section": req.arg("last_section") or snapshot.get("last_section"),
    })
    with db.write() as conn:
        conn.execute("UPDATE record SET elapsed_sec = ?, snapshot = ?, updated_at = ? WHERE id = ?",
                     (elapsed, db.json_dump(snapshot), db.now_iso(), record_id))
    audit.write(req.user, "record.save", "record", record_id, {"elapsed_sec": elapsed}, req)
    return {"saved_at": snapshot["saved_at"], "elapsed_sec": elapsed}


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/resume$")
def resume(req, record_id):
    record = _require_record(record_id, req.user)
    snapshot = db.json_load(record["snapshot"], {})
    stage = record["stage"]
    items = _qa_items(record_id)
    redirect = {
        "intake": f"/index.html?record={record_id}&modal=intake",
        "templates": f"/templates.html?record={record_id}",
        "inquiry": f"/workbench.html?record={record_id}",
        "completed": f"/document.html?record={record_id}",
    }[stage]
    return {
        "record": _record_public(record),
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
        "elapsed_sec": record.get("elapsed_sec", 0),
        "scroll_anchor": snapshot.get("scroll_anchor"),
        "last_section": snapshot.get("last_section"),
        "selected_templates": db.json_load(record["selected_templates"], []),
        "redirect": redirect,
        "progress": _progress(items),
        "resumed_at": db.now_iso(),
    }


# ------------------------------------------------------------------ 问答
@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/qa$", roles=("police", "admin"))
def create_qa(req, record_id):
    record = _require_record(record_id, req.user, edit=True)
    section = req.arg("section") or "process"
    if section not in SECTION_TITLE:
        raise ApiError("章节不合法")
    question = req.arg("question").strip()
    if not question:
        raise ApiError("问题内容不能为空")
    answer = req.json.get("answer") or ""
    source = req.arg("source") or "manual"
    if source not in ("manual", "template", "ai_suggested", "imported"):
        source = "manual"
    flow = req.json.get("flow") or None
    field = req.json.get("field") or None
    max_sort = db.scalar(
        "SELECT COALESCE(MAX(sort_no), -1) FROM qa_item WHERE record_id = ? AND section = ?",
        (record_id, section), -1)
    qa_id = db.new_id("QA", 12)
    now = db.now_iso()
    with db.write() as conn:
        conn.execute(
            "INSERT INTO qa_item (id, record_id, section, question, answer, source, flow, field, sort_no, "
            "is_deleted, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (qa_id, record_id, section, question, answer, source, flow, field, max_sort + 1, now, now),
        )
        conn.execute("UPDATE record SET updated_at = ? WHERE id = ?", (now, record_id))
    audit.write(req.user, "qa.create", "record", record_id,
                {"qa_id": qa_id, "section": section, "source": source}, req)
    return _qa_response(record_id, req.user, qa_id=qa_id)


@api("PATCH", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/qa/(?P<qa_id>[A-Za-z0-9]+)$", roles=("police", "admin"))
def update_qa(req, record_id, qa_id):
    _require_record(record_id, req.user, edit=True)
    qa = db.one("SELECT * FROM qa_item WHERE id = ? AND record_id = ?", (qa_id, record_id))
    if not qa:
        raise ApiError("问答不存在", 404)
    assignments, params = [], []
    for key in ("question", "answer"):
        if key in req.json:
            value = str(req.json.get(key) or "")
            if key == "question" and not value.strip():
                raise ApiError("问题内容不能为空")
            assignments.append(f"{key} = ?")
            params.append(value.strip() if key == "question" else value)
    if not assignments:
        raise ApiError("没有需要更新的字段")
    assignments.append("updated_at = ?")
    params.extend([db.now_iso(), qa_id])
    with db.write() as conn:
        conn.execute(f"UPDATE qa_item SET {', '.join(assignments)} WHERE id = ?", params)
        conn.execute("UPDATE record SET updated_at = ? WHERE id = ?", (db.now_iso(), record_id))
    audit.write(req.user, "qa.update", "record", record_id,
                {"qa_id": qa_id, "fields": [k for k in ("question", "answer") if k in req.json]}, req)
    return _qa_response(record_id, req.user, qa_id=qa_id)


@api("DELETE", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/qa/(?P<qa_id>[A-Za-z0-9]+)$", roles=("police", "admin"))
def delete_qa(req, record_id, qa_id):
    _require_record(record_id, req.user, edit=True)
    qa = db.one("SELECT * FROM qa_item WHERE id = ? AND record_id = ?", (qa_id, record_id))
    if not qa:
        raise ApiError("问答不存在", 404)
    with db.write() as conn:
        conn.execute("UPDATE qa_item SET is_deleted = 1, updated_at = ? WHERE id = ?", (db.now_iso(), qa_id))
        conn.execute("UPDATE record SET updated_at = ? WHERE id = ?", (db.now_iso(), record_id))
    audit.write(req.user, "qa.delete", "record", record_id,
                {"qa_id": qa_id, "section": qa["section"]}, req)
    return _qa_response(record_id, req.user)


def _qa_response(record_id: str, user: dict, qa_id: str | None = None) -> dict:
    record = _record_row(record_id)
    items = _qa_items(record_id)
    flow_state = _flow_state(record, items)
    advice = _refresh_advice(record, items, flow_state)
    data = {
        "outline": _outline(items),
        "flow": flow_state,
        "advice": advice,
        "advice_stats": ai_svc.advice_stats(record_id),
        "progress": _progress(items),
    }
    if qa_id:
        data["qa"] = next((i for i in items if i["id"] == qa_id), None)
    return data


# ------------------------------------------------------------------ AI 建议
@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/advice/refresh$", roles=("police", "admin"))
def refresh_advice(req, record_id):
    record = _require_record(record_id, req.user, edit=True)
    items = _qa_items(record_id)
    flow_state = _flow_state(record, items)
    advice = _refresh_advice(record, items, flow_state)
    return {"advice": advice, "flow": flow_state, "advice_stats": ai_svc.advice_stats(record_id),
            "llm": ai_svc.llm_status()}


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/ai/analyze$", roles=("police", "admin"))
def ai_deep_analyze(req, record_id):
    """调用大模型执行深度研判（类型判定 / 侦查指引 / 追问），失败自动回退规则引擎。"""
    record = _require_record(record_id, req.user, edit=True)
    items = _qa_items(record_id)
    flow_state = _flow_state(record, items)
    recommendations = _recommendations(record)
    result = ai_svc.deep_analysis(record, flow_state, recommendations, _progress(items))
    advice = ai_svc.persist_analysis_advice(record, result["analysis"], result["meta"]["engine"])
    audit.write(req.user, "ai.analyze", "record", record_id, {
        "engine": result["meta"].get("engine"),
        "model": result["meta"].get("model"),
        "latency_ms": result["meta"].get("latency_ms"),
        "tokens": result["meta"].get("tokens"),
        "followups": len(result["analysis"].get("followups", [])),
        "fallback": result["meta"].get("fallback"),
    }, req)
    return {
        "analysis": result["analysis"],
        "meta": result["meta"],
        "advice": advice,
        "flow": flow_state,
        "progress": _progress(items),
        "advice_stats": ai_svc.advice_stats(record_id),
        "llm": ai_svc.llm_status(),
    }


@api("GET", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/ai/last-stream$")
def ai_last_stream(req, record_id):
    """返回最近一次流式研判的回放数据（仅进程内缓存，用于演示与截图定格）。"""
    _require_record(record_id, req.user)
    return {"stream": ai_svc.last_stream(record_id)}


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/ai/analyze/stream$", roles=("police", "admin"))
def ai_deep_analyze_stream(req, record_id):
    """SSE 流式深度研判：实时推送模型增量、可读思维链与最终结构化结果。"""
    record = _require_record(record_id, req.user, edit=True)
    items = _qa_items(record_id)
    flow_state = _flow_state(record, items)
    recommendations = _recommendations(record)
    progress = _progress(items)
    user = req.user

    def generate():
        try:
            yield sse_event("open", {"record_id": record_id, "started_at": db.now_iso()})
            for event in ai_svc.deep_analysis_stream(record, flow_state, recommendations, progress):
                kind = event.get("type")
                if kind == "done":
                    analysis = event["analysis"]
                    meta = event["meta"]
                    advice = ai_svc.persist_analysis_advice(record, analysis, meta.get("engine", "rule"))
                    audit.write(user, "ai.analyze", "record", record_id, {
                        "engine": meta.get("engine"), "model": meta.get("model"),
                        "latency_ms": meta.get("latency_ms"), "tokens": meta.get("tokens"),
                        "followups": len(analysis.get("followups", [])),
                        "fallback": meta.get("fallback"), "streamed": True,
                    }, req)
                    yield sse_event("done", {
                        "analysis": analysis, "meta": meta,
                        "advice": advice, "flow": flow_state,
                        "progress": progress,
                        "advice_stats": ai_svc.advice_stats(record_id),
                        "llm": ai_svc.llm_status(),
                    })
                else:
                    yield sse_event(kind or "message", event)
        except Exception as exc:  # noqa: BLE001 - 流式过程异常以 error 事件返回
            yield sse_event("error", {"message": f"流式研判失败：{exc}"[:300]})

    return StreamResponse(generate(), headers=[("X-Record-Id", record_id)])


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/advice/(?P<advice_id>[A-Za-z0-9]+)/adopt$",
     roles=("police", "admin"))
def adopt_advice(req, record_id, advice_id):
    _require_record(record_id, req.user, edit=True)
    row = db.one("SELECT * FROM advice WHERE id = ? AND record_id = ?", (advice_id, record_id))
    if not row:
        raise ApiError("建议不存在", 404)
    payload = db.json_load(row["payload"], {})
    if row["kind"] != "followup" or not payload.get("question"):
        raise ApiError("该类型建议不支持直接采纳为问询问题")
    if row["status"] == "adopted":
        raise ApiError("该建议已采纳，请勿重复操作", 409)
    section = payload.get("section") or "process"
    max_sort = db.scalar(
        "SELECT COALESCE(MAX(sort_no), -1) FROM qa_item WHERE record_id = ? AND section = ?",
        (record_id, section), -1)
    qa_id = db.new_id("QA", 12)
    now = db.now_iso()
    with db.write() as conn:
        conn.execute(
            "INSERT INTO qa_item (id, record_id, section, question, answer, source, flow, field, sort_no, "
            "is_deleted, created_at, updated_at) VALUES (?, ?, ?, ?, '', 'ai_suggested', ?, ?, ?, 0, ?, ?)",
            (qa_id, record_id, section, payload["question"], payload.get("flow"), payload.get("field"),
             max_sort + 1, now, now),
        )
        conn.execute("UPDATE advice SET status = 'adopted', adopted_at = ? WHERE id = ?", (now, advice_id))
        conn.execute("UPDATE record SET updated_at = ? WHERE id = ?", (now, record_id))
    audit.write(req.user, "advice.adopt", "record", record_id,
                {"advice_id": advice_id, "qa_id": qa_id, "section": section,
                 "flow": payload.get("flow"), "field": payload.get("field")}, req)
    data = _qa_response(record_id, req.user, qa_id=qa_id)
    data["adopted"] = {"advice_id": advice_id, "qa_id": qa_id}
    return data


@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/flow/(?P<flow_key>[a-z]+)/exclude$",
     roles=("police", "admin"))
def exclude_flow(req, record_id, flow_key):
    record = _require_record(record_id, req.user, edit=True)
    if flow_key not in FLOW_LABELS:
        raise ApiError("流类型不合法")
    excluded = db.json_load(record["flow_excluded"], [])
    involved = req.arg_bool("involved", True)
    if involved and flow_key in excluded:
        excluded.remove(flow_key)
    elif not involved and flow_key not in excluded:
        excluded.append(flow_key)
    with db.write() as conn:
        conn.execute("UPDATE record SET flow_excluded = ?, updated_at = ? WHERE id = ?",
                     (db.json_dump(excluded), db.now_iso(), record_id))
    audit.write(req.user, "record.flow_exclude", "record", record_id,
                {"flow": flow_key, "involved": involved}, req)
    items = _qa_items(record_id)
    return {"flow": _flow_state(_record_row(record_id), items), "flow_excluded": excluded}


# ------------------------------------------------------------------ 完成问询
@api("POST", r"^/api/records/(?P<record_id>[A-Za-z0-9]+)/complete$", roles=("police", "admin"))
def complete_record(req, record_id):
    record = _require_record(record_id, req.user, edit=True)
    items = _qa_items(record_id)
    answered = [i for i in items if (i["answer"] or "").strip()]
    if not answered:
        raise ApiError("尚未录入任何答复，不能完成问询")
    now = db.now_iso()
    elapsed = max(0, req.arg_int("elapsed_sec", record.get("elapsed_sec", 0)))
    with db.write() as conn:
        conn.execute("UPDATE record SET stage = 'completed', completed_at = ?, elapsed_sec = ?, "
                     "updated_at = ? WHERE id = ?", (now, elapsed, now, record_id))
    flow_state = _flow_state(_record_row(record_id), items)
    audit.write(req.user, "record.complete", "record", record_id,
                {"answered": len(answered), "key_gaps": len(flow_state.get("key_gaps", []))}, req)
    return {
        "stage": "completed",
        "stage_label": STAGE_LABELS["completed"],
        "completed_at": now,
        "progress": _progress(items),
        "flow": flow_state,
        "redirect": f"/document.html?record={record_id}",
    }


# ------------------------------------------------------------------ 统计
@api("GET", r"^/api/stats/overview$")
def stats_overview(req):
    scope = req.user.get("org_scope")
    where, params = ["r.is_archived = 0"], []
    if scope is not None:
        where.append(f"r.org_id IN ({','.join('?' for _ in scope)})")
        params.extend(scope)
    where_sql = " AND ".join(where)

    total = db.scalar(f"SELECT COUNT(*) FROM record r WHERE {where_sql}", params, 0) or 0
    inquiry = db.scalar(f"SELECT COUNT(*) FROM record r WHERE {where_sql} AND r.stage = 'inquiry'", params, 0) or 0
    completed = db.scalar(f"SELECT COUNT(*) FROM record r WHERE {where_sql} AND r.stage = 'completed'", params, 0) or 0
    pending = db.scalar(f"SELECT COUNT(*) FROM record r WHERE {where_sql} AND r.stage IN ('intake','templates')", params, 0) or 0
    avg_duration = db.scalar(
        f"SELECT AVG(r.elapsed_sec) FROM record r WHERE {where_sql} "
        f"AND r.stage = 'completed' AND r.elapsed_sec > 0", params, 0) or 0
    today = datetime.now().strftime("%Y-%m-%d")
    today_new = db.scalar(
        f"SELECT COUNT(*) FROM record r WHERE {where_sql} AND r.created_at LIKE ?", params + [today + "%"], 0) or 0

    categories = db.rows(
        f"SELECT r.case_category AS name, COUNT(*) AS value FROM record r WHERE {where_sql} "
        f"GROUP BY r.case_category ORDER BY value DESC LIMIT 8", params)

    trend = []
    for offset in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        count = db.scalar(f"SELECT COUNT(*) FROM record r WHERE {where_sql} AND r.created_at LIKE ?",
                          params + [day + "%"], 0) or 0
        trend.append({"date": day[5:], "count": count})

    # 重点缺口案件：只统计已进入问询的案件；问答一次性批量取出，避免逐条 N+1（P3）
    key_gap_cases = 0
    candidates = db.rows(
        f"SELECT * FROM record r WHERE {where_sql} AND r.stage IN ('inquiry','completed')", params)
    if candidates:
        ids = [row["id"] for row in candidates]
        qa_map = _qa_items_map(ids)
        for row in candidates:
            if _flow_state(row, qa_map.get(row["id"], [])).get("key_gaps"):
                key_gap_cases += 1

    recent = db.rows(
        f"SELECT r.case_no, r.case_category, r.stage, r.updated_at, u.name AS police_name "
        f"FROM record r JOIN user u ON u.id = r.police_user_id WHERE {where_sql} "
        f"ORDER BY r.updated_at DESC LIMIT 6", params)
    for row in recent:
        row["stage_label"] = STAGE_LABELS.get(row["stage"], row["stage"])

    return {
        "cards": {
            "total": total,
            "inquiry": inquiry,
            "completed": completed,
            "pending": pending,
            "key_gap_cases": key_gap_cases,
            "today_new": today_new,
            "avg_duration_min": round(float(avg_duration) / 60, 1),
        },
        "by_category": categories,
        "trend": trend,
        "recent": recent,
    }

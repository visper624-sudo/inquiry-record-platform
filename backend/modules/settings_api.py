# -*- coding: utf-8 -*-
"""系统配置、模板维护、字段口径与审计查询接口（管理员/研判员）。"""
from __future__ import annotations

from config import ACTION_LABELS, DB_PATH, ROLES
from core import audit, db
from core.http import ApiError
from core.router import api
from services import ai_svc, fiveflow_svc, templates_svc


# ------------------------------------------------------------------ 五流字段口径
@api("GET", r"^/api/settings/five-flow$")
def get_five_flow(req):
    return {"config": fiveflow_svc.load_field_config(),
            "overridden": bool(db.get_config("five_flow_fields"))}


@api("PUT", r"^/api/settings/five-flow$", roles=("admin",))
def put_five_flow(req):
    payload = req.json
    try:
        config = fiveflow_svc.save_field_config(payload)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    audit.write(req.user, "setting.update", "config", "five_flow_fields", {"flows": 5}, req)
    return {"config": config}


@api("POST", r"^/api/settings/five-flow/reset$", roles=("admin",))
def reset_five_flow(req):
    config = fiveflow_svc.reset_field_config()
    audit.write(req.user, "setting.update", "config", "five_flow_fields", {"action": "reset"}, req)
    return {"config": config}


# ------------------------------------------------------------------ 大模型参数
@api("GET", r"^/api/settings/llm$")
def get_llm(req):
    return ai_svc.llm_status()


@api("PUT", r"^/api/settings/llm$", roles=("admin",))
def put_llm(req):
    try:
        config = ai_svc.save_llm_config(req.json)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    audit.write(req.user, "setting.update", "config", "llm",
                {k: v for k, v in config.items() if k != "api_key"}, req)
    return {"status": ai_svc.llm_status(), "config": config}


@api("POST", r"^/api/settings/llm/test$", roles=("admin",))
def test_llm(req):
    result = ai_svc.llm_self_test()
    audit.write(req.user, "setting.llm_test", "config", "llm",
                {"reachable": result.get("reachable"), "latency_ms": result.get("latency_ms")}, req)
    return result


# ------------------------------------------------------------------ 模板维护
@api("GET", r"^/api/settings/templates$")
def settings_templates(req):
    items = templates_svc.list_templates()
    return {
        "items": [{k: t[k] for k in ("template_id", "code", "name", "category", "description",
                                    "signals", "keywords", "is_general", "question_count", "version")}
                  for t in items],
        "categories": templates_svc.categories(),
        "usage": templates_svc.template_usage_stats(),
        "spec": "模板结构见 docs/模板规范.md；信号词可在本页调整后立即参与推荐打分。",
    }


@api("PATCH", r"^/api/settings/templates/(?P<template_id>[A-Za-z0-9]+)$", roles=("admin",))
def update_template(req, template_id):
    row = db.one("SELECT * FROM template WHERE template_id = ?", (template_id,))
    if not row:
        raise ApiError("模板不存在", 404)
    signals = req.json.get("signals")
    keywords = req.json.get("keywords")
    if signals is not None:
        if not isinstance(signals, list) or not signals:
            raise ApiError("信号词不能为空")
        signals = [str(s).strip() for s in signals if str(s).strip()][:14]
    if keywords is not None:
        keywords = [str(k).strip() for k in keywords if str(k).strip()][:6]
    with db.write() as conn:
        if signals is not None:
            conn.execute("UPDATE template SET signals = ? WHERE template_id = ?",
                         (db.json_dump(signals), template_id))
        if keywords is not None:
            conn.execute("UPDATE template SET keywords = ? WHERE template_id = ?",
                         (db.json_dump(keywords), template_id))
    templates_svc.invalidate_cache()
    audit.write(req.user, "setting.update", "template", template_id,
                {"signals": signals, "keywords": keywords}, req)
    return {"template": templates_svc.get_template(template_id)}


# ------------------------------------------------------------------ 审计
@api("GET", r"^/api/audit$", roles=("admin", "analyst"))
def list_audit(req):
    page = max(1, req.arg_int("page", 1))
    page_size = min(100, max(10, req.arg_int("page_size", 15)))
    action = req.arg("action")
    keyword = req.arg("keyword")
    where, params = ["1 = 1"], []
    if action:
        where.append("a.action = ?")
        params.append(action)
    if keyword:
        where.append("(a.badge_no LIKE ? OR a.target_id LIKE ? OR a.detail LIKE ?)")
        like = f"%{keyword}%"
        params += [like, like, like]
    where_sql = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM audit_log a WHERE {where_sql}", params, 0) or 0
    rows = db.rows(
        f"SELECT a.*, u.name AS user_name FROM audit_log a LEFT JOIN user u ON u.id = a.user_id "
        f"WHERE {where_sql} ORDER BY a.rowid DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    )
    for row in rows:
        row["action_label"] = ACTION_LABELS.get(row["action"], row["action"])
        row["detail"] = db.json_load(row["detail"], {})
        row["hash_short"] = (row["hash"] or "")[:12]
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "actions": [{"key": k, "name": v} for k, v in ACTION_LABELS.items()],
        "chain": audit.verify_chain(),
    }


# ------------------------------------------------------------------ 系统信息
@api("GET", r"^/api/settings/system$")
def system_info(req):
    counts = {
        "records": db.scalar("SELECT COUNT(*) FROM record", (), 0),
        "qa_items": db.scalar("SELECT COUNT(*) FROM qa_item WHERE is_deleted = 0", (), 0),
        "templates": db.scalar("SELECT COUNT(*) FROM template", (), 0),
        "materials": db.scalar("SELECT COUNT(*) FROM material", (), 0),
        "users": db.scalar("SELECT COUNT(*) FROM user", (), 0),
        "audit": db.scalar("SELECT COUNT(*) FROM audit_log", (), 0),
        "imports": db.scalar("SELECT COUNT(*) FROM import_job", (), 0),
    }
    return {
        "counts": counts,
        "db_path": str(DB_PATH),
        "roles": ROLES,
        "chain": audit.verify_chain(),
        "engine": ai_svc.llm_status()["engine_label"],
        "version": "1.0.0",
    }

# -*- coding: utf-8 -*-
"""认证、组织架构与账号管理接口。"""
from __future__ import annotations

import threading
import time

import config
from config import (CASE_CATEGORIES, FLOW_LABELS, FLOW_STATUS_LABELS, ROLES, SECTIONS,
                    STAGE_LABELS, STATUS_LABELS)
from core import audit, db, security
from core.http import ApiError, Response
from core.router import api

# 登录失败计数（进程内）：key = (警号, IP)，成功登录清零（审查报告 S6）
_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES: dict[tuple[str, str], dict] = {}
_PRUNE_THRESHOLD = 2000


def _login_key(req, badge_no: str) -> tuple[str, str]:
    return (badge_no, req.client_ip or "unknown")


def _lock_remaining(key: tuple[str, str]) -> int:
    with _LOGIN_LOCK:
        entry = _LOGIN_FAILURES.get(key)
        if not entry:
            return 0
        if entry["until"] <= 0:          # 尚未达到锁定阈值，保留计数
            return 0
        remain = int(entry["until"] - time.time())
        if remain <= 0:
            _LOGIN_FAILURES.pop(key, None)
            return 0
        return remain


def _record_failure(key: tuple[str, str]) -> None:
    with _LOGIN_LOCK:
        if len(_LOGIN_FAILURES) > _PRUNE_THRESHOLD:
            expired = [k for k, v in _LOGIN_FAILURES.items() if v["until"] <= time.time()]
            for k in expired[:1000]:
                _LOGIN_FAILURES.pop(k, None)
        entry = _LOGIN_FAILURES.setdefault(key, {"count": 0, "until": 0.0})
        entry["count"] += 1
        if entry["count"] >= config.LOGIN_MAX_FAILURES:
            entry["until"] = time.time() + config.LOGIN_LOCK_SECONDS
            entry["count"] = 0


def _clear_failures(key: tuple[str, str]) -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(key, None)


@api("POST", r"^/api/auth/login$", auth=False)
def login(req):
    badge_no = req.arg("badge_no")
    password = req.arg("password")
    if not badge_no or not password:
        raise ApiError("请填写警号与登录密码")
    key = _login_key(req, badge_no)
    remaining = _lock_remaining(key)
    if remaining:
        raise ApiError(f"登录失败次数过多，请 {remaining} 秒后再试", 429, "too_many_attempts")
    row = db.one("SELECT * FROM user WHERE badge_no = ?", (badge_no,))
    if not row:
        _record_failure(key)
        audit.write(None, "login", "user", badge_no, {"result": "fail", "reason": "not_found"}, req)
        raise ApiError("警号或密码错误", 401, "invalid_credentials")
    if row["status"] != "active":
        raise ApiError("该账号已停用，请联系系统管理员", 403, "user_disabled")
    if not security.verify_password(password, row["password_hash"]):
        _record_failure(key)
        audit.write({"id": row["id"], "badge_no": row["badge_no"], "org_id": row["org_id"]},
                    "login", "user", row["id"], {"result": "fail", "reason": "bad_password"}, req)
        raise ApiError("警号或密码错误", 401, "invalid_credentials")
    _clear_failures(key)
    token, expires = security.create_session(row["id"], req.client_ip, req.headers.get("user-agent", ""))
    with db.write() as conn:
        conn.execute("UPDATE user SET last_login_at = ? WHERE id = ?", (db.now_iso(), row["id"]))
    user = security.load_user(row["id"])
    audit.write(user, "login", "user", row["id"], {"result": "success"}, req)
    # 会话 token 只通过 HttpOnly Cookie 下发，不在响应体中回传（审查报告 S9）
    resp = Response.ok({
        "user": security.user_public(user),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    resp.set_cookie(security.cookie_name(), token, max_age=12 * 3600,
                    secure=config.COOKIE_SECURE)
    return resp


@api("POST", r"^/api/auth/logout$")
def logout(req):
    security.destroy_session(req.cookies.get(security.cookie_name(), ""))
    audit.write(req.user, "logout", "user", req.user["id"], {}, req)
    resp = Response.ok({"logged_out": True})
    resp.clear_cookie(security.cookie_name(), secure=config.COOKIE_SECURE)
    return resp


@api("GET", r"^/api/auth/me$")
def me(req):
    return {"user": security.user_public(req.user)}


@api("GET", r"^/api/health$", auth=False)
def health(req):
    return {
        "status": "ok",
        "service": "practice-2.11",
        "time": db.now_iso(),
        "templates": db.scalar("SELECT COUNT(*) FROM template", (), 0),
        "records": db.scalar("SELECT COUNT(*) FROM record", (), 0),
    }


@api("GET", r"^/api/meta$")
def meta(req):
    return {
        "categories": CASE_CATEGORIES,
        "sections": SECTIONS,
        "flow_labels": FLOW_LABELS,
        "flow_status_labels": FLOW_STATUS_LABELS,
        "roles": [{"key": k, "name": v} for k, v in ROLES.items()],
        "stages": [{"key": k, "name": v} for k, v in STAGE_LABELS.items()],
        "statuses": [{"key": k, "name": v} for k, v in STATUS_LABELS.items()],
    }


@api("GET", r"^/api/orgs$")
def orgs(req):
    return {"items": db.rows("SELECT * FROM org ORDER BY sort_no ASC, id ASC")}


@api("GET", r"^/api/orgs/tree$")
def org_tree(req):
    rows = db.rows("SELECT * FROM org ORDER BY sort_no ASC")
    by_parent: dict[str, list[dict]] = {}
    for row in rows:
        by_parent.setdefault(row["parent_id"] or "", []).append({**row, "children": []})
    index = {row["id"]: row for row in rows}

    def attach(node: dict) -> dict:
        node["children"] = [attach(child) for child in by_parent.get(node["id"], [])]
        return node

    roots = [attach(node) for node in by_parent.get("", [])]
    if not roots and index:
        roots = [attach({**row, "children": []}) for row in rows if not row["parent_id"]]
    return {"tree": roots}


@api("GET", r"^/api/users$")
def users(req):
    scope = req.user.get("org_scope")
    sql = ("SELECT u.id, u.badge_no, u.name, u.role, u.org_id, u.title, u.status, u.last_login_at, "
           "o.name AS org_name FROM user u JOIN org o ON o.id = u.org_id")
    params: list = []
    if scope is not None:
        placeholders = ",".join("?" for _ in scope)
        sql += f" WHERE u.org_id IN ({placeholders})"
        params += scope
    sql += " ORDER BY u.badge_no ASC"
    items = db.rows(sql, params)
    for item in items:
        item["role_name"] = ROLES.get(item["role"], item["role"])
    return {"items": items, "roles": ROLES}


@api("POST", r"^/api/users$", roles=("admin",))
def create_user(req):
    badge_no = req.arg("badge_no")
    name = req.arg("name")
    password = req.arg("password")
    role = req.arg("role") or "police"
    org_id = req.arg("org_id")
    if not badge_no or not name or not org_id:
        raise ApiError("警号、姓名、所属机构均为必填")
    if role not in ROLES:
        raise ApiError("角色不合法")
    if not password:
        raise ApiError("请设置初始密码（至少 8 位，且不能是常见弱口令）")
    try:
        security.validate_password(password)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    if db.one("SELECT id FROM user WHERE badge_no = ?", (badge_no,)):
        raise ApiError("该警号已存在")
    user_id = db.new_id("U", 8)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO user (id, badge_no, name, password_hash, role, org_id, title, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
            (user_id, badge_no, name, security.hash_password(password), role, org_id,
             req.arg("title"), db.now_iso()),
        )
    audit.write(req.user, "user.create", "user", user_id, {"badge_no": badge_no, "role": role}, req)
    return {"id": user_id}


@api("PATCH", r"^/api/users/(?P<uid>[A-Za-z0-9]+)$", roles=("admin",))
def update_user(req, uid):
    row = db.one("SELECT * FROM user WHERE id = ?", (uid,))
    if not row:
        raise ApiError("账号不存在", 404)
    fields, params = [], []
    for key in ("name", "role", "org_id", "title", "status"):
        value = req.arg(key)
        if value:
            fields.append(f"{key} = ?")
            params.append(value)
    if not fields:
        raise ApiError("没有需要更新的字段")
    params.append(uid)
    with db.write() as conn:
        conn.execute(f"UPDATE user SET {', '.join(fields)} WHERE id = ?", params)
    audit.write(req.user, "user.update", "user", uid, {"fields": req.json}, req)
    return {"updated": True}


@api("POST", r"^/api/users/(?P<uid>[A-Za-z0-9]+)/reset-password$", roles=("admin",))
def reset_password(req, uid):
    password = req.arg("password")
    if not password:
        raise ApiError("请设置新密码（至少 8 位，且不能是常见弱口令）")
    try:
        security.validate_password(password)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    with db.write() as conn:
        conn.execute("UPDATE user SET password_hash = ? WHERE id = ?",
                     (security.hash_password(password), uid))
    audit.write(req.user, "user.reset_password", "user", uid, {}, req)
    return {"reset": True}

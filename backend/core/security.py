# -*- coding: utf-8 -*-
"""认证、会话与组织数据权限。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta

from config import ROLES, SESSION_TTL_HOURS
from core import db
from core.http import ApiError

_ITERATIONS = 120_000
_COOKIE_NAME = "sid"
# 弱口令黑名单（生产环境应接入更完整的口令强度库）
_WEAK_PASSWORDS = {
    "123456", "12345678", "123456789", "password", "qwerty", "111111",
    "000000", "admin123", "abc123", "iloveyou", "666666", "888888",
}
_ORG_TTL_SEC = 60.0
_org_cache: dict = {"ts": 0.0, "by_id": {}, "children": {}, "paths": {}, "subtree": {}}


def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt, digest = encoded.split("$")
    except (ValueError, AttributeError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)).hex()
    return hmac.compare_digest(calc, digest)


def validate_password(password: str) -> None:
    """口令强度校验：长度、弱口令、字符多样性。"""
    password = password or ""
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    if password.lower() in _WEAK_PASSWORDS:
        raise ValueError("该密码过于常见，请更换")
    if len(set(password)) < 3:
        raise ValueError("密码字符过于单一，请混合字母、数字与符号")


def create_session(user_id: str, ip: str = "", ua: str = "") -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    with db.write() as conn:
        conn.execute("DELETE FROM session WHERE expires_at < ?", (db.now_iso(),))
        conn.execute(
            "INSERT INTO session (token, user_id, created_at, expires_at, ip, ua) VALUES (?, ?, ?, ?, ?, ?)",
            (token, user_id, now.strftime("%Y-%m-%dT%H:%M:%S"), expires.strftime("%Y-%m-%dT%H:%M:%S"), ip, ua[:200]),
        )
    return token, expires


def destroy_session(token: str) -> None:
    if not token:
        return
    with db.write() as conn:
        conn.execute("DELETE FROM session WHERE token = ?", (token,))


def resolve_user(req) -> dict | None:
    token = req.cookies.get(_COOKIE_NAME)
    if not token:
        header = req.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    if not token:
        return None
    row = db.one(
        "SELECT s.expires_at AS sess_exp, u.id, u.badge_no, u.name, u.role, u.org_id, u.title, u.status "
        "FROM session s JOIN user u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    )
    if not row:
        return None
    if row["sess_exp"] < db.now_iso() or row["status"] != "active":
        destroy_session(token)
        return None
    user = enrich(row)
    user["token"] = token
    return user


def load_user(user_id: str) -> dict | None:
    row = db.one(
        "SELECT id, badge_no, name, role, org_id, title, status FROM user WHERE id = ?", (user_id,)
    )
    if not row or row["status"] != "active":
        return None
    return enrich(row)


def enrich(row: dict) -> dict:
    user = dict(row)
    user.pop("sess_exp", None)
    user.pop("password_hash", None)
    user["role_name"] = ROLES.get(user["role"], user["role"])
    user["org_name"] = org_name(user["org_id"])
    user["org_path"] = org_path(user["org_id"])
    user["org_scope"] = visible_org_ids(user)
    return user


def _org_data() -> dict:
    """组织树缓存：60 秒内复用，避免每个请求全表扫描两次（审查报告 P4）。"""
    now = time.time()
    if not _org_cache["by_id"] or now - _org_cache["ts"] > _ORG_TTL_SEC:
        rows = db.rows("SELECT id, name, parent_id FROM org")
        by_id = {row["id"]: row for row in rows}
        children: dict[str, list[str]] = {}
        for row in rows:
            children.setdefault(row["parent_id"] or "", []).append(row["id"])
        _org_cache.update(ts=now, by_id=by_id, children=children, paths={}, subtree={})
    return _org_cache


def invalidate_org_cache() -> None:
    _org_cache.update({"ts": 0.0, "by_id": {}, "children": {}, "paths": {}, "subtree": {}})


def org_name(org_id: str) -> str:
    row = _org_data()["by_id"].get(org_id)
    return row["name"] if row else ""


def org_path(org_id: str) -> str:
    cache = _org_data()
    if org_id in cache["paths"]:
        return cache["paths"][org_id]
    by_id = cache["by_id"]
    names, cursor, guard = [], by_id.get(org_id), 0
    while cursor and guard < 10:
        names.append(cursor["name"])
        cursor = by_id.get(cursor.get("parent_id"))
        guard += 1
    path = " / ".join(reversed(names))
    cache["paths"][org_id] = path
    return path


def org_subtree(org_id: str) -> list[str]:
    cache = _org_data()
    if org_id in cache["subtree"]:
        return list(cache["subtree"][org_id])
    children = cache["children"]
    result, queue = [], [org_id]
    while queue:
        current = queue.pop(0)
        result.append(current)
        queue.extend(children.get(current, []))
    cache["subtree"][org_id] = result
    return list(result)


def visible_org_ids(user: dict) -> list[str] | None:
    """返回可见机构 ID 集合；None 表示全量（管理员/研判员）。"""
    if user["role"] in ("admin", "analyst"):
        return None
    return org_subtree(user["org_id"])


def can_access(user: dict, record: dict) -> bool:
    scope = user.get("org_scope")
    if scope is None:
        return True
    return record.get("org_id") in scope


def can_edit(user: dict) -> bool:
    """研判员为只读复核角色。"""
    return user["role"] in ("police", "admin")


def user_public(user: dict) -> dict:
    return {
        "id": user["id"],
        "badge_no": user["badge_no"],
        "name": user["name"],
        "role": user["role"],
        "role_name": user["role_name"],
        "org_id": user["org_id"],
        "org_name": user["org_name"],
        "org_path": user["org_path"],
        "title": user["title"],
        "permissions": _permissions(user),
    }


def _permissions(user: dict) -> dict:
    role = user["role"]
    return {
        "can_create_record": role in ("police", "admin"),
        "can_edit_inquiry": role in ("police", "admin"),
        "can_adopt_advice": role in ("police", "admin"),
        "can_upload_material": role in ("police", "admin"),
        "can_export_document": role in ("police", "analyst", "admin"),
        "can_manage_settings": role == "admin",
        "can_view_audit": role in ("admin", "analyst"),
        "can_view_all_orgs": user.get("org_scope") is None,
    }


def require_permission(user: dict, key: str) -> None:
    if not _permissions(user).get(key):
        raise ApiError("当前账号无此操作权限", 403, "forbidden")


def cookie_name() -> str:
    return _COOKIE_NAME

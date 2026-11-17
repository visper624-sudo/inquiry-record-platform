# -*- coding: utf-8 -*-
"""操作审计：全量留痕 + 哈希链（prev_hash -> hash），支持完整性校验。

修复说明（对应审查报告 S1/P6）：
- S1：写入路径加进程内互斥锁，使「读链尾 → 计算摘要 → 插入」原子化，
      避免多线程读到同一 prev_hash 造成链分叉；
- P6：校验改为增量模式——缓存「已校验到的 rowid / 链尾哈希 / 条数」，
      只对新增记录重算，审计页翻页不再全表重算；库被重建时自动回退全量。
"""
from __future__ import annotations

import hashlib
import threading

from core import db

_LOCK = threading.Lock()
_cache = {"last_rowid": 0, "last_hash": "", "count": 0, "broken_at": None}


def reset_cache() -> None:
    with _LOCK:
        _cache.update({"last_rowid": 0, "last_hash": "", "count": 0, "broken_at": None})


def _digest(prev_hash: str, payload: str) -> str:
    return hashlib.sha256(f"{prev_hash}|{payload}".encode("utf-8")).hexdigest()


def _row_payload(row: dict) -> str:
    return "|".join([row["id"], row["user_id"], row["badge_no"], row["action"],
                     row["target_type"], row["target_id"], row["detail"], row["created_at"]])


def write(user: dict | None, action: str, target_type: str = "", target_id: str = "",
          detail: dict | None = None, req=None, created_at: str | None = None) -> str:
    """写入一条审计记录，返回记录 ID。异常不影响主流程。"""
    audit_id = db.new_id("AL", 12)
    created_at = created_at or db.now_iso()
    detail_json = db.json_dump(detail or {})
    badge_no = (user or {}).get("badge_no", "")
    user_id = (user or {}).get("id", "")
    org_id = (user or {}).get("org_id", "")
    ip = getattr(req, "client_ip", "") if req is not None else ""
    ua = (req.headers.get("user-agent", "") if req is not None else "")[:200]
    with _LOCK:                      # ← 关键：链尾读取与插入必须原子
        try:
            with db.write() as conn:
                last = conn.execute("SELECT hash FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()
                prev = last[0] if last else ""
                digest = _digest(prev, "|".join([audit_id, user_id, badge_no, action,
                                                 target_type, target_id, detail_json, created_at]))
                conn.execute(
                    "INSERT INTO audit_log (id, user_id, badge_no, org_id, action, target_type, "
                    "target_id, detail, ip, ua, prev_hash, hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (audit_id, user_id, badge_no, org_id, action, target_type, target_id,
                     detail_json, ip, ua, prev, digest, created_at),
                )
                new_rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        except Exception:  # noqa: BLE001 - 审计失败不阻断业务
            return audit_id
        # 若缓存恰好已验证到本条的前驱，可直接续接，避免下次校验全表重算
        if _cache["last_hash"] == prev:
            _cache.update({"last_rowid": new_rowid, "last_hash": digest})
            _cache["count"] += 1
        else:
            _cache.update({"last_rowid": 0, "last_hash": "", "count": 0, "broken_at": None})
    return audit_id


def verify_chain(full: bool = False) -> dict:
    """校验哈希链完整性。full=True 强制全量重算；否则只校验新增记录。"""
    with _LOCK:
        total = db.scalar("SELECT COUNT(*) FROM audit_log", (), 0) or 0
        if _cache["broken_at"]:
            return {"total": total, "broken_at": _cache["broken_at"], "ok": False}
        # 库被重建 / 记录变少：缓存失效，回退全量
        if full or total < _cache["count"]:
            _cache.update({"last_rowid": 0, "last_hash": "", "count": 0, "broken_at": None})
        start_rowid = _cache["last_rowid"] if not full else 0
        prev = _cache["last_hash"] if not full else ""
        rows = db.rows(
            "SELECT rowid AS rid, id, user_id, badge_no, action, target_type, target_id, "
            "detail, prev_hash, hash, created_at FROM audit_log WHERE rowid > ? ORDER BY rowid ASC",
            (start_rowid,),
        )
        for row in rows:
            if row["prev_hash"] != prev or _digest(prev, _row_payload(row)) != row["hash"]:
                _cache["broken_at"] = row["id"]
                return {"total": total, "broken_at": row["id"], "ok": False}
            prev = row["hash"]
            _cache["last_rowid"] = row["rid"]
            _cache["last_hash"] = prev
            _cache["count"] += 1
        return {"total": total, "broken_at": None, "ok": True}

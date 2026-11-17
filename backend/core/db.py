# -*- coding: utf-8 -*-
"""SQLite 访问层：按线程复用连接、事务、查询助手与初始化。

性能说明（对应审查报告 P1）：
- 早期实现每次查询都 connect()/close() 并重复执行 PRAGMA，一次工作台请求会建几十条连接；
- 现改为 threading.local() 每线程一条连接，PRAGMA 只在建连与初始化时执行一次；
- 连接不跨线程共享，因此无需 check_same_thread=False。
"""
from __future__ import annotations

import atexit
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime

from config import DB_PATH, BACKEND_DIR

_SCHEMA = BACKEND_DIR / "schema.sql"
_local = threading.local()


def now_iso() -> str:
    """统一时间口径：本地时区 ISO 字符串（秒级）。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def new_id(prefix: str, length: int = 10) -> str:
    return f"{prefix}{uuid.uuid4().hex[:length].upper()}"


def connect() -> sqlite3.Connection:
    """返回当前线程复用的连接（首次调用时创建）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        _local.conn = conn
    return conn


def close_conn() -> None:
    """关闭当前线程连接（重建库、进程退出时调用）。"""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


atexit.register(close_conn)


@contextmanager
def write():
    """写事务上下文：成功提交，异常回滚（连接保留给当前线程复用）。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def rows(sql: str, params: tuple | list = ()) -> list[dict]:
    return [dict(r) for r in connect().execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list = ()) -> dict | None:
    row = connect().execute(sql, params).fetchone()
    return dict(row) if row else None


def scalar(sql: str, params: tuple | list = (), default=None):
    row = connect().execute(sql, params).fetchone()
    if row is None:
        return default
    return row[0]


def init_db(force: bool = False) -> None:
    """建表。force=True 时重建（仅用于种子脚本）。

    注意：Windows 下必须先关闭本线程连接，否则删除 db 文件会失败。
    """
    close_conn()
    if force and DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            target = DB_PATH.with_name(DB_PATH.name + suffix)
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode = WAL")   # 只在初始化时设置一次
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def json_load(text: str | None, default=None):
    if default is None:
        default = {}
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def json_dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def get_config(key: str, default=None):
    row = one("SELECT value FROM app_config WHERE key = ?", (key,))
    if not row:
        return default
    return json_load(row["value"], default)


def set_config(key: str, value, conn: sqlite3.Connection | None = None) -> None:
    payload = json_dump(value)
    sql = ("INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, ?) "
           "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at")
    if conn is not None:
        conn.execute(sql, (key, payload, now_iso()))
        return
    with write() as c:
        c.execute(sql, (key, payload, now_iso()))

# -*- coding: utf-8 -*-
"""极简 HTTP 层：请求/响应对象、multipart 解析、路由匹配。"""
from __future__ import annotations

import json
import mimetypes
import re
import urllib.parse
from http.cookies import SimpleCookie


class ApiError(Exception):
    """业务异常：统一转换为 {ok:false, error:{code,message}}。"""

    def __init__(self, message: str, status: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class Request:
    def __init__(self, method: str, raw_path: str, headers: dict, body: bytes, client_ip: str):
        parts = urllib.parse.urlsplit(raw_path)
        self.method = method.upper()
        self.path = urllib.parse.unquote(parts.path)
        self.raw_query = parts.query
        self.query = {
            k: v[-1] for k, v in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()
        }
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body or b""
        self.client_ip = client_ip or ""
        self.user: dict | None = None

        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("cookie", ""))
        except Exception:
            pass
        self.cookies = {k: m.value for k, m in cookie.items()}

        self._json: dict | None = None
        self.form: dict[str, str] = {}
        self.files: list[dict] = []
        self._parse_body()

    # ------------------------------------------------------------ 请求体
    def _parse_body(self) -> None:
        if not self.body:
            return
        ctype = self.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                data = json.loads(self.body.decode("utf-8"))
                self._json = data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json = {}
        elif "application/x-www-form-urlencoded" in ctype:
            parsed = urllib.parse.parse_qs(self.body.decode("utf-8", "replace"), keep_blank_values=True)
            self.form = {k: v[-1] for k, v in parsed.items()}
        elif ctype.startswith("multipart/form-data"):
            self._parse_multipart(ctype)

    def _parse_multipart(self, ctype: str) -> None:
        match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
        if not match:
            return
        boundary = (match.group(1) or match.group(2)).strip().encode("utf-8")
        delimiter = b"--" + boundary
        for chunk in self.body.split(delimiter):
            chunk = chunk.strip(b"\r\n")
            if not chunk or chunk == b"--":
                continue
            if b"\r\n\r\n" not in chunk:
                continue
            head, data = chunk.split(b"\r\n\r\n", 1)
            header_text = head.decode("utf-8", "replace")
            disp = re.search(r'name="([^"]*)"', header_text)
            filename = re.search(r'filename="([^"]*)"', header_text)
            ftype = re.search(r"Content-Type:\s*([^\r\n]+)", header_text, re.I)
            field = disp.group(1) if disp else ""
            if filename and filename.group(1):
                self.files.append({
                    "field": field,
                    "filename": filename.group(1),
                    "content_type": (ftype.group(1).strip() if ftype else "application/octet-stream"),
                    "data": data,
                })
            elif field:
                self.form[field] = data.decode("utf-8", "replace")

    @property
    def json(self) -> dict:
        return self._json or {}

    # ------------------------------------------------------------ 取值助手
    def arg(self, name: str, default: str = "") -> str:
        if name in self.json:
            val = self.json.get(name)
        elif name in self.form:
            val = self.form.get(name)
        else:
            val = self.query.get(name, default)
        if val is None:
            return default
        if isinstance(val, (list, dict)):
            return val
        return str(val).strip()

    def arg_int(self, name: str, default: int = 0) -> int:
        try:
            return int(float(self.arg(name, str(default))))
        except (TypeError, ValueError):
            return default

    def arg_bool(self, name: str, default: bool = False) -> bool:
        val = self.arg(name, "1" if default else "0").lower()
        return val in ("1", "true", "yes", "on")


class Response:
    def __init__(self, body: bytes = b"", status: int = 200,
                 content_type: str = "application/json; charset=utf-8",
                 headers: list[tuple[str, str]] | None = None):
        self.body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.status = status
        self.content_type = content_type
        self.headers = list(headers or [])
    @classmethod
    def json(cls, data, status: int = 200) -> "Response":
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return cls(payload.encode("utf-8"), status, "application/json; charset=utf-8")

    @classmethod
    def ok(cls, data=None) -> "Response":
        return cls.json({"ok": True, "data": data if data is not None else {}})

    @classmethod
    def fail(cls, message: str, status: int = 400, code: str = "bad_request") -> "Response":
        return cls.json({"ok": False, "error": {"code": code, "message": message}}, status)

    @classmethod
    def redirect(cls, location: str, status: int = 302,
                 headers: list[tuple[str, str]] | None = None) -> "Response":
        resp = cls(b"", status, "text/html; charset=utf-8", headers)
        resp.headers.append(("Location", location))
        return resp

    def set_cookie(self, name: str, value: str, max_age: int | None = None,
                   http_only: bool = True, secure: bool = False) -> "Response":
        parts = [f"{name}={value}", "Path=/", "SameSite=Lax"]
        if http_only:
            parts.append("HttpOnly")
        if secure:
            parts.append("Secure")
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        self.headers.append(("Set-Cookie", "; ".join(parts)))
        return self

    def clear_cookie(self, name: str, secure: bool = False) -> "Response":
        parts = f"{name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
        if secure:
            parts += "; Secure"
        self.headers.append(("Set-Cookie", parts))
        return self

    def to_bytes(self) -> bytes:
        return self.body


def guess_content_type(path) -> str:
    ctype, _ = mimetypes.guess_type(str(path))
    if ctype is None:
        return "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
        return f"{ctype}; charset=utf-8"
    return ctype


class StreamResponse:
    """SSE / 分块流式响应：由 app.py 以 chunked 编码直接写出。"""

    def __init__(self, iterator, status: int = 200,
                 content_type: str = "text/event-stream; charset=utf-8",
                 headers: list[tuple[str, str]] | None = None):
        self.iterator = iterator
        self.status = status
        self.content_type = content_type
        self.headers = list(headers or [])


def sse_event(event: str, data) -> str:
    """构造一条 SSE 事件文本。"""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"

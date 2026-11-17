#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""智能问询笔录平台 —— 后端启动入口。

启动：
    python backend/app.py                # 默认 127.0.0.1:8765
    python backend/app.py --port 9000    # 指定端口
    python backend/app.py --demo         # 开放截图/演示专用登录入口 /demo/session
    python backend/app.py --seed         # 重建演示数据后启动

浏览器访问 http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import mimetypes
import socket
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from config import FRONTEND_DIR, LOG_DIR  # noqa: E402
from core import db, security  # noqa: E402
from core.http import ApiError, Request, Response, StreamResponse, guess_content_type  # noqa: E402
from core.router import router  # noqa: E402
from services import templates_svc  # noqa: E402

# 导入即注册路由
from modules import (auth_api, document_api, import_api, materials_api,  # noqa: E402,F401
                     records_api, settings_api)

SERVER_VERSION = "PracticeRecordServer/1.0"
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

# 并发连接闸门：超过上限直接 503，避免慢连接耗尽线程（审查报告 P9）
_CONN_SEM = threading.BoundedSemaphore(config.MAX_CONNECTIONS)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def resolve_client_ip(peer_ip: str, headers: dict) -> str:
    """仅当直连地址属于可信代理时才采信 X-Forwarded-For（审查报告 S5）。"""
    peer = (peer_ip or "").strip()
    if peer and peer in config.TRUSTED_PROXIES:
        forwarded = headers.get("x-forwarded-for", "")
        if forwarded:
            # 取最右侧一跳：最贴近服务端的代理追加的地址
            return forwarded.split(",")[-1].strip()
    return peer


def _safe_next_path(raw: str | None) -> str:
    """防开放重定向：只允许站内绝对路径，拒绝 //host、反斜杠与协议。"""
    if not raw:
        return "/index.html"
    path = raw.strip()
    if (not path.startswith("/") or path.startswith("//")
            or "\\" in path or "://" in path or "\r" in path or "\n" in path):
        return "/index.html"
    return path


# ------------------------------------------------------------------ 静态资源
def serve_static(req: Request) -> Response:
    path = req.path if req.path not in ("", "/") else "/index.html"
    rel = path.lstrip("/")
    front = FRONTEND_DIR.resolve()
    target = (front / rel).resolve()
    if not target.is_relative_to(front):      # 语义化路径判断，避免前缀误判（S7）
        return Response("<h1>403 Forbidden</h1>", 403, "text/html; charset=utf-8")
    if target.is_dir():
        target = target / "index.html"
    if not target.exists():
        return Response(
            "<!doctype html><meta charset='utf-8'><h1>404 Not Found</h1>"
            f"<p>资源不存在：{path}</p><p><a href='/index.html'>返回控制台</a></p>",
            404, "text/html; charset=utf-8")
    resp = Response(target.read_bytes(), 200, guess_content_type(target))
    # 前端无构建流程，禁用 HTML/JS/CSS 强缓存，避免改完代码浏览器仍跑旧文件
    if target.suffix.lower() in (".html", ".js", ".css"):
        resp.headers.append(("Cache-Control", "no-cache, no-store, must-revalidate"))
    return resp


# ------------------------------------------------------------------ 演示登录（仅 --demo）
def demo_session(req: Request) -> Response:
    if not config.DEMO_MODE:
        return Response.fail("演示入口未开启（启动时加 --demo）", 404, "demo_disabled")
    badge_no = req.query.get("badge") or "100101"
    next_path = _safe_next_path(req.query.get("next"))
    row = db.one("SELECT * FROM user WHERE badge_no = ?", (badge_no,))
    if not row:
        return Response.fail(f"演示账号不存在：{badge_no}", 404)
    token, _ = security.create_session(row["id"], req.client_ip, "demo-screenshot")
    resp = Response.redirect(next_path)
    resp.set_cookie(security.cookie_name(), token, max_age=12 * 3600,
                    secure=config.COOKIE_SECURE)
    return resp


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = SERVER_VERSION

    # --------------------------------------------------------------- 请求处理
    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def handle(self):
        """浏览器预连接/刷新会直接断开，避免在请求行读取阶段刷 traceback。"""
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def _handle(self):
        if not _CONN_SEM.acquire(blocking=False):
            resp = Response.fail("服务繁忙，请稍后重试", 503, "server_busy")
            resp.headers.append(("Retry-After", "2"))
            self._send(resp)
            return
        try:
            # 慢连接防护：读请求体超时即断开，避免线程被长期占用（P9）
            self.connection.settimeout(config.REQUEST_TIMEOUT_SEC)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > config.MAX_UPLOAD_BYTES + 1024 * 1024:
                self._send(Response.fail("请求体超过大小限制", 413, "payload_too_large"))
                return
            body = self.rfile.read(length) if length > 0 else b""
            headers = dict(self.headers.items())
            client_ip = resolve_client_ip(self.client_address[0], {k.lower(): v for k, v in headers.items()})
            try:
                req = Request(self.command, self.path, headers, body, client_ip)
            except Exception:  # noqa: BLE001
                self._send(Response.fail("请求解析失败", 400))
                return
            try:
                if req.path.startswith("/api/"):
                    resp = router.dispatch(req)
                elif req.path.startswith("/demo/"):
                    resp = demo_session(req)
                else:
                    resp = serve_static(req)
            except ApiError as exc:
                resp = Response.fail(exc.message, exc.status, exc.code)
            except Exception:  # noqa: BLE001
                self._log_exception(req.path)
                resp = Response.fail("服务内部异常，请联系管理员", 500, "internal_error")
            self._send(resp)
        except (socket.timeout, TimeoutError):
            self.close_connection = True
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True
        finally:
            _CONN_SEM.release()

    # --------------------------------------------------------------- 响应
    def _send(self, resp) -> None:
        if isinstance(resp, StreamResponse):
            self._send_stream(resp)
            return
        try:
            self.send_response(resp.status)
            for key, value in resp.headers:
                self.send_header(key, value)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD" and resp.body:
                self.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_stream(self, resp: StreamResponse) -> None:
        """以 chunked 编码流式写出 SSE 事件。"""
        try:
            self.send_response(resp.status)
            for key, value in resp.headers:
                self.send_header(key, value)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.flush()
            for chunk in resp.iterator:
                data = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
                if not data:
                    continue
                self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:  # noqa: BLE001 - 流式过程异常不应影响服务
            self._log_exception("stream")
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def log_message(self, fmt, *args):
        stamp = datetime.now().strftime("%H:%M:%S")
        sys.stdout.write(f"[{stamp}] {self.command:6s} {self.path.split('?')[0]} {fmt % args}\n")

    def _log_exception(self, path: str):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / f"error-{datetime.now():%Y%m%d}.log").open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== {datetime.now().isoformat()} {path} =====\n")
            fh.write(traceback.format_exc())


# ------------------------------------------------------------------ 启动
def bootstrap(force_seed: bool = False) -> None:
    db.init_db()
    if force_seed:
        from seed import seed_all

        seed_all(force=True)
        return
    count = templates_svc.sync_from_files()
    if not db.one("SELECT id FROM user LIMIT 1"):
        from seed import seed_all

        seed_all(force=False)
        print(f"[bootstrap] 首次启动，已自动生成演示数据（模板 {count} 个）")
    else:
        print(f"[bootstrap] 模板同步完成：{count} 个")


def main() -> int:
    parser = argparse.ArgumentParser(description="智能问询笔录平台后端服务")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--demo", action="store_true", help="开放 /demo/session 演示登录入口")
    parser.add_argument("--seed", action="store_true", help="重建演示数据后启动")
    args = parser.parse_args()

    if args.demo:
        if args.host not in _LOOPBACK_HOSTS:
            print("[FATAL] --demo 演示登录仅允许绑定回环地址（127.0.0.1 / localhost / ::1）")
            return 2
        config.DEMO_MODE = True
    if config.DEMO_MODE and args.host not in _LOOPBACK_HOSTS:
        print("[FATAL] PRACTICE_DEMO=1 时禁止绑定非回环地址，请关闭演示模式")
        return 2
    bootstrap(force_seed=args.seed)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}"
    print("=" * 66)
    print("  智能问询笔录平台  |  反诈接处警结构化问询与五流证据闭环")
    print("=" * 66)
    print(f"  控制台地址 : {url}/login.html")
    print(f"  健康检查   : {url}/api/health")
    print(f"  运行模式   : {'演示模式（/demo/session 已开放）' if config.DEMO_MODE else '常规模式'}")
    print(f"  数据文件   : {config.DB_PATH}")
    print("  演示账号   : 100101/123456（办案民警） 100201/123456（研判员） 100001/123456（管理员）")
    print("=" * 66)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[shutdown] 服务已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

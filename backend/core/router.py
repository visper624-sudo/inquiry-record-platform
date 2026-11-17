# -*- coding: utf-8 -*-
"""路由注册与分发：统一鉴权、异常兜底与响应封装。"""
from __future__ import annotations

import re
import traceback
from datetime import datetime

from config import LOG_DIR
from core import security
from core.http import ApiError, Response, StreamResponse


class Route:
    def __init__(self, method: str, pattern: str, handler, auth: bool, roles: tuple | None):
        self.method = method.upper()
        self.pattern = pattern
        self.regex = re.compile(pattern)
        self.handler = handler
        self.auth = auth
        self.roles = roles


class Router:
    def __init__(self):
        self.routes: list[Route] = []

    def add(self, method: str, pattern: str, handler, auth: bool = True,
            roles: tuple | None = None) -> None:
        self.routes.append(Route(method, pattern, handler, auth, roles))

    def dispatch(self, req) -> Response:
        path = req.path
        method_mismatch = False
        for route in self.routes:
            match = route.regex.match(path)
            if not match:
                continue
            if route.method != req.method:
                method_mismatch = True
                continue
            if route.auth:
                user = security.resolve_user(req)
                if not user:
                    return Response.fail("登录状态已失效，请重新登录", 401, "unauthorized")
                req.user = user
                if route.roles and user.get("role") not in route.roles:
                    return Response.fail("当前账号角色无此操作权限", 403, "forbidden")
            try:
                result = route.handler(req, **match.groupdict())
            except ApiError as exc:
                return Response.fail(exc.message, exc.status, exc.code)
            except Exception:  # noqa: BLE001 - 统一兜底，避免线程内异常中断连接
                _log_exception(path)
                return Response.fail("服务内部异常，请联系管理员查看后端日志", 500, "internal_error")
            if isinstance(result, (Response, StreamResponse)):
                return result
            return Response.ok(result)
        if method_mismatch:
            return Response.fail("请求方法不被支持", 405, "method_not_allowed")
        return Response.fail(f"接口不存在: {path}", 404, "not_found")


def _log_exception(path: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d")
    log_file = LOG_DIR / f"error-{stamp}.log"
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== {datetime.now().isoformat()} {path} =====\n")
        fh.write(traceback.format_exc())


router = Router()


def api(method: str, pattern: str, auth: bool = True, roles: tuple | None = None):
    """接口装饰器：@api("GET", r"^/api/records$")。"""
    def decorator(func):
        router.add(method, pattern, func, auth=auth, roles=roles)
        return func

    return decorator

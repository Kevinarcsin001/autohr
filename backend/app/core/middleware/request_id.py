"""请求 ID 中间件：每个请求注入唯一 request_id，传播到日志与响应头。

行为：
- 若客户端 ``X-Request-ID`` header 存在则用之；否则生成 ``uuid4``
- 注入 structlog contextvars，所有日志自动带 request_id 字段
- 响应头 ``X-Request-ID`` 回传给客户端
- 请求结束清理 contextvars，避免跨请求泄露
"""
from __future__ import annotations

from uuid import uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """注入 X-Request-ID（若客户端提供则用，否则生成）。"""

    HEADER_NAME = "X-Request-ID"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(self.HEADER_NAME) or str(uuid4())

        # 重置 contextvars 并绑定 request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        try:
            response = await call_next(request)
        finally:
            # 请求结束清理，避免 contextvars 跨请求污染
            structlog.contextvars.clear_contextvars()

        response.headers[self.HEADER_NAME] = request_id
        return response

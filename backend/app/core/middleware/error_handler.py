"""全局异常处理器 + 业务异常基类。

设计：
- ``AppError`` 为所有业务异常基类，带 status_code / code / message / context
- 具体异常：NotFoundError / UnauthorizedError / ForbiddenError / ConflictError / ValidationError
- FastAPI 默认的 RequestValidationError 也包装成统一错误响应
- 兜底 Exception → 500，记录完整 traceback 但只对客户端返回通用消息
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

logger = structlog.get_logger(__name__)


# ============================================================================
# 业务异常
# ============================================================================


class AppError(Exception):
    """业务异常基类。

    Usage:
        raise NotFoundError("User not found", resource="user", resource_id=user_id)
        raise ConflictError("Email already registered", email=email)
    """

    status_code: int = HTTP_400_BAD_REQUEST
    default_code: str = "AppError"

    def __init__(
        self,
        message: str,
        code: str | None = None,
        **context: Any,
    ) -> None:
        self.message = message
        self.code = code or self.default_code
        self.context = context
        super().__init__(message)


class NotFoundError(AppError):
    status_code = HTTP_404_NOT_FOUND
    default_code = "NotFound"


class UnauthorizedError(AppError):
    status_code = HTTP_401_UNAUTHORIZED
    default_code = "Unauthorized"


class ForbiddenError(AppError):
    status_code = HTTP_403_FORBIDDEN
    default_code = "Forbidden"


class ConflictError(AppError):
    status_code = HTTP_409_CONFLICT
    default_code = "Conflict"


class ValidationError(AppError):
    status_code = HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "ValidationError"


# ============================================================================
# 错误响应格式
# ============================================================================


def _error_body(
    code: str,
    message: str,
    request_id: str | None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            **extra,
        },
    }
    if request_id:
        body["request_id"] = request_id
    return body


# ============================================================================
# 注册处理器
# ============================================================================


def install_error_handlers(app: FastAPI) -> None:
    """注册全局异常处理器到 FastAPI app。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app_error",
            error_code=exc.code,
            message=exc.message,
            path=str(request.url.path),
            method=request.method,
            **exc.context,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(
                _error_body(
                    exc.code,
                    exc.message,
                    request.headers.get("X-Request-ID"),
                    **exc.context,
                )
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning(
            "validation_error",
            errors=exc.errors(),
            path=str(request.url.path),
            method=request.method,
        )
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            content=jsonable_encoder(
                _error_body(
                    "ValidationError",
                    "Request validation failed",
                    request.headers.get("X-Request-ID"),
                    details=exc.errors(),
                )
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            error_type=exc.__class__.__name__,
            error_message=str(exc),
            path=str(request.url.path),
            method=request.method,
        )
        return JSONResponse(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            content=jsonable_encoder(
                _error_body(
                    "InternalServerError",
                    "An unexpected error occurred",
                    request.headers.get("X-Request-ID"),
                )
            ),
        )

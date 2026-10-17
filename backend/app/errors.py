"""统一错误模型。

§8.3 的错误码表在这里落地：异常带 HTTP 状态 + 稳定 code + 可读 message，
前端只认 code，不解析 message。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """业务异常基类。"""

    status_code: int = 400
    code: str = "invalid_input"

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class InvalidInput(AppError):
    status_code = 400
    code = "invalid_input"


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class DuplicateContent(AppError):
    status_code = 409
    code = "duplicate_content"


class QuotaExceeded(AppError):
    status_code = 429
    code = "quota_exceeded"


class ModelUnavailable(AppError):
    status_code = 503
    code = "model_unavailable"


class BudgetExhausted(AppError):
    status_code = 504
    code = "budget_exhausted"


class InternalError(AppError):
    status_code = 500
    code = "internal_error"

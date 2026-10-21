"""FastAPI 依赖：身份、连接池、模型。

一条硬规矩：**只信 JWT 里的 sub，绝不信请求体里的任何 user_id**。
所有仓储函数的第一个参数都是这个 sub，模型和用户都改不了它。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, Request

from app.config import Settings, get_settings
from app.errors import Unauthenticated
from app.infra.db import Database
from app.infra.llm import Embeddings, Models
from app.infra.security import decode_token


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_worker_db(request: Request) -> Database:
    return request.app.state.worker_db


def get_models(request: Request) -> Models:
    return request.app.state.models


def get_embeddings(request: Request) -> Embeddings:
    return request.app.state.embeddings


def get_app_settings() -> Settings:
    return get_settings()


@dataclass(frozen=True)
class AuthUser:
    id: UUID


async def current_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
) -> AuthUser:
    if not authorization:
        raise Unauthenticated("未登录")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Unauthenticated("凭证格式不正确")
    return AuthUser(id=decode_token(parts[1], settings.jwt_secret, settings.jwt_alg))

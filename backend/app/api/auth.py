"""注册与登录。

登录/注册时还不知道 user_id，没法 `SET LOCAL app.user_id`，
所以这两条走数据库里的定义者函数（`auth_lookup` / `auth_register`）。
除此之外，没有任何地方能绕过 RLS。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AuthUser, current_user, get_db, get_app_settings
from app.config import Settings
from app.errors import InvalidInput, Unauthenticated
from app.infra.db import Database
from app.infra.security import create_token, hash_password, verify_password
from app.repo import users as users_repo
from app.schemas.models import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_payload(user: dict, settings: Settings) -> dict:
    token = create_token(user["id"], settings.jwt_secret, settings.jwt_ttl_hours, settings.jwt_alg)
    return {
        "accessToken": token,
        "tokenType": "bearer",
        "expiresIn": settings.jwt_ttl_hours * 3600,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "displayName": user.get("display_name"),
            "createdAt": user["created_at"],
        },
    }


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(
    body: RegisterIn,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    try:
        email = users_repo.normalize_email(body.email)
    except users_repo.EmailInvalid as exc:
        raise InvalidInput(str(exc)) from exc

    if len(body.password) < settings.min_password_length:
        raise InvalidInput(f"密码至少 {settings.min_password_length} 位")

    async with db.system_tx() as conn:
        user = await users_repo.create_user(
            conn,
            email=email,
            password_hash=hash_password(body.password),
            display_name=(body.display_name or "").strip() or None,
        )
    if user is None:
        # 邮箱已存在。不暴露"这个邮箱注册过"这一信息，统一提示去登录。
        raise HTTPException(status_code=409, detail={"code": "email_taken", "message": "该邮箱已注册，请直接登录"})

    return _token_payload(user, settings)


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginIn,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    email = (body.email or "").strip()
    if not email or not body.password:
        raise InvalidInput("邮箱和密码都不能为空")

    async with db.system_tx() as conn:
        user = await users_repo.find_by_email(conn, email)

    # 无论用户不存在还是密码错，都返回同一句话——避免账号枚举
    if not user or not verify_password(body.password, user["password_hash"]):
        raise Unauthenticated("邮箱或密码不正确")

    return _token_payload(user, settings)


@router.get("/me", response_model=UserOut)
async def me(
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        user = await users_repo.get_by_id(conn, auth.id)
    if not user:
        raise Unauthenticated("用户不存在")
    return {
        "id": user["id"],
        "email": user["email"],
        "displayName": user.get("display_name"),
        "createdAt": user["created_at"],
    }

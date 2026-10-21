"""密码哈希与 JWT 签发/校验。

不引入 passlib / bcrypt 这类需要编译的依赖：
- 密码用标准库 `hashlib.pbkdf2_hmac`，60 万轮，盐随机 16 字节；
- JWT 用 PyJWT，HS256，只信签名里的 sub。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from app.errors import Unauthenticated

_PBKDF2_ROUNDS = 600_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return "$".join(
        [
            _ALGO,
            str(_PBKDF2_ROUNDS),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt_b64, digest_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds_s))
        # 定长比较，避免时序侧信道
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_token(user_id: UUID | str, secret: str, ttl_hours: int, alg: str = "HS256") -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=alg)


def decode_token(token: str, secret: str, alg: str = "HS256") -> UUID:
    try:
        payload = jwt.decode(token, secret, algorithms=[alg])
        return UUID(payload["sub"])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthenticated("登录已过期，请重新登录") from exc
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise Unauthenticated("凭证无效") from exc

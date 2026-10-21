"""口令与 JWT 的单测。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.errors import Unauthenticated
from app.infra.security import create_token, decode_token, hash_password, verify_password


def test_password_hash_is_not_plaintext():
    stored = hash_password("s3cret-password")
    assert "s3cret-password" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_password_verify_ok():
    stored = hash_password("s3cret-password")
    assert verify_password("s3cret-password", stored)


def test_password_verify_wrong():
    stored = hash_password("s3cret-password")
    assert not verify_password("s3cret-passworD", stored)


def test_password_salt_is_random():
    assert hash_password("same") != hash_password("same")


def test_verify_malformed_hash_returns_false():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "md5$1$a$b")


def test_jwt_roundtrip():
    uid = uuid.uuid4()
    token = create_token(uid, "secret", 1)
    assert decode_token(token, "secret") == uid


def test_jwt_wrong_secret_rejected():
    token = create_token(uuid.uuid4(), "secret", 1)
    with pytest.raises(Unauthenticated):
        decode_token(token, "other-secret")


def test_jwt_expired_rejected():
    uid = uuid.uuid4()
    payload = {
        "sub": str(uid),
        "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
        "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, "secret", algorithm="HS256")
    with pytest.raises(Unauthenticated):
        decode_token(token, "secret")


def test_jwt_garbage_rejected():
    with pytest.raises(Unauthenticated):
        decode_token("not.a.token", "secret")

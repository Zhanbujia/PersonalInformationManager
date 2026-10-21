"""用户与认证的数据访问。

只有两个动作需要绕过 RLS（登录、注册），它们走数据库里的定义者函数，
见 `migrations/001_init.sql` 末尾。其余一律走普通租户事务。
"""

from __future__ import annotations

import re
from uuid import UUID

from psycopg import AsyncConnection

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailInvalid(ValueError):
    pass


def normalize_email(email: str) -> str:
    email = (email or "").strip()
    if not _EMAIL_RE.match(email):
        raise EmailInvalid("邮箱格式不正确")
    return email


async def create_user(
    conn: AsyncConnection, *, email: str, password_hash: str, display_name: str | None
) -> dict | None:
    """注册。返回 None 表示邮箱已存在。

    这条调用必须放在 `system_tx()` 里——此刻还不知道 user_id。
    """
    cur = await conn.execute(
        "select * from auth_register(%s, %s, %s)", (email, password_hash, display_name)
    )
    return await cur.fetchone()


async def find_by_email(conn: AsyncConnection, email: str) -> dict | None:
    cur = await conn.execute("select * from auth_lookup(%s)", (email,))
    return await cur.fetchone()


async def get_by_id(conn: AsyncConnection, user_id: UUID) -> dict | None:
    """读自己的资料。走普通 RLS 路径，只能读到 id = 自己的那一行。"""
    cur = await conn.execute(
        "select id, email, display_name, created_at from users where id = %s", (str(user_id),)
    )
    return await cur.fetchone()

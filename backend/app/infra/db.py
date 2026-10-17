"""数据库连接与多租户事务上下文。

这一层是安全的地基，有三条硬规矩：

1. **每次开事务都必须带 user_id**，由 `set_config('app.user_id', ..., true)` 注入，
   数据库侧的 RLS 策略靠它判断"你是谁"。用 `true` 表示作用域仅限当前事务。
2. **不能用拼接字符串写这个设置**。`SET LOCAL` 不接受参数占位符，所以用
   `set_config()` 函数——它接受参数，天然免疫注入。
3. **应用角色不能是表 owner，也不能是超级用户**。PostgreSQL 里这两者默认绕过 RLS；
   超级用户即使 `force row level security` 也照样绕过。所以部署时必须分角色（见 deploy/init）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class Database:
    """一个连接池 + 受控的事务入口。"""

    def __init__(
        self,
        url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        timeout: float = 10.0,
        name: str = "app",
    ) -> None:
        self.name = name
        self._pool = AsyncConnectionPool(
            conninfo=url,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            open=False,
            name=name,
            # 断连后自动重连，避免长时间空闲后第一条请求失败
            check=AsyncConnectionPool.check_connection,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        logger.info("db pool opened", extra={"pool": self.name})

    async def close(self) -> None:
        await self._pool.close()
        logger.info("db pool closed", extra={"pool": self.name})

    @asynccontextmanager
    async def tx(self, user_id: UUID | str) -> AsyncGenerator[AsyncConnection, None]:
        """租户事务：事务内所有 SQL 都被 RLS 限定到这个用户。

        正常结束自动提交，异常自动回滚。
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                # set_config(..., is_local=true) == SET LOCAL，且支持参数绑定
                await conn.execute(
                    "select set_config('app.user_id', %s, true)", (str(user_id),)
                )
                yield conn

    @asynccontextmanager
    async def system_tx(self) -> AsyncGenerator[AsyncConnection, None]:
        """无租户事务：只允许用于 jobs 这类没有租户列的基础设施表。

        业务表的读写**一律走 `tx()`**，否则 RLS 会让你的 SQL 静默命中 0 行。
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                yield conn

    async def healthy(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                await conn.execute("select 1")
            return True
        except Exception:  # noqa: BLE001 - 健康检查不该抛出
            logger.exception("db health check failed")
            return False


async def fetch_all(conn: AsyncConnection, sql: str, params: Any = None) -> list[dict]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def fetch_one(conn: AsyncConnection, sql: str, params: Any = None) -> dict | None:
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


async def execute(conn: AsyncConnection, sql: str, params: Any = None) -> int:
    """返回受影响行数。

    RLS 的坑就在这里：无权限时它返回 0，**不报错**。
    所以任何"更新/删除"之后都要检查返回值，别把 0 行当成功。
    """
    cur = await conn.execute(sql, params)
    return cur.rowcount

"""初始建表 + RLS 策略 + 触发器

Revision ID: 0001_init
Revises:
Create Date: 2026-10-17

DDL 正文放在 `migrations/001_init.sql`，这里只负责加载并执行。
为什么要把 SQL 单独放：这份 DDL 是安全边界（RLS 策略逐表写全），
放在 .sql 里能用 psql 直接审、能 diff，比埋在 Python 字符串里可读得多。
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[2] / "migrations" / "001_init.sql"


def upgrade() -> None:
    dim = os.environ.get("EMBEDDING_DIM", "1024")
    if not dim.isdigit():
        raise ValueError(f"EMBEDDING_DIM 必须是整数，当前值：{dim!r}")
    sql = _SQL_FILE.read_text(encoding="utf-8").replace("{{DIM}}", dim)

    # 这份 DDL 有几十条语句，必须一次执行完。
    # psycopg3 在"不带参数"时走简单查询协议，才允许一次发多条；
    # 带一个空参数元组就会切到扩展协议，然后报 "cannot insert multiple commands"。
    bind = op.get_bind()
    try:
        raw = bind.connection.dbapi_connection
        with raw.cursor() as cur:
            cur.execute(sql)
    except AttributeError:
        # 换了驱动时的兜底路径
        bind.exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        drop table if exists usage_ledger cascade;
        drop table if exists jobs cascade;
        drop table if exists runs cascade;
        drop table if exists qa_messages cascade;
        drop table if exists qa_sessions cascade;
        drop table if exists digest_evidence cascade;
        drop table if exists digests cascade;
        drop table if exists todos cascade;
        drop table if exists item_links cascade;
        drop table if exists chunks cascade;
        drop table if exists item_analyses cascade;
        drop table if exists items cascade;
        drop table if exists users cascade;
        drop function if exists on_new_analysis() cascade;
        drop function if exists auth_lookup(text) cascade;
        drop function if exists auth_register(text, text, text) cascade;
        drop function if exists current_user_id() cascade;
        """
    )

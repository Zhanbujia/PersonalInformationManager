"""items: 文件上传的元数据字段

Revision ID: 0003_item_files
Revises: 0002_jobs_returning
Create Date: 2026-10-21

文件上传只新增**元数据列**，不新建表：一条上传就是一条 items（kind='file'），
正文由 worker 抽取后写进 body_md，后面的分析/切片/检索完全复用现有链路。

全部用 `add column if not exists`，重复执行不会报错（本地手工补列也安全）。
新列沿用表级授权，不需要额外 grant。
"""

from __future__ import annotations

from alembic import op

revision = "0003_item_files"
down_revision = "0002_jobs_returning"
branch_labels = None
depends_on = None

_UPGRADE = """
alter table items
    add column if not exists file_name   text,
    add column if not exists mime_type   text,
    add column if not exists file_size   bigint,
    add column if not exists file_ext    text,
    add column if not exists storage_key text,
    add column if not exists parse_error text;
"""

_DOWNGRADE = """
alter table items
    drop column if exists parse_error,
    drop column if exists storage_key,
    drop column if exists file_ext,
    drop column if exists file_size,
    drop column if exists mime_type,
    drop column if exists file_name;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWNGRADE)

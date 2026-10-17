"""jobs: 给应用角色开放 id 列的可读权限

Revision ID: 0002_jobs_returning
Revises: 0001_init
Create Date: 2026-10-17

**问题**：写一条记录时接口直接 500，堆栈是

    psycopg.errors.InsufficientPrivilege: permission denied for table jobs

原因不是漏给了 insert 权限，而是 `insert ... returning id` 在 PostgreSQL 里
除了 insert 权限，还要求对**返回的列**有 select 权限。而 0001 为了「应用读不到
别人投了什么任务」，把 pim_app 对 jobs 的 select 整个撤掉了，于是连自己刚插入
的自增 id 也拿不回来。

**修法**：不是把整表 select 还回去（那会泄漏别的用户的任务参数和总结 id），
而是做列级授权，只放开 id 一列。应用因此能拿到自己的任务号去轮询，
`select payload from jobs` 仍然会被拒绝。
"""

from __future__ import annotations

from alembic import op

revision = "0002_jobs_returning"
down_revision = "0001_init"
branch_labels = None
depends_on = None

# 用 DO 块包住：不是所有部署都分角色（比如单机调试），缺角色时跳过而不是让迁移失败
_UPGRADE = """
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'pim_app') then
        execute 'grant select (id) on jobs to pim_app';
    end if;
end $$;
"""

_DOWNGRADE = """
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'pim_app') then
        execute 'revoke select (id) on jobs from pim_app';
    end if;
end $$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_UPGRADE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWNGRADE)

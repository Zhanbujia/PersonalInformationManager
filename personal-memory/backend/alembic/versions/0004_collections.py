"""collections: 资料库的一级文件夹

Revision ID: 0004_collections
Revises: 0003_item_files
Create Date: 2026-10-21

资料库只收录 `kind='file'` 的记录；文件夹做成**一级**（不嵌套），
文件通过 `items.collection_id` 归属。删除文件夹时文件回到「未分类」：
FK 用 `on delete set null`，API 层还会先显式把文件挪回未分类，双保险。

新表必须单独授权：001 的 `grant ... on all tables` 只覆盖当时已存在的表。
"""

from __future__ import annotations

from alembic import op

revision = "0004_collections"
down_revision = "0003_item_files"
branch_labels = None
depends_on = None

_UPGRADE = """
create table if not exists collections (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null,
    name       text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create unique index if not exists collections_user_name
    on collections (user_id, lower(name));
create index if not exists collections_user on collections (user_id, created_at);

alter table collections enable row level security;
alter table collections force  row level security;
drop policy if exists collections_tenant on collections;
create policy collections_tenant on collections
    using (user_id = current_user_id()) with check (user_id = current_user_id());

alter table items
    add column if not exists collection_id uuid references collections(id) on delete set null;
create index if not exists items_collection
    on items (user_id, collection_id)
    where deleted_at is null and kind = 'file';

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'pim_app') then
        execute 'grant select, insert, update, delete on collections to pim_app';
    end if;
    if exists (select 1 from pg_roles where rolname = 'pim_worker') then
        execute 'grant select, insert, update, delete on collections to pim_worker';
    end if;
end $$;
"""

_DOWNGRADE = """
alter table items drop column if exists collection_id;
drop table if exists collections;
"""


def _exec(sql: str) -> None:
    # 多语句必须走简单查询协议；带参数会切到扩展协议并报 "cannot insert multiple commands"
    bind = op.get_bind()
    try:
        raw = bind.connection.dbapi_connection
        with raw.cursor() as cur:
            cur.execute(sql)
    except AttributeError:
        bind.exec_driver_sql(sql)


def upgrade() -> None:
    _exec(_UPGRADE)


def downgrade() -> None:
    _exec(_DOWNGRADE)

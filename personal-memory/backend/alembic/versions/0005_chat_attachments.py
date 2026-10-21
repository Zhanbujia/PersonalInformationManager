"""chat_attachments: 问答附件（文档与图片）+ 消息附件快照

Revision ID: 0005_chat_attachments
Revises: 0004_collections
Create Date: 2026-10-21

聊天附件独立于「资料库」：用户问问题时临时上传的文件不该污染收件箱/资料库。
- 文档：上传时同步抽出文本存 `extracted_text`，提问时注入 prompt；
- 图片：存文件，提问时转 base64 走多模态（DeepSeek v4-flash 支持图片输入）；
- `qa_messages.attachments` 存当时的附件快照（文件名/类型/id），重新打开会话仍能显示。
"""

from __future__ import annotations

from alembic import op

revision = "0005_chat_attachments"
down_revision = "0004_collections"
branch_labels = None
depends_on = None

_UPGRADE = """
create table if not exists chat_attachments (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null,
    session_id     uuid references qa_sessions(id) on delete set null,
    file_name      text not null,
    mime_type      text,
    file_size      bigint,
    storage_key    text not null,
    ext            text,
    kind           text not null check (kind in ('doc','image')),
    extracted_text text,
    status         text not null default 'ready' check (status in ('ready','failed')),
    error          text,
    created_at     timestamptz not null default now()
);
create index if not exists chat_attachments_user on chat_attachments (user_id, created_at desc);
create index if not exists chat_attachments_session on chat_attachments (session_id);

alter table chat_attachments enable row level security;
alter table chat_attachments force  row level security;
drop policy if exists chat_attachments_tenant on chat_attachments;
create policy chat_attachments_tenant on chat_attachments
    using (user_id = current_user_id()) with check (user_id = current_user_id());

alter table qa_messages
    add column if not exists attachments jsonb not null default '[]'::jsonb;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'pim_app') then
        execute 'grant select, insert, update, delete on chat_attachments to pim_app';
    end if;
    if exists (select 1 from pg_roles where rolname = 'pim_worker') then
        execute 'grant select, insert, update, delete on chat_attachments to pim_worker';
    end if;
end $$;
"""

_DOWNGRADE = """
alter table qa_messages drop column if exists attachments;
drop table if exists chat_attachments;
"""


def _exec(sql: str) -> None:
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

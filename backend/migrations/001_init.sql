-- =============================================================================
-- 拾穗 · 初始化建表
--
-- 用法：alembic upgrade head 时由 migrations 读取本文件执行，
--       其中 {{DIM}} 会被替换成 EMBEDDING_DIM（默认 1024）。
--
-- 三条必须遵守的规矩：
--   1. 含租户数据的表，必须 enable + force row level security，并逐表写策略；
--   2. 有 user_id 列的走直接比对，没有的用 exists 回溯父表；
--   3. 应用角色必须是非 owner 且非超级用户，否则 RLS 形同虚设。
-- =============================================================================

-- ---------- 扩展 ----------
-- 用 DO 块包一层：迁移通常以 owner 角色执行，而 owner 没有建扩展的权限。
-- 扩展一般由部署脚本（超级用户身份）预先建好，这里只在缺失时才尝试创建，
-- 已经存在就走"跳过"，不会因为权限不足而让整次迁移失败。
do $$
declare
    ext text;
begin
    foreach ext in array array['vector', 'pg_trgm', 'pgcrypto'] loop
        if not exists (select 1 from pg_extension where extname = ext) then
            execute format('create extension %I', ext);
        end if;
    end loop;
end $$;

-- ---------- 租户上下文 ----------
-- 不用 auth.uid()：那是 Supabase 的内置函数，标准 PostgreSQL 没有。
-- 我们自己读会话变量 app.user_id，由应用在每个事务开头 set_config 注入。
create or replace function current_user_id() returns uuid
language sql stable as $$
    select nullif(current_setting('app.user_id', true), '')::uuid
$$;

-- =============================================================================
-- 1. 用户
-- =============================================================================
create table if not exists users (
    id            uuid primary key default gen_random_uuid(),
    email         text not null,
    password_hash text not null,
    display_name  text,
    created_at    timestamptz not null default now()
);
-- 邮箱大小写不敏感唯一
create unique index if not exists users_email_lower on users (lower(email));

-- users 这里只 enable，**不加 force**。原因见文末「认证的两个受控出口」：
-- 登录/注册发生在"还不知道 user_id"的时刻，没法设置 app.user_id。
-- 不加 force 时，表 owner（migrator）可绕过 RLS，正好用来跑那两个函数；
-- 应用角色是非 owner，策略照常生效。
alter table users enable row level security;

drop policy if exists users_self on users;
create policy users_self on users
    using (id = current_user_id())
    with check (id = current_user_id());

-- =============================================================================
-- 2. 记录主表
-- =============================================================================
create table if not exists items (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid not null,
    kind             text not null default 'note'
                     check (kind in ('note','idea','link','file','todo')),
    title            text,
    body_md          text not null default '',       -- 规范化后的正文
    raw_input        text not null,                  -- 用户原始输入，永不修改
    source_channel   text not null default 'web',
    source_url       text,
    lang             text not null default 'zh',
    content_hash     text not null,                  -- sha256(body_md)，用于去重
    search_text      text not null default '',       -- jieba 分词结果，供列表页全文搜索
    tags             text[] not null default '{}',
    captured_at      timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    analysis_status  text not null default 'pending'
                     check (analysis_status in ('pending','running','done','failed','skipped')),
    analysis_version int not null default 0,
    user_pinned      boolean not null default false, -- 用户手改过，模型不得覆盖
    deleted_at       timestamptz,
    constraint items_dedup unique (user_id, content_hash)
);

create index if not exists items_user_time on items (user_id, captured_at desc)
    where deleted_at is null;
create index if not exists items_search on items using gin (to_tsvector('simple', search_text));
create index if not exists items_tags on items using gin (tags);
create index if not exists items_body_trgm on items using gin (body_md gin_trgm_ops);

-- =============================================================================
-- 3. 模型分析结果（推断，与原文分表）
-- =============================================================================
create table if not exists item_analyses (
    id              uuid primary key default gen_random_uuid(),
    item_id         uuid not null references items(id) on delete cascade,
    version         int not null,
    summary         text not null,
    key_points      jsonb not null default '[]'::jsonb,   -- [{quote,start,end}]
    todos_extracted jsonb not null default '[]'::jsonb,
    confidence      real not null default 0,
    model           text not null,
    prompt_version  text not null,
    raw_json        jsonb not null,
    created_at      timestamptz not null default now(),
    unique (item_id, version)
);
create index if not exists item_analyses_latest on item_analyses (item_id, version desc);

-- =============================================================================
-- 4. 切片 + 向量
-- =============================================================================
create table if not exists chunks (
    id          uuid primary key default gen_random_uuid(),
    item_id     uuid not null references items(id) on delete cascade,
    user_id     uuid not null,                     -- 冗余存储，让 RLS 不必回溯
    idx         int not null,
    text        text not null,
    search_text text not null default '',          -- jieba 分词结果，供切片级全文检索
    start_off   int not null,
    end_off     int not null,
    embedding   vector({{DIM}}),                   -- 维度必须与 embedding 模型一致
    model       text,
    created_at  timestamptz not null default now(),
    unique (item_id, idx)
);
create index if not exists chunks_item on chunks (item_id);
create index if not exists chunks_vec on chunks using hnsw (embedding vector_cosine_ops)
    where embedding is not null;
-- 全文索引必须建在 chunks.search_text 上：检索单元是切片，不是整篇。
-- 存的是分词结果，因为 PostgreSQL 没有中文词典，直接索引原文等于没索引。
create index if not exists chunks_fts on chunks using gin (to_tsvector('simple', search_text));

-- =============================================================================
-- 5. 记录关联
-- =============================================================================
create table if not exists item_links (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null,
    src_item_id uuid not null references items(id) on delete cascade,
    dst_item_id uuid not null references items(id) on delete cascade,
    relation    text not null default 'similar'
                check (relation in ('similar','same_topic','follows','contradicts')),
    score       real not null,
    reason      text,
    created_at  timestamptz not null default now(),
    unique (src_item_id, dst_item_id),
    check (src_item_id <> dst_item_id)
);
create index if not exists item_links_src on item_links (src_item_id, score desc);
create index if not exists item_links_dst on item_links (dst_item_id);

-- =============================================================================
-- 6. 待办
-- =============================================================================
create table if not exists todos (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null,
    text           text not null,
    status         text not null default 'open'
                   check (status in ('open','done','dropped')),
    due_date       date,
    priority       int not null default 2 check (priority between 1 and 3),
    source_item_id uuid references items(id) on delete set null,
    source_quote   text,
    client_id      text,                           -- 幂等键，防连点两次建两条
    created_at     timestamptz not null default now(),
    done_at        timestamptz,
    unique (user_id, client_id)
);
create index if not exists todos_open on todos (user_id, status, due_date)
    where status = 'open';

-- =============================================================================
-- 7. 阶段总结
-- =============================================================================
create table if not exists digests (
    id             uuid primary key default gen_random_uuid(),
    user_id        uuid not null,
    kind           text not null check (kind in ('weekly','monthly','adhoc','todo_review')),
    period_start   date not null,
    period_end     date not null,
    content_md     text not null,
    stats          jsonb not null,                 -- 所有数字的真实来源
    action_items   jsonb not null default '[]'::jsonb,
    model          text,
    prompt_version text,
    stale          boolean not null default false,
    created_at     timestamptz not null default now()
);
create index if not exists digests_user on digests (user_id, kind, period_start desc);

-- =============================================================================
-- 8. 总结的证据（版本绑定）
-- =============================================================================
create table if not exists digest_evidence (
    id           uuid primary key default gen_random_uuid(),
    digest_id    uuid not null references digests(id) on delete cascade,
    item_id      uuid not null references items(id) on delete cascade,
    item_version int not null,                     -- 引用时的 analysis_version 快照
    quote        text,
    unique (digest_id, item_id)
);

-- =============================================================================
-- 9/10. 问答
-- =============================================================================
create table if not exists qa_sessions (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null,
    title      text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists qa_sessions_user on qa_sessions (user_id, updated_at desc);

create table if not exists qa_messages (
    id            uuid primary key default gen_random_uuid(),
    session_id    uuid not null references qa_sessions(id) on delete cascade,
    user_id       uuid not null,
    role          text not null check (role in ('user','assistant','tool')),
    content       text not null,
    citations     jsonb not null default '[]'::jsonb,
    tokens_in     int, tokens_out int, cached_tokens int,
    model         text,
    created_at    timestamptz not null default now()
);
create index if not exists qa_messages_session on qa_messages (session_id, created_at);

-- =============================================================================
-- 11. 执行记录（幂等 + 观测）
-- =============================================================================
create table if not exists runs (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null,
    kind         text not null check (kind in ('ingest','qa','digest','link')),
    idem_key     text not null,
    step         text,
    status       text not null default 'running'
                 check (status in ('running','succeeded','failed')),
    attempt      int not null default 1,
    input_hash   text,
    output_ref   jsonb,
    error_code   text,
    tokens_in    int default 0,
    tokens_out   int default 0,
    cached_tokens int default 0,
    latency_ms   int,
    created_at   timestamptz not null default now(),
    finished_at  timestamptz,
    unique (kind, idem_key)
);
create index if not exists runs_user_time on runs (user_id, created_at desc);

-- =============================================================================
-- 12. 任务队列（全局，无租户列）
-- =============================================================================
create table if not exists jobs (
    id           bigserial primary key,
    kind         text not null,
    payload      jsonb not null,
    run_at       timestamptz not null default now(),
    locked_at    timestamptz,
    locked_by    text,
    attempts     int not null default 0,
    max_attempts int not null default 3,
    last_error   text,
    result       jsonb,                  -- 任务产出（如 digestId），供轮询接口回读
    finished_at  timestamptz,
    created_at   timestamptz not null default now()
);
create index if not exists jobs_ready on jobs (run_at) where locked_at is null;

-- =============================================================================
-- 13. token 账本
-- =============================================================================
create table if not exists usage_ledger (
    id            bigserial primary key,
    user_id       uuid not null,
    day           date not null default current_date,
    path          text not null,           -- ingest / qa / digest / eval
    model         text not null,
    tokens_in     bigint not null default 0,
    tokens_out    bigint not null default 0,
    cached_tokens bigint not null default 0,
    calls         int not null default 1
);
create unique index if not exists usage_ledger_key on usage_ledger (user_id, day, path, model);
create index if not exists usage_ledger_user_day on usage_ledger (user_id, day);

-- =============================================================================
-- 触发器：新分析写入后，主表记录版本 + 把旧的总结标记为过期
--
-- 关键点：触发器挂在 item_analyses 上，**不挂在 items 上**。
-- 如果挂在 items 上，函数体里的 update items 会再次触发自己，形成死循环。
-- =============================================================================
create or replace function on_new_analysis() returns trigger
language plpgsql as $$
declare
    v_user_id uuid;
begin
    select user_id into v_user_id from items where id = new.item_id;
    if v_user_id is null then
        return new;
    end if;

    -- ① 主表记录最新分析版本
    update items
       set updated_at = now(),
           analysis_version = new.version
     where id = new.item_id;

    -- ② 把依据这条记录的总结标记为过期
    --    只标记"引用版本更旧"的总结；同版本重复写入不会误伤
    update digests d
       set stale = true
     where d.user_id = v_user_id
       and d.stale = false
       and exists (
           select 1 from digest_evidence e
            where e.digest_id = d.id
              and e.item_id = new.item_id
              and e.item_version < new.version
       );

    return new;
end $$;

drop trigger if exists trg_on_new_analysis on item_analyses;
create trigger trg_on_new_analysis
after insert on item_analyses
for each row execute function on_new_analysis();

-- =============================================================================
-- 行级安全：逐表 enable + force + 策略
-- =============================================================================
alter table items           enable row level security;
alter table items           force  row level security;
alter table item_analyses   enable row level security;
alter table item_analyses   force  row level security;
alter table chunks          enable row level security;
alter table chunks          force  row level security;
alter table item_links      enable row level security;
alter table item_links      force  row level security;
alter table todos           enable row level security;
alter table todos           force  row level security;
alter table digests         enable row level security;
alter table digests         force  row level security;
alter table digest_evidence enable row level security;
alter table digest_evidence force  row level security;
alter table qa_sessions     enable row level security;
alter table qa_sessions     force  row level security;
alter table qa_messages     enable row level security;
alter table qa_messages     force  row level security;
alter table runs            enable row level security;
alter table runs            force  row level security;
alter table usage_ledger    enable row level security;
alter table usage_ledger    force  row level security;

-- 有 user_id 列的表：直接比对
drop policy if exists items_tenant on items;
create policy items_tenant on items
    using (user_id = current_user_id()) with check (user_id = current_user_id());

drop policy if exists chunks_tenant on chunks;
create policy chunks_tenant on chunks
    using (user_id = current_user_id()) with check (user_id = current_user_id());

drop policy if exists item_links_tenant on item_links;
create policy item_links_tenant on item_links
    using (user_id = current_user_id()) with check (user_id = current_user_id());

drop policy if exists todos_tenant on todos;
create policy todos_tenant on todos
    using (user_id = current_user_id()) with check (user_id = current_user_id());

drop policy if exists digests_tenant on digests;
create policy digests_tenant on digests
    using (user_id = current_user_id()) with check (user_id = current_user_id());

drop policy if exists qa_sessions_tenant on qa_sessions;
create policy qa_sessions_tenant on qa_sessions
    using (user_id = current_user_id()) with check (user_id = current_user_id());

drop policy if exists qa_messages_tenant on qa_messages;
create policy qa_messages_tenant on qa_messages
    using (user_id = current_user_id()) with check (user_id = current_user_id());

drop policy if exists runs_tenant on runs;
create policy runs_tenant on runs
    using (user_id = current_user_id()) with check (user_id = current_user_id());

drop policy if exists usage_ledger_tenant on usage_ledger;
create policy usage_ledger_tenant on usage_ledger
    using (user_id = current_user_id()) with check (user_id = current_user_id());

-- 没有 user_id 列的子表：用 exists 回溯父表
drop policy if exists item_analyses_tenant on item_analyses;
create policy item_analyses_tenant on item_analyses
    using (exists (select 1 from items i where i.id = item_analyses.item_id))
    with check (exists (select 1 from items i where i.id = item_analyses.item_id));

drop policy if exists digest_evidence_tenant on digest_evidence;
create policy digest_evidence_tenant on digest_evidence
    using (exists (select 1 from digests d where d.id = digest_evidence.digest_id))
    with check (exists (select 1 from digests d where d.id = digest_evidence.digest_id));

-- =============================================================================
-- 认证的两个受控出口
--
-- 登录/注册发生在"还不知道 user_id"的时刻，没法 set app.user_id，
-- 所以不能走普通 RLS 路径。解决办法不是给 users 关掉 RLS，
-- 而是开两个**只做一件事**的定义者函数：它们能绕过 RLS，但也只能读/写 users。
--
-- 注意 users 表只 enable 不 force：owner 可绕过，非 owner 应用角色照常受策略约束。
-- 若加了 force，这两个函数（以 owner 身份运行）也会被自己的策略挡住。
-- =============================================================================
create or replace function auth_lookup(p_email text)
returns table (id uuid, email text, password_hash text, display_name text, created_at timestamptz)
language sql security definer set search_path = public as $$
    select u.id, u.email, u.password_hash, u.display_name, u.created_at
    from users u
    where lower(u.email) = lower(p_email)
    limit 1
$$;

create or replace function auth_register(p_email text, p_password_hash text, p_display_name text)
returns table (id uuid, email text, display_name text, created_at timestamptz)
language sql security definer set search_path = public as $$
    insert into users (email, password_hash, display_name)
    values (p_email, p_password_hash, p_display_name)
    on conflict do nothing
    returning users.id, users.email, users.display_name, users.created_at
$$;

revoke all on function auth_lookup(text) from public;
revoke all on function auth_register(text, text, text) from public;

-- =============================================================================
-- 授权
--
-- 三个角色分工，缺一个 RLS 就会失效或误伤：
--   pim_owner  —— 迁移用，是表的 owner（由 01_roles.sql 创建）
--   pim_app    —— 应用连接用，非 owner 非超级用户，RLS 对它生效
--   pim_worker —— 后台任务用，同上
--
-- 用 DO 块判断角色是否存在：不是所有部署都分角色（比如本地单机调试），
-- 缺失时跳过而不是让迁移失败。
-- =============================================================================
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'pim_app') then
        execute 'grant usage on schema public to pim_app';
        execute 'grant select, insert, update, delete on all tables in schema public to pim_app';
        execute 'grant usage, select on all sequences in schema public to pim_app';
        execute 'grant execute on function auth_lookup(text) to pim_app';
        execute 'grant execute on function auth_register(text, text, text) to pim_app';
        -- jobs 是全局队列：应用只负责投递，读不到别人的任务参数
        execute 'revoke all on jobs from pim_app';
        execute 'grant insert on jobs to pim_app';
        execute 'grant usage on sequence jobs_id_seq to pim_app';
    end if;

    if exists (select 1 from pg_roles where rolname = 'pim_worker') then
        execute 'grant usage on schema public to pim_worker';
        execute 'grant select, insert, update, delete on all tables in schema public to pim_worker';
        execute 'grant usage, select on all sequences in schema public to pim_worker';
    end if;
end $$;

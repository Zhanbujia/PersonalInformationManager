-- =============================================================================
-- 数据库初始化（容器首次启动时自动执行，以超级用户身份连到 pim 库）
--
-- 这里只做三件超级用户才能做的事：建扩展、建角色、给 schema 权限。
-- 建表在 Alembic 里做（以 pim_owner 身份），这样表 owner 天然是 pim_owner。
--
-- 为什么要三个角色，不能用一个：
--   RLS 对「表 owner」和「超级用户」是默认绕过的（owner 需要 force 才受约束，
--   超级用户连 force 都不管用）。如果应用用 postgres 连库，
--   `row level security` 配得再全也等于零，而且不会报任何错。
-- =============================================================================

create extension if not exists vector;
create extension if not exists pg_trgm;
create extension if not exists pgcrypto;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'pim_owner') then
        create role pim_owner login password 'pim_owner';
    end if;
    if not exists (select 1 from pg_roles where rolname = 'pim_app') then
        create role pim_app login password 'pim_app';
    end if;
    if not exists (select 1 from pg_roles where rolname = 'pim_worker') then
        create role pim_worker login password 'pim_worker';
    end if;
end $$;

-- PostgreSQL 15 起 public schema 默认不给普通角色 create 权限，这里显式给迁移角色
grant usage, create on schema public to pim_owner;
grant usage on schema public to pim_app;
grant usage on schema public to pim_worker;

-- 时区：待办分组、总结周期都依赖 current_date，统一到北京时间。
-- 不写死库名，避免改了 POSTGRES_DB 之后这一步悄悄失败。
do $$
begin
    execute format('alter database %I set timezone to %L', current_database(), 'Asia/Shanghai');
end $$;

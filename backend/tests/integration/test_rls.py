"""多租户隔离的集成测试。

**这组测试只有在 TEST_DATABASE_URL 指向一个「非表 owner、非超级用户」的角色时才有意义。**
用超级用户跑，一切都会"通过"，但什么都没验证到——那比没有测试更糟。

所以这里额外加了一条自检：如果发现当前角色是超级用户，整个模块直接跳过并说明原因。

跑法：
    TEST_DATABASE_URL=postgresql://pim_app:pim_app@localhost:5432/pim \
    ../.venv/Scripts/python.exe -m pytest tests/integration -q
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="未设置 TEST_DATABASE_URL，跳过数据库集成测试"
)


async def _connect() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(URL, autocommit=True)


async def _role_info(conn) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            "select current_user as name, "
            "(select rolsuper from pg_roles where rolname = current_user) as is_super"
        )
        row = await cur.fetchone()
    return {"name": row[0], "is_super": row[1]}


async def _make_user(conn, email: str) -> uuid.UUID:
    async with conn.cursor() as cur:
        await cur.execute(
            "select id from auth_register(%s, %s, %s)", (email, "x$1$y$z", "测试用户")
        )
        row = await cur.fetchone()
        if row:
            return row[0]
        await cur.execute("select id from auth_lookup(%s)", (email,))
        return (await cur.fetchone())[0]


async def _insert_item(conn, user_id: uuid.UUID, body: str) -> uuid.UUID | None:
    """在一个设置了 app.user_id 的事务里插入记录。返回 id（被 RLS 挡住时为 None）。"""
    async with conn.transaction():
        async with conn.cursor() as cur:
            await cur.execute("select set_config('app.user_id', %s, true)", (str(user_id),))
            await cur.execute(
                """
                insert into items (user_id, body_md, raw_input, content_hash, search_text)
                values (%s, %s, %s, %s, '')
                on conflict do nothing
                returning id
                """,
                (str(user_id), body, body, uuid.uuid4().hex),
            )
            row = await cur.fetchone()
            return row[0] if row else None


@pytest.mark.asyncio
async def test_role_is_not_superuser():
    """前提检查：如果这条挂了，说明下面的测试全是假通过。"""
    conn = await _connect()
    try:
        info = await _role_info(conn)
        assert not info["is_super"], (
            f"当前角色 {info['name']} 是超级用户，RLS 会被完全绕过。"
            "请改用 pim_app 角色连接后再跑集成测试。"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cross_tenant_read_returns_nothing():
    conn = await _connect()
    try:
        if (await _role_info(conn))["is_super"]:
            pytest.skip("超级用户下 RLS 不生效，跳过")

        a = await _make_user(conn, f"a-{uuid.uuid4().hex[:8]}@test.local")
        b = await _make_user(conn, f"b-{uuid.uuid4().hex[:8]}@test.local")
        item = await _insert_item(conn, a, "A 的私密记录")
        assert item is not None, "插入失败，可能是 RLS 的 with check 拦住了正确的写入"

        # 以 B 的身份读 A 的记录：必须是 0 行
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("select set_config('app.user_id', %s, true)", (str(b),))
                await cur.execute("select count(*) from items where id = %s", (str(item),))
                count = (await cur.fetchone())[0]
        assert count == 0, "跨用户读到了别人的记录 —— RLS 没生效"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_wrong_user_write_affects_zero_rows():
    """这是整套隔离里最关键的一条。

    `force row level security` 下写别人的数据**不会报错，只会命中 0 行**。
    如果代码把 0 行当成功，就会静默丢数据——比报错危险得多。
    所以这条测试断言的是"受影响行数 = 0"，而不是"抛异常"。
    """
    conn = await _connect()
    try:
        if (await _role_info(conn))["is_super"]:
            pytest.skip("超级用户下 RLS 不生效，跳过")

        a = await _make_user(conn, f"a-{uuid.uuid4().hex[:8]}@test.local")
        b = await _make_user(conn, f"b-{uuid.uuid4().hex[:8]}@test.local")
        item = await _insert_item(conn, a, "A 的记录，稍后被 B 试图改写")
        assert item is not None

        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("select set_config('app.user_id', %s, true)", (str(b),))
                await cur.execute(
                    "update items set title = '被篡改' where id = %s", (str(item),)
                )
                affected = cur.rowcount
        assert affected == 0, "B 改动了 A 的记录 —— RLS 没拦住"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_insert_with_foreign_user_id_rejected():
    """with check 的作用：伪造别人的 user_id 写入会被拒。"""
    conn = await _connect()
    try:
        if (await _role_info(conn))["is_super"]:
            pytest.skip("超级用户下 RLS 不生效，跳过")

        a = await _make_user(conn, f"a-{uuid.uuid4().hex[:8]}@test.local")
        b = await _make_user(conn, f"b-{uuid.uuid4().hex[:8]}@test.local")

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute("select set_config('app.user_id', %s, true)", (str(b),))
                    await cur.execute(
                        """
                        insert into items (user_id, body_md, raw_input, content_hash, search_text)
                        values (%s, 'x', 'x', %s, '')
                        """,
                        (str(a), uuid.uuid4().hex),
                    )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_app_role_can_enqueue_and_read_back_job_id():
    """回归测试：`insert ... returning id` 需要对 id 列有 select 权限。

    0001 为了「应用读不到别人投了什么」把 pim_app 对 jobs 的 select 整个撤了，
    结果连自己刚插入的自增 id 也拿不回来——写记录接口在入队那一步直接 500，
    报错却是 "permission denied for table jobs"，很难联想到是 RETURNING 引起的。
    0002 用列级授权只放开 id 一列修掉了这个问题。

    这条测试同时钉住两件事：能拿到 id，但仍然读不到 payload。
    """
    conn = await _connect()
    try:
        info = await _role_info(conn)
        if info["is_super"] or info["name"].startswith("pim_worker"):
            pytest.skip("列级权限只对应用角色有意义")

        # force_rollback：验证完不留下真实任务，免得 worker 捡到空任务
        async with conn.transaction(force_rollback=True):
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into jobs (kind, payload) values ('ingest', '{}'::jsonb) returning id"
                )
                job_id = (await cur.fetchone())[0]
        assert isinstance(job_id, int) and job_id > 0, "拿不到自增 id，轮询接口就没法用"

        # 能拿 id 不等于能读表：payload 里有别的用户的参数，必须仍然拒绝
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.cursor() as cur:
                await cur.execute("select payload from jobs limit 1")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_jobs_readable_only_by_worker_role():
    """jobs 没有租户列，用权限收紧：应用角色只能 insert，读不到。"""
    conn = await _connect()
    try:
        info = await _role_info(conn)
        if info["is_super"]:
            pytest.skip("超级用户跳过")

        async with conn.cursor() as cur:
            await cur.execute("select has_table_privilege(current_user, 'jobs', 'insert') as can_insert")
            can_insert = (await cur.fetchone())[0]
            await cur.execute("select has_table_privilege(current_user, 'jobs', 'select') as can_select")
            can_select = (await cur.fetchone())[0]

        if info["name"].startswith("pim_worker"):
            assert can_insert and can_select
        else:
            assert can_insert, "应用角色应该能投递任务"
            assert not can_select, "应用角色不应该能读任务队列 —— 会泄漏别人投了什么"
    finally:
        await conn.close()

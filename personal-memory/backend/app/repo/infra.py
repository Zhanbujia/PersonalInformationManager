"""任务队列、执行记录、token 账本。

队列用数据库轮询而不是 Redis：任务量很小（每天几十条），
多一个中间件就多一个故障点。领取用 `for update skip locked`，
保证多个 worker 不会抢到同一条。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

# --------------------------------------------------------------------------- jobs


async def enqueue(
    conn: AsyncConnection,
    *,
    kind: str,
    payload: dict[str, Any],
    max_attempts: int = 3,
) -> int:
    """投递任务。注意：jobs 表没有租户列，用 `system_tx()` 调用。

    app 角色只有 insert 权限，读不到别人投了什么——这是故意的。
    """
    cur = await conn.execute(
        "insert into jobs (kind, payload, max_attempts) values (%s, %s::jsonb, %s) returning id",
        (kind, json.dumps(payload, ensure_ascii=False), max_attempts),
    )
    return (await cur.fetchone())["id"]


async def claim_job(conn: AsyncConnection, worker_id: str, kinds: list[str]) -> dict | None:
    """领取一个到期的任务。

    `for update skip locked` 是重点：并发 worker 各拿各的，不会互相等待。
    锁超时（默认 5 分钟）后 `run_at` 会被推后，避免 worker 崩了任务永久卡住。
    """
    cur = await conn.execute(
        """
        update jobs
           set locked_at = now(),
               locked_by = %(worker)s,
               attempts = attempts + 1
         where id = (
             select id from jobs
              where locked_at is null
                and finished_at is null
                and run_at <= now()
                and kind = any(%(kinds)s)
              order by run_at asc, id asc
              limit 1
              for update skip locked
         )
        returning id, kind, payload, attempts, max_attempts
        """,
        {"worker": worker_id, "kinds": kinds},
    )
    return await cur.fetchone()


async def finish_job(conn: AsyncConnection, job_id: int, result: dict | None = None) -> None:
    await conn.execute(
        "update jobs set finished_at = now(), locked_at = null, result = %s::jsonb where id = %s",
        (None if result is None else json.dumps(result, ensure_ascii=False, default=str), job_id),
    )


async def job_progress(conn: AsyncConnection, job_id: int) -> dict | None:
    cur = await conn.execute(
        "select id, kind, attempts, max_attempts, finished_at, last_error, result "
        "from jobs where id = %s",
        (job_id,),
    )
    return await cur.fetchone()


async def retry_job(conn: AsyncConnection, job_id: int, error: str, delay_sec: int = 30) -> None:
    """失败重排。delay 按次数退避，避免一条坏任务高频空转。"""
    await conn.execute(
        """
        update jobs
           set locked_at = null,
               locked_by = null,
               last_error = %s,
               run_at = now() + make_interval(secs => %s),
               finished_at = case when attempts >= max_attempts then now() else null end
         where id = %s
        """,
        (error[:2000], delay_sec, job_id),
    )


async def release_job(conn: AsyncConnection, job_id: int) -> None:
    """正常结束后释放锁（finished_at 已标记，这里保持 locked_at 也无妨）。"""
    await conn.execute("update jobs set locked_at = null where id = %s", (job_id,))


# --------------------------------------------------------------------------- runs


async def start_run(
    conn: AsyncConnection,
    *,
    user_id: UUID,
    kind: str,
    idem_key: str,
    step: str | None = None,
    input_hash: str | None = None,
) -> dict | None:
    """登记一次执行。

    返回 None 表示这个幂等键**已经成功执行过**——调用方应当直接复用结果，
    不要再算一遍。这是"重试不重复干活"的唯一依据。
    """
    cur = await conn.execute(
        "select id, status, output_ref from runs where kind = %s and idem_key = %s",
        (kind, idem_key),
    )
    existing = await cur.fetchone()
    if existing and existing["status"] == "succeeded":
        return None

    if existing:
        cur = await conn.execute(
            """
            update runs
               set status = 'running', attempt = attempt + 1, step = %s,
                   input_hash = coalesce(%s, input_hash), error_code = null
             where id = %s
            returning id, status, output_ref
            """,
            (step, input_hash, existing["id"]),
        )
        return await cur.fetchone()

    cur = await conn.execute(
        """
        insert into runs (user_id, kind, idem_key, step, input_hash)
        values (%s, %s, %s, %s, %s)
        returning id, status, output_ref
        """,
        (str(user_id), kind, idem_key, step, input_hash),
    )
    return await cur.fetchone()


async def finish_run(
    conn: AsyncConnection,
    *,
    run_id: UUID,
    status: str,
    output_ref: dict | None = None,
    error_code: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cached_tokens: int = 0,
    latency_ms: int | None = None,
) -> None:
    await conn.execute(
        """
        update runs
           set status = %s, output_ref = %s::jsonb, error_code = %s,
               tokens_in = tokens_in + %s, tokens_out = tokens_out + %s,
               cached_tokens = cached_tokens + %s,
               latency_ms = %s, finished_at = now()
         where id = %s
        """,
        (
            status,
            None if output_ref is None else json.dumps(output_ref, ensure_ascii=False, default=str),
            error_code,
            tokens_in,
            tokens_out,
            cached_tokens,
            latency_ms,
            str(run_id),
        ),
    )


# --------------------------------------------------------------------------- usage


async def record_usage(
    conn: AsyncConnection,
    *,
    user_id: UUID,
    path: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cached_tokens: int = 0,
) -> None:
    """记账。同一用户同一天同一条路径累加，冲突时相加。"""
    await conn.execute(
        """
        insert into usage_ledger (user_id, day, path, model, tokens_in, tokens_out, cached_tokens, calls)
        values (%s, current_date, %s, %s, %s, %s, %s, 1)
        on conflict (user_id, day, path, model) do update
           set tokens_in = usage_ledger.tokens_in + excluded.tokens_in,
               tokens_out = usage_ledger.tokens_out + excluded.tokens_out,
               cached_tokens = usage_ledger.cached_tokens + excluded.cached_tokens,
               calls = usage_ledger.calls + 1
        """,
        (str(user_id), path, model, tokens_in, tokens_out, cached_tokens),
    )


async def usage_of_month(conn: AsyncConnection, user_id: UUID, month: str) -> list[dict]:
    """month 形如 '2026-10'。"""
    cur = await conn.execute(
        """
        select day, path, model,
               sum(tokens_in)::bigint as tokens_in,
               sum(tokens_out)::bigint as tokens_out,
               sum(cached_tokens)::bigint as cached_tokens,
               sum(calls)::int as calls
        from usage_ledger
        where user_id = %s and to_char(day, 'YYYY-MM') = %s
        group by day, path, model
        order by day desc, path
        """,
        (str(user_id), month),
    )
    return await cur.fetchall()


async def tokens_used_today(conn: AsyncConnection, user_id: UUID) -> int:
    cur = await conn.execute(
        """
        select coalesce(sum(tokens_in + tokens_out), 0)::bigint as n
        from usage_ledger where user_id = %s and day = current_date
        """,
        (str(user_id),),
    )
    return int((await cur.fetchone())["n"])


async def digest_job_status(conn: AsyncConnection, job_id: int) -> dict | None:
    cur = await conn.execute(
        "select id, kind, finished_at, last_error, payload from jobs where id = %s", (job_id,)
    )
    return await cur.fetchone()


def today() -> date:
    from datetime import datetime

    return datetime.now().date()

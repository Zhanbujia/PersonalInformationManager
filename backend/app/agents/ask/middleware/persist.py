"""拦截点 ⑥：落账。

一次问答结束后必须落三样东西，缺一样就会在排查时变瞎：

1. **消息**（含引用快照）——用户重新打开会话要能看到历史与出处；
2. **执行记录**（`runs`）——幂等键 + token + 延迟，这是"重放不重复扣费"的依据；
3. **用量账本**（`usage_ledger`）——按月按路径累计，额度控制靠它。

注意这里全程使用租户事务：`qa_messages` 有 user_id，写错租户会被 RLS 挡住，
而且是静默 0 行。所以写完之后查一次消息 id 是否存在，不给自己留盲区。
"""

from __future__ import annotations

import logging
import time

from langchain.agents.middleware import after_agent

from app.agents.ask.context import AskContext
from app.repo import infra as infra_repo
from app.repo import qa as qa_repo

logger = logging.getLogger(__name__)


@after_agent
async def persist_run(state, runtime):
    ctx: AskContext = runtime.context
    if ctx.db is None or ctx.session_id is None:
        return None

    text = ctx.answer_text
    if not text:
        # 兜底：从最终消息里取
        for m in reversed(state["messages"]):
            content = getattr(m, "content", "")
            if isinstance(content, str) and content.strip():
                text = content
                break

    started = getattr(ctx, "_started_at", None) or time.time()
    latency_ms = int((time.time() - started) * 1000) if started else None

    try:
        async with ctx.db.tx(ctx.user_id) as conn:
            msg_id = await qa_repo.add_message(
                conn,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                role="assistant",
                content=text or "",
                citations=ctx.citations,
                tokens_in=ctx.tokens_in,
                tokens_out=ctx.tokens_out,
                cached_tokens=ctx.cached_tokens,
                model=ctx.model_name or None,
            )
            await qa_repo.touch_session(conn, ctx.session_id)

            run = await infra_repo.start_run(
                conn, user_id=ctx.user_id, kind="qa", idem_key=f"qa:{ctx.user_id}:{ctx.turn_id}"
            )
            if run is not None:
                await infra_repo.finish_run(
                    conn,
                    run_id=run["id"],
                    status="succeeded",
                    output_ref={"messageId": str(msg_id), "citations": len(ctx.citations)},
                    tokens_in=ctx.tokens_in,
                    tokens_out=ctx.tokens_out,
                    cached_tokens=ctx.cached_tokens,
                    latency_ms=latency_ms,
                )
            if ctx.tokens_in or ctx.tokens_out:
                await infra_repo.record_usage(
                    conn,
                    user_id=ctx.user_id,
                    path="qa",
                    model=ctx.model_name or "unknown",
                    tokens_in=ctx.tokens_in,
                    tokens_out=ctx.tokens_out,
                    cached_tokens=ctx.cached_tokens,
                )
        logger.info(
            "问答落账 session=%s rounds=%s tokens=%s/%s citations=%s",
            ctx.session_id,
            ctx.rounds,
            ctx.tokens_in,
            ctx.tokens_out,
            len(ctx.citations),
        )
    except Exception:  # noqa: BLE001 - 落账失败不该让已经生成的答案变空
        logger.exception("问答落账失败 session=%s", ctx.session_id)
    return None

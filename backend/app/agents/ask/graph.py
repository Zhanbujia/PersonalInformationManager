"""问答 Agent 装配。

这是整个项目**唯一**用 Agent 的地方，也是唯一用 LangChain Agent 层的地方。

`create_agent` 帮我们做了一件很值的事：**保证六个拦截点的调用时机**。
自己写循环也能做，但"某次改动之后某个钩子不再执行"这类 bug 极难发现；
由框架保证时序，我们只写每个钩子里的业务判断，这是净收益。

中间件顺序就是执行顺序，和设计文档里的六个拦截点一一对应：
① before_agent（在 API 层注入身份）
② before_model  预算 + 水位
③ wrap_model_call 缓存标记
④ wrap_tool_call  工具授权与失败计数
⑤ after_model     引用校验与用量累计
⑥ after_agent     落账
"""

from __future__ import annotations

import logging

from langchain.agents import create_agent
from langchain_core.messages import SystemMessage

from app.agents.ask.context import AskContext
from app.agents.ask.middleware.budget import budget_guard
from app.agents.ask.middleware.cache_marker import cache_marker
from app.agents.ask.middleware.citation import citation_check
from app.agents.ask.middleware.persist import persist_run
from app.agents.ask.middleware.tool_guard import tool_guard
from app.agents.ask.middleware.trimmer import context_trimmer
from app.agents.ask.prompt import ASK_SYSTEM
from app.agents.ask.tools.registry import build_tools
from app.config import settings

logger = logging.getLogger(__name__)


def build_ask_agent(strong_llm, ctx: AskContext, db, checkpointer=None):
    """按请求装配。工具要绑定当前用户，所以不能做成进程级单例。"""
    tools = build_tools(ctx, db)

    agent = create_agent(
        model=strong_llm,
        tools=tools,
        # 用 SystemMessage 而不是裸字符串：只有它才能携带 content block，
        # 也才有可能被 cache_marker 打上缓存标记
        system_prompt=SystemMessage(content=ASK_SYSTEM),
        middleware=[
            # 用装饰器写出来的已经是 AgentMiddleware 实例，直接放进来，不要加括号
            budget_guard,
            context_trimmer,
            cache_marker,
            tool_guard,
            citation_check,
            persist_run,
        ],
        context_schema=AskContext,
        checkpointer=checkpointer,
        name="ask",
    )
    return agent


def agent_runtime_config(ctx: AskContext, thread_id: str | None = None) -> dict:
    """运行时配置。

    `recursion_limit` 必须放在 config **根层**，放进 `configurable` 里会静默失效。
    它数的是"超级步"（一次模型调用或一次工具调用算一步），不是"轮"：
    5 轮 ≈ 10~11 步，所以设 12，留一步余量。
    """
    return {
        "recursion_limit": settings.recursion_limit,
        "configurable": {"thread_id": thread_id or f"{ctx.user_id}:{ctx.turn_id}"},
    }

"""引用校验与预算守卫的单测。

用假模型跑通整条 Agent 装配，不联网、不连库。
这一步能验证到的比想象的少（真实模型行为验证不了），
但能验证到最关键的：**中间件真的被调用了，且顺序没错**。
"""

from __future__ import annotations

import uuid

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.ask.context import AskContext
from app.agents.ask.graph import build_ask_agent
from app.agents.ask.middleware.budget import budget_guard_impl
from app.agents.ask.middleware.citation import UNVERIFIED, verify_and_render


class ScriptedChatModel(BaseChatModel):
    """按脚本依次返回固定文本的假模型。"""

    responses: list[str]
    cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN201
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN201
        text = self.responses[min(self.cursor, len(self.responses) - 1)]
        self.cursor += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


@pytest.fixture
def ctx() -> AskContext:
    c = AskContext(user_id=uuid.uuid4())
    c.evidence = {
        1: {"chunkId": "c1", "itemId": "i1", "title": "笔记一", "quote": "原文片段一"},
        2: {"chunkId": "c2", "itemId": "i2", "title": "笔记二", "quote": "原文片段二"},
    }
    return c


def test_citation_kept_when_backed(ctx: AskContext):
    text, cites = verify_and_render("你记录过这件事 [1]。", ctx)
    assert cites and cites[0]["n"] == 1
    assert "[1]" in text
    assert UNVERIFIED not in text


def test_citation_downgraded_when_not_backed(ctx: AskContext):
    text, cites = verify_and_render("你记录过这件事 [9]。", ctx)
    assert cites == []
    assert UNVERIFIED in text
    assert "[9]" not in text


def test_citation_renumbered_contiguously(ctx: AskContext):
    text, cites = verify_and_render("先说一 [2]，再说二 [1]。", ctx)
    assert [c["n"] for c in cites] == [1, 2]
    # 原来的 [2] 现在排第一，编号应为 1
    assert text.startswith("先说一 [1]")


def test_mixed_valid_and_invalid(ctx: AskContext):
    text, cites = verify_and_render("真的 [1] 假 [7]", ctx)
    assert len(cites) == 1
    assert UNVERIFIED in text


def test_no_citation_no_problem(ctx: AskContext):
    text, cites = verify_and_render("你的记录里没有提到这个。", ctx)
    assert cites == []
    assert text == "你的记录里没有提到这个。"


@pytest.mark.asyncio
async def test_agent_assembles_and_runs(ctx: AskContext):
    """整套装配（工具 + 6 个中间件 + 假模型）能跑起来，且引用被校验。"""
    model = ScriptedChatModel(responses=["你上次记的是混合检索 [1]。"])
    agent = build_ask_agent(model, ctx, db=None)

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "我上次记了什么"}]},
        config={"recursion_limit": 12},
        context=ctx,
    )
    assert result["messages"]
    # citation_check 是 after_model，应该已经把引用收集起来了
    assert len(ctx.citations) == 1
    assert ctx.citations[0]["chunkId"] == "c1"
    assert ctx.rounds >= 1


@pytest.mark.asyncio
async def test_budget_guard_stops_when_rounds_exhausted(ctx: AskContext):
    ctx.rounds = 99

    class _Runtime:
        context = ctx

    out = await budget_guard_impl({"messages": []}, _Runtime())
    assert out is not None
    assert out["jump_to"] == "end"
    assert ctx.truncated
    assert ctx.truncate_reason == "max_rounds"


@pytest.mark.asyncio
async def test_budget_guard_passes_when_on_budget(ctx: AskContext):
    ctx.rounds = 1

    class _Runtime:
        context = ctx

    assert await budget_guard_impl({"messages": []}, _Runtime()) is None

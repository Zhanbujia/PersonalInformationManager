"""mock 提供商的单元测试（不需要数据库、不需要模型密钥）。

保证三件事在改代码后不会被破坏：
1. 结构化抽取产出能被程序校验通过（quote 是原文子串、摘要无数字）；
2. 向量确定性且维度一致；
3. 问答 Agent 两步调用：第一次发 search_records 工具调用，
   第二次（已有工具结果）给出带 [n] 引用的答案。
"""

from __future__ import annotations

import asyncio

from app.agents.digest.generate import DigestDraft
from app.agents.ingest.extract import locate_spans
from app.agents.ingest.schema import ItemAnalysis
from app.infra.mock import (
    MockChatModel,
    MockEmbeddings,
    _mock_digest,
    _mock_item_analysis,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def test_item_analysis_is_validatable() -> None:
    text = "周五之前给团队讲一次 RLS 的原理。下周要把检索效果调好。这是一条普通记录。"
    ia = _mock_item_analysis(text)
    assert isinstance(ia, ItemAnalysis)
    # 关键：quote 必须真的出现在原文里，否则 locate_spans 会失败
    kp, todos = locate_spans(ia, text)
    assert len(kp) >= 1 and len(todos) >= 1
    # 摘要不许有数字
    assert not any(c.isdigit() for c in ia.summary)


def test_digest_uses_only_stat_placeholder() -> None:
    prompt = (
        "## 本期记录摘录\n"
        "id=05d9da15-11e5-43ed-8a45-2298398eb483\n类型=note\n标题=RLS 笔记\n摘录=x\n"
    )
    dg = _mock_digest(prompt)
    assert isinstance(dg, DigestDraft)
    body = dg.body_markdown
    assert "{{stat:itemCount}}" in body
    # 去掉占位符后不能有任何数字
    stripped = body.replace("{{stat:itemCount}}", "")
    assert not any(c.isdigit() for c in stripped)
    assert dg.action_items and dg.action_items[0].evidence_item_ids


def test_embeddings_deterministic_and_dimension() -> None:
    emb = MockEmbeddings(1024)
    v1 = asyncio.run(emb.aembed_query("同一段文本"))
    v2 = asyncio.run(emb.aembed_query("同一段文本"))
    v3 = asyncio.run(emb.aembed_query("另一段文本"))
    assert len(v1) == 1024
    assert v1 == v2  # 确定性
    assert v1 != v3  # 不同文本不同向量


def test_agent_emits_tool_call_first_then_cited_answer() -> None:
    m = MockChatModel()
    r1 = m.invoke([HumanMessage(content="RLS 是什么")])
    assert r1.tool_calls, "第一次调用应当发出工具调用"
    assert r1.tool_calls[0]["name"] == "search_records"

    tool_payload = (
        '{"hits":[{"n":1,"title":"RLS 原理","text":"关于 RLS 的笔记"}]}'
    )
    r2 = m.invoke(
        [
            HumanMessage(content="RLS 是什么"),
            AIMessage(content="", tool_calls=r1.tool_calls),
            ToolMessage(content=tool_payload, tool_call_id=r1.tool_calls[0]["id"]),
        ]
    )
    final = r2.content
    assert "[1]" in final, "最终答案应当引用检索结果 [1]"

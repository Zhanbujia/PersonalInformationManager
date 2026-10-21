"""本地无密钥的演示用模型实现。

只在 `LLM_PROVIDER=mock` 时启用，目的是让"入库分析 → 向量化 → 检索 → 问答 → 总结"
整条链路在没有真实模型密钥的情况下也能跑通、能被测试。

设计上刻意做到三件事：

1. **结构化抽取（with_structured_output）产出的内容可被程序校验通过**。
   - 入库分析要求 quote 必须是原文字段里的连续片段，否则判定失败；
     所以 mock 直接把原文的句子切片当 quote，保证 `_locate` 一定成功。
   - 摘要里不许出现数字：`check_no_digits` 会拦，所以 summary 先剥掉数字。
2. **向量是确定性的**，维度与配置一致（`Embeddings.embed` 会校验维度）。
   用文本 sha256 派生伪随机向量并归一化，相同文本永远得到相同向量，
   这样纯关键词检索降级 / 向量检索两条路都能演示。
3. **问答 Agent 是无状态的**：看消息里有没有 ToolMessage 决定这一步该
   发工具调用还是给最终答案。这样并发请求不会互相串状态。

这些实现都是"长得像真实模型输出"的占位，不追求质量，只追求链路可跑、可测。
真正上线请改用 OpenAI 兼容网关（改 `LLM_PROVIDER` 与密钥即可）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from pydantic import PrivateAttr


# --------------------------------------------------------------------------- #
# 文本抽取辅助
# --------------------------------------------------------------------------- #
_SENT_SPLIT = re.compile(r"([。！？\n])")
_DIGIT = re.compile(r"\d")
_TAG_HINTS = {
    "检索": ["检索", "搜索", "查询", "找"],
    "向量": ["向量", "embedding", "相似"],
    "安全": ["权限", "RLS", "隔离", "策略"],
    "总结": ["总结", "回顾", "阶段"],
    "待办": ["待办", "任务", "todo"],
    "数据库": ["数据库", "postgres", "sql"],
    "模型": ["模型", "llm", "agent", "智能"],
    "知识库": ["知识库", "笔记", "记录"],
}


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text)
    out: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        seg = (parts[i] + parts[i + 1]).strip()
        if seg:
            out.append(seg)
    tail = parts[-1].strip() if parts else ""
    if tail:
        out.append(tail)
    return out


def _extract_text(messages: Any) -> str:
    """把模型输入（[(role,text)] 元组或消息对象）压成一段文本。

    只取 user 角色的内容：system 提示词里也有句子，
    一旦被当成 quote 来源就会在原文里定位不到（SpanMismatch）。
    入库分析时真正的"原文"只在 user 消息里。
    """
    if isinstance(messages, (list, tuple)):
        chunks: list[str] = []
        for item in messages:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                if item[0] == "user":
                    chunks.append(str(item[1]))
            elif isinstance(item, HumanMessage):
                chunks.append(str(item.content))
            elif isinstance(item, BaseMessage) and item.type == "human":
                chunks.append(str(item.content))
        return "\n".join(chunks)
    if isinstance(messages, HumanMessage):
        return str(messages.content)
    if isinstance(messages, BaseMessage):
        return str(messages.content) if messages.type == "human" else ""
    return str(messages)


def _last_human(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


# --------------------------------------------------------------------------- #
# 结构化输出：入库分析
# --------------------------------------------------------------------------- #
def _ingest_original(user_msg: str) -> str:
    """入库的用户消息把原文包在 `---` 之间，并带一句固定引导语。

    真正的原文只在那对 `---` 里面；prompt 指令（"以下三段横线之间是用户记录的原文"）
    本身不是用户内容，不能当 quote 来源，否则 locate 会失败。
    digest 的 prompt 也含 `---` 但没这句引导语，所以靠这句标记来区分。
    """
    marker = "以下三段横线之间是用户记录的原文"
    if marker in user_msg:
        start = user_msg.index("---", user_msg.index(marker))
        end = user_msg.index("---", start + 3)
        return user_msg[start + 3 : end].strip("\n")
    return user_msg


def _mock_item_analysis(text: str):
    from app.agents.ingest.schema import ItemAnalysis, QuoteSpan

    text = _ingest_original(text)
    sents = _split_sentences(text)
    if not sents:
        sents = [text[:40]]

    key_points = [QuoteSpan(quote=s).model_dump() for s in sents[:3]]
    todo_re = re.compile(r"计划|要做|需要|必须|打算|周五|下周|截止|之前|应该|建议")
    todos = [
        QuoteSpan(quote=s).model_dump()
        for s in sents
        if todo_re.search(s)
    ][:3]

    title = re.sub(r"\s+", " ", text)[:18].strip()
    summary_src = sents[0]
    title_for_summary = title
    # 摘要不许有数字
    summary = _DIGIT.sub("", summary_src)[:120] or "这是一条个人记录"

    tags: list[str] = []
    low = text.lower()
    for tag, hints in _TAG_HINTS.items():
        if any(h in low for h in hints):
            tags.append(tag)
    while len(tags) < 3:
        tags.append(["个人记录", "想法", "待整理"][len(tags) % 3])
    tags = tags[:6]

    kind = "idea" if todo_re.search(text) else "note"

    return ItemAnalysis(
        kind=kind,
        title=title_for_summary,
        summary=summary,
        tags=tags,
        key_points=key_points,
        todos=todos,
        confidence=0.9,
    )


# --------------------------------------------------------------------------- #
# 结构化输出：阶段总结
# --------------------------------------------------------------------------- #
_UUID_RE = re.compile(r"id=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")


def _mock_digest(text: str):
    from app.agents.digest.generate import DigestDraft, ActionDraft

    candidate_ids = _UUID_RE.findall(text)
    body = (
        "## 这个阶段你记了什么\n\n"
        "本期共记录 {{stat:itemCount}} 条，内容跨了几个不同的主题。\n\n"
        "## 几条线索\n\n"
        "这些记录之间暂时看不出强关联，先各自独立观察即可。\n\n"
        "## 建议接下来做什么\n\n"
        "挑一个出现频率最高的主题，再补几条细节，让它更完整。"
    )

    action_items = []
    if candidate_ids:
        action_items.append(
            ActionDraft(
                text="把最关心的那条记录再补充一些背景细节",
                reason="现有记录已经显出线索，值得深入",
                evidence_item_ids=[candidate_ids[0]],
            )
        )

    return DigestDraft(body_markdown=body, action_items=action_items)


# --------------------------------------------------------------------------- #
# with_structured_output 的承载 Runnable
# --------------------------------------------------------------------------- #
class _StructuredRunnable(Runnable):
    """把 schema 类交给它，ainvoke 时根据 schema 名产出一个实例。"""

    def __init__(self, schema: type) -> None:
        self._schema = schema

    def _build(self, messages: Any):
        text = _extract_text(messages)
        name = getattr(self._schema, "__name__", "")
        if name == "ItemAnalysis":
            return _mock_item_analysis(text)
        if name == "DigestDraft":
            return _mock_digest(text)
        # 兜底：返回空 dict，调用方自行处理
        return {}

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._build(input)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._build(input)


# --------------------------------------------------------------------------- #
# 聊天模型（问答 Agent 用）
# --------------------------------------------------------------------------- #
class MockChatModel(BaseChatModel):
    """无密钥演示用聊天模型。

    - `with_structured_output(schema)` → 返回 _StructuredRunnable
    - `bind_tools(tools)` → 直接返回自身（不依赖工具定义，工具名硬编码已知）
    - 两次调用之间靠"消息里有没有 ToolMessage"区分：第一次发工具调用，
      第二次（已有工具结果）给最终答案。
    """

    model_config = {"arbitrary_types_allowed": True}

    model_name: str = "mock"
    temperature: float = 0.0
    _tool_name: str = PrivateAttr(default="search_records")

    @property
    def _llm_type(self) -> str:
        return "mock"

    def bind_tools(self, tools: list, **kwargs: Any) -> "MockChatModel":
        for t in tools or []:
            if getattr(t, "name", "") == self._tool_name:
                break
        return self

    def with_structured_output(self, schema: type, **kwargs: Any) -> Runnable:
        return _StructuredRunnable(schema)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        has_tool_result = any(isinstance(m, ToolMessage) for m in messages)
        usage = {"input_tokens": 32, "output_tokens": 24, "total_tokens": 56}
        if has_tool_result:
            content = self._build_final(messages)
            msg: BaseMessage = AIMessage(content=content, usage_metadata=usage)
        else:
            query = _last_human(messages)
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self._tool_name,
                        "args": {"query": query, "k": 5},
                        "id": "mock_tc_1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=usage,
            )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @staticmethod
    def _build_final(messages: list[BaseMessage]) -> str:
        tool_text = ""
        for m in messages:
            if isinstance(m, ToolMessage):
                tool_text = m.content if isinstance(m.content, str) else str(m.content)
        hits: list[dict] = []
        try:
            data = json.loads(tool_text)
            hits = data.get("hits", []) or []
        except Exception:
            hits = []

        if not hits:
            return (
                "（演示模式）没有检索到相关记录，因此无法引用具体来源。"
                "你可以先记录一些内容，再来提问。"
            )

        parts = []
        for h in hits[:5]:
            n = h.get("n")
            title = h.get("title") or "（无标题）"
            snippet = (h.get("text") or "")[:48]
            parts.append(f"[{n}] 《{title}》：{snippet}")
        body = "；".join(parts)
        return (
            f"（演示模式）根据检索到的记录：{body}。"
            "这些内容彼此呼应，建议把它们整理成一条行动线。"
        )


# --------------------------------------------------------------------------- #
# 向量化（确定性，无网络）
# --------------------------------------------------------------------------- #
class MockEmbeddings(Embeddings):
    """把文本哈希成固定维度的伪向量，相同文本结果稳定。"""

    def __init__(self, dim: int = 1024) -> None:
        super().__init__()
        self._dim = dim

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec: list[float] = []
        for i in range(self._dim):
            seed = digest[(i * 2) % len(digest) : (i * 2) % len(digest) + 2]
            val = int.from_bytes(seed, "big") / 65535.0 - 0.5
            vec.append(val)
        norm = (sum(v * v for v in vec) ** 0.5) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)

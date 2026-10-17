"""一次问答请求的运行时上下文。

所有"运行中累加的量"都放这里，而不是放进 LangGraph 的 state：

- 放 state 里要改 `state_schema`，还要处理断点持久化，重启后计数残留会误判；
- 放这里就是一个普通对象，一次请求一个，请求结束自动回收。

中间件通过 `runtime.context` 拿到它，工具通过闭包拿到它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.config import settings
from app.infra.db import Database


@dataclass
class ToolFailure:
    count: int = 0
    last_error: str = ""


@dataclass
class AskContext:
    # ---- 身份与依赖 ----
    user_id: UUID
    db: Database | None = None
    embeddings: Any = None

    # ---- 预算（三个终止条件的两个，第三个是打转检测）----
    max_rounds: int = 5
    turn_tokens: int = 70_000
    max_tool_calls: int = 12
    failure_threshold: int = 3

    # ---- 运行中累加 ----
    rounds: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0

    # ---- 会话 ----
    session_id: UUID | None = None
    turn_id: str = ""
    query: str = ""
    answer_text: str = ""
    model_name: str = ""
    tool_failures: dict[str, ToolFailure] = field(default_factory=dict)
    # 工具返回过的片段：key = 序号，值 = {chunkId, itemId, quote}
    evidence: dict[int, dict] = field(default_factory=dict)
    # 最终确认可用的引用
    citations: list[dict] = field(default_factory=list)
    # 本轮是否因为预算/打转被动结束
    truncated: bool = False
    truncate_reason: str = ""

    # 供工具去重：避免同一次问答里反复重试同一个检索
    search_cache: dict[str, list[dict]] = field(default_factory=dict)
    fingerprint_ring: list[str] = field(default_factory=list)

    def next_citation_no(self) -> int:
        return len(self.evidence) + 1

    def note_tool_failure(self, name: str, error: str) -> int:
        f = self.tool_failures.setdefault(name, ToolFailure())
        f.count += 1
        f.last_error = error[:200]
        return f.count

    def is_disabled(self, name: str) -> bool:
        f = self.tool_failures.get(name)
        return bool(f and f.count >= self.failure_threshold)

    def reset_tool_failures(self, name: str) -> None:
        self.tool_failures.pop(name, None)

    @classmethod
    def from_user(cls, user_id: UUID, db: Database, embeddings: Any = None) -> AskContext:
        return cls(
            user_id=user_id,
            db=db,
            embeddings=embeddings,
            max_rounds=settings.max_rounds,
            turn_tokens=settings.turn_tokens,
            max_tool_calls=settings.max_tool_calls,
            failure_threshold=settings.tool_failure_threshold,
        )

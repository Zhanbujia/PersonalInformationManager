"""Pydantic 模型：请求、响应、以及模型输出的结构化约束。

命名约定：入参以 `In` 结尾，出参以 `Out` 结尾，模型输出用业务名（如 `ItemAnalysis`）。
所有出参用 camelCase，前端拿到就能直接用，不需要再做命名转换。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


class ApiModel(BaseModel):
    """所有对外模型的基类：出参统一 camelCase。"""

    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# --------------------------------------------------------------------------- 公共


class Page[T](ApiModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class OkOut(ApiModel):
    ok: bool = True


class HealthOut(ApiModel):
    status: str
    db: bool
    version: str


# --------------------------------------------------------------------------- 认证


class RegisterIn(ApiModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=40)


class LoginIn(ApiModel):
    email: str
    password: str


class UserOut(ApiModel):
    id: UUID
    email: str
    display_name: str | None = None
    created_at: datetime


class TokenOut(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# --------------------------------------------------------------------------- 记录


ItemKind = Literal["note", "idea", "link", "file", "todo"]
AnalysisStatus = Literal["pending", "running", "done", "failed", "skipped"]


class ItemCreateIn(ApiModel):
    raw_input: str = Field(min_length=1, max_length=50_000)
    kind: ItemKind = "note"
    source_channel: str = Field(default="web", max_length=32)
    source_url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=200)


class ItemPatchIn(ApiModel):
    title: str | None = Field(default=None, max_length=200)
    body_md: str | None = Field(default=None, max_length=50_000)
    kind: ItemKind | None = None
    tags: list[str] | None = None


class ItemCreatedOut(ApiModel):
    """创建成功。重复内容不会走到这里——它抛 409 duplicate_content，
    并在 detail.duplicateOf 里给出已有记录的 id。
    所以这里不再带 duplicate 标记，免得读接口的人以为"可能返回 201 且 duplicate=true"。
    """

    id: UUID
    analysis_status: AnalysisStatus
    captured_at: datetime


class SpanOut(ApiModel):
    quote: str
    start: int
    end: int


class AnalysisOut(ApiModel):
    summary: str
    key_points: list[SpanOut] = Field(default_factory=list)
    todos_extracted: list[SpanOut] = Field(default_factory=list)
    confidence: float
    model: str
    version: int
    created_at: datetime


class ItemLinkOut(ApiModel):
    item_id: UUID
    title: str | None = None
    relation: str
    score: float
    reason: str | None = None


class ItemOut(ApiModel):
    id: UUID
    kind: str
    title: str | None
    body_md: str
    raw_input: str
    source_channel: str
    source_url: str | None
    tags: list[str]
    captured_at: datetime
    updated_at: datetime
    analysis_status: str
    analysis_version: int
    user_pinned: bool
    # 文件类记录才有值；文本记录为 None
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    file_ext: str | None = None
    parse_error: str | None = None
    collection_id: UUID | None = None
    analysis: AnalysisOut | None = None
    links: list[ItemLinkOut] = Field(default_factory=list)


class ItemStatusOut(ApiModel):
    id: UUID
    analysis_status: AnalysisStatus
    analysis_version: int
    error_code: str | None = None


# --------------------------------------------------------------------------- 检索 / 问答


class SearchHitOut(ApiModel):
    chunk_id: UUID
    item_id: UUID
    title: str | None
    text: str
    vec_score: float | None
    kw_score: float | None
    rrf_score: float
    captured_at: datetime


class SearchOut(ApiModel):
    hits: list[SearchHitOut]
    took_ms: int


class AskIn(ApiModel):
    query: str = Field(min_length=1, max_length=2_000)
    session_id: UUID | None = None
    client_turn_id: UUID | None = None


class CitationOut(ApiModel):
    n: int
    chunk_id: UUID | None = None
    item_id: UUID | None = None
    title: str | None = None
    quote: str | None = None
    verified: bool = True


class SessionOut(ApiModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessageOut(ApiModel):
    id: UUID
    role: str
    content: str
    citations: list[CitationOut] = Field(default_factory=list)
    model: str | None = None
    created_at: datetime


# --------------------------------------------------------------------------- 待办


TodoStatus = Literal["open", "done", "dropped"]


class TodoCreateIn(ApiModel):
    text: str = Field(min_length=1, max_length=1_000)
    due_date: date | None = None
    priority: int = Field(default=2, ge=1, le=3)
    source_item_id: UUID | None = None
    client_id: str | None = Field(default=None, max_length=64)


class TodoPatchIn(ApiModel):
    text: str | None = Field(default=None, max_length=1_000)
    status: TodoStatus | None = None
    due_date: date | None = None
    priority: int | None = Field(default=None, ge=1, le=3)


class TodoOut(ApiModel):
    id: UUID
    text: str
    status: str
    due_date: date | None
    priority: int
    source_item_id: UUID | None
    created_at: datetime
    done_at: datetime | None


class TodoGroupOut(ApiModel):
    bucket: str
    label: str
    todos: list[TodoOut]


# --------------------------------------------------------------------------- 总结


DigestKind = Literal["weekly", "monthly", "adhoc", "todo_review"]


class DigestCreateIn(ApiModel):
    kind: DigestKind = "weekly"
    period_start: date | None = None
    period_end: date | None = None


class DigestJobOut(ApiModel):
    job_id: int
    digest_id: UUID | None = None
    estimated_seconds: int = 60


class ActionItemOut(ApiModel):
    text: str
    reason: str
    evidence_item_ids: list[UUID] = Field(default_factory=list)


class DigestOut(ApiModel):
    id: UUID
    kind: str
    period_start: date
    period_end: date
    content_md: str
    stats: dict[str, Any]
    action_items: list[ActionItemOut]
    stale: bool
    model: str | None
    created_at: datetime


class UsageOut(ApiModel):
    day: date
    path: str
    model: str
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    calls: int


class UsageSummaryOut(ApiModel):
    month: str
    total_in: int
    total_out: int
    total_cached: int
    quota: int
    by_path: list[UsageOut]

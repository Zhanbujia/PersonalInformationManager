"""全局配置。

所有可调参数集中在这里，从环境变量（或 .env）读取。
一个原则：**代码里不出现魔法数字**，凡是要调的值都能在部署时改。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 基础 ----------
    app_name: str = "拾穗"
    env: Literal["dev", "prod", "test"] = "dev"
    debug: bool = False
    api_prefix: str = "/api"
    # 前端开发服务器地址，用于 CORS；生产环境前后端同域时留空即可
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ---------- 数据库 ----------
    database_url: str = "postgresql://pim:pim@localhost:5432/pim"
    # 迁移专用连接（表 owner）。不填则复用 database_url，
    # 但那时建出来的表 owner 就是应用角色，需要额外的 FORCE RLS 才安全。
    migration_database_url: str | None = None
    # 后台任务专用连接（pim_worker）。jobs 表对应用角色只开了 insert + id 列，
    # 领取任务、读任务进度都必须用这个角色，否则报 permission denied。
    # 不填则复用 database_url —— 功能能跑，但任务轮询接口会 500，启动时会告警。
    worker_database_url: str | None = None
    db_pool_min: int = 1
    db_pool_max: int = 10
    db_pool_timeout: float = 10.0

    # ---------- 认证 ----------
    jwt_secret: str = "dev-only-change-me-in-production"
    jwt_alg: str = "HS256"
    jwt_ttl_hours: int = 24 * 7
    min_password_length: int = 8

    # ---------- 模型 ----------
    # 任何 OpenAI 兼容网关都能用：填好 base_url + api_key + 模型名即可。
    llm_provider: str = "openai"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_timeout: float = 30.0
    llm_max_retries: int = 1

    fast_model: str = "gpt-4o-mini"
    strong_model: str = "gpt-4o"
    # 结构化抽取用的档位：默认 fast（便宜、延迟低）。若抽取质量不达标可切 strong。
    extract_model_tier: Literal["fast", "strong"] = "fast"

    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1024
    embedding_batch: int = 16

    # ---------- 预算（§6.2 三个终止条件）----------
    max_rounds: int = 5
    turn_tokens: int = 70_000
    max_tool_calls: int = 12
    # LangGraph 计的是超级步：5 轮 ≈ 10~11 步，留 1 步余量
    recursion_limit: int = 12
    tool_failure_threshold: int = 3

    # ---------- 检索 ----------
    retrieval_k: int = 8
    retrieval_pool_multiplier: int = 4  # 每路先取 k*4 再融合
    rrf_k: int = 60
    link_similarity_threshold: float = 0.75
    link_top_n: int = 5
    low_score_threshold: float = 0.35

    # ---------- 上下文预算（§10.1）----------
    context_window: int = 32_000
    ctx_system_tokens: int = 2_000
    ctx_tools_tokens: int = 1_200
    ctx_history_tokens: int = 4_000
    ctx_retrieval_tokens: int = 12_000
    ctx_input_tokens: int = 1_000
    ctx_output_reserve: int = 4_000
    compact_watermark: float = 0.75
    keep_recent_turns: int = 4

    # ---------- prompt 缓存 ----------
    # 只有供应商要求"显式标记"时才打开（如 Anthropic 系）。
    # OpenAI 兼容网关多按前缀自动命中，打开反而可能因为不认识 cache_control 被拒。
    prompt_cache_enabled: bool = False

    # ---------- 入库 ----------
    chunk_size: int = 500
    chunk_overlap: int = 50
    analyze_max_chars: int = 6_000
    analyze_head_chars: int = 4_000
    analyze_tail_chars: int = 2_000

    # ---------- 总结 ----------
    digest_tokens: int = 30_000

    # ---------- worker ----------
    worker_poll_interval: float = 1.0
    worker_concurrency: int = 3
    job_max_attempts: int = 3
    job_lock_timeout_sec: int = 300

    # ---------- 额度（v1 邀请制，防刷）----------
    daily_token_quota: int = 300_000

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def usable_input_tokens(self) -> int:
        """压缩水位的基数：窗口扣掉输出预留与安全余量后，真正可用于输入的额度。

        20200(六段输入) + 4000(输出) + 7800(余量) = 32000
        """
        return (
            self.ctx_system_tokens
            + self.ctx_tools_tokens
            + self.ctx_history_tokens
            + self.ctx_retrieval_tokens
            + self.ctx_input_tokens
        )

    @property
    def compact_trigger_tokens(self) -> int:
        return int(self.usable_input_tokens * self.compact_watermark)

    @property
    def sync_database_url(self) -> str:
        """Alembic / 同步脚本用。psycopg3 的 URL 形式一致，去掉可能的 +async 标记。"""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://")

    @property
    def effective_worker_database_url(self) -> str:
        """后台任务连接。没单独配就退回应用连接。

        注意退回后是被降级的：jobs 表对应用角色只有 insert 和 id 列的权限，
        worker 领不到任务、任务轮询接口会 permission denied。
        所以启动自检会对这种配置告警，而不是静默接受。
        """
        return self.worker_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

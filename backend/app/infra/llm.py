"""模型工厂。

换供应商只改这一个文件（也就是只改环境变量）。凡是 OpenAI 兼容的网关，
填 `LLM_BASE_URL` + `LLM_API_KEY` + 模型名就能跑。

设计上有两个刻意决定：

1. **建两个静态实例，不在运行时换档**。跨档切换会让 prompt 缓存整段作废，
   省下的钱抵不上重算成本；而且换模型是"每次调用都可能发生"的动作，很难保证确定性。
2. **embedding 维度必须与数据库列一致**。启动时用一句探针文本实测一次，
   不一致就直接拒绝启动——否则会在写入时抛一个很难懂的 pgvector 错误。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Settings, settings as default_settings

logger = logging.getLogger(__name__)


def _chat(model: str, temperature: float, s: Settings):
    """建一个聊天模型。base_url 为空时用官方默认。"""
    if s.llm_provider == "mock":
        from app.infra.mock import MockChatModel

        return MockChatModel(model_name=model, temperature=temperature)
    try:
        from langchain.chat_models import init_chat_model

        kwargs: dict[str, Any] = {
            "model": model,
            "model_provider": s.llm_provider,
            "temperature": temperature,
            "timeout": s.llm_timeout,
            "max_retries": s.llm_max_retries,
            "max_tokens": s.llm_max_tokens,
        }
        if s.llm_base_url:
            kwargs["base_url"] = s.llm_base_url
        if s.llm_api_key:
            kwargs["api_key"] = s.llm_api_key
        return init_chat_model(**kwargs)
    except Exception as exc:  # noqa: BLE001 - 旧版本没有 init_chat_model 时兜底
        # 一定要把原因带出来。之前这里只打一句"不可用"，没配密钥时也会走到这儿，
        # 结果真正的配置错误（provider 名字写错、api_key 缺失）全被这行日志掩盖了。
        logger.warning("init_chat_model 不可用，回退到 ChatOpenAI：%s: %s", type(exc).__name__, exc)
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            timeout=s.llm_timeout,
            max_retries=s.llm_max_retries,
            max_tokens=s.llm_max_tokens,
            base_url=s.llm_base_url,
            api_key=s.llm_api_key or "not-needed",
        )


class Models:
    """两个档位的模型实例。整个进程共用，不按请求新建。"""

    def __init__(self, s: Settings | None = None) -> None:
        self.settings = s or default_settings
        self.fast = _chat(self.settings.fast_model, 0.0, self.settings)
        self.strong = _chat(self.settings.strong_model, 0.2, self.settings)

    def for_tier(self, tier: str):
        return self.strong if tier == "strong" else self.fast

    @property
    def extract_llm(self):
        """结构化抽取用的模型：temperature=0，同一段话每次结果一致。"""
        return self.for_tier(self.settings.extract_model_tier)


class Embeddings:
    """向量化客户端。

    注意：这里**不用** langchain 的向量库封装（`PGVectorStore`），只用它算向量。
    向量库封装会自己建表、自己开连接池，把行级安全策略整个绕过去。
    """

    def __init__(self, s: Settings | None = None) -> None:
        self.settings = s or default_settings
        if self.settings.llm_provider == "mock":
            from app.infra.mock import MockEmbeddings

            self._client = MockEmbeddings(self.settings.embedding_dim)
            self._dim_checked = False
            return
        # 没有独立的 embedding 提供方（DeepSeek 等纯对话网关没有 embedding 接口），
        # 不要拿 LLM 的 api_key/base_url 去打 /embeddings（会 4xx，且每条约一条异常日志）。
        # 退化为本地确定性向量：入库/检索链路照常跑，只是向量不具语义，检索以关键词为主。
        if not (self.settings.embedding_api_key or self.settings.embedding_base_url):
            from app.infra.mock import MockEmbeddings

            self._client = MockEmbeddings(self.settings.embedding_dim)
            self._dim_checked = False
            logger.warning(
                "未配置 EMBEDDING_API_KEY/EMBEDDING_BASE_URL，向量化降级为本地确定性向量"
                "（非语义）；检索以关键词为主。如需语义向量请单独配置 embedding 提供方。"
            )
            return
        from langchain_openai import OpenAIEmbeddings

        kwargs: dict[str, Any] = {
            "model": self.settings.embedding_model,
            "api_key": self.settings.embedding_api_key or self.settings.llm_api_key or "not-needed",
            "chunk_size": self.settings.embedding_batch,
            "max_retries": self.settings.llm_max_retries,
        }
        base = self.settings.embedding_base_url or self.settings.llm_base_url
        if base:
            kwargs["base_url"] = base
        # 只有显式给了维度才传：部分网关不认 dimensions 参数，传了会 400
        if self.settings.embedding_dim:
            kwargs["dimensions"] = self.settings.embedding_dim
        self._client = OpenAIEmbeddings(**kwargs)
        self._dim_checked = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await self._client.aembed_documents(texts)
        if not self._dim_checked and vectors:
            self._dim_checked = True
            got = len(vectors[0])
            if got != self.settings.embedding_dim:
                raise RuntimeError(
                    f"embedding 维度不一致：模型返回 {got} 维，数据库列是 "
                    f"{self.settings.embedding_dim} 维。请改 EMBEDDING_DIM 并重建向量列。"
                )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]

    async def probe_dim(self) -> int:
        """启动自检用：真发一次请求，确认维度与配置一致。"""
        vec = await self.embed_one("维度探针")
        return len(vec)


async def with_retry(fn, *, attempts: int = 3, base_delay: float = 0.6):
    """轻量重试：只用于可恢复的瞬时错误（网络、限流）。

    业务错误（schema 不匹配）不要用这个，那类要回喂错误信息重试，属于另一套逻辑。
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i == attempts - 1:
                break
            await asyncio.sleep(base_delay * (2**i))
    assert last is not None
    raise last

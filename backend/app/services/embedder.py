"""
T019: Embedder 抽象 + 本地 BGE 实现

对标:
  - RAG-001 (openspec spec): BGE-large-zh-v1.5 (1024 维) embedding
  - 决策: 本地 sentence-transformers (离线、确定、不依赖讯飞非标准协议)

设计要点:
  - Embedder Protocol: embed(texts) -> list[vector], 实现可切换 (local/api)
  - LocalEmbedder: 启动时加载本地 BGE 单例 (对标 get_llm_client 模式)
  - 惰性加载: 构造不加载, 首次 embed 才加载 (失败降级由调用方决定)
  - dim 与 vector_store 对齐 (config.embedding_dim)

为什么本地不调 API:
  - 讯飞 xopkimik26 走非标准协议 (/v2/embeddings 返回 400)
  - 本地 BGE 维度确定 (1024), 离线可用, 中文检索效果好
  - sentence-transformers 是 HuggingFace 标准库, 跨平台稳定
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# sentence-transformers 是重依赖, 惰性 import (api 模式时根本不加载)
try:
    from sentence_transformers import SentenceTransformer
    _ST_AVAILABLE = True
except ImportError:
    SentenceTransformer = None  # type: ignore
    _ST_AVAILABLE = False


# ── 抽象接口 ──────────────────────────────────────────────────

@runtime_checkable
class Embedder(Protocol):
    """Embedder 抽象。实现可切换 (local/api), 调用方不绑死。"""

    @property
    def dim(self) -> int:
        """输出向量维度。"""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → 向量列表 (顺序与输入一致)。

        Args:
            texts: 文本列表 (单条用于查询, 批量用于索引构建)

        Returns:
            list[list[float]], 每个内层 list 长度 == dim
        """
        ...


# ── 本地 BGE 实现 ─────────────────────────────────────────────

class LocalEmbedder:
    """本地 sentence-transformers BGE embedding。

    惰性加载: 构造不加载模型 (省内存, 失败不阻塞构造),
    首次 embed 时加载, 之后单例缓存。
    """

    def __init__(self, model_path: str, dim: int):
        self._model_path = model_path
        self._dim = dim
        self._model = None  # 惰性加载
        # 向量缓存: 同一文本不重复 encode (查询问题/fewshot 检索会反复 embed 相同问题)
        # 用 dict 缓存 (文本→向量), LRU 淘汰防内存膨胀
        self._cache: dict[str, list[float]] = {}
        self._cache_max = 2048

    @property
    def dim(self) -> int:
        return self._dim

    def _ensure_loaded(self) -> None:
        """惰性加载模型 (首次 embed 时触发)。"""
        if self._model is not None:
            return

        if not _ST_AVAILABLE:
            raise RuntimeError(
                "embedding model unavailable: sentence-transformers not installed. "
                "Run: uv add sentence-transformers"
            )

        if not self._model_path:
            raise RuntimeError("embedding model path not configured")

        try:
            logger.info("Loading embedding model: %s", self._model_path)
            self._model = SentenceTransformer(self._model_path)
            logger.info("Embedding model loaded (dim=%d)", self._dim)
        except Exception as e:
            raise RuntimeError(
                f"failed to load embedding model from {self._model_path}: {e}. "
                f"Run: uv run python backend/scripts/download_embedding_model.py"
            ) from e

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → 向量。空列表直接返回空 (不触发加载)。

        带缓存: 相同文本复用已编码向量 (查询问题/fewshot 检索反复 embed 同一问题时不重算)。
        """
        if not texts:
            return []

        self._ensure_loaded()

        # 分离已缓存 / 未缓存, 只 encode 未缓存的
        results: list[list[float] | None] = [None] * len(texts)
        to_encode: list[str] = []
        to_encode_idx: list[int] = []
        for i, t in enumerate(texts):
            cached = self._cache.get(t)
            if cached is not None:
                results[i] = cached
            else:
                to_encode.append(t)
                to_encode_idx.append(i)

        if to_encode:
            import asyncio
            import numpy as np

            def _encode() -> list[list[float]]:
                # BGE 模型推荐 normalize=True (对标检索余弦相似度)
                embeddings = self._model.encode(to_encode, normalize_embeddings=True)
                if isinstance(embeddings, np.ndarray):
                    return embeddings.tolist()
                return [e.tolist() for e in embeddings]

            encoded = await asyncio.to_thread(_encode)
            # 回填结果 + 写缓存
            for idx, text, vec in zip(to_encode_idx, to_encode, encoded):
                results[idx] = vec
                # LRU 淘汰: 缓存满则清最早一半 (不依赖 OrderedDict, 简单可靠)
                if len(self._cache) >= self._cache_max:
                    drop_count = self._cache_max // 2
                    for k in list(self._cache.keys())[:drop_count]:
                        del self._cache[k]
                self._cache[text] = vec

        return results  # type: ignore[return-value]


# ── 模块级单例 (对标 get_llm_client / get_milvus_client) ──────

_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """获取全局 Embedder 单例 (按 config.embedding_backend 选实现)。

    local: 本地 BGE (默认)
    api:   外部 OpenAI 兼容 (备用, 实现待补)
    """
    global _embedder
    if _embedder is not None:
        return _embedder

    settings = get_settings()
    if settings.embedding_backend == "local":
        _embedder = LocalEmbedder(
            model_path=settings.embedding_model_path,
            dim=settings.embedding_dim,
        )
    else:
        # api 模式预留 (将来补 ApiEmbedder)
        raise RuntimeError(
            f"embedding_backend='{settings.embedding_backend}' not yet implemented; "
            f"use 'local'"
        )
    return _embedder


def reset_embedder() -> None:
    """重置单例 (测试用)。"""
    global _embedder
    _embedder = None


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """便捷函数: 用全局单例 embed 文本。"""
    return await get_embedder().embed(texts)

"""
T019: Embedder 接口 + LocalEmbedder — 单元测试

对标:
  - RAG-001 (openspec spec): BGE-large-zh-v1.5 (1024 维) embedding
  - 设计: Embedder Protocol, 实现可切换 (local/api), 调用方不绑死

设计要点:
  - Embedder Protocol: embed(texts) -> list[vector]
  - LocalEmbedder: sentence-transformers 加载本地 BGE, 启动时加载单例
  - dim 由 config 决定 (1024), 与 vector_store dim 对齐
  - 单条/批量都支持 (对标 RAG-002 用户问题 embed + RAG-001 批量索引)
  - 模型加载失败降级 (对标 Milvus/Redis fail-closed)

测试策略:
  - 单元测试用 mock (不依赖真实模型, 快)
  - 真实模型集成测试单独标记 (慢, 需要模型已下载)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.embedder import (
    Embedder,
    LocalEmbedder,
    embed_texts,
)


# ── Embedder Protocol 兼容 ───────────────────────────────────

class TestProtocol:
    def test_local_embedder_is_embedder(self):
        """LocalEmbedder 必须满足 Embedder Protocol。"""
        emb: Embedder = LocalEmbedder(model_path="/fake", dim=1024)
        assert hasattr(emb, "embed")
        assert hasattr(emb, "dim")


# ── LocalEmbedder: 单例 + 加载 ────────────────────────────────

class TestLocalEmbedderLoading:
    """模型加载: 惰性加载单例 + 加载失败降级。"""

    def test_lazy_load(self):
        """模型在首次 embed 时才加载（构造时不加载）。"""
        with patch("app.services.embedder.SentenceTransformer") as mock_st:
            emb = LocalEmbedder(model_path="/fake", dim=1024)
            # 构造后模型还没加载
            mock_st.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_on_first_embed(self):
        """首次 embed 触发模型加载。"""
        fake_model = MagicMock()
        fake_model.encode.return_value = MagicMock()
        # sentence-transformers encode 返回 numpy array, 模拟 .tolist()
        import numpy as np
        fake_model.encode.return_value = np.array([[0.1] * 1024])

        with patch("app.services.embedder.SentenceTransformer", return_value=fake_model) as mock_st:
            emb = LocalEmbedder(model_path="/fake", dim=1024)
            await emb.embed(["测试"])
            mock_st.assert_called_once_with("/fake")

    @pytest.mark.asyncio
    async def test_load_failure_raises(self):
        """模型加载失败 → 抛 RuntimeError（调用方决定降级）。"""
        with patch("app.services.embedder.SentenceTransformer", side_effect=Exception("load fail")):
            emb = LocalEmbedder(model_path="/fake", dim=1024)
            with pytest.raises(RuntimeError, match="embedding"):
                await emb.embed(["测试"])

    @pytest.mark.asyncio
    async def test_load_once_cached(self):
        """模型只加载一次（单例缓存，多次 embed 不重复加载）。"""
        fake_model = MagicMock()
        import numpy as np
        fake_model.encode.return_value = np.array([[0.1] * 1024])

        with patch("app.services.embedder.SentenceTransformer", return_value=fake_model) as mock_st:
            emb = LocalEmbedder(model_path="/fake", dim=1024)
            await emb.embed(["a"])
            await emb.embed(["b"])
            await emb.embed(["c"])
            assert mock_st.call_count == 1  # 只加载一次


# ── LocalEmbedder: embed 行为 ─────────────────────────────────

class TestEmbedBehavior:
    """embed 文本 → 向量。"""

    @pytest.mark.asyncio
    async def test_embed_returns_correct_dim(self):
        """embed 输出维度 = config dim。"""
        fake_model = MagicMock()
        import numpy as np
        fake_model.encode.return_value = np.array([[0.1] * 1024])

        with patch("app.services.embedder.SentenceTransformer", return_value=fake_model):
            emb = LocalEmbedder(model_path="/fake", dim=1024)
            vecs = await emb.embed(["测试"])
            assert len(vecs) == 1
            assert len(vecs[0]) == 1024

    @pytest.mark.asyncio
    async def test_embed_batch_preserves_order(self):
        """批量 embed 返回顺序与输入一致。"""
        fake_model = MagicMock()
        import numpy as np
        fake_model.encode.return_value = np.array([
            [0.1] * 1024,
            [0.2] * 1024,
            [0.3] * 1024,
        ])

        with patch("app.services.embedder.SentenceTransformer", return_value=fake_model):
            emb = LocalEmbedder(model_path="/fake", dim=1024)
            vecs = await emb.embed(["a", "b", "c"])
            assert len(vecs) == 3
            assert vecs[0][0] == pytest.approx(0.1)
            assert vecs[2][0] == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_embed_single_text(self):
        """单条文本返回单元素列表。"""
        fake_model = MagicMock()
        import numpy as np
        fake_model.encode.return_value = np.array([[0.5] * 1024])

        with patch("app.services.embedder.SentenceTransformer", return_value=fake_model):
            emb = LocalEmbedder(model_path="/fake", dim=1024)
            vecs = await emb.embed(["一句话"])
            assert len(vecs) == 1

    @pytest.mark.asyncio
    async def test_embed_empty_list(self):
        """空列表 → 空列表（不调模型）。"""
        fake_model = MagicMock()
        with patch("app.services.embedder.SentenceTransformer", return_value=fake_model):
            emb = LocalEmbedder(model_path="/fake", dim=1024)
            vecs = await emb.embed([])
            assert vecs == []
            fake_model.encode.assert_not_called()

    @pytest.mark.asyncio
    async def test_dim_property(self):
        """dim 属性返回配置维度。"""
        emb = LocalEmbedder(model_path="/fake", dim=768)
        assert emb.dim == 768


# ── 模块级单例 embed_texts ────────────────────────────────────

class TestEmbedTextsSingleton:
    """模块级 get_embedder/embed_texts 单例便捷函数。"""

    @pytest.mark.asyncio
    async def test_embed_texts_uses_singleton(self):
        """embed_texts 内部用全局单例（对标 get_llm_client 模式）。"""
        fake_model = MagicMock()
        import numpy as np
        fake_model.encode.return_value = np.array([[0.1] * 1024])

        with patch("app.services.embedder.SentenceTransformer", return_value=fake_model), \
             patch("app.services.embedder.get_settings") as mock_settings:
            mock_settings.return_value.embedding_model_path = "/fake"
            mock_settings.return_value.embedding_dim = 1024
            mock_settings.return_value.embedding_backend = "local"

            # 重置单例
            import app.services.embedder as emb_mod
            emb_mod._embedder = None

            vecs = await embed_texts(["测试"])
            assert len(vecs) == 1
            assert len(vecs[0]) == 1024

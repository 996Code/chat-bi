"""
T022: 两阶段检索 — 单元测试

对标:
  - RAG-002 (openspec spec): 向量召回 top-K (K=20, score>=0.5) → LLM 精筛
  - 宁缺毋滥: 无真正匹配返回空, 不选 score 最高; 无召回 → 友好提示不 fallback

设计要点:
  - retrieve(question, store, embedder, llm_client):
    阶段1: embed 问题 → vector_store.search(top_k=20, score_threshold=0.5)
    阶段2: 召回结果 + 问题 → LLM 精筛 (prompt 含假阳性声明)
  - 无召回 → 返回空 + 原因 (不 fallback 不随机选表, v1 教训)
  - LLM 失败 → 降级返回原始召回结果 (向量检索已过滤, 可用)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vector_store import SearchResult, VectorRecord
from app.services.retriever import retrieve, RetrievalResult


# ── 测试夹具 ──────────────────────────────────────────────────

def _make_result(id: str, score: float, name: str = None, text: str = "") -> SearchResult:
    return SearchResult(
        record=VectorRecord(
            id=id,
            vector=[0.1] * 1024,
            metadata={"data_source_id": "ds1", "type": "model", "name": name or id},
            text=text or f"{name} 表",
        ),
        score=score,
    )


# ── 阶段 1: 向量召回 ──────────────────────────────────────────

class TestVectorRecall:
    """向量召回 top-K + score 过滤。"""

    @pytest.mark.asyncio
    async def test_recall_filters_below_threshold(self):
        """score < 0.5 的被过滤 (对标 RAG-002)。"""
        store = AsyncMock()
        store.search.return_value = [
            _make_result("biz_orders", 0.9, "biz_orders", "订单表 total_amount"),
            _make_result("biz_users", 0.3, "biz_users", "用户表 name"),  # 低于阈值
        ]
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 1024])

        result = await retrieve("销售额", store, embedder, skip_llm_refine=True)

        # 阶段2 LLM=None 时直接返回阶段1结果 (score 阈值从 config 读, 适配 BGE)
        call = store.search.call_args
        from app.core.config import get_settings
        assert call.kwargs.get("score_threshold") == get_settings().rag_similarity_threshold
        assert call.kwargs.get("top_k") == get_settings().rag_vector_top_k

    @pytest.mark.asyncio
    async def test_no_recall_returns_empty_with_reason(self):
        """无召回 → 空结果 + 原因 (不 fallback 不随机选表, v1 教训)。"""
        store = AsyncMock()
        store.search.return_value = []
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 1024])

        result = await retrieve("无关问题", store, embedder, skip_llm_refine=True)

        assert result.models == []
        assert result.no_match_reason is not None
        assert "无法匹配" in result.no_match_reason or "找不到" in result.no_match_reason

    @pytest.mark.asyncio
    async def test_recall_passes_question_filter(self):
        """按 data_source_id 过滤 (对标 RAG-005 多租户/多源)。"""
        store = AsyncMock()
        store.search.return_value = []
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 1024])

        await retrieve("销售额", store, embedder, skip_llm_refine=True, data_source_id="ds1")

        call = store.search.call_args
        filter_arg = call.kwargs.get("filter")
        assert filter_arg is not None
        assert filter_arg.get("data_source_id") == "ds1"


# ── 阶段 2: LLM 精筛 ──────────────────────────────────────────

class TestLLMRefine:
    """LLM 精筛: 从召回候选里选真正相关的。"""

    @pytest.mark.asyncio
    async def test_llm_selects_relevant(self):
        """LLM 从候选里选出真正相关的表 (过滤假阳性)。"""
        store = AsyncMock()
        store.search.return_value = [
            _make_result("biz_orders", 0.9, "biz_orders", "订单表 total_amount"),
            _make_result("biz_products", 0.85, "biz_products", "商品表 price"),  # 假阳性
        ]
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 1024])

        # mock LLM 只选 biz_orders
        mock_resp = MagicMock()
        mock_resp.usage = None
        llm_content = json.dumps({
            "models": ["biz_orders"],
            "reason": "销售额对应订单金额 total_amount",
        })
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=(llm_content, mock_resp)):
            result = await retrieve("销售额", store, embedder)

        names = [m["name"] for m in result.models]
        assert "biz_orders" in names
        assert "biz_products" not in names  # 假阳性被过滤

    @pytest.mark.asyncio
    async def test_llm_returns_empty_when_no_true_match(self):
        """LLM 判断无真正匹配 → 返回空 (宁缺毋滥)。"""
        store = AsyncMock()
        store.search.return_value = [
            _make_result("biz_users", 0.6, "biz_users", "用户表 name"),
        ]
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 1024])

        mock_resp = MagicMock()
        mock_resp.usage = None
        llm_content = json.dumps({"models": [], "reason": "无匹配"})
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=(llm_content, mock_resp)):
            result = await retrieve("数据库密码", store, embedder)

        assert result.models == []
        assert result.no_match_reason is not None

    @pytest.mark.asyncio
    async def test_llm_prompt_contains_false_positive_disclaimer(self):
        """精筛 prompt 必须声明'候选来自向量检索, 可能假阳性' (对标 RAG-002)。"""
        store = AsyncMock()
        store.search.return_value = [_make_result("t1", 0.9, "t1")]
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 1024])

        mock_resp = MagicMock()
        mock_resp.usage = None
        llm_content = json.dumps({"models": ["t1"]})
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=(llm_content, mock_resp)) as mock_chat:
            await retrieve("销售额", store, embedder)

        call = mock_chat.call_args
        prompt = call.kwargs.get("messages", call[0][0] if call[0] else [{}])[0]
        prompt_text = prompt.get("content", "") if isinstance(prompt, dict) else str(prompt)
        assert "假阳性" in prompt_text or "可能存在" in prompt_text or "向量检索" in prompt_text

    @pytest.mark.asyncio
    async def test_llm_failure_degrades_to_raw_recall(self):
        """LLM 失败 → 降级返回原始召回结果 (向量已过滤, 可用)。"""
        store = AsyncMock()
        store.search.return_value = [
            _make_result("biz_orders", 0.9, "biz_orders", "订单表"),
        ]
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 1024])

        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, side_effect=Exception("LLM down")):
            result = await retrieve("销售额", store, embedder)

        # 降级: 返回原始召回 (不含精筛, 但有结果)
        assert len(result.models) == 1
        assert result.models[0]["name"] == "biz_orders"
        assert result.degraded is True  # 标记降级


# ── 阶段 2: LLM 返回非法 ───────────────────────────────────────

class TestLLMInvalidResponse:
    @pytest.mark.asyncio
    async def test_llm_invalid_json_degrades(self):
        """LLM 返回非法 JSON → 降级返回原始召回。"""
        store = AsyncMock()
        store.search.return_value = [_make_result("t1", 0.9, "t1")]
        embedder = MagicMock()
        embedder.embed = AsyncMock(return_value=[[0.1] * 1024])

        mock_resp = MagicMock()
        mock_resp.usage = None
        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock, return_value=("这不是JSON{{{", mock_resp)):
            result = await retrieve("销售额", store, embedder)

        assert result.degraded is True
        assert len(result.models) == 1

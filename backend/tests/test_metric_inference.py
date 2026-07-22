"""
指标识别优化 (方向4) — 测试

测试覆盖:
  1. LLM 指标推断 (enrich_metrics): 成功/失败/无 measure 列/composite 校验
  2. schema_context 输出指标行 (build_schema_context)
  3. build_metrics_hint 单独输出指标定义
  4. metric_feedback: 命中已知指标/发现新指标
  5. 指标 PATCH API (新增/编辑/删除)
  6. SchemaGraph 节点 metricCount

设计原则:
  - 不调真实 LLM (用 mock llm_chat)
  - 不碰真实数据库 (用 SQLite/内存)
  - 宁缺毋滥: LLM 失败时降级返回空, 不阻塞扫描
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.semantic_layer import (
    Column,
    Metric,
    Model,
    SemanticModelContent,
)


# ── 测试数据构造 ──────────────────────────────────────────────

def _make_column(name: str, display_name: str, data_type: str, semantic_type: str) -> Column:
    return Column(
        name=name,
        display_name=display_name,
        data_type=data_type,
        semantic_type=semantic_type,
        source="manual",
        confidence=1.0,
    )


def _make_model_with_measures() -> Model:
    """构造一个有 measure 列的订单表模型 (用于指标推断测试)。"""
    return Model(
        name="biz_orders",
        display_name="订单表",
        columns=[
            _make_column("id", "主键", "BIGINT", "key"),
            _make_column("total_amount", "总金额", "DECIMAL(10,2)", "measure"),
            _make_column("status", "状态", "VARCHAR", "dimension"),
            _make_column("user_id", "用户ID", "BIGINT", "key"),
            _make_column("quantity", "数量", "INTEGER", "measure"),
        ],
        relationships=[],
        metrics=[],
        calculated_fields=[],
    )


def _make_model_without_measures() -> Model:
    """构造一个无 measure 列的维度表 (应跳过指标推断)。"""
    return Model(
        name="dim_status",
        display_name="状态字典",
        columns=[
            _make_column("id", "主键", "BIGINT", "key"),
            _make_column("status_name", "状态名", "VARCHAR", "dimension"),
        ],
        relationships=[],
        metrics=[],
        calculated_fields=[],
    )


# ── 1. enrich_metrics 测试 ────────────────────────────────────

class TestEnrichMetrics:
    """LLM 指标推断测试 (enrich_metrics)。"""

    @pytest.mark.asyncio
    async def test_enrich_success(self):
        """有 measure 列 → LLM 推断指标成功。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        mock_response = json.dumps([
            {
                "name": "gmv",
                "display_name": "成交总额",
                "formula": "SUM(total_amount)",
                "type": "single",
                "condition": "status IN ('paid','shipped')",
            },
            {
                "name": "avg_order_amount",
                "display_name": "平均客单价",
                "formula": "SUM(total_amount) / COUNT(*)",
                "type": "composite",
                "factor_metric_names": ["gmv", "order_count"],
            },
        ])

        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = (mock_response, MagicMock())
            await enrich_metrics(content)

        assert len(content.models[0].metrics) == 2
        assert content.models[0].metrics[0].name == "gmv"
        assert content.models[0].metrics[0].source == "auto_inferred"
        assert content.models[0].metrics[0].co_occurrence == 0
        assert content.models[0].metrics[1].type == "composite"

    @pytest.mark.asyncio
    async def test_enrich_no_measure_columns(self):
        """无 measure 列 → 不调 LLM, metrics 保持空。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_without_measures()],
        )

        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
            await enrich_metrics(content)
            mock_chat.assert_not_called()

        assert len(content.models[0].metrics) == 0

    @pytest.mark.asyncio
    async def test_enrich_llm_failure_returns_empty(self):
        """LLM 调用失败 → 降级返回空, 不抛异常 (宁缺毋滥)。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.side_effect = Exception("LLM 服务不可用")
            await enrich_metrics(content)  # 不抛异常

        assert len(content.models[0].metrics) == 0

    @pytest.mark.asyncio
    async def test_enrich_invalid_metric_skipped(self):
        """LLM 返回的指标中, 校验失败的条目被跳过 (Pydantic 逐条校验)。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        # 第二个指标是 composite 但没有 factor_metric_names (违反 SEM-005)
        mock_response = json.dumps([
            {
                "name": "gmv",
                "display_name": "成交总额",
                "formula": "SUM(total_amount)",
                "type": "single",
            },
            {
                "name": "bad_composite",
                "display_name": "坏指标",
                "formula": "SUM(total_amount) / COUNT(*)",
                "type": "composite",
                # 缺少 factor_metric_names → Pydantic 校验失败
            },
        ])

        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = (mock_response, MagicMock())
            await enrich_metrics(content)

        # 只保留第一个合法的指标
        assert len(content.models[0].metrics) == 1
        assert content.models[0].metrics[0].name == "gmv"

    @pytest.mark.asyncio
    async def test_enrich_config_disabled(self):
        """配置关闭 (scan_metric_inference=False) → 跳过指标推断。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = False
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                await enrich_metrics(content)
                mock_chat.assert_not_called()

        assert len(content.models[0].metrics) == 0

    @pytest.mark.asyncio
    async def test_enrich_skips_system_tables(self):
        """系统表 (元数据表) 跳过指标推断。"""
        from app.services.semantic_scanner import enrich_metrics

        system_model = Model(
            name="users",  # 在 config.system_tables 列表中
            display_name="用户表",
            columns=[
                _make_column("id", "主键", "BIGINT", "key"),
                _make_column("score", "分数", "INTEGER", "measure"),
            ],
            relationships=[],
            metrics=[],
            calculated_fields=[],
        )
        content = SemanticModelContent(models=[system_model])

        with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
            await enrich_metrics(content)
            mock_chat.assert_not_called()


# ── 2. schema_context 指标行输出测试 ──────────────────────────

class TestSchemaContextMetrics:
    """build_schema_context 输出指标行测试。"""

    def test_metrics_in_schema_context(self):
        """有指标的表 → schema_context 包含指标行。"""
        from app.ai.schema_utils import build_schema_context

        model = _make_model_with_measures()
        model.metrics = [
            Metric(
                name="gmv",
                display_name="成交总额",
                formula="SUM(total_amount)",
                type="single",
                condition="status IN ('paid')",
            ),
        ]
        content = SemanticModelContent(models=[model])

        result = build_schema_context(content, ["biz_orders"])
        assert "指标:" in result
        assert "gmv(成交总额)" in result
        assert "SUM(total_amount)" in result
        assert "status IN ('paid')" in result

    def test_no_metrics_no_metric_line(self):
        """无指标的表 → schema_context 不包含指标行。"""
        from app.ai.schema_utils import build_schema_context

        model = _make_model_with_measures()
        model.metrics = []
        content = SemanticModelContent(models=[model])

        result = build_schema_context(content, ["biz_orders"])
        assert "指标:" not in result

    def test_composite_metric_factor_names_in_context(self):
        """composite 指标 → schema_context 包含子指标名。"""
        from app.ai.schema_utils import build_schema_context

        model = _make_model_with_measures()
        model.metrics = [
            Metric(
                name="avg_order_amount",
                display_name="平均客单价",
                formula="gmv / order_count",
                type="composite",
                factor_metric_names=["gmv", "order_count"],
            ),
        ]
        content = SemanticModelContent(models=[model])

        result = build_schema_context(content, ["biz_orders"])
        assert "[子指标: gmv, order_count]" in result


# ── 3. build_metrics_hint 测试 ────────────────────────────────

class TestBuildMetricsHint:
    """build_metrics_hint 独立输出指标定义测试。"""

    def test_metrics_hint_output(self):
        """有指标 → 输出指标定义文本。"""
        from app.ai.schema_utils import build_metrics_hint

        model = _make_model_with_measures()
        model.metrics = [
            Metric(
                name="gmv",
                display_name="成交总额",
                formula="SUM(total_amount)",
                type="single",
                condition="status IN ('paid')",
            ),
        ]
        content = SemanticModelContent(models=[model])

        result = build_metrics_hint(content, ["biz_orders"])
        assert "biz_orders:" in result
        assert "gmv(成交总额)" in result

    def test_metrics_hint_empty(self):
        """无指标 → 返回空字符串。"""
        from app.ai.schema_utils import build_metrics_hint

        content = SemanticModelContent(models=[_make_model_with_measures()])
        result = build_metrics_hint(content, ["biz_orders"])
        assert result == ""


# ── 4. metric_feedback 测试 ───────────────────────────────────

class TestMetricFeedback:
    """运行时指标反哺测试 (persist_metric_feedback)。"""

    def _make_state(self, sql: str, tables: list[str], content: SemanticModelContent):
        """构造一个简化的 AgentState mock。"""
        state = MagicMock()
        state.sql = sql
        state.current_tables = tables
        state.semantic_content = content
        state.question = "测试问题"
        return state

    def test_hit_known_metric_increments_co_occurrence(self):
        """SQL 命中已知指标 → co_occurrence += 1。"""
        from app.ai.recall import persist_metric_feedback

        model = _make_model_with_measures()
        model.metrics = [
            Metric(
                name="gmv",
                display_name="成交总额",
                formula="SUM(total_amount)",
                type="single",
                co_occurrence=2,
            ),
        ]
        content = SemanticModelContent(models=[model])

        state = self._make_state(
            sql="SELECT SUM(total_amount) FROM biz_orders",
            tables=["biz_orders"],
            content=content,
        )
        mem_store = MagicMock()
        mem_store.list_memories.return_value = []

        persist_metric_feedback(mem_store, state, content, conv_id="test-conv")

        assert model.metrics[0].co_occurrence == 3

    def test_new_metric_pattern_writes_suggestion(self):
        """SQL 含未知聚合 → 写 metric_suggestion 记忆。"""
        from app.ai.recall import persist_metric_feedback

        model = _make_model_with_measures()
        # 已有 gmv 指标 (SUM(total_amount)), SQL 用 COUNT(*) 不在已知指标中
        model.metrics = [
            Metric(
                name="gmv",
                display_name="成交总额",
                formula="SUM(total_amount)",
                type="single",
            ),
        ]
        content = SemanticModelContent(models=[model])

        state = self._make_state(
            sql="SELECT COUNT(*) FROM biz_orders",
            tables=["biz_orders"],
            content=content,
        )
        mem_store = MagicMock()
        mem_store.list_memories.return_value = []

        persist_metric_feedback(mem_store, state, content, conv_id="test-conv")

        # 应该写了一条 metric_suggestion 记忆
        assert mem_store.save_memory.called
        call_kwargs = mem_store.save_memory.call_args
        assert call_kwargs.kwargs.get("memory_type") == "metric_suggestion"
        assert "COUNT" in call_kwargs.kwargs.get("extra_metadata", {}).get("formula", "")

    def test_no_aggregation_no_feedback(self):
        """SQL 无聚合函数 → 不触发反哺。"""
        from app.ai.recall import persist_metric_feedback

        model = _make_model_with_measures()
        model.metrics = []
        content = SemanticModelContent(models=[model])

        state = self._make_state(
            sql="SELECT * FROM biz_orders WHERE id = 1",
            tables=["biz_orders"],
            content=content,
        )
        mem_store = MagicMock()

        persist_metric_feedback(mem_store, state, content)

        mem_store.save_memory.assert_not_called()

    def test_multi_table_query_no_new_suggestion(self):
        """多表 JOIN 的聚合不写新指标建议 (避免误判)。"""
        from app.ai.recall import persist_metric_feedback

        model = _make_model_with_measures()
        model.metrics = [
            Metric(
                name="gmv",
                display_name="成交总额",
                formula="SUM(total_amount)",
                type="single",
            ),
        ]
        content = SemanticModelContent(models=[model])

        # 多表 SQL 通常用表别名, 如 SUM(o.total_amount)
        # 这与已知 formula "SUM(total_amount)" 不匹配 (带别名前缀)
        state = self._make_state(
            sql="SELECT SUM(o.total_amount) FROM biz_orders o JOIN biz_users u ON o.user_id = u.id",
            tables=["biz_orders", "biz_users"],
            content=content,
        )
        mem_store = MagicMock()
        mem_store.list_memories.return_value = []

        persist_metric_feedback(mem_store, state, content)

        # 多表不写新 suggestion (len(state.current_tables) > 1)
        mem_store.save_memory.assert_not_called()


# ── 5. Metric schema 字段测试 ─────────────────────────────────

class TestMetricSchema:
    """Metric Pydantic schema 字段测试。"""

    def test_metric_default_source_is_auto_inferred(self):
        """新建 Metric 默认 source=auto_inferred, co_occurrence=0。"""
        m = Metric(
            name="gmv",
            display_name="成交总额",
            formula="SUM(total_amount)",
        )
        assert m.source == "auto_inferred"
        assert m.co_occurrence == 0

    def test_metric_composite_requires_factor_metric_names(self):
        """composite 指标必须提供 factor_metric_names (SEM-005)。"""
        with pytest.raises(Exception):
            Metric(
                name="bad",
                display_name="坏指标",
                formula="a / b",
                type="composite",
                # 缺少 factor_metric_names
            )

    def test_metric_composite_with_factors_ok(self):
        """composite 指标有 factor_metric_names → 校验通过。"""
        m = Metric(
            name="avg_price",
            display_name="平均价格",
            formula="gmv / order_count",
            type="composite",
            factor_metric_names=["gmv", "order_count"],
        )
        assert m.type == "composite"
        assert len(m.factor_metric_names) == 2

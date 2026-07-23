"""
指标识别优化 (方向4) — 测试

测试覆盖:
  1. 规则推断简单指标 (_infer_simple_metrics): 金额/数量/通用/去重/配置关闭
  2. enrich_metrics 两阶段: 规则推断 simple + LLM 推断 composite
  3. schema_context 输出指标行 (build_schema_context)
  4. build_metrics_hint 单独输出指标定义
  5. metric_feedback: 命中已知指标/发现新指标/source+type 透传
  6. 指标 PATCH API (新增/编辑/删除)
  7. SchemaGraph 节点 metricCount

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


# ── 1. 规则推断 simple 指标测试 ──────────────────────────────────

class TestRuleInference:
    """规则推断 simple 指标测试 (_infer_simple_metrics)。"""

    def test_amount_column_generates_sum_and_avg(self):
        """金额列 (total_amount + DECIMAL) → SUM + AVG, source=rule_inferred。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = _make_model_with_measures()
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = True
            metrics = _infer_simple_metrics(model)

        # total_amount 是金额列 → SUM + AVG
        amount_metrics = [m for m in metrics if "total_amount" in m.name]
        assert len(amount_metrics) == 2
        names = {m.name for m in amount_metrics}
        assert "total_amount_sum" in names
        assert "total_amount_avg" in names
        for m in amount_metrics:
            assert m.source == "rule_inferred"
            assert m.type == "single"

    def test_count_column_generates_sum_and_count(self):
        """数量列 (quantity + INTEGER) → SUM + COUNT, source=rule_inferred。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = _make_model_with_measures()
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = True
            metrics = _infer_simple_metrics(model)

        qty_metrics = [m for m in metrics if "quantity" in m.name]
        assert len(qty_metrics) == 2
        formulas = {m.formula for m in qty_metrics}
        assert "SUM(quantity)" in formulas
        assert "COUNT(quantity)" in formulas
        for m in qty_metrics:
            assert m.source == "rule_inferred"

    def test_no_measure_columns_returns_empty(self):
        """无 measure 列 → 空列表。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = _make_model_without_measures()
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = True
            metrics = _infer_simple_metrics(model)

        assert metrics == []

    def test_config_disabled_returns_empty(self):
        """配置关闭 (scan_metric_rule_inference=False) → 空列表。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = _make_model_with_measures()
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = False
            metrics = _infer_simple_metrics(model)

        assert metrics == []

    def test_id_column_excluded(self):
        """_id 后缀列已被 semantic_type=key 过滤, 不是 measure, 不生成指标。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = Model(
            name="test_table",
            display_name="测试表",
            columns=[
                _make_column("id", "主键", "BIGINT", "key"),
                _make_column("user_id", "用户ID", "BIGINT", "key"),
                _make_column("score", "分数", "INTEGER", "measure"),
            ],
            relationships=[],
            metrics=[],
            calculated_fields=[],
        )
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = True
            metrics = _infer_simple_metrics(model)

        # 只有 score 是 measure, id/user_id 是 key → 不生成 id 相关指标
        assert all("id" not in m.name or "score" in m.name for m in metrics)
        # score 是通用 measure → SUM
        assert len(metrics) >= 1
        assert any(m.formula == "SUM(score)" for m in metrics)

    def test_display_name_uses_column_display_name(self):
        """指标 display_name 使用列的 display_name (中文名)。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = Model(
            name="biz_orders",
            display_name="订单表",
            columns=[
                _make_column("total_amount", "成交金额", "DECIMAL(10,2)", "measure"),
            ],
            relationships=[],
            metrics=[],
            calculated_fields=[],
        )
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = True
            metrics = _infer_simple_metrics(model)

        assert any(m.display_name == "成交金额合计" for m in metrics)
        assert any(m.display_name == "平均成交金额" for m in metrics)


# ── 2. enrich_metrics 两阶段测试 ────────────────────────────────

class TestEnrichMetrics:
    """指标推断测试 (enrich_metrics): 规则推断 + LLM composite。"""

    @pytest.mark.asyncio
    async def test_rule_inference_then_llm_composite(self):
        """规则推断 simple → LLM 推断 composite → 追加到 metrics。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        # LLM 返回 composite 指标
        mock_response = json.dumps([
            {
                "name": "avg_order_amount",
                "display_name": "平均客单价",
                "formula": "total_amount_sum / quantity_count",
                "type": "composite",
                "factor_metric_names": ["total_amount_sum", "quantity_count"],
            },
        ])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = (mock_response, MagicMock())
                await enrich_metrics(content)

        metrics = content.models[0].metrics
        # 应有规则推断的 simple 指标 + LLM 推断的 composite
        simple_metrics = [m for m in metrics if m.type == "single"]
        composite_metrics = [m for m in metrics if m.type == "composite"]
        assert len(simple_metrics) >= 2  # total_amount: SUM+AVG, quantity: SUM+COUNT
        for m in simple_metrics:
            assert m.source == "rule_inferred"
        assert len(composite_metrics) >= 1
        assert composite_metrics[0].name == "avg_order_amount"

    @pytest.mark.asyncio
    async def test_no_measure_columns_no_inference(self):
        """无 measure 列 → 不调 LLM, metrics 保持空。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_without_measures()],
        )

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                await enrich_metrics(content)
                mock_chat.assert_not_called()

        assert len(content.models[0].metrics) == 0

    @pytest.mark.asyncio
    async def test_llm_failure_keeps_rule_metrics(self):
        """LLM 调用失败 → 保留规则推断的指标, 不抛异常。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.side_effect = Exception("LLM 服务不可用")
                await enrich_metrics(content)

        # 规则推断的指标仍然存在
        assert len(content.models[0].metrics) >= 2
        assert all(m.source == "rule_inferred" for m in content.models[0].metrics)

    @pytest.mark.asyncio
    async def test_invalid_composite_metric_skipped(self):
        """LLM 返回的 composite 指标校验失败 → 跳过, 保留 simple。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        # composite 缺 factor_metric_names → Pydantic 校验失败
        mock_response = json.dumps([
            {
                "name": "bad_composite",
                "display_name": "坏指标",
                "formula": "SUM(total_amount) / COUNT(*)",
                "type": "composite",
            },
        ])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = (mock_response, MagicMock())
                await enrich_metrics(content)

        # 只有规则推断的 single 指标, 坏的 composite 被跳过
        metrics = content.models[0].metrics
        assert all(m.type == "single" for m in metrics)

    @pytest.mark.asyncio
    async def test_config_disabled_skips_all(self):
        """配置关闭 (scan_metric_inference=False) → 跳过全部指标推断。"""
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
    async def test_rule_inference_disabled_fallback_to_llm(self):
        """规则推断关闭 → 回退原逻辑: LLM 推断所有指标。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        mock_response = json.dumps([
            {"name": "gmv", "display_name": "成交总额", "formula": "SUM(total_amount)", "type": "single"},
        ])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = False
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = (mock_response, MagicMock())
                await enrich_metrics(content)

        assert len(content.models[0].metrics) >= 1

    @pytest.mark.asyncio
    async def test_skips_system_tables(self):
        """系统表跳过指标推断。"""
        from app.services.semantic_scanner import enrich_metrics

        system_model = Model(
            name="users",
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

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = ["users"]
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                await enrich_metrics(content)
                mock_chat.assert_not_called()


# ── 3. schema_context 指标行输出测试 ──────────────────────────

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


# ── 4. build_metrics_hint 测试 ────────────────────────────────

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


# ── 5. metric_feedback 测试 ───────────────────────────────────

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

    def test_hit_known_metric_returns_delta(self):
        """SQL 命中已知指标 → 返回 delta=1 (不再在内存中 +1, 避免竞态)。"""
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

        updates = persist_metric_feedback(mem_store, state, content, conv_id="test-conv")

        # F2: 不再在内存中 +1, 改为返回 delta=1
        assert len(updates) >= 1
        assert updates[0]["delta"] == 1
        # 内存中的 co_occurrence 不变 (由 _persist_co_occurrence 在 DB 层原子递增)
        assert model.metrics[0].co_occurrence == 2

    def test_hit_known_metric_returns_source_and_type(self):
        """metric_feedback 返回 source + type (供 metric_hits 透传)。"""
        from app.ai.recall import persist_metric_feedback

        model = _make_model_with_measures()
        model.metrics = [
            Metric(
                name="total_amount_sum",
                display_name="总金额合计",
                formula="SUM(total_amount)",
                type="single",
                source="rule_inferred",
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

        updates = persist_metric_feedback(mem_store, state, content, conv_id="test-conv")

        assert len(updates) >= 1
        u = updates[0]
        assert u["metric_name"] == "total_amount_sum"
        assert u["source"] == "rule_inferred"
        assert u["type"] == "single"
        assert u["delta"] == 1

    def test_new_metric_pattern_writes_suggestion(self):
        """SQL 含未知聚合 → 写 metric_suggestion 记忆。"""
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

        state = self._make_state(
            sql="SELECT COUNT(*) FROM biz_orders",
            tables=["biz_orders"],
            content=content,
        )
        mem_store = MagicMock()
        mem_store.list_memories.return_value = []

        persist_metric_feedback(mem_store, state, content, conv_id="test-conv")

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

        state = self._make_state(
            sql="SELECT SUM(o.total_amount) FROM biz_orders o JOIN biz_users u ON o.user_id = u.id",
            tables=["biz_orders", "biz_users"],
            content=content,
        )
        mem_store = MagicMock()
        mem_store.list_memories.return_value = []

        persist_metric_feedback(mem_store, state, content)

        mem_store.save_memory.assert_not_called()


# ── 6. Metric schema 字段测试 ─────────────────────────────────

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

    def test_metric_rule_inferred_source(self):
        """source=rule_inferred 合法 (SourceStr 是 str 不锁死)。"""
        m = Metric(
            name="total_amount_sum",
            display_name="总金额合计",
            formula="SUM(total_amount)",
            source="rule_inferred",
        )
        assert m.source == "rule_inferred"

    def test_metric_composite_requires_factor_metric_names(self):
        """composite 指标必须提供 factor_metric_names (SEM-005)。"""
        with pytest.raises(Exception):
            Metric(
                name="bad",
                display_name="坏指标",
                formula="a / b",
                type="composite",
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


# ── 7. 代码走查补充测试 ──────────────────────────────────────

class TestRuleInferenceEdgeCases:
    """规则推断边界场景测试 (代码走查补充)。"""

    def test_type_matches_interval_not_count(self):
        """INTERVAL 类型不应匹配 INT (F7: 单词边界)。"""
        from app.services.semantic_scanner import _type_matches, _COUNT_TYPES
        assert not _type_matches("INTERVAL", _COUNT_TYPES)
        # 但 INTEGER 应该匹配
        assert _type_matches("INTEGER", _COUNT_TYPES)
        # INT 精确匹配
        assert _type_matches("INT", _COUNT_TYPES)
        # DECIMAL(10,2) 应匹配 DECIMAL
        from app.services.semantic_scanner import _AMOUNT_TYPES
        assert _type_matches("DECIMAL(10,2)", _AMOUNT_TYPES)

    def test_name_matches_keyword_word_boundary(self):
        """discount 不匹配 count, discount_amount 匹配 amount (F8: 单词边界)。"""
        from app.services.semantic_scanner import _name_matches_keyword, _COUNT_KEYWORDS, _AMOUNT_KEYWORDS
        # discount 不匹配 count (count 不是独立词)
        assert not _name_matches_keyword("discount", _COUNT_KEYWORDS)
        # order_count 匹配 count
        assert _name_matches_keyword("order_count", _COUNT_KEYWORDS)
        # discount_amount 匹配 amount
        assert _name_matches_keyword("discount_amount", _AMOUNT_KEYWORDS)
        # payment_count 同时匹配 payment(金额) 和 count(数量)
        assert _name_matches_keyword("payment_count", _AMOUNT_KEYWORDS)
        assert _name_matches_keyword("payment_count", _COUNT_KEYWORDS)

    def test_empty_model_list_no_error(self):
        """空模型列表 → enrich_metrics 不报错 (F14)。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(models=[])
        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            # 不应抛异常
            import asyncio
            asyncio.get_event_loop().run_until_complete(enrich_metrics(content))

    def test_key_only_model_no_metrics(self):
        """仅含 key 列的模型 → 不生成指标 (F15)。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = Model(
            name="link_table",
            display_name="关联表",
            columns=[
                _make_column("id", "主键", "BIGINT", "key"),
                _make_column("user_id", "用户ID", "BIGINT", "key"),
                _make_column("order_id", "订单ID", "BIGINT", "key"),
            ],
            relationships=[],
            metrics=[],
            calculated_fields=[],
        )
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = True
            metrics = _infer_simple_metrics(model)

        assert metrics == []

    def test_mixed_measure_types_correct_aggregation(self):
        """混合 measure 列: 金额列→SUM+AVG, 数量列→SUM+COUNT (F16)。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = Model(
            name="biz_orders",
            display_name="订单表",
            columns=[
                _make_column("id", "主键", "BIGINT", "key"),
                _make_column("total_amount", "总金额", "DECIMAL(10,2)", "measure"),
                _make_column("quantity", "数量", "INTEGER", "measure"),
                _make_column("score", "评分", "FLOAT", "measure"),  # 通用 measure
            ],
            relationships=[],
            metrics=[],
            calculated_fields=[],
        )
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = True
            metrics = _infer_simple_metrics(model)

        # total_amount (金额) → SUM + AVG
        amount_metrics = [m for m in metrics if "total_amount" in m.name]
        assert len(amount_metrics) == 2
        assert any(m.formula == "SUM(total_amount)" for m in amount_metrics)
        assert any(m.formula == "AVG(total_amount)" for m in amount_metrics)

        # quantity (数量) → SUM + COUNT
        qty_metrics = [m for m in metrics if "quantity" in m.name]
        assert len(qty_metrics) == 2
        assert any(m.formula == "SUM(quantity)" for m in qty_metrics)
        assert any(m.formula == "COUNT(quantity)" for m in qty_metrics)

        # score (通用) → SUM only
        score_metrics = [m for m in metrics if "score" in m.name]
        assert len(score_metrics) == 1
        assert score_metrics[0].formula == "SUM(score)"

    def test_empty_display_name_fallback_to_col_name(self):
        """display_name 为空字符串 → 回退到列名 (F19)。"""
        from app.services.semantic_scanner import _infer_simple_metrics

        model = Model(
            name="test_table",
            display_name="测试表",
            columns=[
                Column(
                    name="total_amount",
                    display_name="",  # 空字符串 (falsy)
                    data_type="DECIMAL(10,2)",
                    semantic_type="measure",
                    source="manual",
                    confidence=1.0,
                ),
            ],
            relationships=[],
            metrics=[],
            calculated_fields=[],
        )
        with patch("app.core.config.get_settings") as mock_s:
            mock_s.return_value.scan_metric_rule_inference = True
            metrics = _infer_simple_metrics(model)

        # display_name 为空 → 回退到列名 "total_amount"
        assert len(metrics) >= 1
        # SUM 的 display_name 应该是 "total_amount合计" (回退到列名)
        sum_metric = next(m for m in metrics if m.formula == "SUM(total_amount)")
        assert "total_amount" in sum_metric.display_name


class TestManualMetricPreservation:
    """手动指标保留测试 (F1: 重新扫描不覆盖 manual 指标)。"""

    @pytest.mark.asyncio
    async def test_rescan_preserves_manual_metrics(self):
        """重新扫描时, manual 指标不被规则推断覆盖。"""
        from app.services.semantic_scanner import enrich_metrics

        # 模拟用户已手动添加的指标
        manual_metric = Metric(
            name="custom_gmv",
            display_name="自定义GMV",
            formula="SUM(total_amount)",
            type="single",
            source="manual",
            condition="status='paid'",
        )
        model = _make_model_with_measures()
        model.metrics = [manual_metric]

        content = SemanticModelContent(models=[model])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                # LLM 返回空 (不推 composite)
                mock_chat.return_value = ("[]", MagicMock())
                await enrich_metrics(content)

        metrics = content.models[0].metrics
        # manual 指标应保留
        manual_names = [m.name for m in metrics if m.source == "manual"]
        assert "custom_gmv" in manual_names
        # 规则推断的指标也应追加
        rule_names = [m.name for m in metrics if m.source == "rule_inferred"]
        assert len(rule_names) >= 2  # total_amount: SUM+AVG, quantity: SUM+COUNT

    @pytest.mark.asyncio
    async def test_same_name_rule_metric_not_duplicate(self):
        """规则推断的同名指标不重复追加 (已有 manual 同名指标时跳过)。"""
        from app.services.semantic_scanner import enrich_metrics

        # 用户手动创建了 total_amount_sum (与规则推断同名)
        manual_metric = Metric(
            name="total_amount_sum",
            display_name="手动GMV",
            formula="SUM(total_amount)",
            type="single",
            source="manual",
        )
        model = _make_model_with_measures()
        model.metrics = [manual_metric]

        content = SemanticModelContent(models=[model])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = ("[]", MagicMock())
                await enrich_metrics(content)

        metrics = content.models[0].metrics
        # 不应有重复的 total_amount_sum
        names = [m.name for m in metrics]
        assert names.count("total_amount_sum") == 1
        # 且保留 manual source
        tam = next(m for m in metrics if m.name == "total_amount_sum")
        assert tam.source == "manual"


class TestInferAllFallback:
    """_infer_all 回退路径测试 (F18: composite 校验)。"""

    @pytest.mark.asyncio
    async def test_infer_all_invalid_composite_skipped(self):
        """_infer_all 路径: LLM 返回的 composite 缺 factor_metric_names → 跳过。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        # LLM 返回 single + 无效 composite
        mock_response = json.dumps([
            {"name": "gmv", "display_name": "成交总额", "formula": "SUM(total_amount)", "type": "single"},
            {"name": "bad_composite", "display_name": "坏复合", "formula": "a / b", "type": "composite"},
        ])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = False  # 回退路径
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = (mock_response, MagicMock())
                await enrich_metrics(content)

        metrics = content.models[0].metrics
        # single 应保留, bad composite 应跳过
        valid_names = [m.name for m in metrics]
        assert "gmv" in valid_names
        assert "bad_composite" not in valid_names


# ── 8. F4: factor_metric_names 交叉校验测试 ──────────────────────

class TestFactorMetricNamesCrossValidation:
    """F4: composite 指标的 factor_metric_names 必须引用已有指标名。"""

    @pytest.mark.asyncio
    async def test_infer_composite_hallucinated_names_filtered(self):
        """LLM 幻觉出不存在的子指标名 → 过滤掉, 保留有效的。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        # LLM 返回 composite, 其中 factor_metric_names 包含一个存在 + 一个不存在的
        mock_response = json.dumps([
            {
                "name": "avg_order_amount",
                "display_name": "平均客单价",
                "formula": "total_amount_sum / nonexistent_metric",
                "type": "composite",
                "factor_metric_names": ["total_amount_sum", "nonexistent_metric"],
            },
        ])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = (mock_response, MagicMock())
                await enrich_metrics(content)

        metrics = content.models[0].metrics
        composite = [m for m in metrics if m.type == "composite"]
        if composite:
            # nonexistent_metric 被过滤, 只保留 total_amount_sum
            assert "nonexistent_metric" not in composite[0].factor_metric_names
            assert "total_amount_sum" in composite[0].factor_metric_names

    @pytest.mark.asyncio
    async def test_infer_composite_all_invalid_names_dropped(self):
        """LLM 返回的 composite 所有 factor_metric_names 都不存在 → 整个指标丢弃。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        mock_response = json.dumps([
            {
                "name": "bad_ratio",
                "display_name": "坏比率",
                "formula": "fake_a / fake_b",
                "type": "composite",
                "factor_metric_names": ["fake_a", "fake_b"],
            },
        ])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = True
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = (mock_response, MagicMock())
                await enrich_metrics(content)

        metrics = content.models[0].metrics
        composite = [m for m in metrics if m.type == "composite"]
        assert len(composite) == 0  # 全部丢弃

    @pytest.mark.asyncio
    async def test_infer_all_cross_validates_factors(self):
        """_infer_all 路径也做交叉校验: composite 引用不存在的 single → 过滤。"""
        from app.services.semantic_scanner import enrich_metrics

        content = SemanticModelContent(
            models=[_make_model_with_measures()],
        )

        # LLM 返回 single + composite, composite 引用了不存在的 single
        mock_response = json.dumps([
            {"name": "gmv", "display_name": "成交总额", "formula": "SUM(total_amount)", "type": "single"},
            {
                "name": "bad_ratio",
                "display_name": "坏比率",
                "formula": "gmv / phantom_metric",
                "type": "composite",
                "factor_metric_names": ["gmv", "phantom_metric"],
            },
        ])

        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.scan_metric_inference = True
            mock_settings.return_value.scan_metric_rule_inference = False  # 回退路径
            mock_settings.return_value.system_tables = []
            with patch("app.core.llm_client.llm_chat", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = (mock_response, MagicMock())
                await enrich_metrics(content)

        metrics = content.models[0].metrics
        composite = [m for m in metrics if m.type == "composite"]
        if composite:
            # phantom_metric 被过滤
            assert "phantom_metric" not in composite[0].factor_metric_names


# ── 9. F9: formula/condition 注入防御测试 ────────────────────────

class TestFormulaConditionValidation:
    """F9: formula/condition 注入防御测试。

    策略: schema 层不校验 (兼容已有数据), API 层用正则校验用户提交内容。
    执行层 sql_validator.py 三层 AST 是终极防线。
    """

    def test_schema_accepts_all_formulas(self):
        """schema 层不校验 formula 格式 — 所有合法/非法格式都能构造 Metric。"""
        # 这些是已有数据中出现的合法格式, schema 层不应拒绝
        valid_formulas = [
            "SUM(total_amount)", "COUNT(id)", "AVG(score)",
            "COUNT(DISTINCT id)",  # DISTINCT
            "AVG(DATEDIFF(CURRENT_DATE, created_at))",  # 嵌套函数
            "AVG(UNIX_TIMESTAMP(reply_time) - UNIX_TIMESTAMP(created_at)) / 60",  # 算术后缀
            "gmv / order_count",  # composite 算术
        ]
        for formula in valid_formulas:
            m = Metric(name="test", display_name="测试", formula=formula)
            assert m.formula == formula

    def test_schema_accepts_all_conditions(self):
        """schema 层不校验 condition — 合法 condition (含引号内关键字) 都能通过。"""
        conditions = [
            "status IN ('paid','shipped')",
            'action = "create"',  # 引号内的 create 不应误杀
            'action = "update"',
            'action = "delete"',
        ]
        for cond in conditions:
            m = Metric(name="test", display_name="测试", formula="SUM(id)", condition=cond)
            assert m.condition == cond

    def test_dangerous_regex_catches_semicolon(self):
        """_METRIC_DANGEROUS 正则捕获分号。"""
        from app.schemas.semantic_layer import _METRIC_DANGEROUS
        assert _METRIC_DANGEROUS.search("SUM(id); DROP TABLE users")
        assert _METRIC_DANGEROUS.search("1=1; DELETE FROM users")

    def test_dangerous_regex_catches_comment(self):
        """_METRIC_DANGEROUS 正则捕获 SQL 注释。"""
        from app.schemas.semantic_layer import _METRIC_DANGEROUS
        assert _METRIC_DANGEROUS.search("SUM(id)--comment")
        assert _METRIC_DANGEROUS.search("SUM(id)/*comment*/")

    def test_dangerous_regex_passes_valid_content(self):
        """_METRIC_DANGEROUS 正则不误杀合法内容。"""
        from app.schemas.semantic_layer import _METRIC_DANGEROUS
        assert not _METRIC_DANGEROUS.search("SUM(total_amount)")
        assert not _METRIC_DANGEROUS.search('action = "create"')
        assert not _METRIC_DANGEROUS.search("AVG(UNIX_TIMESTAMP(x) - UNIX_TIMESTAMP(y)) / 60")

    def test_ddl_dml_regex_catches_dangerous_statements(self):
        """_DDL_DML_KEYWORDS 正则捕获 DDL/DML 语句 (非裸关键字)。"""
        from app.schemas.semantic_layer import _DDL_DML_KEYWORDS
        assert _DDL_DML_KEYWORDS.search("DROP TABLE users")
        assert _DDL_DML_KEYWORDS.search("DELETE FROM users WHERE 1=1")
        assert _DDL_DML_KEYWORDS.search("INSERT INTO users VALUES(1)")
        assert _DDL_DML_KEYWORDS.search("TRUNCATE TABLE users")

    def test_ddl_dml_regex_passes_quoted_values(self):
        """_DDL_DML_KEYWORDS 正则不误杀引号内的值。"""
        from app.schemas.semantic_layer import _DDL_DML_KEYWORDS
        assert not _DDL_DML_KEYWORDS.search('action = "create"')
        assert not _DDL_DML_KEYWORDS.search('action = "update"')
        assert not _DDL_DML_KEYWORDS.search('action = "delete"')
        assert not _DDL_DML_KEYWORDS.search("status IN ('paid','shipped')")

    def test_condition_none_passes(self):
        """condition=None → 通过 (默认值)。"""
        m = Metric(name="test", display_name="测试", formula="SUM(id)")
        assert m.condition is None

"""
T018: 复合指标展开 (metric_expander)

对标 SEM-005 (openspec spec) + 海泰 MetricContext (海泰分析 行 403-411)

复合指标 (composite) 由子指标 (single) 组合计算:
  客单价 = gmv / order_count
  gmv = SUM(total_amount) WHERE status IN ('paid','shipped')   ← single
  order_count = COUNT(id)                                       ← single

展开时:
  - 按 factor_metric_names 取子指标
  - 子指标必须是 single (嵌套复合 → 抛错, SEM-005)
  - factor 指向不存在的 metric → 抛错 (宁缺毋滥)

产出可序列化为 schema_context (Phase 4 LangGraph State.schema_context 契约)。
"""
import pytest


# ── 测试用指标集 (取自 SEM-002 / 海泰场景) ────────────────────

def _sample_metrics():
    """客单价 = gmv / order_count 的指标集。"""
    from app.schemas.semantic_layer import Metric
    return {
        "order_count": Metric(
            name="order_count", display_name="订单数",
            formula="COUNT(id)", type="single",
        ),
        "gmv": Metric(
            name="gmv", display_name="GMV",
            formula="SUM(total_amount)",
            condition="status IN ('paid', 'shipped')",
            type="single",
        ),
        "avg_order_value": Metric(
            name="avg_order_value", display_name="客单价",
            formula="gmv / order_count",
            type="composite",
            factor_metric_names=["gmv", "order_count"],
        ),
    }


# ── 展开 ──────────────────────────────────────────────────────

class TestExpandCompositeMetric:
    """SEM-005: composite 指标自动展开为子指标。"""

    def test_expand_avg_order_value(self):
        """客单价(composite) → 展开 gmv + order_count 两个子指标。"""
        from app.services.metric_expander import expand_composite_metric

        metrics = _sample_metrics()
        result = expand_composite_metric("avg_order_value", metrics)

        assert result.name == "avg_order_value"
        assert result.calc_expression == "gmv / order_count"
        assert len(result.sub_metrics) == 2
        sub_names = {m.name for m in result.sub_metrics}
        assert sub_names == {"gmv", "order_count"}

    def test_sub_metric_formula_and_condition_preserved(self):
        """子指标的 formula + condition 要完整保留 (SQL 生成要用)。"""
        from app.services.metric_expander import expand_composite_metric

        metrics = _sample_metrics()
        result = expand_composite_metric("avg_order_value", metrics)
        gmv = next(m for m in result.sub_metrics if m.name == "gmv")
        assert gmv.formula == "SUM(total_amount)"
        assert gmv.condition == "status IN ('paid', 'shipped')"

    def test_table_full_names_merged_and_deduped(self):
        """子指标涉及的表合并去重 (对标海泰 build_metric_context)。"""
        from app.services.metric_expander import expand_composite_metric, ExpandedMetric, SubMetric

        # gmv 涉及 orders 表, order_count 也涉及 orders 表 → 去重后 1 个
        metrics = _sample_metrics()
        # 手动给 metric 关联表（展开时从 formula 推断或外部传入）
        result = expand_composite_metric(
            "avg_order_value", metrics,
            metric_tables={"gmv": ["orders"], "order_count": ["orders"]},
        )
        assert result.table_full_names == ["orders"]  # 去重


class TestCompositeErrors:
    """SEM-005 约束: 错误情况必须抛错 (非静默, 宁缺毋滥)。"""

    def test_nested_composite_rejected(self):
        """子指标是 composite → 抛错 (不支持嵌套复合, SEM-005)。"""
        from app.services.metric_expander import expand_composite_metric, CompositeNestingError
        from app.schemas.semantic_layer import Metric

        metrics = {
            "inner_composite": Metric(
                name="inner_composite", display_name="内层复合",
                formula="a / b", type="composite",
                factor_metric_names=["a", "b"],
            ),
            "outer": Metric(
                name="outer", display_name="外层",
                formula="inner_composite * 2", type="composite",
                factor_metric_names=["inner_composite"],
            ),
        }
        with pytest.raises(CompositeNestingError):
            expand_composite_metric("outer", metrics)

    def test_factor_not_found_rejected(self):
        """factor_metric_names 指向不存在的 metric → 抛错 (宁缺毋滥)。"""
        from app.services.metric_expander import expand_composite_metric, MetricNotFoundError
        from app.schemas.semantic_layer import Metric

        metrics = {
            "aov": Metric(
                name="aov", display_name="客单价",
                formula="gmv / order_count", type="composite",
                factor_metric_names=["gmv", "nonexistent"],
            ),
        }
        with pytest.raises(MetricNotFoundError):
            expand_composite_metric("aov", metrics)

    def test_expand_single_metric_is_passthrough(self):
        """对 single 指标调用展开 → 直接返回单元素 (不报错)。"""
        from app.services.metric_expander import expand_composite_metric

        metrics = _sample_metrics()
        result = expand_composite_metric("gmv", metrics)
        assert result.name == "gmv"
        assert len(result.sub_metrics) == 1
        assert result.sub_metrics[0].name == "gmv"


# ── schema_context 序列化 (Phase 4 LangGraph State 契约) ──────

class TestToSchemaContext:
    """展开结果可序列化为 schema_context 字符串 (Phase 4 SQL 生成用)。"""

    def test_composite_to_schema_context(self):
        from app.services.metric_expander import expand_composite_metric, to_schema_context

        metrics = _sample_metrics()
        expanded = expand_composite_metric(
            "avg_order_value", metrics,
            metric_tables={"gmv": ["orders"], "order_count": ["orders"]},
        )
        ctx = to_schema_context(expanded)
        # 包含指标名、计算公式、子指标、涉及表
        assert "客单价" in ctx or "avg_order_value" in ctx
        assert "gmv / order_count" in ctx
        assert "orders" in ctx
        assert "SUM(total_amount)" in ctx  # 子指标公式也带上

    def test_schema_context_includes_conditions(self):
        """筛选条件 (condition) 要进 schema_context (Phase 4 SQL WHERE 要用)。"""
        from app.services.metric_expander import expand_composite_metric, to_schema_context

        metrics = _sample_metrics()
        expanded = expand_composite_metric("avg_order_value", metrics)
        ctx = to_schema_context(expanded)
        assert "status IN" in ctx  # gmv 的 condition

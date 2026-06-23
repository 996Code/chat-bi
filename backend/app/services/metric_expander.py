"""
T018: 复合指标展开 (metric_expander)

对标 SEM-005 (openspec spec) + 海泰 MetricContext (海泰分析 行 403-411)

复合指标 (composite) 由子指标 (single) 组合:
  客单价 = gmv / order_count

展开规则 (SEM-005):
  - 按 factor_metric_names 取子指标
  - 子指标必须是 single (嵌套复合 → CompositeNestingError, 非静默)
  - factor 指向不存在的 metric → MetricNotFoundError (宁缺毋滥)
  - 不支持嵌套复合是有意选择 (对标海泰, 避免无限递归)

产出:
  - ExpandedMetric: 展开后的结构 (含 calc_expression + sub_metrics + table_full_names)
  - to_schema_context(): 序列化为文本, 喂给 Phase 4 LangGraph State.schema_context
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.semantic_layer import Metric


class CompositeNestingError(Exception):
    """子指标是 composite (嵌套复合), SEM-005 不支持。"""


class MetricNotFoundError(Exception):
    """factor_metric_names 指向不存在的 metric (宁缺毋滥)。"""


@dataclass
class SubMetric:
    """展开后的单个子指标 (对标海泰 MetricContext single)。"""
    name: str
    display_name: str
    formula: str
    condition: str | None = None


@dataclass
class ExpandedMetric:
    """展开后的复合指标 (对标海泰 MetricContext)。

    name: 指标名
    calc_expression: 组合公式 (如 "gmv / order_count")
    sub_metrics: 展开的子指标列表 (每个是 single)
    table_full_names: 涉及的表 (去重)
    """
    name: str
    display_name: str
    calc_expression: str
    sub_metrics: list[SubMetric] = field(default_factory=list)
    table_full_names: list[str] = field(default_factory=list)


def expand_composite_metric(
    name: str,
    metrics_by_name: dict[str, Metric],
    metric_tables: dict[str, list[str]] | None = None,
) -> ExpandedMetric:
    """展开复合指标 (或 single 直接返回)。

    Args:
        name: 要展开的指标名
        metrics_by_name: 全部指标查找表 {name: Metric}
        metric_tables: 可选, 每个指标涉及的表 {metric_name: [table_names]}
                       (表信息在 Model 层, 展开时需外部传入)

    Returns:
        ExpandedMetric

    Raises:
        MetricNotFoundError: factor 指向不存在的 metric
        CompositeNestingError: 子指标是 composite (嵌套, SEM-005 不支持)
    """
    if name not in metrics_by_name:
        raise MetricNotFoundError(f"指标不存在: {name}")

    metric = metrics_by_name[name]
    tables_map = metric_tables or {}

    if metric.type == "single":
        # single 指标: 直接包装成单元素结果
        return ExpandedMetric(
            name=metric.name,
            display_name=metric.display_name,
            calc_expression=metric.formula,
            sub_metrics=[SubMetric(
                name=metric.name,
                display_name=metric.display_name,
                formula=metric.formula,
                condition=metric.condition,
            )],
            table_full_names=list(tables_map.get(name, [])),
        )

    # composite: 按 factor_metric_names 取子指标
    factors = metric.factor_metric_names or []
    sub_metrics: list[SubMetric] = []
    all_tables: set[str] = set()

    for factor_name in factors:
        if factor_name not in metrics_by_name:
            raise MetricNotFoundError(
                f"复合指标 {name} 的子指标 {factor_name} 不存在"
            )
        sub = metrics_by_name[factor_name]
        if sub.type == "composite":
            # SEM-005: 不支持嵌套复合 (必须抛错, 非静默)
            raise CompositeNestingError(
                f"复合指标 {name} 的子指标 {factor_name} 也是复合指标, "
                f"不支持嵌套复合 (SEM-005)"
            )
        sub_metrics.append(SubMetric(
            name=sub.name,
            display_name=sub.display_name,
            formula=sub.formula,
            condition=sub.condition,
        ))
        all_tables.update(tables_map.get(factor_name, []))

    return ExpandedMetric(
        name=metric.name,
        display_name=metric.display_name,
        calc_expression=metric.formula,  # 如 "gmv / order_count"
        sub_metrics=sub_metrics,
        table_full_names=sorted(all_tables),  # 去重 + 排序稳定
    )


def to_schema_context(expanded: ExpandedMetric) -> str:
    """序列化展开结果为 schema_context 文本 (Phase 4 LangGraph State 契约)。

    Phase 4 SQL 生成要从这里读: 指标名 + 组合公式 + 子指标公式/条件 + 涉及表。
    这是 design.md ConversationState.schema_context 的数据来源之一。
    """
    lines = [
        f"# 指标: {expanded.display_name} ({expanded.name})",
        f"计算公式: {expanded.calc_expression}",
    ]
    if expanded.table_full_names:
        lines.append(f"涉及表: {', '.join(expanded.table_full_names)}")
    if expanded.sub_metrics:
        lines.append("子指标:")
        for sm in expanded.sub_metrics:
            cond = f" [条件: {sm.condition}]" if sm.condition else ""
            lines.append(f"  - {sm.display_name} ({sm.name}): {sm.formula}{cond}")
    return "\n".join(lines)

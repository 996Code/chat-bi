"""
T012: 语义层 JSON Schema 定义 (Pydantic v2)

对标 SEM-002 (openspec/changes/chatbi-v2/specs/semantic-layer/spec.md)
字段名与 spec JSON 示例 1:1 对应。

设计要点:
- 每个推断字段带 source (manual/auto_inferred/foreign_key/name_pattern/ai_inferred)
  + confidence [0,1]，方便人工复核优先级 (SEM-001 验收标准)
- composite metric 必须有 factor_metric_names (SEM-005)
- 复合指标的子指标只能 single，不支持嵌套复合 (对标海泰限制，避免无限递归)
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── source 枚举（关系/字段的来源，影响人工复核优先级）─────────
# manual: 人工标注 (最高优先级)
# auto_inferred: LLM 推断的中文语义
# foreign_key: 来自数据库外键 (确定性最高)
# name_pattern: 命名模式推断 (xxx_id → xxx)
# ai_inferred: LLM 推断的表关系
SourceStr = str  # 不用 Literal 锁死，允许后续扩展来源标签


class _Inferred(BaseModel):
    """带 source + confidence 的字段基类 (减少重复)。

    所有可能被 AI 推断的字段都继承它，标注来源和置信度。
    默认 source=manual (人工标注优先级最高)，AI 推断时显式覆盖。
    """
    model_config = ConfigDict(extra="forbid")

    source: SourceStr = Field(default="manual", description="字段来源")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度 0-1")


# ── Column ────────────────────────────────────────────────────

# semantic_type: 列在分析中的语义角色
# measure:  可度量数值 (SUM/AVG，如金额)
# dimension: 维度 (GROUP BY，如类目、日期)
# key:      主键/外键
SemanticType = Literal["measure", "dimension", "key"]


class Column(_Inferred):
    """列定义。"""
    name: str
    display_name: str
    data_type: str = Field(description="数据库原始类型，如 DECIMAL(10,2)")
    semantic_type: SemanticType | None = None
    description: str | None = None


# ── Relationship ──────────────────────────────────────────────

JoinType = Literal["INNER", "LEFT", "RIGHT", "FULL"]
Cardinality = Literal["N:1", "1:N", "1:1", "N:N"]


class Relationship(_Inferred):
    """表间关系 (JOIN 依据)。

    替代海泰 extract_table_name 的正则推断 (海泰只取第一个 FROM，不支持 JOIN)。
    显式 relationship 让 Agent 可直接读取 JOIN 条件。
    """
    name: str
    target_model: str
    join_type: JoinType
    on: str = Field(description="JOIN ON 条件，如 orders.user_id = users.id")
    type: Cardinality = Field(description="基数 N:1/1:N/1:1/N:N")


# ── Metric ────────────────────────────────────────────────────

MetricType = Literal["single", "composite"]


class Metric(BaseModel):
    """指标定义。

    single:    直接聚合公式，如 SUM(total_amount)
    composite: 由子指标组合，如 gmv / order_count
               composite 必须有 factor_metric_names 指向子指标 (SEM-005)
               子指标只能 single (不支持嵌套复合，对标海泰)

    指标是数据模型的附属品，归表所有。GMV 属于 biz_orders，不属于独立命名空间。
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    formula: str
    type: MetricType = Field(default="single")
    condition: str | None = Field(
        default=None,
        description="过滤条件，如 status IN ('paid','shipped')",
    )
    description: str | None = None
    factor_metric_names: list[str] | None = Field(
        default=None,
        description="仅 composite 必填：子指标名列表",
    )
    co_occurrence: int = Field(
        default=0,
        description="查询命中次数 (运行时反哺递增, 0=未命中)",
    )
    source: SourceStr = Field(
        default="auto_inferred",
        description="指标来源: auto_inferred=扫描推断, manual=人工校正, "
                    "metric_suggestion=运行时建议",
    )

    @model_validator(mode="after")
    def _composite_requires_factors(self) -> Metric:
        """SEM-005: composite 必须有 factor_metric_names。"""
        if self.type == "composite":
            if not self.factor_metric_names:
                raise ValueError(
                    "composite metric 必须提供 factor_metric_names "
                    "(指向子指标名)，SEM-005"
                )
        return self


# ── CalculatedField ───────────────────────────────────────────

class CalculatedField(BaseModel):
    """计算字段 (行级表达式，区别于 Metric 的聚合)。"""
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    formula: str


# ── Model ─────────────────────────────────────────────────────

class Model(_Inferred):
    """语义层模型 (对应一张表 + 它的列/关系/指标/计算字段)。"""
    name: str = Field(description="表名")
    display_name: str
    description: str | None = None
    columns: list[Column] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    calculated_fields: list[CalculatedField] = Field(default_factory=list)


# ── 顶层：SemanticModelContent ────────────────────────────────

class SemanticModelContent(BaseModel):
    """语义层 JSON 顶层结构 (写入 SemanticModel.content 字段)。

    对应 SEM-002 spec 的完整 JSON。
    """
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1, description="语义层 schema 版本")
    models: list[Model] = Field(default_factory=list)
    # 扫描时生成的示例问题 (LLM 基于表/列/关系推断, 前端空状态展示)
    sample_questions: list[str] = Field(default_factory=list)

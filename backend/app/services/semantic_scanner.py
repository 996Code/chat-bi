"""
T013: 数据源自动扫描 → 生成语义层 JSON

用 SQLAlchemy inspect(engine) 取表/列/外键，组装成 SemanticModelContent。
LLM 中文推断函数可注入（infer_llm 参数），不强制真实调用。

对标:
  - SEM-001 (openspec spec): AI 推断的'可能是'和'确定是'要标注 source/confidence
  - v1 教训 #15: 元数据质量是准确率根本（中文描述为空 → LLM 只能猜）
  - 海泰缺陷规避: 用显式 Relationship 替代 extract_table_name 正则取 FROM
"""
from __future__ import annotations

from typing import Callable, Protocol

from app.schemas.semantic_layer import (
    Column,
    Model,
    Relationship,
    SemanticModelContent,
)


# ── Inspector 协议（duck typing，兼容真实 inspect(engine) 和 mock）────

class InspectorLike(Protocol):
    def get_table_names(self) -> list[str]: ...
    def get_columns(self, table_name: str) -> list[dict]: ...
    def get_table_comment(self, table_name: str) -> dict: ...
    def get_foreign_keys(self, table_name: str) -> list[dict]: ...


# LLM 推断函数签名: (表名, 列列表) -> {列名: 中文名}
InferFn = Callable[[str, list[dict]], dict[str, str]]


def _column_comment(col: dict) -> str:
    """从 get_columns() 返回的列字典里读注释。

    不同方言位置不同:
      - PostgreSQL/MySQL: col["comment"] (字段级，PG 没有 get_column_comment 方法)
      - SQLite/其他: 无，返回空

    注意: 之前用 inspector.get_column_comment(table, col) 是错的 ——
    PG inspector 根本没有此方法，try-except 静默吞掉导致列注释全部丢失
    (v1 "安全降级无声" 模式，端到端真库扫描才发现)。
    """
    comment = col.get("comment")
    if isinstance(comment, dict):
        return comment.get("text") or ""
    return comment or ""


def _infer_cardinality(constrained_cols: list[str]) -> str:
    """推断关系基数。外键侧默认 N:1（多行指向被引用表的主键）。"""
    # 简单规则: 外键 → 被引用表 是 N:1（最常见的业务关系）
    return "N:1"


def scan_data_source(
    inspector: InspectorLike,
    infer_llm: InferFn | None = None,
) -> SemanticModelContent:
    """扫描数据源，返回语义层 JSON。

    Args:
        inspector: SQLAlchemy inspect(engine) 或兼容对象
        infer_llm: 可选的 LLM 中文推断函数。无注释时调用它补 display_name。
                   为 None 则退化用列名（source 仍标 auto_inferred，提示需人工补全）。

    Returns:
        SemanticModelContent（可写入 SemanticModel.content 字段）

    对标 SEM-001:
      - 有注释的表/列 → source=manual, confidence=1.0
      - 外键关系 → source=foreign_key, confidence=1.0
      - LLM 推断的 → source=auto_inferred, confidence<1.0
      - 无注释也无 LLM → 退化列名, source=auto_inferred, confidence=0.5 (待人工补全)
    """
    table_names = inspector.get_table_names()
    models: list[Model] = []

    for table_name in table_names:
        models.append(_scan_table(inspector, table_name, infer_llm))

    return SemanticModelContent(version=1, models=models)


def _scan_table(
    inspector: InspectorLike,
    table_name: str,
    infer_llm: InferFn | None,
) -> Model:
    """扫描单张表 → Model。"""
    raw_columns = inspector.get_columns(table_name)
    table_comment = inspector.get_table_comment(table_name)
    table_comment_text = (table_comment.get("text") if isinstance(table_comment, dict) else "") or ""

    # LLM 批量推断（仅在该表有缺注释的列时才调，省 token）
    needs_infer = infer_llm is not None and any(
        not _column_comment(c)
        for c in raw_columns
    )
    inferred_names: dict[str, str] = {}
    if needs_infer:
        try:
            inferred_names = infer_llm(table_name, raw_columns) or {}
        except Exception:
            inferred_names = {}  # LLM 失败不阻塞扫描（宁缺毋滥：退化列名）

    # 列
    columns = []
    for c in raw_columns:
        col_comment = _column_comment(c)
        if col_comment:
            display_name = col_comment
            source, confidence = "manual", 1.0
        elif c["name"] in inferred_names:
            display_name = inferred_names[c["name"]]
            source, confidence = "auto_inferred", 0.8
        else:
            display_name = c["name"]  # 退化: 列名即展示名
            source, confidence = "auto_inferred", 0.5

        columns.append(Column(
            name=c["name"],
            display_name=display_name,
            data_type=str(c["type"]),
            semantic_type=_infer_semantic_type(c),
            description=col_comment or None,
            source=source,
            confidence=confidence,
        ))

    # 外键 → Relationship
    relationships = _scan_relationships(inspector, table_name)

    # 表 display_name: 优先注释，否则表名
    if table_comment_text:
        t_display = table_comment_text
        t_source, t_conf = "manual", 1.0
    else:
        t_display = table_name
        t_source, t_conf = "auto_inferred", 0.5

    return Model(
        name=table_name,
        display_name=t_display,
        description=table_comment_text or None,
        source=t_source,
        confidence=t_conf,
        columns=columns,
        relationships=relationships,
        metrics=[],  # 初始扫描不生成指标，留给人工/T018
        calculated_fields=[],
    )


def _infer_semantic_type(col: dict) -> str | None:
    """推断列的语义角色: measure/dimension/key。

    对标海泰 ColumnInfo.data_type 约束 SQL 函数选择。
    """
    if col.get("primary_key"):
        return "key"
    type_str = str(col.get("type", "")).upper()
    # 数值类型 → measure
    if any(t in type_str for t in ("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "INT", "SERIAL", "MONEY")):
        # 但纯外键整型更像 key 而非 measure
        if col["name"].endswith("_id") and "SERIAL" not in type_str:
            return "key"
        return "measure"
    return "dimension"


def _scan_relationships(
    inspector: InspectorLike,
    table_name: str,
) -> list[Relationship]:
    """外键 → Relationship（source=foreign_key, confidence=1.0）。"""
    rels = []
    for fk in inspector.get_foreign_keys(table_name):
        constrained = fk.get("constrained_columns", [])
        ref_table = fk.get("referred_table")
        ref_cols = fk.get("referred_columns", [])
        if not constrained or not ref_table or not ref_cols:
            continue
        rels.append(Relationship(
            name=f"{table_name}_to_{ref_table}",
            target_model=ref_table,
            join_type="LEFT",
            on=f"{table_name}.{constrained[0]} = {ref_table}.{ref_cols[0]}",
            type=_infer_cardinality(constrained),
            source="foreign_key",
            confidence=1.0,
        ))
    return rels

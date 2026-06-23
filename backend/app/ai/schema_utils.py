"""
Schema 工具: 从 SemanticModelContent 提取白名单列 + schema context

对标:
  - RAG-005: data_type 必须随检索返回 (SQL 类型约束)
  - AEE-001: SQL 生成的白名单列 + schema context 来自语义层完整定义
  - 修复 v1: 不再从检索文本正则猜列名 (脆弱, 漏列)

正确数据流:
  SemanticModelContent (语义层 JSON) → extract_allowed_columns / build_schema_context
  → 传给 T029 SQL 生成 + T030 Layer3 白名单校验

为什么从语义层而非检索文本:
  - 检索只返回召回的表名, 不含完整列定义
  - 文本正则提取会漏列 (无 display_name 时) + 误匹配
  - 语义层是唯一权威列定义来源
"""
from __future__ import annotations

from app.schemas.semantic_layer import SemanticModelContent, Model


def extract_allowed_columns(
    content: SemanticModelContent | None,
    model_names: list[str] | None = None,
) -> set[str]:
    """从语义层提取白名单列名集合。

    Args:
        content: 语义层内容 (完整列定义)
        model_names: 只取指定表的列 (None = 全部表)

    Returns:
        列名集合 (不含表名前缀, 纯列名)

    用途: T030 Layer3 白名单校验 + T029 prompt 注入
    """
    if content is None or not content.models:
        return set()

    names_filter = set(model_names) if model_names else None
    columns: set[str] = set()
    for model in content.models:
        if names_filter is not None and model.name not in names_filter:
            continue
        for col in model.columns:
            if col.name:
                columns.add(col.name)
    return columns


def build_schema_context(
    content: SemanticModelContent | None,
    model_names: list[str] | None = None,
) -> str:
    """从语义层构建 schema context 文本 (供 SQL 生成 prompt)。

    格式 (含 data_type, 对标 RAG-005 类型约束):
      biz_orders(订单表): id[BIGINT] user_id[BIGINT] total_amount[DECIMAL]

    Args:
        content: 语义层内容
        model_names: 只取指定表 (None = 全部)

    Returns:
        schema context 文本
    """
    if content is None or not content.models:
        return ""

    names_filter = set(model_names) if model_names else None
    lines: list[str] = []
    for model in content.models:
        if names_filter is not None and model.name not in names_filter:
            continue
        col_descs = []
        for col in model.columns:
            desc = col.name
            if col.data_type:
                desc += f"[{col.data_type}]"
            col_descs.append(desc)
        # 含关系提示 (对标海泰 JOIN 依据)
        rels = []
        for rel in model.relationships:
            rels.append(f"→{rel.target_model}({rel.on})")
        rel_str = " ".join(rels)
        display = f"({model.display_name})" if model.display_name != model.name else ""
        lines.append(f"{model.name}{display}: {' '.join(col_descs)} {rel_str}".strip())
    return "\n".join(lines)

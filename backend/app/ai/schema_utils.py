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


def expand_with_relationships(
    content: SemanticModelContent | None,
    selected_names: list[str],
    max_depth: int = 2,
) -> list[str]:
    """沿着语义层关系定义扩展关联表 (对标 V1 _expand_via_relationships)。

    V1 两阶段流程: 选表(表名级) → 关联扩展 → 生成 SQL(字段级)。
    V2 向量检索替代了第一阶段(选表), 但需补这一步: 选完后沿关系图谱
    把 JOIN 需要的关联表补进来, 否则 schema_context 缺关联表 → LLM 看不到
    完整 JOIN 信息 (如选了 biz_products 但漏了 biz_order_items)。

    设计原则: 用语义层结构化关系数据 (Relationship), 不硬编码命名规则。
    语义层的关系由 _scan_relationships (外键) + knowledge_graph (LLM 推断) 产出,
    这里只消费不推断。

    Args:
        content: 语义层内容 (含 relationships 定义)
        selected_names: 检索命中的表名
        max_depth: 关系扩展深度 (默认 2 跳, 防 A→B→C→... 全库扩散)

    Returns:
        扩展后的表名列表 (含原始命中 + 关联表)
    """
    if content is None or not content.models:
        return list(selected_names)

    # 构建双向邻接表 (正向: 表→关系目标; 反向: 被关系指向的表→源表)
    # 只看正向会漏反向 JOIN: biz_order_items→biz_products (product_id),
    # 但用户问 biz_products 时需要反向找到 biz_order_items
    adjacency: dict[str, set[str]] = {}
    for model in content.models:
        for rel in model.relationships:
            target = rel.target_model
            if not target:
                continue
            # 正向边: model.name → target
            adjacency.setdefault(model.name, set()).add(target)
            # 反向边: target → model.name (双向图, JOIN 可从任一方向发起)
            adjacency.setdefault(target, set()).add(model.name)

    if not adjacency:
        return list(selected_names)

    # BFS 双向遍历 (限深度, 防全库扩散)
    result_set = set(selected_names)
    frontier = set(selected_names)
    for _ in range(max_depth):
        next_frontier = set()
        for table_name in frontier:
            for neighbor in adjacency.get(table_name, set()):
                if neighbor not in result_set:
                    result_set.add(neighbor)
                    next_frontier.add(neighbor)
        if not next_frontier:
            break
        frontier = next_frontier

    return list(result_set)


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
            # 附上中文列名 (供 LLM 生成 AS 中文别名)
            if col.display_name and col.display_name != col.name:
                desc += f"(中文: {col.display_name})"
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

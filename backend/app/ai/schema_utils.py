"""
Schema 工具: 从 SemanticModelContent 提取白名单列 + schema context + JOIN 路径

对标:
  - RAG-005: data_type 必须随检索返回 (SQL 类型约束)
  - AEE-001: SQL 生成的白名单列 + schema context 来自语义层完整定义
  - 图驱动: expand_with_relationships 委托 SchemaGraph (最短路径+社区补全)
  - JOIN 路径: 预计算 JOIN 路径+ON 条件, 减少 LLM 推理负担

正确数据流:
  SemanticModelContent (语义层 JSON) → extract_allowed_columns / build_schema_context
  → 传给 T029 SQL 生成 + T030 Layer3 白名单校验

为什么从语义层而非检索文本:
  - 检索只返回召回的表名, 不含完整列定义
  - 文本正则提取会漏列 (无 display_name 时) + 误匹配
  - 语义层是唯一权威列定义来源
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.schemas.semantic_layer import SemanticModelContent, Model

if TYPE_CHECKING:
    from app.services.graph_service import SchemaGraph

logger = logging.getLogger(__name__)


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


def get_schema_graph(content: SemanticModelContent | None) -> SchemaGraph:
    """构建 SchemaGraph 实例 (请求级工厂函数)。

    调用方 (agent.py / chat_stream.py) 在请求处理流程中调用一次,
    将返回的 SchemaGraph 传给 expand_with_relationships / build_join_path_section,
    避免同一请求内重复构建。

    Args:
        content: 语义层内容 (None → 空图)

    Returns:
        SchemaGraph 实例
    """
    from app.services.graph_service import SchemaGraph
    sg = SchemaGraph(content)
    logger.info(
        "🕸️ SchemaGraph 构建: %d 节点, %d 边",
        sg.node_count, sg.edge_count,
    )
    return sg


def expand_with_relationships(
    content: SemanticModelContent | None,
    selected_names: list[str],
    max_depth: int = 2,
    max_total: int | None = None,
    graph: SchemaGraph | None = None,
) -> list[str]:
    """沿着语义层关系定义扩展关联表。

    优先使用 SchemaGraph (最短路径 + 社区补全) 替代纯 BFS。
    SchemaGraph 构建失败时降级回原始 BFS (fail-open, 保证查询可用)。

    设计原则: 用语义层结构化关系数据 (Relationship), 不硬编码命名规则。
    语义层的关系由 _scan_relationships (外键) + knowledge_graph (LLM 推断) 产出,
    这里只消费不推断。

    防扩散: 种子表 (selected_names) 永远保留, 不受上限影响。
    SchemaGraph 模式下按图距离排序 (近的优先), 不会因 BFS 遍历顺序导致
    超级枢纽的远亲占满名额。

    Args:
        content: 语义层内容 (含 relationships 定义)
        selected_names: 检索命中的表名
        max_depth: 关系扩展深度上限 (默认 2 跳; SchemaGraph 模式下按距离排序, 近的优先)
        max_total: 扩展后总表数上限 (None → 从 config 读 rag_max_schema_tables)
        graph: 请求级 SchemaGraph 单例 (None → 内部自建, 向后兼容)

    Returns:
        扩展后的表名列表 (含原始命中 + 关联表)
    """
    if content is None or not content.models:
        return list(selected_names)

    # 从 config 读上限
    if max_total is None:
        from app.core.config import get_settings
        max_total = get_settings().rag_max_schema_tables

    # 优先使用 SchemaGraph (最短路径 + 社区补全)
    try:
        sg = graph or get_schema_graph(content)
        if sg.node_count > 0:
            result = sg.expand_tables(selected_names, max_depth=max_depth, max_total=max_total)
            if result:
                added = set(result) - set(selected_names)
                logger.info(
                    "🕸️ 图谱表扩展: 种子 %d 张 → 扩展后 %d 张 (新增 %d: %s), 方法=SchemaGraph",
                    len(selected_names), len(result), len(added),
                    ",".join(sorted(added)) if added else "无",
                )
                return result
    except Exception as e:
        logger.warning(
            "expand_with_relationships: SchemaGraph 扩展失败, 降级为 BFS: %s", e,
        )

    # 降级: 原始 BFS 双向遍历
    result = _expand_with_bfs(content, selected_names, max_depth, max_total)
    added = set(result) - set(selected_names)
    logger.info(
        "🕸️ 图谱表扩展: 种子 %d 张 → 扩展后 %d 张 (新增 %d: %s), 方法=BFS(降级)",
        len(selected_names), len(result), len(added),
        ",".join(sorted(added)) if added else "无",
    )
    return result


def _expand_with_bfs(
    content: SemanticModelContent,
    selected_names: list[str],
    max_depth: int,
    max_total: int,
) -> list[str]:
    """原始 BFS 扩展 (降级备用)。

    BFS 按深度优先 (先 1 跳后 2 跳), 累计表数达到 max_total 后停止。
    超级枢纽 (如 uc_users 有 45 邻居) 不会把全库拉进来, 因为到达上限后
    后续邻居不再加入。种子表 (selected_names) 永远保留, 不受上限影响。
    """
    # 构建双向邻接表 (正向: 表→关系目标; 反向: 被关系指向的表→源表)
    adjacency: dict[str, set[str]] = {}
    for model in content.models:
        for rel in model.relationships:
            target = rel.target_model
            if not target:
                continue
            adjacency.setdefault(model.name, set()).add(target)
            adjacency.setdefault(target, set()).add(model.name)

    if not adjacency:
        return list(selected_names)

    result_set = set(selected_names)
    frontier = set(selected_names)
    for depth in range(max_depth):
        if len(result_set) >= max_total:
            break
        next_frontier = set()
        for table_name in frontier:
            if len(result_set) >= max_total:
                break
            for neighbor in adjacency.get(table_name, set()):
                if neighbor not in result_set and len(result_set) < max_total:
                    result_set.add(neighbor)
                    next_frontier.add(neighbor)
        if not next_frontier:
            break
        frontier = next_frontier

    if len(result_set) >= max_total:
        logger.warning(
            "expand_with_relationships(BFS): 达到表数上限 %d (种子 %d 张), 截断扩展 (深度=%d)",
            max_total, len(selected_names), max_depth,
        )

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


def build_join_path_section(
    content: SemanticModelContent | None,
    table_names: list[str],
    graph: SchemaGraph | None = None,
    seed_names: list[str] | None = None,
) -> str:
    """构建【JOIN 路径】prompt 块 (预计算 JOIN 路径 + ON 条件)。

    从 SchemaGraph 获取种子表之间的最短 JOIN 路径, 格式化为 LLM 可直接使用的
    JOIN 语句, 减少 LLM 自行推断 JOIN 逻辑的负担。

    优化: 只对种子表 + 种子表的 1-hop 邻居计算 JOIN 路径,
    避免社区远亲产生大量无意义路径对 (C(n,2) 爆炸)。

    Args:
        content: 语义层内容
        table_names: 扩展后的表名列表
        graph: 请求级 SchemaGraph 单例 (None → 内部自建, 向后兼容)
        seed_names: 检索命中的种子表名 (None → 退化为对全部 table_names 算路径)

    Returns:
        JOIN 路径文本块 (空字符串表示无法生成或配置关闭)
    """
    from app.core.config import get_settings
    settings = get_settings()

    if not settings.graph_join_path_in_prompt:
        return ""

    if content is None or not table_names or len(table_names) < 2:
        return ""

    try:
        sg = graph or get_schema_graph(content)

        # 只对种子表 + 种子表的 1-hop 邻居计算 JOIN 路径
        # 社区远亲不需要 JOIN 路径 (LLM 不会用它们做 JOIN)
        if seed_names:
            seed_set = set(seed_names) & set(table_names)
            join_tables = set(seed_set)
            for s in seed_set:
                if s in sg._graph:
                    for nb in list(sg._graph.neighbors(s)) + list(sg._graph.predecessors(s)):
                        if nb in set(table_names):
                            join_tables.add(nb)
            join_paths = sg.get_join_context(list(join_tables))
        else:
            join_paths = sg.get_join_context(table_names)
    except Exception as e:
        logger.warning("build_join_path_section: SchemaGraph 失败, 跳过 JOIN 路径块: %s", e)
        return ""

    if not join_paths:
        logger.info("🕸️ JOIN 路径: %d 张表, 未找到连通路径", len(table_names))
        return ""

    logger.info(
        "🕸️ JOIN 路径: %d 张表 (计算 %d 张), 预计算 %d 条路径 (%s)",
        len(table_names),
        len(join_tables) if seed_names else len(table_names),
        len(join_paths),
        " → ".join(p.tables[0] + ".." + p.tables[-1] for p in join_paths),
    )

    lines = ["涉及表之间的 JOIN 路径 (系统预计算, 请直接使用):"]
    for path in join_paths:
        # 格式: table1 JOIN table2 ON ... JOIN table3 ON ...
        parts = [path.tables[0]]
        for i in range(len(path.on_conditions)):
            join_type = path.join_types[i] if i < len(path.join_types) else "LEFT"
            on = path.on_conditions[i]
            conf = path.confidences[i] if i < len(path.confidences) else 0.0
            parts.append(f"{join_type} JOIN {path.tables[i + 1]} ON {on}  (confidence={conf:.1f})")
        lines.append("  " + " ".join(parts))

    # 检查无路径的表 (只检查参与计算的表, 不报社区远亲)
    connected_tables: set[str] = set()
    for path in join_paths:
        connected_tables.update(path.tables)
    check_tables = set(join_tables) if seed_names else set(table_names)
    disconnected = check_tables - connected_tables
    if disconnected:
        lines.append(f"  以下表无直接关联路径: {', '.join(sorted(disconnected))}")

    return "\n".join(lines)

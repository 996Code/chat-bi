"""
T016/T017: 知识图谱推断与演化

对标:
  - SEM-003 (openspec spec): 知识图谱推断 + confidence/source 标注 + 演化
  - 海泰缺陷: 只取 FROM 第一个表，无 JOIN 推断；本模块显式产出 Relationship 建议

设计要点 (5 大设计原则):
  - source + confidence 双标注: manual > foreign_key > ai_inferred > name_pattern
    confidence 优先级影响人工复核顺序 (SEM-001 验收标准)
  - 只返回建议，不自动写回: 知识图谱是"软知识"，需人工审核后才进语义层
    (避免错误关系污染下游 SQL 生成 — v1 教训 #46 SQL 校验)
  - fail-closed: LLM 失败 → 降级为 name_pattern，不抛异常 (对标 infer_column_chinese)
  - 可演化: T017 mine_implicit_relationships / apply_feedback_signals 调整 confidence

T016: infer_knowledge_graph (name_pattern + ai_inferred)
T017: mine_implicit_relationships + apply_feedback_signals (算法先行，e2e 留 Phase 6)
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import TYPE_CHECKING

from app.schemas.semantic_layer import (
    Cardinality,
    Model,
    Relationship,
    SemanticModelContent,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


# ── 常量 ──────────────────────────────────────────────────────

# name_pattern 推断的置信度 (低于 FK=1.0 和 ai_inferred=0.7)
NAME_PATTERN_CONFIDENCE = 0.6
AI_INFERRED_CONFIDENCE = 0.7

# T017: 频繁 JOIN 阈值（出现 >= 此次数才视为"频繁"，提升 confidence）
FREQUENT_JOIN_THRESHOLD = 3
FREQUENT_JOIN_BOOST = 0.1  # 每次 +0.1，封顶 0.95

# T017: 反馈信号
CORRECTION_PENALTY = 0.15   # 被纠正 → confidence -0.15
PRAISE_BOOST = 0.05         # 被点赞 → confidence +0.05
MIN_CONFIDENCE = 0.1
MAX_CONFIDENCE = 0.95


# ── T016: 推断 ────────────────────────────────────────────────

def _strip_table_prefix(name: str) -> str:
    """去掉常见表前缀，便于 xxx_id → xxx 匹配。

    biz_users → users, t_order → order, dim_date → date
    """
    return re.sub(r"^(biz_|t_|dim_|fact_|fct_)", "", name)


def _infer_relationships_by_name(
    model: Model,
    all_model_names: list[str],
) -> list[Relationship]:
    """命名模式推断: xxx_id → <xxx> 表 (confidence=0.6, source=name_pattern)。

    匹配逻辑:
      1. 找 model 里所有 *_id 结尾的列 (排除自身主键 id)
      2. xxx_id → 找表名为 <prefix>xxx 或 xxx 的目标表
         (biz_orders.user_id → biz_users: 去前缀后 users == user 的复数匹配)
      3. 目标表必须真实存在 (all_model_names)，否则不推断 (避免幻觉)

    宁缺毋滥 (v1 教训 #29): 找不到就跳过，不硬猜。
    """
    if not all_model_names:
        return []

    # 预处理: {去前缀名: 原始表名}，支持单复数匹配
    # e.g. {"users": "biz_users", "user": "biz_users", "products": "biz_products"}
    target_lookup: dict[str, str] = {}
    for table_name in all_model_names:
        if table_name == model.name:
            continue  # 不自引用
        bare = _strip_table_prefix(table_name)
        target_lookup[bare] = table_name
        # 去掉复数 s，让 user_id 也能匹配 users 表
        if bare.endswith("s"):
            target_lookup[bare[:-1]] = table_name

    rels: list[Relationship] = []
    seen_targets: set[str] = set()

    for col in model.columns:
        name = col.name
        if not name.endswith("_id") or name == "id":
            continue

        # user_id → user, category_id → category
        stem = name[:-3]  # 去掉 _id 后缀
        target_table = target_lookup.get(stem)
        if not target_table:
            continue
        if target_table in seen_targets:
            continue

        rels.append(Relationship(
            name=f"{model.name}_to_{target_table}",
            target_model=target_table,
            join_type="LEFT",
            on=f"{model.name}.{name} = {target_table}.id",
            type="N:1",  # xxx_id 指向主键，通常是 N:1
            source="name_pattern",
            confidence=NAME_PATTERN_CONFIDENCE,
        ))
        seen_targets.add(target_table)

    return rels


async def _infer_relationships_batch_by_llm(
    models: list[Model],
    all_model_names: list[str],
    llm_client: "AsyncOpenAI",
) -> list[Relationship]:
    """LLM 批量推断表间关系 (confidence=0.7, source=ai_inferred)。

    策略: 优先一次性全量发送 (200K 上下文足够容纳 121 表的列名)；
    若表数 > 200 则分批，每批 100 张表，但始终在 prompt 中携带全部表名
    列表 (表名列表很小，~500 tokens)，确保 LLM 能看到跨批的表进行关联，
    结果按 source_model 天然去重合并，不会断层。

    保留全列信息 (LLM 需要完整列上下文才能发现 name_pattern 漏掉的
    语义关联, 如 order_no → orders.order_no 等非 _id 关系)。
    失败/异常/非法 JSON → 返回空 list (fail-closed 降级)。
    不设每批超时: asyncio.wait_for 会断开 LLM 连接中断生成，
    靠外层 infer_knowledge_graph 整体超时兜底。
    """
    # 只对有 _id 列的表调 LLM (这些表最可能有外键关系)
    candidates = [m for m in models if any(c.name.endswith("_id") for c in m.columns)]
    if not candidates:
        return []

    all_names_set = set(all_model_names)

    # 全量优先: 200 表以内一次性发送 (输入 ~5K tokens, 输出 ~8K tokens, 200K 上下文绰绰有余)
    batch_size = 200 if len(candidates) <= 200 else 100
    all_rels: list[Relationship] = []

    # 全部表名列表 (很小，每批都带上，确保 LLM 能跨批关联)
    all_names_str = ", ".join(all_model_names[:500])

    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start:batch_start + batch_size]

        # 保留全列: LLM 需要完整上下文发现语义关联 (非 _id 列也有价值)
        tables_desc = []
        for m in batch:
            col_desc = ", ".join(c.name for c in m.columns)
            tables_desc.append(f"{m.name}: [{col_desc}]")

        prompt = (
            "你是数据库关系推断助手。以下是多张表的列信息，"
            "请判断它们之间的关联关系"
            "（基于列名语义，特别是 xxx_id 列指向其他表主键的关联）。\n\n"
            "所有表名: " + all_names_str + "\n\n"
            "需要推断的表:\n"
            + "\n".join(tables_desc)
            + "\n\n为每张表推断它与其他表的关联关系。"
            "只返回 JSON 数组，每项: "
            '{"source_model": "表名", "target_model": "表名", '
            '"on": "ON条件", "type": "N:1|1:N|1:1"}。\n'
            "没有关联就返回空数组 []。不要解释。"
        )

        try:
            from app.core.config import get_settings
            from app.core.llm_client import extract_content

            settings = get_settings()
            model_name = settings.llm_model

            # 不设每批超时: asyncio.wait_for 会断开 LLM 连接中断生成
            # 靠外层 infer_knowledge_graph 整体超时兜底
            resp = await llm_client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.llm_max_tokens,
                temperature=0.1,
            )
            content = extract_content(resp)
            from app.core.llm_json import parse_json_response
            raw_list = parse_json_response(content)
            if raw_list is None or not isinstance(raw_list, list):
                logger.warning(
                    "_infer_relationships_batch_by_llm: LLM 返回非法 JSON, 降级为空"
                )
                continue
        except Exception as e:
            logger.warning(
                "_infer_relationships_batch_by_llm: LLM 调用失败, 降级为空: %s", e
            )
            continue

        # 解析结果
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            source = item.get("source_model", "")
            target = item.get("target_model")
            # 校验 source 和 target 都在表名集合里
            if not source or not target:
                continue
            if source not in all_names_set or target not in all_names_set:
                continue  # 过滤幻觉：表名不存在
            if source == target:
                continue  # 自引用跳过
            on_clause = item.get("on", "").strip()
            if not on_clause:
                continue
            try:
                card = item.get("type", "N:1")
                if card not in ("N:1", "1:N", "1:1", "N:N"):
                    card = "N:1"
                all_rels.append(Relationship(
                    name=f"{source}_to_{target}",
                    target_model=target,
                    join_type="LEFT",
                    on=on_clause,
                    type=card,
                    source="ai_inferred",
                    confidence=AI_INFERRED_CONFIDENCE,
                ))
            except Exception:
                continue  # 单条非法不阻塞整体

    return all_rels


async def infer_knowledge_graph(
    content: SemanticModelContent,
    use_llm: bool = True,
    llm_client: "AsyncOpenAI | None" = None,
) -> list[Relationship]:
    """聚合 name_pattern + ai_inferred，产出**新**关系建议（不写回）。

    Args:
        content: 语义层内容（**不会被修改**）
        use_llm: 是否启用 LLM 推断（False 则只用 name_pattern，测试用）
        llm_client: 可注入 LLM client（None 则用全局 get_llm_client）

    Returns:
        新关系建议列表。已存在的 FK/name_pattern/手动关系会被去重。
        去重 key: (model_name, target_model) — 同一表对只保留最高置信度建议。

    设计:
      - 不修改 content (人工审核后才写回，T015 编辑器负责)
      - LLM 失败降级为只有 name_pattern (fail-closed)
    """
    if not content.models:
        return []

    all_names = [m.name for m in content.models]

    # 收集已有关系（用于去重）
    existing: dict[tuple[str, str], float] = {}  # (from, to) -> confidence
    for m in content.models:
        for r in m.relationships:
            key = (m.name, r.target_model)
            existing[key] = max(existing.get(key, 0.0), r.confidence)

    # 解析 client（惰性 import 避免循环）
    client = llm_client
    if use_llm and client is None:
        try:
            from app.core.llm_client import get_llm_client
            client = get_llm_client()
        except Exception:
            client = None  # 配置缺失 → 跳过 LLM，只用 name_pattern

    suggestions: list[Relationship] = []
    for model in content.models:
        # name_pattern 总是跑（纯本地，无副作用）
        for r in _infer_relationships_by_name(model, all_names):
            suggestions.append(r)

    # ai_inferred: 一次性批量推断 (利用 200K 上下文, 1 次调用替代 N 次)
    if use_llm and client is not None:
        try:
            batch_rels = await _infer_relationships_batch_by_llm(
                content.models, all_names, client,
            )
            suggestions.extend(batch_rels)
        except Exception as e:
            logger.warning("infer_knowledge_graph: LLM 批量推断失败, 降级: %s", e)

    # 去重：已存在的跳过；同表对只保留最高 confidence
    best: dict[tuple[str, str], Relationship] = {}
    for r in suggestions:
        # 推断建议附带的 model_name 信息（Relationship 本身不带 from）
        # 通过 name 字段 "<from>_to_<to>" 解析
        from_table = r.name.split("_to_")[0] if "_to_" in r.name else ""
        key = (from_table, r.target_model)
        if not from_table:
            continue

        # 已有更高/同等置信度的关系 → 跳过
        if key in existing:
            continue

        # 同表对多条建议 → 保留最高 confidence
        if key not in best or r.confidence > best[key].confidence:
            best[key] = r

    return list(best.values())


# ── T017: 演化（算法先行，e2e 留 Phase 6）─────────────────────

def mine_implicit_relationships(
    query_history: list[str],
    existing_relationships: list[Relationship],
) -> dict[tuple[str, str], float]:
    """挖掘历史查询中的频繁 JOIN 表对 → confidence 提升。

    对标 SEM-003 演化: 用户实际查询行为是知识的"信号源"。

    Args:
        query_history: SavedQuery.sql_text 列表
        existing_relationships: 当前语义层关系（用于定位要 boost 的表对）

    Returns:
        {(from_table, to_table): new_confidence} 变更建议。
        只返回有提升的表对（频繁 JOIN >= FREQUENT_JOIN_THRESHOLD 次）。

    实现:
      1. 解析每条 SQL 里的表名（简单正则，FROM/JOIN 后的标识符）
      2. 统计同一条 SQL 里出现的表对共现次数
      3. >= 阈值 → 该表对 confidence += FREQUENT_JOIN_BOOST (封顶 MAX)
    """
    if not query_history or not existing_relationships:
        return {}

    # 已知关系表对 → 当前 confidence
    known_pairs: dict[tuple[str, str], float] = {}
    for r in existing_relationships:
        from_table = r.name.split("_to_")[0] if "_to_" in r.name else ""
        if from_table:
            known_pairs[(from_table, r.target_model)] = r.confidence

    if not known_pairs:
        return {}

    # 统计表对在历史查询中的共现
    cooccur: Counter[tuple[str, str]] = Counter()
    table_re = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
    for sql in query_history:
        tables_in_sql = set(table_re.findall(sql))
        for (t1, t2) in known_pairs:
            if t1 in tables_in_sql and t2 in tables_in_sql:
                cooccur[(t1, t2)] += 1

    # 频繁 → 提升
    suggestions: dict[tuple[str, str], float] = {}
    for pair, count in cooccur.items():
        if count < FREQUENT_JOIN_THRESHOLD:
            continue
        current = known_pairs.get(pair, 0.0)
        boosted = min(
            current + FREQUENT_JOIN_BOOST * count,
            MAX_CONFIDENCE,
        )
        if boosted > current:
            suggestions[pair] = boosted
    return suggestions


def apply_feedback_signals(
    corrections: list[tuple[str, str]],
    praises: list[tuple[str, str]],
    existing_relationships: list[Relationship],
) -> dict[tuple[str, str], float]:
    """根据用户反馈调整关系 confidence（算法先行，e2e 留 Phase 6）。

    对标 SEM-003 演化 + 三态审核 (v1 教训 #41):
      - corrections: 用户纠正过的表对 → confidence 下降
      - praises: 用户点赞的表对 → confidence 提升

    Args:
        corrections: [(from, to), ...] 被纠正的表对
        praises: [(from, to), ...] 被点赞的表对
        existing_relationships: 当前语义层关系

    Returns:
        {(from, to): new_confidence} 变更建议。
    """
    if not existing_relationships:
        return {}

    known: dict[tuple[str, str], float] = {}
    for r in existing_relationships:
        from_table = r.name.split("_to_")[0] if "_to_" in r.name else ""
        if from_table:
            known[(from_table, r.target_model)] = r.confidence

    suggestions: dict[tuple[str, str], float] = {}

    # 纠正 → 下降
    for pair in corrections:
        if pair in known:
            new_conf = max(known[pair] - CORRECTION_PENALTY, MIN_CONFIDENCE)
            if new_conf != known[pair]:
                suggestions[pair] = new_conf

    # 点赞 → 提升（不覆盖纠正结果，纠正优先级更高）
    for pair in praises:
        if pair not in known:
            continue
        if pair in suggestions:
            continue  # 已被纠正，跳过提升
        new_conf = min(known[pair] + PRAISE_BOOST, MAX_CONFIDENCE)
        if new_conf != known[pair]:
            suggestions[pair] = new_conf

    return suggestions

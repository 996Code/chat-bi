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

import logging
from typing import Callable, Protocol

from app.schemas.semantic_layer import (
    Column,
    Metric,
    Model,
    Relationship,
    SemanticModelContent,
)

logger = logging.getLogger(__name__)


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
        metrics=[],  # 同步扫描不推断指标，留给异步 enrich_metrics()
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


# ── 规则推断简单指标 (零 LLM) ──────────────────────────────────────

import re as _re

# 金额类列名关键词 (→ SUM + AVG); 匹配时用单词边界, 避免 discount 匹配 count
_AMOUNT_KEYWORDS = frozenset(("amount", "price", "fee", "cost", "revenue", "salary", "income", "payment"))
# 数量类列名关键词 (→ SUM + COUNT); 后缀优先: _count > _total
_COUNT_KEYWORDS = frozenset(("count", "num", "qty", "quantity", "cnt", "number"))
# 金额类数据类型 (用单词边界匹配, 避免 INTERVAL 匹配 INT)
_AMOUNT_TYPES = frozenset(("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "MONEY", "REAL"))
# 数量类数据类型
_COUNT_TYPES = frozenset(("INT", "BIGINT", "SERIAL", "SMALLINT", "INTEGER", "BIGSERIAL"))


def _type_matches(data_type: str, type_set: frozenset[str]) -> bool:
    """检查 data_type 是否匹配 type_set 中的任一类型 (单词边界, 避免误匹配)。

    例: "INTEGER" 匹配 "INT" (前缀), "INTERVAL" 不匹配 "INT" (非单词边界)。
    """
    type_upper = data_type.upper()
    for t in type_set:
        # 精确匹配或前缀匹配 (DECIMAL(10,2) 匹配 DECIMAL)
        if type_upper == t or type_upper.startswith(t + "(") or type_upper.startswith(t + " "):
            return True
    return False


def _name_matches_keyword(name: str, keywords: frozenset[str]) -> bool:
    """检查列名是否包含关键词 (单词边界, 避免 discount 匹配 count)。

    例: "order_count" 匹配 "count", "discount_amount" 匹配 "amount" (amount 是独立词),
    "discount" 不匹配 "count" (count 不是独立词)。
    """
    name_lower = name.lower()
    for kw in keywords:
        # 用正则单词边界匹配
        if _re.search(rf"(?:^|[_\b]){_re.escape(kw)}(?:[_\b]|$)", name_lower):
            return True
    return False


def _infer_simple_metrics(model: Model) -> list[Metric]:
    """规则推断 simple 指标 (零 LLM 调用)。

    逻辑:
      - 金额类列 (名含 amount/price/... + DECIMAL/...) → SUM + AVG
      - 数量类列 (名含 count/num/... + INT/...) → SUM + COUNT
      - 通用 measure 列 → SUM
      - 去重: 同一 (列名, 聚合函数) 只生成一个
      - source = "rule_inferred"

    Args:
        model: 语义层表模型 (需已有 columns)

    Returns:
        推断出的 simple 指标列表 (可能为空)
    """
    from app.core.config import get_settings

    if not get_settings().scan_metric_rule_inference:
        return []

    metrics: list[Metric] = []
    seen: set[tuple[str, str]] = set()  # (col_name, func) 去重

    for col in model.columns:
        if col.semantic_type != "measure":
            continue

        col_name = col.name
        col_display = col.display_name or col_name
        type_upper = (col.data_type or "").upper()
        name_lower = col_name.lower()

        # 判断列类别 (关键词 + 数据类型 双重匹配, 避免误判)
        is_amount = (
            _name_matches_keyword(col_name, _AMOUNT_KEYWORDS)
            and _type_matches(type_upper, _AMOUNT_TYPES)
        )
        is_count = (
            _name_matches_keyword(col_name, _COUNT_KEYWORDS)
            and _type_matches(type_upper, _COUNT_TYPES)
        )

        if is_amount:
            # SUM
            key = (col_name, "SUM")
            if key not in seen:
                seen.add(key)
                metrics.append(Metric(
                    name=f"{col_name}_sum",
                    display_name=f"{col_display}合计",
                    formula=f"SUM({col_name})",
                    type="single",
                    source="rule_inferred",
                ))
            # AVG
            key = (col_name, "AVG")
            if key not in seen:
                seen.add(key)
                metrics.append(Metric(
                    name=f"{col_name}_avg",
                    display_name=f"平均{col_display}",
                    formula=f"AVG({col_name})",
                    type="single",
                    source="rule_inferred",
                ))
        elif is_count:
            # SUM
            key = (col_name, "SUM")
            if key not in seen:
                seen.add(key)
                metrics.append(Metric(
                    name=f"{col_name}_sum",
                    display_name=f"{col_display}合计",
                    formula=f"SUM({col_name})",
                    type="single",
                    source="rule_inferred",
                ))
            # COUNT
            key = (col_name, "COUNT")
            if key not in seen:
                seen.add(key)
                metrics.append(Metric(
                    name=f"{col_name}_count",
                    display_name=f"{col_display}计数",
                    formula=f"COUNT({col_name})",
                    type="single",
                    source="rule_inferred",
                ))
        else:
            # 通用 measure → SUM
            key = (col_name, "SUM")
            if key not in seen:
                seen.add(key)
                metrics.append(Metric(
                    name=f"{col_name}_sum",
                    display_name=f"{col_display}合计",
                    formula=f"SUM({col_name})",
                    type="single",
                    source="rule_inferred",
                ))

    return metrics


# ── 异步指标推断 (扫描后 LLM 推断，与 _enrich_with_llm 同级) ────────

async def enrich_metrics(content: SemanticModelContent) -> None:
    """扫描后推断业务指标 (原地 patch)。

    两阶段:
      1. 规则推断 simple 指标 (零 LLM): 金额列→SUM+AVG, 数量列→SUM+COUNT, 通用→SUM
      2. LLM 推断 composite 指标: 只对有 2+ simple 指标的表调 LLM

    设计原则:
      - 指标是数据模型的附属品，归表所有
      - 无 measure 列的表 → 跳过
      - 宁缺毋滥：推断失败不阻塞扫描
      - 跳过系统表 (ChatBI 元数据表, 用户不查)

    调用时机: 在 _enrich_with_llm 之后 (列已有中文 display_name, 指标推断更准)
    """
    from app.core.config import get_settings

    # 配置开关关闭 → 跳过
    if not get_settings().scan_metric_inference:
        return

    _system_tables = frozenset(get_settings().system_tables)

    # ── 阶段 1: 规则推断 simple 指标 (零 LLM) ──────────────────
    # 保留已有 manual 指标, 只追加规则推断的新指标 (同名不覆盖)
    for model in content.models:
        if model.name in _system_tables:
            continue
        simple_metrics = _infer_simple_metrics(model)
        if simple_metrics:
            existing_names = {m.name for m in model.metrics}
            new_count = 0
            for m in simple_metrics:
                if m.name not in existing_names:
                    model.metrics.append(m)
                    existing_names.add(m.name)
                    new_count += 1
            if new_count:
                logger.info(
                    "enrich_metrics (规则): 表 %s 追加 %d 个 simple 指标 (已有 %d 个): %s",
                    model.name, new_count, len(model.metrics) - new_count,
                    ", ".join(m.name for m in simple_metrics[:5]),
                )

    # ── 阶段 2: LLM 推断指标 ──────────────────────────────────────
    if not get_settings().scan_metric_rule_inference:
        # 规则推断关闭 → 回退原逻辑: 所有有 measure 列的表都调 LLM (single + composite)
        candidates = [
            m for m in content.models
            if m.name not in _system_tables
            and any(c.semantic_type == "measure" for c in m.columns)
        ]
    else:
        # 规则推断开启 → 只对有 2+ simple 指标的表调 LLM 推 composite
        candidates = [
            m for m in content.models
            if m.name not in _system_tables
            and len(m.metrics) >= 2  # 有 2+ simple 指标才值得推 composite
        ]

    if not candidates:
        return

    from app.core.llm_client import llm_chat
    from app.core.llm_json import parse_json_response

    # 逐表推断 (每张表的指标语义独立, 不适合批量)
    # 优化: 表数多时并发, 但限制并发数避免 LLM 限流
    semaphore = _make_semaphore(max_concurrent=3)

    rule_enabled = get_settings().scan_metric_rule_inference

    async def _infer_composite(model: Model) -> None:
        """LLM 推断 composite 指标 (输入是已有的 simple 指标列表)。"""
        # 构造 prompt: 已有 simple 指标列表
        simple_desc = ", ".join(
            f"{m.name}({m.display_name}) = {m.formula}"
            + (f" WHERE {m.condition}" if m.condition else "")
            for m in model.metrics
            if m.type == "single"
        )
        if not simple_desc:
            return

        prompt = (
            f"你是 BI 业务指标推断助手。表名: {model.name}\n"
            f"已有简单指标: {simple_desc}\n\n"
            f"基于以上简单指标，推断复合业务指标。规则:\n"
            f"1. composite 指标: 由子指标组合，如 gmv / order_count，需填 factor_metric_names\n"
            f"2. 每个指标需有 name(英文标识), display_name(中文名), formula, type(composite)\n"
            f"3. factor_metric_names 必须指向已有简单指标的 name\n"
            f"4. 如有过滤条件填 condition\n"
            f"5. 只推断有业务含义的复合指标，不要凑数\n\n"
            f"只返回 JSON 数组，格式:\n"
            f'[{{"name": "avg_order_amount", "display_name": "客单价", "formula": "total_amount_sum / id_count", '
            f'"type": "composite", "factor_metric_names": ["total_amount_sum", "id_count"]}}]\n'
            f"不要解释，不要 markdown 包裹。"
        )

        async with semaphore:
            try:
                result_text, _ = await llm_chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
            except Exception as e:
                logger.warning("enrich_metrics: LLM 调用失败 (表=%s), 跳过: %s", model.name, e)
                return

        # 解析 LLM 响应
        parsed = parse_json_response(result_text)
        if not isinstance(parsed, list):
            logger.warning("enrich_metrics: LLM 返回非数组 (表=%s), 跳过", model.name)
            return

        # Pydantic 逐条校验，失败跳过 (宁缺毋滥)
        # 交叉校验: factor_metric_names 必须引用已有 simple 指标名
        existing_names = {m.name for m in model.metrics}
        composite_metrics: list[Metric] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            try:
                m = Metric(**item)
            except Exception as e:
                logger.warning(
                    "enrich_metrics: 指标校验失败 (表=%s, 数据=%s): %s",
                    model.name, item, e,
                )
                continue
            # F4: 过滤 factor_metric_names 中不存在的名字
            if m.factor_metric_names:
                valid = [n for n in m.factor_metric_names if n in existing_names]
                invalid = [n for n in m.factor_metric_names if n not in existing_names]
                if invalid:
                    logger.warning(
                        "enrich_metrics: composite 指标 %s 的 factor_metric_names "
                        "引用了不存在的子指标 %s, 已过滤 (表=%s)",
                        m.name, invalid, model.name,
                    )
                if not valid:
                    # 过滤后为空 → composite 无有效子指标, 丢弃
                    logger.warning(
                        "enrich_metrics: composite 指标 %s 无有效 factor_metric_names, 丢弃 (表=%s)",
                        m.name, model.name,
                    )
                    continue
                m.factor_metric_names = valid
            composite_metrics.append(m)

        if composite_metrics:
            # 追加到已有 simple 指标后
            model.metrics = list(model.metrics) + composite_metrics
            logger.info(
                "enrich_metrics (LLM): 表 %s 推断出 %d 个 composite 指标: %s",
                model.name, len(composite_metrics),
                ", ".join(m.name for m in composite_metrics),
            )

    async def _infer_all(model: Model) -> None:
        """LLM 推断所有指标 (single + composite) — 规则推断关闭时的回退路径。"""
        # 构造 prompt: 列出所有 measure 列
        measure_cols = [c for c in model.columns if c.semantic_type == "measure"]
        if not measure_cols:
            return

        col_desc = ", ".join(
            f"{c.name}({c.display_name}, {c.data_type})"
            for c in measure_cols
        )

        prompt = (
            f"你是 BI 业务指标推断助手。表名: {model.name}\n"
            f"度量列: {col_desc}\n\n"
            f"基于以上度量列，推断业务指标。规则:\n"
            f"1. single 指标: 单列聚合，如 SUM(col), AVG(col), COUNT(col)\n"
            f"2. composite 指标: 由子指标组合，如 gmv / order_count，需填 factor_metric_names\n"
            f"3. 每个指标需有 name(英文标识), display_name(中文名), formula, type(single/composite)\n"
            f"4. composite 指标的 factor_metric_names 必须指向已有指标的 name\n"
            f"5. 如有过滤条件填 condition\n"
            f"6. 只推断有业务含义的指标，不要凑数\n\n"
            f"只返回 JSON 数组，格式:\n"
            f'[{{"name": "gmv", "display_name": "成交总额", "formula": "SUM(total_amount)", "type": "single"}}, '
            f'{{"name": "avg_order_amount", "display_name": "客单价", "formula": "gmv / order_count", '
            f'"type": "composite", "factor_metric_names": ["gmv", "order_count"]}}]\n'
            f"不要解释，不要 markdown 包裹。"
        )

        async with semaphore:
            try:
                result_text, _ = await llm_chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                )
            except Exception as e:
                logger.warning("enrich_metrics: LLM 调用失败 (表=%s), 跳过: %s", model.name, e)
                return

        # 解析 LLM 响应
        parsed = parse_json_response(result_text)
        if not isinstance(parsed, list):
            logger.warning("enrich_metrics: LLM 返回非数组 (表=%s), 跳过", model.name)
            return

        # Pydantic 逐条校验，失败跳过 (宁缺毋滥)
        # 交叉校验: composite 的 factor_metric_names 必须引用同批次已有指标名
        # _infer_all 是 LLM 全量推断, single 和 composite 在同一批次,
        # 所以先收集所有 single 指标名, 再校验 composite 的引用
        llm_metrics: list[Metric] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            try:
                m = Metric(**item)
                llm_metrics.append(m)
            except Exception as e:
                logger.warning(
                    "enrich_metrics: 指标校验失败 (表=%s, 数据=%s): %s",
                    model.name, item, e,
                )

        # F4: 交叉校验 composite 的 factor_metric_names
        single_names = {m.name for m in llm_metrics if m.type == "single"}
        validated: list[Metric] = []
        for m in llm_metrics:
            if m.type == "composite" and m.factor_metric_names:
                valid = [n for n in m.factor_metric_names if n in single_names]
                invalid = [n for n in m.factor_metric_names if n not in single_names]
                if invalid:
                    logger.warning(
                        "enrich_metrics: composite 指标 %s 的 factor_metric_names "
                        "引用了不存在的子指标 %s, 已过滤 (表=%s)",
                        m.name, invalid, model.name,
                    )
                if not valid:
                    logger.warning(
                        "enrich_metrics: composite 指标 %s 无有效 factor_metric_names, 丢弃 (表=%s)",
                        m.name, model.name,
                    )
                    continue
                m.factor_metric_names = valid
            validated.append(m)

        if validated:
            model.metrics = validated
            logger.info(
                "enrich_metrics (LLM 回退): 表 %s 推断出 %d 个指标: %s",
                model.name, len(validated),
                ", ".join(m.name for m in llm_metrics),
            )

    # 并发推断所有候选表
    import asyncio
    if rule_enabled:
        await asyncio.gather(*[_infer_composite(m) for m in candidates], return_exceptions=True)
    else:
        await asyncio.gather(*[_infer_all(m) for m in candidates], return_exceptions=True)


def _make_semaphore(max_concurrent: int):
    """创建并发信号量 (延迟导入 asyncio)。"""
    import asyncio
    return asyncio.Semaphore(max_concurrent)

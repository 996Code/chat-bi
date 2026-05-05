"""RAG 语义检索层：根据用户问题找到最相关的表和字段。"""
import json
import re
from difflib import SequenceMatcher
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Chroma integration: try import, fall back to keyword-only if unavailable
try:
    from app.services.chroma_service import get_chroma_service
    _CHROMA_AVAILABLE = True
except ImportError:
    _CHROMA_AVAILABLE = False
    get_chroma_service = None  # type: ignore

# 中文停用词
STOP_WORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "他", "她", "它", "们", "那", "里", "哪", "什么", "怎么", "多少",
    "个", "请", "问", "帮", "查", "给", "想", "能", "吗", "呢", "吧", "啊", "哦",
}

# 同义词映射表（内存，无需外部中间件）
# key: 标准词，value: 同义词列表
SYNONYM_MAP: dict[str, list[str]] = {
    "用户": ["user", "member", "customer", "客户", "会员"],
    "订单": ["order", "purchase", "交易"],
    "商品": ["product", "item", "goods", "货物"],
    "金额": ["amount", "money", "price", "cost", "total", "费用", "总价"],
    "数量": ["count", "num", "quantity", "qty", "总数"],
    "收入": ["revenue", "income", "earning"],
    "时间": ["time", "date", "created_at", "updated_at", "日期"],
    "状态": ["status", "state"],
    "退款": ["refund", "cancelled", "退款"],
    "分类": ["category", "type", "kind", "类型"],
    "名称": ["name", "title"],
    "电话": ["phone", "mobile", "tel"],
    "地址": ["address", "addr", "location"],
    "城市": ["city"],
    "地区": ["region", "area", "district"],
    "销售额": ["sale", "sales", "revenue", "营业额"],
    "利润": ["profit", "margin"],
    "成本": ["cost", "expense"],
    "VIP": ["vip", "等级", "level", "tier"],
    "发货": ["ship", "deliver", "delivery", "物流"],
    "支付": ["pay", "payment", "payment_method", "method", "付款"],
    "配送": ["carrier", "delivery", "shipping", "物流", "快递"],
    "快递": ["carrier", "delivery", "shipping", "物流"],
    "库存": ["inventory", "stock", "quantity", "warehouse"],
    "仓库": ["warehouse", "warehouse_id", "location"],
    "优惠券": ["coupon", "discount", "code"],
    "复购": ["repurchase", "repeat", "order_count"],
    "趋势": ["trend", "date", "month", "created_at", "时间"],
    "月份": ["month", "date", "created_at"],
    "评价": ["review", "rating", "comment", "评分"],
    "好评": ["review", "rating", "good", "高分"],
    "占比": ["percentage", "ratio", "proportion", "count", "占比"],
    "分布": ["distribution", "breakdown", "group", "count"],
}


def _expand_keywords(keywords: list[str]) -> set[str]:
    """通过同义词映射扩展关键词。"""
    expanded = set(keywords)
    for kw in keywords:
        for standard, synonyms in SYNONYM_MAP.items():
            if kw == standard or kw in synonyms:
                expanded.add(standard)
                expanded.update(synonyms)
    return expanded


def extract_keywords(question: str) -> list[str]:
    """从问题中提取关键词（去除停用词）。"""
    # Remove punctuation
    text = re.sub(r'[^\w\s一-鿿]', ' ', question)
    # Split into words (simple char-level for Chinese)
    chars = list(text.lower())
    # Also extract 2-3 char ngrams for Chinese phrases
    words = set()
    clean_chars = [c for c in chars if c.strip() and c not in STOP_WORDS]
    words.update(clean_chars)
    # 2-grams and 3-grams
    for i in range(len(clean_chars) - 1):
        words.add(clean_chars[i] + clean_chars[i + 1])
    if len(clean_chars) > 2:
        for i in range(len(clean_chars) - 2):
            words.add(clean_chars[i] + clean_chars[i + 1] + clean_chars[i + 2])
    # English words
    english_words = re.findall(r'[a-z]+', text.lower())
    words.update(english_words)
    # Remove single-char Chinese stop words
    words = {w for w in words if len(w) > 1 or not re.match(r'^[一-鿿]$', w)}
    return sorted(words)


def similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度。"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_relevant_tables(
    question: str,
    metadata: dict[str, Any],
    max_tables: int = 5,
) -> list[dict[str, Any]]:
    """根据问题找到最相关的表。"""
    keywords = extract_keywords(question)
    expanded_keywords = _expand_keywords(keywords)
    models = metadata.get("models", [])
    # Build relationship map from top-level config relationships
    all_rels = metadata.get("relationships", [])
    rel_map = _build_relationship_map(all_rels)

    scores = []
    for model in models:
        table_name = (model.get("name") or "").lower()
        table_desc = (model.get("description") or "").lower()

        # Score: name match is highest priority
        name_score = 0
        for kw in expanded_keywords:
            if kw in table_name:
                name_score += 3
            elif similarity(kw, table_name) > 0.5:
                name_score += 2

        # Description match
        desc_score = 0
        for kw in expanded_keywords:
            if kw in table_desc:
                desc_score += 2
            elif similarity(kw, table_desc) > 0.4:
                desc_score += 1

        # Column name match
        col_score = 0
        for col in model.get("columns", []):
            col_name = (col.get("name") or "").lower()
            col_comment = (col.get("comment") or "").lower()
            for kw in expanded_keywords:
                if kw in col_name:
                    col_score += 1
                elif kw in col_comment:
                    col_score += 1

        total = name_score + desc_score + col_score
        if total > 0:
            scores.append((total, model))

    # Sort by score, take top N
    scores.sort(key=lambda x: -x[0])
    result = []
    for score, model in scores[:max_tables]:
        # Only include relevant columns
        relevant_cols = []
        for col in model.get("columns", []):
            col_name = (col.get("name") or "").lower()
            col_comment = (col.get("comment") or "").lower()
            is_relevant = any(
                kw in col_name or kw in col_comment or similarity(kw, col_name) > 0.5
                for kw in expanded_keywords
            )
            # Always include primary keys, foreign keys, and protected columns
            if col.get("primary") or col.get("column_key") in ("PRI", "FK", "MUL"):
                is_relevant = True
            if _is_protected_column(col):
                is_relevant = True
            if is_relevant:
                relevant_cols.append(col)

        # If no columns matched, include all (table name matched but no column keyword)
        if not relevant_cols:
            relevant_cols = model.get("columns", [])[:15]  # cap at 15

        result.append({
            "name": model["name"],
            "description": model.get("description") or "",
            "columns": relevant_cols,
            "relationships": rel_map.get(model["name"], []),
        })

    return result


def _build_relationship_map(all_rels: list[dict]) -> dict[str, list[dict]]:
    """将顶层 relationships 列表转为按表名索引的 map。

    输入格式: [{from_table, from_column, to_table, to_column}, ...]
    输出格式: {table_name: [{column, referenced_table, referenced_column}, ...]}
    """
    rel_map: dict[str, list[dict]] = {}
    for rel in all_rels:
        ft = rel.get("from_table", "")
        fc = rel.get("from_column", "")
        tt = rel.get("to_table", "")
        tc = rel.get("to_column", "")
        if ft and fc and tt and tc:
            rel_map.setdefault(ft, []).append({
                "column": fc,
                "referenced_table": tt,
                "referenced_column": tc,
            })
            # Also add reverse direction
            rel_map.setdefault(tt, []).append({
                "column": tc,
                "referenced_table": ft,
                "referenced_column": fc,
            })
    return rel_map


def _expand_via_relationships(
    tables: list[dict[str, Any]],
    model_map: dict[str, dict],
    rel_map: dict[str, list[dict]],
    max_tables: int,
) -> list[dict[str, Any]]:
    """沿着关联关系扩展检索结果，把被引用的表也加进来。

    例如：检索到 t_orders，但 t_orders.user_id -> t_users.id，
    则自动把 t_users 也加入结果。
    """
    seen = {t["name"] for t in tables}
    expanded = list(tables)

    for t in tables:
        for rel in t.get("relationships", []):
            ref_table = rel.get("referenced_table", "")
            if ref_table and ref_table not in seen and ref_table in model_map and len(expanded) < max_tables:
                seen.add(ref_table)
                full_model = model_map[ref_table]
                expanded.append({
                    "name": ref_table,
                    "description": full_model.get("description", "") or "",
                    "columns": full_model.get("columns", [])[:15],
                    "relationships": rel_map.get(ref_table, []),
                })

    return expanded[:max_tables]


async def _llm_retrieve_tables(
    question: str,
    models: list[dict],
    max_tables: int = 5,
) -> list[str]:
    """Use LLM to identify relevant tables when keyword matching fails."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return []

    # Build concise table list for LLM
    table_list = []
    for m in models[:30]:
        cols = [c.get("name", "") for c in m.get("columns", [])[:10]]
        desc = m.get("description", "") or m.get("comment", "") or ""
        table_list.append(f"- {m['name']} ({desc}): {', '.join(cols)}")

    prompt = f"""根据用户问题和以下数据库表列表，找出最相关的 1-{max_tables} 张表。

表列表:
{chr(10).join(table_list)}

用户问题: {question}

要求:
1. 只输出表名，每行一个
2. 不要编号，不要解释
3. 只输出确定相关的表"""

    try:
        llm = ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=0.0,
            max_tokens=200,
        )
        response = await llm.ainvoke(prompt)
        text = response.content.strip()
        table_names = {m["name"] for m in models}
        result = []
        for line in text.split("\n"):
            name = line.strip().lstrip("-0123456789.) ")
            if name in table_names and name not in result:
                result.append(name)
        return result[:max_tables]
    except Exception as e:
        logger.warning("LLM table retrieval failed: %s", e)
        return []


async def resolve_tables(
    question: str,
    metadata: dict[str, Any],
    max_tables: int = 5,
    datasource_id: str | None = None,
) -> list[dict[str, Any]]:
    """主入口：Chroma 向量检索优先，关键词/同义词兜底，LLM 补充，关联扩展。

    1. 尝试用 Chroma 向量相似度检索
    2. 关键词/同义词匹配
    3. 如果前两种结果不足，用 LLM 补充
    4. 沿关联关系自动扩展相关表
    5. 去重后返回

    返回值格式与 find_relevant_tables 一致，保证向后兼容。
    """
    models = metadata.get("models", [])
    model_map = {m["name"]: m for m in models}
    all_rels = metadata.get("relationships", [])
    rel_map = _build_relationship_map(all_rels)

    # --- Chroma retrieval (primary) ---
    chroma_tables = []
    chroma_service = get_chroma_service()
    if _CHROMA_AVAILABLE and chroma_service and datasource_id:
        try:
            chroma_results = chroma_service.query_similar(
                datasource_id=datasource_id,
                query_text=question,
                max_results=max_tables,
            )
            for ct in chroma_results.get("tables", []):
                t_name = ct["name"]
                if t_name in model_map:
                    full_model = model_map[t_name]
                    matched_cols = ct.get("matched_columns", [])
                    if matched_cols:
                        matched_names = {mc["name"] for mc in matched_cols}
                        relevant_cols = []
                        for col in full_model.get("columns", []):
                            if col.get("name") in matched_names or \
                               col.get("primary") or col.get("column_key") in ("PRI", "FK", "MUL"):
                                relevant_cols.append(col)
                        if not relevant_cols:
                            relevant_cols = full_model.get("columns", [])[:15]
                    else:
                        relevant_cols = full_model.get("columns", [])[:15]

                    chroma_tables.append({
                        "name": t_name,
                        "description": full_model.get("description", "") or "",
                        "columns": relevant_cols,
                        "relationships": rel_map.get(t_name, []),
                        "_chroma_score": ct.get("score", 0),
                    })
        except Exception as e:
            logger.warning("Chroma retrieval failed, falling back to keyword: %s", e)

    # --- Keyword retrieval (fallback) ---
    keyword_tables = find_relevant_tables(question, metadata, max_tables)

    # --- Merge: Chroma results first, then keyword results (deduplicated) ---
    seen = set()
    merged = []
    for t in chroma_tables:
        if t["name"] not in seen:
            seen.add(t["name"])
            t.pop("_chroma_score", None)
            merged.append(t)

    for t in keyword_tables:
        if t["name"] not in seen:
            seen.add(t["name"])
            merged.append(t)

    # --- LLM retrieval to supplement keyword/Chroma results ---
    # Always run LLM retrieval to catch tables that keyword matching missed
    try:
        llm_tables = await _llm_retrieve_tables(question, models, max_tables)
        for t_name in llm_tables:
            if t_name not in seen and t_name in model_map:
                seen.add(t_name)
                full_model = model_map[t_name]
                merged.append({
                    "name": t_name,
                    "description": full_model.get("description", "") or "",
                    "columns": full_model.get("columns", [])[:15],
                    "relationships": rel_map.get(t_name, []),
                })
    except Exception as e:
        logger.warning("LLM table retrieval failed (non-fatal): %s", e)

    # --- Expand via relationships ---
    merged = _expand_via_relationships(merged, model_map, rel_map, max_tables)

    return merged[:max_tables]


def format_schema_context(tables: list[dict[str, Any]]) -> str:
    """将相关表格式化为 LLM 可读的 schema 上下文。"""
    if not tables:
        return ""

    lines = ["可用的数据库表结构：", ""]
    for table in tables:
        lines.append(f"表名: {table['name']}")
        if table.get("description"):
            lines.append(f"说明: {table['description']}")
        lines.append("字段:")
        for col in table.get("columns", []):
            nullable = "NULL" if col.get("nullable") else "NOT NULL"
            primary = " [主键]" if col.get("primary") else ""
            comment = f" — {col['comment']}" if col.get("comment") else ""
            lines.append(f"  - {col.get('name', '?')} ({col.get('type', 'unknown')}) {nullable}{primary}{comment}")

        if table.get("relationships"):
            lines.append("关联:")
            for rel in table["relationships"]:
                lines.append(
                    f"  - {rel['column']} -> {rel['referenced_table']}.{rel['referenced_column']}"
                )
        lines.append("")

    return "\n".join(lines)


def _score_column(col: dict, keywords: set[str], expanded_keywords: set[str]) -> float:
    """Score a single column's relevance to the user's question.

    Higher score = more relevant. Scoring rules:
    - Exact column name match: +10
    - Column name contains keyword: +5
    - Column comment/alias contains keyword: +3
    - Synonym match on comment/alias: +4
    - Fuzzy similarity > threshold: +2

    Key improvement: synonyms from Chinese keywords are checked against
    English column names (e.g. "支付" → "payment" matches "payment_method").
    """
    col_name = (col.get("name") or "").lower()
    col_comment = (col.get("comment") or "").lower()
    col_alias = (col.get("alias") or "").lower()
    col_text = f"{col_name} {col_comment} {col_alias}"

    score = 0.0
    for kw in expanded_keywords:
        # Exact match on column name
        if kw == col_name:
            score += 10
        # Substring match on column name
        elif kw in col_name:
            score += 5
        # Match on comment or alias (semantic layer)
        elif kw in col_comment or kw in col_alias:
            score += 3
        # Bidirectional synonym match: if keyword's synonyms appear
        # in the column name (Chinese kw → English col names)
        elif any(kw in syn_list for syn_list in SYNONYM_MAP.values()
                 if any(s in col_text for s in syn_list)):
            score += 4
        # Fuzzy match
        elif similarity(kw, col_name) > 0.5:
            score += 2
        elif col_comment and similarity(kw, col_comment) > 0.5:
            score += 2

    # Bonus: check if any synonym of Chinese keywords matches the column name
    # This handles cases like "退款" → "refund"/"cancelled" matching status column
    for kw in keywords:
        for standard, synonyms in SYNONYM_MAP.items():
            if kw == standard or kw in synonyms:
                for syn in synonyms:
                    if syn in col_name and syn != kw:
                        score += 6  # Higher than simple substring match
                break

    return score


def _is_protected_column(col: dict) -> bool:
    """Check if a column must always be kept regardless of relevance score.

    Protected columns:
    - Primary key columns
    - Foreign key columns (column_key in FK, MUL)
    - Columns with user-provided alias or comment (semantic layer)
    - Common business-critical columns: status, name, type, method, etc.
    """
    if col.get("primary"):
        return True
    if col.get("column_key") in ("PRI", "FK", "MUL"):
        return True
    # Semantic layer columns: user explicitly added alias or comment
    if col.get("alias") or col.get("comment"):
        return True
    # Common business-critical columns used in WHERE/GROUP BY frequently
    protected_names = frozenset({
        "status", "type", "name", "method", "code", "created_at",
        "updated_at", "amount", "price", "quantity", "total",
    })
    if col.get("name", "").lower() in protected_names:
        return True
    return False


def prune_columns(
    tables: list[dict[str, Any]],
    question: str,
    max_columns: int = 10,
) -> list[dict[str, Any]]:
    """Stage 2 retrieval: prune columns per table based on question relevance.

    For each table returned by Stage 1 (resolve_tables), this function:
    1. Scores each column by relevance to the user's question
    2. Always keeps protected columns (PKs, FKs, semantic layer columns)
    3. Keeps top-scoring columns up to max_columns
    4. Drops columns with zero semantic relevance

    Returns: list[dict] with the same structure as input, but with pruned columns.
    """
    if not tables:
        return []

    keywords = extract_keywords(question)
    expanded_keywords = _expand_keywords(keywords)

    result = []
    for table in tables:
        columns = table.get("columns", [])
        if not columns:
            result.append(table)
            continue

        # Separate protected vs scored columns
        protected = []
        scored = []
        for col in columns:
            if _is_protected_column(col):
                protected.append(col)
            else:
                s = _score_column(col, set(keywords), expanded_keywords)
                if s > 0:
                    scored.append((s, col))

        # Sort scored columns by relevance (descending)
        scored.sort(key=lambda x: -x[0])

        # Calculate budget: max_columns minus protected columns
        protected_names = {c.get("name") for c in protected}
        budget = max(0, max_columns - len(protected))

        # Take top scored columns within budget, avoiding duplicates with protected
        selected_scored = []
        for s, col in scored:
            if len(selected_scored) >= budget:
                break
            if col.get("name") not in protected_names:
                selected_scored.append(col)

        # Combine: protected first, then scored
        pruned = protected + selected_scored

        # If pruning removed all columns (no protected, no scored), keep all
        # as a safety fallback — the table was matched for a reason
        if not pruned:
            pruned = columns[:max_columns]

        result.append({
            "name": table["name"],
            "description": table.get("description") or "",
            "columns": pruned,
            "relationships": table.get("relationships") or [],
        })

    return result


async def get_rag_schema(
    question: str,
    metadata_json: str,
    max_tables: int = 5,
    datasource_id: str | None = None,
) -> str:
    """入口函数：从 metadata JSON 中找到相关表并格式化。"""
    try:
        metadata = json.loads(metadata_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse metadata JSON")
        return metadata_json  # fallback to raw metadata

    relevant = await resolve_tables(question, metadata, max_tables, datasource_id)

    # Stage 2: column pruning (two-stage retrieval)
    if settings.rag_pruning_enabled:
        relevant = prune_columns(
            relevant,
            question,
            max_columns=settings.rag_max_columns_per_query,
        )

    return format_schema_context(relevant)

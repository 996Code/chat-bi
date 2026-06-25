"""
T029: SQL 生成 — prompt 分层 + 白名单约束 + data_type 约束 + 校验集成

对标:
  - AEE-001: 生成 SQL + 白名单列 + data_type 约束 + Skills + 历史
  - Claude Code §4: Prompt 分层 (静态 schema/约束 可缓存, 动态 问题/fewshot 分离)
  - RAG-005: data_type 必须注入 (防 SUM on VARCHAR, 数值运算 on DATE)
  - v1 教训 #15: 元数据质量是准确率根本 (schema context 要详尽)

设计:
  - generate_sql: prompt 分层组装 → LLM → 提取 SQL → T030 校验
  - 静态层: schema_context + allowed_columns 约束 + data_type 约束 (可缓存)
  - 动态层: question + fewshot + history (每次变)
  - 生成后立即 T030 三层校验 (校验不过 → error, 不执行)
  - 失败降级: LLM 失败/空响应 → error (不抛)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.sql_validator import ValidationResult, validate_sql

logger = logging.getLogger(__name__)


@dataclass
class GenerateResult:
    """SQL 生成结果。"""
    sql: str = ""
    validation: ValidationResult = field(default_factory=lambda: ValidationResult(ok=False, reason="未生成"))
    error: str | None = None


# ── Prompt 模板 (对标 Claude Code §4 分层) ────────────────────

_SYSTEM_PROMPT = """你是 BI 系统的 SQL 生成器。根据用户问题和数据库 schema 生成 PostgreSQL 查询。

严格规则 (违反则拒绝执行):
1. 只能生成 SELECT 语句, 禁止任何写操作 (INSERT/UPDATE/DELETE/DROP/ALTER)
2. 只能使用下方"允许的列"里列出的列名, 禁止臆造列名
3. 遵守 data_type 约束: 不能对 VARCHAR/TEXT 做数值聚合(SUM/AVG), 不能对 DATE 做数值运算
4. 禁止危险函数: LOAD_FILE/SLEEP/BENCHMARK/INTO OUTFILE
5. 只返回 SQL, 不要解释文字, 不要 markdown 包裹

只返回一条 SQL 语句。"""


def _build_allowed_columns_section(allowed_columns: set[str]) -> str:
    """白名单列约束 (注入 prompt, 防幻觉, 对标 AEE-001)。"""
    cols = ", ".join(sorted(allowed_columns)) if allowed_columns else "(无)"
    return f"【允许的列名】只能使用以下列, 禁止臆造: {cols}"


def _build_datatype_constraint_section(schema_context: str) -> str:
    """data_type 约束提示 (从 schema_context 提取, 对标 RAG-005)。

    schema_context 格式: "orders(id BIGINT, total_amount DECIMAL, status VARCHAR)"
    提示 LLM 注意类型, 防止类型误用。
    """
    if not schema_context:
        return ""
    return f"【schema 与类型约束】\n{schema_context}\n注意: 遵守 data_type, 数值聚合(SUM/AVG)只能用于数值类型列。"


def _build_dynamic_context(
    fewshot: str | None,
    history: str | None,
    question: str,
    skills: str | None = None,
) -> str:
    """构建动态 prompt 段 (每次查询都变)。

    skills: 业务规则文本 (Phase 6 Skills 系统产出, 如 GMV 定义/特殊计算口径)。
    """
    parts = []
    if skills:
        parts.append(f"【业务规则 (Skills)】\n{skills}")
    if fewshot:
        parts.append(f"【参考示例】\n{fewshot}")
    if history:
        parts.append(f"【对话历史】\n{history}")
    parts.append(f"【用户问题】{question}\n\n请生成 SQL:")
    return "\n\n".join(parts)


def _extract_sql(content: str) -> str:
    """从 LLM 响应提取纯 SQL (去掉 markdown 包裹/解释)。"""
    if not content:
        return ""
    # 去 markdown ```sql ... ``` 包裹
    sql = content.strip()
    sql = re.sub(r"^```(?:sql|SQL)?\s*\n?", "", sql)
    sql = re.sub(r"\n?```\s*$", "", sql)
    # 去尾部多余分号和空白
    return sql.strip().rstrip(";").strip()


async def generate_sql(
    question: str,
    schema_context: str,
    allowed_columns: set[str],
    llm_client,
    fewshot_examples: str | None = None,
    history: str | None = None,
    skills: str | None = None,
) -> GenerateResult:
    """生成 SQL (prompt 分层 + 校验集成)。

    Args:
        question: 用户问题 (已 normalize, 剥离可视化)
        schema_context: 检索到的表/列/类型上下文 (Phase 3 retriever 输出)
        allowed_columns: 语义层白名单列 (T030 Layer3 用)
        llm_client: AsyncOpenAI
        fewshot_examples: few-shot 示例文本 (Phase 3 format_fewshot_prompt)
        history: 多轮历史上下文 (Phase 5 State Store)

    Returns:
        GenerateResult — error 非空表示生成/校验失败 (不抛, T025 决定下一步)
    """
    from app.core.config import get_settings
    from app.core.prompt_cache import get_prompt_cache, PromptCache
    from app.core.text_sanitize import sanitize_text
    settings = get_settings()

    # SEC-007: 用户问题进 LLM prompt 前清洗 (NFKC + 去零宽/方向控制字符)
    question = sanitize_text(question)

    # ── Prompt 分层组装 (对标 Claude Code §4) ──
    # 用全局单例 PromptCache: static 段(system_rules)与请求无关可安全复用,
    # dynamic 段(schema/columns/问题)每次覆盖重算, 不会跨数据源泄漏白名单。
    # 对标 §4.4: prefix cache 命中靠 static 段稳定 (跨请求复用)
    cache = get_prompt_cache()
    cache.set_static("system_rules", lambda: _SYSTEM_PROMPT)
    cache.set_dynamic("schema_type", lambda: _build_datatype_constraint_section(schema_context))
    cache.set_dynamic("allowed_cols", lambda: _build_allowed_columns_section(allowed_columns))
    cache.set_dynamic("context", lambda: _build_dynamic_context(fewshot_examples, history, question, skills))

    sections = cache.assemble()
    # assemble 返回 [static..., BOUNDARY, dynamic...]
    # 静态段拼成 system prompt, 动态段拼成 user prompt
    boundary = PromptCache.PROMPT_DYNAMIC_BOUNDARY
    if boundary in sections:
        idx = sections.index(boundary)
        system_content = "\n\n".join(sections[:idx])
        user_content = "\n\n".join(sections[idx + 1:])
    else:
        # 无动态段兜底
        system_content = "\n\n".join(sections) or _SYSTEM_PROMPT
        user_content = question

    # ── LLM 调用 ─────────────────────────────────────────────
    try:
        resp = await llm_client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            max_tokens=1000,
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
        # OBS-002: 记录 token + prompt (请求级累加, T049 trace / T050 dump-prompts)
        from app.core.token_tracker import track_usage
        from app.core.prompt_capture import record_prompt
        track_usage(getattr(resp, "usage", None))
        record_prompt("generate_sql", system_content, user_content, getattr(resp, "usage", None))
    except Exception as e:
        logger.warning("SQL 生成 LLM 调用失败: %s", e)
        return GenerateResult(error=f"LLM 调用失败: {e}")

    # ── 提取 SQL ─────────────────────────────────────────────
    sql = _extract_sql(content)
    if not sql:
        logger.warning("SQL 生成: LLM 返回空内容")
        return GenerateResult(error="LLM 未返回有效 SQL")

    # ── T030 三层校验 (生成后立即校验, 不执行未校验的 SQL) ───
    validation = validate_sql(sql, allowed_columns)
    if not validation.ok:
        logger.warning("SQL 生成校验失败 (layer=%s): %s | SQL=%s",
                       validation.violated_layer, validation.reason, sql[:100])
        return GenerateResult(
            sql=sql,
            validation=validation,
            error=f"SQL 校验失败 ({validation.violated_layer}): {validation.reason}",
        )

    logger.info("SQL 生成成功: %s", sql[:80])
    return GenerateResult(sql=sql, validation=validation)

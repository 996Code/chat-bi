"""
T027: 预思考机制 — 生成前先"想" (选表理由 + 聚合 + 注意事项)

对标:
  - REF-001 (agent-reflection spec): 问题分解 + 选表理由 + 聚合方式 + 陷阱警告
  - AEE-001 step2: 预思考 (generate_sql 之前)
  - Claude Code: 思维链可见性 (emit SSE thinking 事件)

预思考内容 (对标 REF-001):
  1. 选表理由: 为什么选这些表 (基于检索结果)
  2. 聚合方式: SUM/COUNT/AVG 还是 GROUP BY
  3. 注意事项/陷阱: Fan-Trap (扇形陷阱)、多对多 JOIN、维度混淆

设计:
  - think(question, schema_context, retrieved_models) → ThinkingResult
  - LLM 生成结构化思考 (供 SSE thinking 事件 + 辅助 SQL 生成)
  - 失败降级: LLM 失败 → 空思考 (不阻塞, SQL 生成照常)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ThinkingResult:
    """预思考结果。"""
    tables: list[str] = field(default_factory=list)  # 选中的表 + 理由
    aggregation: str = ""  # 聚合方式说明
    caveats: list[str] = field(default_factory=list)  # 注意事项/陷阱
    raw: str = ""  # 原始 LLM 输出
    error: str | None = None


_THINKING_PROMPT = """你是 BI 分析师。在生成 SQL 前, 先分析查询思路。

用户问题: {question}

可用表/列:
{schema}

检索到的候选:
{candidates}

请分析 (只返回 JSON):
{{
  "tables": ["选中的表名 (附一句理由)"],
  "aggregation": "聚合方式说明 (SUM/COUNT/AVG + GROUP BY 维度)",
  "caveats": ["注意事项 (Fan-Trap/多对多JOIN/维度混淆等陷阱)"]
}}"""


async def think(
    question: str,
    schema_context: str,
    retrieved_models: list[dict],
    llm_client,
) -> ThinkingResult:
    """预思考: 选表理由 + 聚合 + 陷阱 (对标 REF-001)。

    失败降级: LLM 失败/解析失败 → 空 ThinkingResult (不阻塞 SQL 生成)。
    """
    from app.core.config import get_settings
    settings = get_settings()

    candidates = "\n".join(
        f"- {m.get('name', '')} (score={m.get('score', 0):.2f})"
        for m in retrieved_models[:5]
    ) or "(无候选)"

    prompt = _THINKING_PROMPT.format(
        question=question,
        schema=schema_context or "(无)",
        candidates=candidates,
    )

    try:
        resp = await llm_client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning("预思考 LLM 失败, 跳过: %s", e)
        return ThinkingResult(error=str(e))

    try:
        parsed = json.loads(content)
        return ThinkingResult(
            tables=parsed.get("tables", []),
            aggregation=parsed.get("aggregation", ""),
            caveats=parsed.get("caveats", []),
            raw=content,
        )
    except json.JSONDecodeError:
        logger.warning("预思考返回非 JSON, 降级为空")
        return ThinkingResult(raw=content, error="LLM 返回非 JSON")

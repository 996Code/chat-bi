"""LLM 生成推荐查询问题：根据数据模型表结构，让 LLM 生成用户可能想问的自然语言问题。"""
import json
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def generate_suggested_questions(models: list[dict], relationships: list[dict] | None = None, metrics: list[dict] | None = None) -> list[str]:
    """根据数据模型生成推荐查询问题。

    Args:
        models: 数据模型中的表列表，每个表包含 name, comment, columns 等
        relationships: 表之间的关联关系
        metrics: 预定义指标

    Returns:
        推荐问题列表（最多 8 个）
    """
    try:
        from app.ai.nodes.shared_utils import get_llm, LLM_NO_THINKING
    except ImportError:
        logger.warning("langchain_openai not available, using fallback questions")
        return _fallback_questions(models)

    # 构建表结构描述（包含实际表名和字段名，确保 LLM 生成的问题能匹配真实表）
    tables_desc = []
    for model in models[:10]:
        cols = []
        for col in model.get("columns", [])[:15]:
            col_info = col.get("name", "")
            if col.get("comment"):
                col_info += f"({col['comment']})"
            cols.append(col_info)
        desc = model.get("description", "") or model.get("comment", "") or ""
        table_line = f"- 表名: {model.get('name', '?')}" + (f" (说明: {desc})" if desc else "")
        table_line += f"\n  字段: {', '.join(cols)}"
        tables_desc.append(table_line)

    if relationships:
        tables_desc.append("\n关联关系:")
        for rel in relationships[:8]:
            tables_desc.append(f"- {rel.get('from_table', '')}.{rel.get('from_column', '')} -> {rel.get('to_table', '')}.{rel.get('to_column', '')}")

    if metrics:
        tables_desc.append("\n预定义指标:")
        for m in metrics[:6]:
            tables_desc.append(f"- {m.get('name', '')}: {m.get('expression', '')} ({m.get('description', '')})")

    prompt = f"""根据以下数据库表结构，生成 6-8 个用户最可能想问的自然语言查询问题。

表结构:
{chr(10).join(tables_desc)}

要求:
1. 问题必须是中文，简短自然，像业务人员会问的
2. 覆盖不同类型的查询：计数/汇总/分组/排名/趋势
3. 问题中提到的概念必须能对应到上面的表名和字段名（不要引用不存在的表或字段）
4. 不要加编号，每行一个问题
5. 只输出问题列表，不要其他内容
6. 避免生成需要多表 JOIN 的复杂问题，优先单表查询"""

    try:
        llm = get_llm(max_tokens=500, temperature=0.7)
        response = await llm.ainvoke(prompt)
        text = response.content.strip()
        questions = [q.strip().lstrip("0123456789.-) ") for q in text.split("\n") if q.strip()]
        return questions[:8]
    except Exception as e:
        logger.warning("LLM generate suggested questions failed: %s, using fallback", e)
        return _fallback_questions(models)


def _fallback_questions(models: list[dict]) -> list[str]:
    """LLM 不可用时的兜底推荐问题。"""
    questions = []
    for model in models[:4]:
        name = model.get("comment") or model.get("description") or model.get("name", "")
        if name:
            questions.append(f"查询{name}的总数")
            questions.append(f"按维度分组查询{name}")
    return questions[:8]

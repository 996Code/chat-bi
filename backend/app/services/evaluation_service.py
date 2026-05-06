"""Evaluation system for Text-to-SQL accuracy."""
import json
import uuid
import time
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.logging import get_logger

logger = get_logger(__name__)


async def run_evaluation(
    db: AsyncSession,
    tenant_id: str,
    datasource_id: str,
    dataset: list[dict],
) -> dict[str, Any]:
    """Run evaluation against a dataset of question→expected_sql pairs.

    Each item: {"question": "...", "expected_sql": "...", "expected_columns": [...]}
    """
    from app.ai.graph import build_graph

    results = []
    correct = 0
    sql_correct = 0

    for item in dataset:
        question = item["question"]
        expected_sql = item.get("expected_sql", "")
        expected_columns = item.get("expected_columns", [])

        start = time.monotonic()
        try:
            graph = build_graph()
            state = await graph.ainvoke({
                "question": question,
                "datasource_id": datasource_id,
                "tenant_id": tenant_id,
            })

            elapsed_ms = int((time.monotonic() - start) * 1000)
            generated_sql = state.get("sql", "")
            success = state.get("success", False)

            # SQL similarity check (normalize whitespace + compare)
            sql_match = False
            if expected_sql and generated_sql:
                norm_gen = " ".join(generated_sql.lower().split())
                norm_exp = " ".join(expected_sql.lower().split())
                sql_match = norm_gen == norm_exp

            # Column accuracy
            gen_columns = state.get("columns", [])
            col_match = False
            if expected_columns and gen_columns:
                col_match = set(gen_columns) == set(expected_columns)

            if sql_match or (success and col_match):
                correct += 1
            if sql_match:
                sql_correct += 1

            results.append({
                "question": question,
                "success": success,
                "generated_sql": generated_sql[:200],
                "sql_match": sql_match,
                "columns_match": col_match,
                "execution_time_ms": elapsed_ms,
            })

        except Exception as e:
            results.append({
                "question": question,
                "success": False,
                "error": str(e),
            })

    total = len(dataset)
    return {
        "total": total,
        "correct": correct,
        "sql_correct": sql_correct,
        "accuracy": round(correct / total * 100, 1) if total > 0 else 0,
        "sql_accuracy": round(sql_correct / total * 100, 1) if total > 0 else 0,
        "results": results,
    }

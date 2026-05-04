"""Shared AI pipeline utilities — decoupled helpers used by multiple nodes."""
import json


def append_all_table_names(schema_context: str, raw_metadata: str) -> str:
    """在 schema_context 末尾附加完整表名列表，防止 LLM 捏造表名。"""
    try:
        metadata = json.loads(raw_metadata)
        all_tables = [m["name"] for m in metadata.get("models", []) if m.get("name")]
    except (json.JSONDecodeError, TypeError, KeyError):
        return schema_context

    if not all_tables:
        return schema_context

    table_list = ", ".join(all_tables)
    footer = (
        f"\n注意：以下是数据库中所有可用的表名，"
        f"SQL 中引用的表名必须严格使用以下名称之一：\n"
        f"可用表名: {table_list}"
    )
    return schema_context + footer

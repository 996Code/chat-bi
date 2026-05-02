import json
import uuid
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.encryption import decrypt_value
from app.core.logging import get_logger
from app.db.models import DataSource, MetadataConfig

logger = get_logger(__name__)


async def scan_mysql_schema(engine: AsyncEngine, db: AsyncSession, ds: DataSource) -> dict:
    """Scan MySQL INFORMATION_SCHEMA and save metadata as JSON."""
    async with engine.connect() as conn:
        # Get tables
        tables_result = await conn.execute(text("""
            SELECT TABLE_NAME, TABLE_COMMENT
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE'
        """), {"db": ds.database_name})
        tables = tables_result.fetchall()

        # Get columns for each table
        columns_result = await conn.execute(text("""
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
                   COLUMN_KEY, COLUMN_COMMENT, DATA_TYPE, ORDINAL_POSITION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :db
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """), {"db": ds.database_name})
        columns = columns_result.fetchall()

        # Get foreign keys
        fk_result = await conn.execute(text("""
            SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = :db AND REFERENCED_TABLE_NAME IS NOT NULL
        """), {"db": ds.database_name})
        fks = fk_result.fetchall()

    # Build column index
    col_map: dict[str, list[dict]] = {}
    for row in columns:
        tname = row[0]
        col_map.setdefault(tname, []).append({
            "name": row[1],
            "column": row[1],
            "type": row[2],
            "nullable": row[3] == "YES",
            "primary": row[4] == "PRI",
            "comment": row[5] or "",
            "data_type": row[6],
        })

    # Build relationship index
    rel_map: dict[str, list[dict]] = {}
    for row in fks:
        tname = row[0]
        rel_map.setdefault(tname, []).append({
            "column": row[1],
            "referenced_table": row[2],
            "referenced_column": row[3],
        })

    # Build MDL-style metadata
    models = []
    for table_name, table_comment in tables:
        model = {
            "name": table_name,
            "table": table_name,
            "description": table_comment or "",
            "columns": col_map.get(table_name, []),
            "relationships": rel_map.get(table_name, []),
        }
        models.append(model)

    metadata = {
        "version": "1.0",
        "database": {"type": "mysql", "name": ds.database_name},
        "models": models,
    }

    # Save to metadata_configs (new version)
    import json
    config = MetadataConfig(
        tenant_id=ds.tenant_id,
        datasource_id=ds.id,
        config=json.dumps(metadata, ensure_ascii=False),
    )
    db.add(config)
    await db.commit()

    logger.info(f"Scanned {len(models)} tables for datasource {ds.name}")
    return {"tables": [m["name"] for m in models], "total_tables": len(models)}

import json
import uuid
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.encryption import decrypt_value
from app.core.logging import get_logger
from app.db.models import DataSource, MetadataConfig

logger = get_logger(__name__)


async def scan_schema_raw(engine: AsyncEngine, ds: DataSource) -> dict:
    """Unified schema scanner — dispatches to MySQL or PostgreSQL scanner."""
    if ds.db_type == "postgresql":
        return await _scan_postgres_schema_raw(engine, ds)
    return await scan_mysql_schema_raw(engine)


async def _scan_postgres_schema_raw(engine: AsyncEngine, ds: DataSource) -> dict:
    """Scan PostgreSQL schema without saving — for incremental diff comparison."""
    async with engine.connect() as conn:
        tables_result = await conn.execute(text("""
            SELECT tablename, obj_description((schemaname || '.' || tablename)::regclass, 'pg_class') as comment
            FROM pg_tables
            WHERE schemaname = 'public'
        """))
        tables = tables_result.fetchall()

        columns_result = await conn.execute(text("""
            SELECT table_name, column_name, data_type, is_nullable,
                   (SELECT 'PRI' FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attname = c.column_name
                    WHERE i.indrelid = (table_schema || '.' || table_name)::regclass AND i.indisprimary)
            FROM information_schema.columns c
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """))
        columns = columns_result.fetchall()

        fk_result = await conn.execute(text("""
            SELECT
                tc.table_name, kcu.column_name,
                ccu.table_name AS referenced_table,
                ccu.column_name AS referenced_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
                AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_schema = 'public'
        """))
        fks = fk_result.fetchall()

    col_map: dict[str, list[dict]] = {}
    for row in columns:
        tname = row[0]
        col_map.setdefault(tname, []).append({
            "name": row[1],
            "column": row[1],
            "type": row[2],
            "nullable": row[3] == "YES",
            "primary": row[4] == "PRI",
            "comment": "",
            "data_type": row[2],
        })

    rel_map: dict[str, list[dict]] = {}
    for row in fks:
        tname = row[0]
        rel_map.setdefault(tname, []).append({
            "column": row[1],
            "referenced_table": row[2],
            "referenced_column": row[3],
        })

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

    return {
        "version": "1.0",
        "database": {"type": "postgresql", "name": ds.database_name},
        "models": models,
    }


async def scan_mysql_schema_raw(engine: AsyncEngine) -> dict:
    """Scan schema without saving — for incremental diff comparison."""
    async with engine.connect() as conn:
        tables_result = await conn.execute(text("""
            SELECT TABLE_NAME, TABLE_COMMENT
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
        """))
        tables = tables_result.fetchall()

        columns_result = await conn.execute(text("""
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
                   COLUMN_KEY, COLUMN_COMMENT, DATA_TYPE, ORDINAL_POSITION
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """))
        columns = columns_result.fetchall()

        fk_result = await conn.execute(text("""
            SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
        """))
        fks = fk_result.fetchall()

        # Get database name
        db_name_result = await conn.execute(text("SELECT DATABASE()"))
        db_name = db_name_result.scalar()

    # Build structures
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

    rel_map: dict[str, list[dict]] = {}
    for row in fks:
        tname = row[0]
        rel_map.setdefault(tname, []).append({
            "column": row[1],
            "referenced_table": row[2],
            "referenced_column": row[3],
        })

    models = []
    model_map: dict[str, dict] = {}
    for table_name, table_comment in tables:
        model = {
            "name": table_name,
            "table": table_name,
            "description": table_comment or "",
            "columns": col_map.get(table_name, []),
            "relationships": rel_map.get(table_name, []),
        }
        models.append(model)
        model_map[table_name] = model

    return {
        "version": "1.0",
        "database": {"type": "mysql", "name": db_name or ""},
        "models": models,
        "model_map": model_map,
    }


async def scan_mysql_schema(engine: AsyncEngine, db: AsyncSession, ds: DataSource) -> dict:
    """Scan database INFORMATION_SCHEMA and save metadata as JSON. Supports MySQL and PostgreSQL."""
    async with engine.connect() as conn:
        if ds.db_type == "postgresql":
            return await _scan_postgres_schema(engine, db, ds)

        # MySQL scan (existing logic)
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

    # Upsert: update existing config or create new
    existing = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == ds.id,
            MetadataConfig.tenant_id == ds.tenant_id,
        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
    )
    existing_config = existing.scalar_one_or_none()
    if existing_config:
        existing_config.config = json.dumps(metadata, ensure_ascii=False)
    else:
        config = MetadataConfig(
            tenant_id=ds.tenant_id,
            datasource_id=ds.id,
            config=json.dumps(metadata, ensure_ascii=False),
        )
        db.add(config)
    await db.commit()

    logger.info(f"Scanned {len(models)} tables for datasource {ds.name}")
    return {"tables": [m["name"] for m in models], "total_tables": len(models)}


async def _scan_postgres_schema(engine: AsyncEngine, db: AsyncSession, ds: DataSource) -> dict:
    """Scan PostgreSQL information_schema and save metadata as JSON."""
    async with engine.connect() as conn:
        # Get tables
        tables_result = await conn.execute(text("""
            SELECT tablename, obj_description((schemaname || '.' || tablename)::regclass, 'pg_class') as comment
            FROM pg_tables
            WHERE schemaname = 'public'
        """))
        tables = tables_result.fetchall()

        # Get columns
        columns_result = await conn.execute(text("""
            SELECT table_name, column_name, data_type, is_nullable,
                   (SELECT 'PRI' FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attname = c.column_name
                    WHERE i.indrelid = (table_schema || '.' || table_name)::regclass AND i.indisprimary)
            FROM information_schema.columns c
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """))
        columns = columns_result.fetchall()

        # Get foreign keys
        fk_result = await conn.execute(text("""
            SELECT
                tc.table_name, kcu.column_name,
                ccu.table_name AS referenced_table,
                ccu.column_name AS referenced_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
                AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_schema = 'public'
        """))
        fks = fk_result.fetchall()

    # Build structures
    col_map: dict[str, list[dict]] = {}
    for row in columns:
        tname = row[0]
        col_map.setdefault(tname, []).append({
            "name": row[1],
            "column": row[1],
            "type": row[2],
            "nullable": row[3] == "YES",
            "primary": row[4] == "PRI",
            "comment": "",
            "data_type": row[2],
        })

    rel_map: dict[str, list[dict]] = {}
    for row in fks:
        tname = row[0]
        rel_map.setdefault(tname, []).append({
            "column": row[1],
            "referenced_table": row[2],
            "referenced_column": row[3],
        })

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
        "database": {"type": "postgresql", "name": ds.database_name},
        "models": models,
    }

    # Upsert: update existing config or create new
    existing = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == ds.id,
            MetadataConfig.tenant_id == ds.tenant_id,
        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
    )
    existing_config = existing.scalar_one_or_none()
    if existing_config:
        existing_config.config = json.dumps(metadata, ensure_ascii=False)
    else:
        config = MetadataConfig(
            tenant_id=ds.tenant_id,
            datasource_id=ds.id,
            config=json.dumps(metadata, ensure_ascii=False),
        )
        db.add(config)
    await db.commit()

    logger.info(f"Scanned {len(models)} PostgreSQL tables for datasource {ds.name}")
    return {"tables": [m["name"] for m in models], "total_tables": len(models)}

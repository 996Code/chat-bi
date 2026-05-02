import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import DataSource, MetadataConfig
from app.core.security import get_current_user
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/data-models", tags=["数据模型"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.get("")
async def list_data_models(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前租户所有数据模型（按数据源分组）。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(MetadataConfig)
        .where(MetadataConfig.tenant_id == uuid.UUID(tenant_id))
        .order_by(MetadataConfig.updated_at.desc())
    )
    configs = result.scalars().all()

    # Enrich with datasource name
    ds_ids = [str(c.datasource_id) for c in configs]
    ds_result = await db.execute(select(DataSource).where(DataSource.id.in_(ds_ids)))
    ds_map = {str(ds.id): ds.name for ds in ds_result.scalars().all()}

    return [
        {
            "id": str(c.id),
            "datasource_id": str(c.datasource_id),
            "datasource_name": ds_map.get(str(c.datasource_id), "未知"),
            "tables_count": len(json.loads(c.config).get("models", [])),
            "has_relationships": bool(json.loads(c.config).get("relationships", [])),
            "has_metrics": bool(json.loads(c.config).get("metrics", [])),
            "updated_at": str(c.updated_at),
        }
        for c in configs
    ]


@router.get("/{ds_id}")
async def get_data_model(
    ds_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取指定数据源的语义层模型配置。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == uuid.UUID(ds_id),
            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "该数据源尚未配置数据模型"),
        )
    return {
        "id": str(config.id),
        "datasource_id": str(config.datasource_id),
        "config": json.loads(config.config),
        "updated_at": str(config.updated_at),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_data_model(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    为数据源创建语义层模型配置。
    请求体格式：{ "datasource_id": "...", "config": { "models": [...], "relationships": [...], "metrics": [...] } }
    """
    tenant_id = user["tenant_id"]
    ds_id = data.get("datasource_id")
    config_content = data.get("config")

    if not ds_id or not config_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "需要 datasource_id 和 config 字段"),
        )

    # Verify datasource belongs to tenant
    result = await db.execute(
        select(DataSource).where(
            DataSource.id == uuid.UUID(ds_id),
            DataSource.tenant_id == uuid.UUID(tenant_id),
        )
    )
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )

    # Check if already exists
    existing = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == uuid.UUID(ds_id),
            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error("ALREADY_EXISTS", "该数据源已存在模型配置，请使用 PUT 更新"),
        )

    config = MetadataConfig(
        tenant_id=uuid.UUID(tenant_id),
        datasource_id=uuid.UUID(ds_id),
        config=json.dumps(config_content, ensure_ascii=False),
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)

    return {
        "id": str(config.id),
        "datasource_id": str(config.datasource_id),
        "config": json.loads(config.config),
        "updated_at": str(config.updated_at),
    }


@router.put("/{ds_id}")
async def update_data_model(
    ds_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新指定数据源的语义层模型配置。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(MetadataConfig.id, MetadataConfig.config).where(
            MetadataConfig.datasource_id == uuid.UUID(ds_id),
            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "该数据源尚未配置数据模型"),
        )

    config_id = row.id
    if "config" in data:
        new_config = json.dumps(data["config"], ensure_ascii=False)
        await db.execute(
            text("UPDATE metadata_configs SET config = :config, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"config": new_config, "id": str(config_id)},
        )
        await db.commit()
    else:
        new_config = row.config

    return {
        "id": str(config_id),
        "datasource_id": ds_id,
        "config": json.loads(new_config),
        "updated_at": None,  # will be set on next GET
    }


@router.delete("/{ds_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_model(
    ds_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除指定数据源的语义层模型配置。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == uuid.UUID(ds_id),
            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "该数据源尚未配置数据模型"),
        )
    await db.delete(config)
    await db.commit()
    return None


@router.post("/{ds_id}/sync")
async def sync_data_model(
    ds_id: str,
    data: dict = {},
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    同步数据库表结构到语义层模型。

    mode: "full" (全量覆盖) | "incremental" (增量合并，默认)

    增量同步逻辑：
    1. 扫描当前数据库 schema
    2. 对比已有模型
    3. 新增表 → 添加
    4. 删除表 → 标记 inactive（不物理删除，保留用户自定义的别名/描述）
    5. 表结构变更 → 更新字段列表，保留用户自定义的 alias/comment
    """
    from app.services.mysql_schema_scanner import scan_mysql_schema, scan_mysql_schema_raw
    from app.services.connection_pool import pool_manager
    from app.services.datasource_service import DataSourceService
    import hashlib

    tenant_id = user["tenant_id"]
    mode = data.get("mode", "incremental")

    service = DataSourceService(db, tenant_id=tenant_id)
    ds = await service.get_by_id(ds_id)
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "数据源不存在"),
        )

    # Test connection
    test_result = await service.test_connection(ds_id)
    if not test_result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("CONNECTION_FAILED", test_result.get("error", "连接失败")),
        )

    # Scan current schema
    engine = await pool_manager.get_pool(ds)
    raw_schema = await scan_mysql_schema_raw(engine)

    if mode == "full":
        # Full sync: replace everything
        scan_result = await scan_mysql_schema(engine, db, ds)
        return {"mode": "full", **scan_result}

    # Incremental sync: diff and merge
    result = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == uuid.UUID(ds_id),
            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
    )
    existing_config = result.scalar_one_or_none()

    existing_models = {}
    if existing_config:
        existing_data = json.loads(existing_config.config)
        for model in existing_data.get("models", []):
            existing_models[model["name"]] = model

    new_table_names = {m["name"] for m in raw_schema["models"]}
    existing_table_names = set(existing_models.keys())

    added = new_table_names - existing_table_names
    removed = existing_table_names - new_table_names
    changed = set()

    for tname in new_table_names & existing_table_names:
        new_cols = {c["name"] for c in raw_schema["model_map"][tname]["columns"]}
        old_cols = {c["name"] for c in existing_models[tname].get("columns", [])}
        if new_cols != old_cols:
            changed.add(tname)

    if not added and not removed and not changed:
        return {"mode": "incremental", "status": "up_to_date", "added": [], "removed": [], "changed": []}

    # Merge: start from existing, apply diffs
    merged_models = []
    for model in raw_schema["models"]:
        tname = model["name"]
        if tname in existing_models:
            # Merge: keep user customizations (alias, description, column aliases)
            existing = existing_models[tname]
            merged = _merge_model(existing, model)
            merged_models.append(merged)
        else:
            # New table: use scanned data as-is
            merged_models.append(model)

    # Keep removed tables as inactive (preserve user work)
    for tname in removed:
        existing = existing_models[tname]
        existing["_deleted"] = True
        merged_models.append(existing)

    merged_metadata = {
        "version": "1.1",
        "database": raw_schema["database"],
        "models": merged_models,
        "relationships": existing_data.get("relationships", []) if existing_config else [],
        "metrics": existing_data.get("metrics", []) if existing_config else [],
    }

    if existing_config:
        existing_config.config = json.dumps(merged_metadata, ensure_ascii=False)
    else:
        config = MetadataConfig(
            tenant_id=uuid.UUID(tenant_id),
            datasource_id=uuid.UUID(ds_id),
            config=json.dumps(merged_metadata, ensure_ascii=False),
        )
        db.add(config)

    await db.commit()

    return {
        "mode": "incremental",
        "status": "synced",
        "added": list(added),
        "removed": list(removed),
        "changed": list(changed),
        "total_tables": len(merged_models),
    }


def _merge_model(existing: dict, scanned: dict) -> dict:
    """Merge scanned model into existing model, preserving user customizations."""
    merged = {**scanned}
    # Preserve user-set fields
    if existing.get("alias"):
        merged["alias"] = existing["alias"]
    if existing.get("description") and existing["description"] != scanned.get("description", ""):
        merged["description"] = existing["description"]

    # Merge columns: preserve user customizations per column
    existing_col_map = {c["name"]: c for c in existing.get("columns", [])}
    merged_columns = []
    for col in scanned.get("columns", []):
        merged_col = {**col}
        if col["name"] in existing_col_map:
            existing_col = existing_col_map[col["name"]]
            if existing_col.get("alias"):
                merged_col["alias"] = existing_col["alias"]
            if existing_col.get("comment") and existing_col.get("comment") != col.get("comment", ""):
                merged_col["comment"] = existing_col["comment"]
        merged_columns.append(merged_col)
    merged["columns"] = merged_columns

    return merged

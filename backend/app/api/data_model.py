import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession


def _iso(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

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
            "updated_at": _iso(c.updated_at),
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
        "updated_at": _iso(config.updated_at),
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
        "updated_at": _iso(config.updated_at),
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

    # Clean up Chroma collection
    try:
        from app.services.chroma_service import get_chroma_service
        chroma_svc = get_chroma_service()
        if chroma_svc:
            chroma_svc.delete_collection(ds_id)
    except Exception as e:
        logger.warning("Chroma cleanup failed (non-fatal): %s", e)

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
    from app.services.mysql_schema_scanner import scan_schema_raw, scan_mysql_schema
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
    raw_schema = await scan_schema_raw(engine, ds)

    if mode == "full":
        # Full sync: replace everything
        scan_result = await scan_mysql_schema(engine, db, ds)

        # Re-read the saved config to add inferred relationships/metrics/suggestions
        result = await db.execute(
            select(MetadataConfig).where(
                MetadataConfig.datasource_id == uuid.UUID(ds_id),
                MetadataConfig.tenant_id == uuid.UUID(tenant_id),
            ).order_by(MetadataConfig.updated_at.desc()).limit(1)
        )
        full_config = result.scalar_one_or_none()
        if full_config:
            config_data = json.loads(full_config.config)
            models = config_data.get("models", [])

            # Infer relationships and metrics (LLM first, code rules fallback)
            from app.services.inference_service import infer_relationships, infer_metrics
            inferred_rels = await infer_relationships(models)
            inferred_metrics = await infer_metrics(models, inferred_rels)

            config_data["relationships"] = inferred_rels
            config_data["metrics"] = inferred_metrics
            config_data["version"] = "1.1"

            # Move per-model relationships to top-level if they exist
            for model in models:
                model.pop("relationships", None)

            full_config.config = json.dumps(config_data, ensure_ascii=False)
            await db.commit()

            # Refresh Chroma
            try:
                from app.services.chroma_service import get_chroma_service
                chroma_svc = get_chroma_service()
                if chroma_svc:
                    chroma_svc.refresh_collection(ds_id, models)
            except Exception as e:
                logger.warning("Chroma refresh failed (non-fatal): %s", e)

            # Generate suggested questions
            try:
                from app.services.suggested_questions_service import generate_suggested_questions
                suggested = await generate_suggested_questions(
                    models, inferred_rels, inferred_metrics,
                )
                config_data["suggested_questions"] = suggested
                full_config.config = json.dumps(config_data, ensure_ascii=False)
                await db.commit()
            except Exception as e:
                logger.warning("Suggested questions generation failed (non-fatal): %s", e)

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

    # Infer relationships and metrics for first sync (LLM first, code rules fallback)
    from app.services.inference_service import infer_relationships, infer_metrics
    if existing_config:
        # Keep existing user-customized relationships and metrics
        inferred_rels = existing_data.get("relationships", [])
        inferred_metrics = existing_data.get("metrics", [])
    else:
        # First sync: use LLM inference
        active_models_for_inference = [m for m in merged_models if not m.get("_deleted")]
        inferred_rels = await infer_relationships(active_models_for_inference)
        inferred_metrics = await infer_metrics(active_models_for_inference, inferred_rels)

    merged_metadata = {
        "version": "1.1",
        "database": raw_schema["database"],
        "models": merged_models,
        "relationships": inferred_rels,
        "metrics": inferred_metrics,
        "suggested_questions": existing_data.get("suggested_questions", []) if existing_config else [],
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

    # Refresh Chroma vector store with synced schema
    try:
        from app.services.chroma_service import get_chroma_service
        chroma_svc = get_chroma_service()
        if chroma_svc:
            active_models = [m for m in merged_models if not m.get("_deleted")]
            chroma_svc.refresh_collection(ds_id, active_models)
    except Exception as e:
        logger.warning("Chroma refresh failed (non-fatal): %s", e)

    # Generate suggested questions via LLM
    try:
        from app.services.suggested_questions_service import generate_suggested_questions
        active_models_for_questions = [m for m in merged_models if not m.get("_deleted")]
        suggested = await generate_suggested_questions(
            active_models_for_questions,
            merged_metadata.get("relationships", []),
            merged_metadata.get("metrics", []),
        )
        merged_metadata["suggested_questions"] = suggested
        # Update config with suggested questions
        if existing_config:
            existing_config.config = json.dumps(merged_metadata, ensure_ascii=False)
        else:
            # Find the newly created config and update it
            result = await db.execute(
                select(MetadataConfig).where(
                    MetadataConfig.datasource_id == uuid.UUID(ds_id),
                    MetadataConfig.tenant_id == uuid.UUID(tenant_id),
                ).order_by(MetadataConfig.updated_at.desc()).limit(1)
            )
            new_config = result.scalar_one_or_none()
            if new_config:
                new_config.config = json.dumps(merged_metadata, ensure_ascii=False)
        await db.commit()
        logger.info("Generated %d suggested questions for datasource %s", len(suggested), ds_id)
    except Exception as e:
        logger.warning("Suggested questions generation failed (non-fatal): %s", e)

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


def _infer_relationships(models: list[dict]) -> list[dict]:
    """根据表名和字段名自动推断外键关联关系。

    规则：
    1. 如果表 A 有字段 `xxx_id`，且存在表 `xxx`（或各种变体）有 `id` 字段，则推断 A.xxx_id -> xxx.id
    2. 变体匹配：xxx → xxx, xxxs, xxxes, t_xxx, t_xxxs, t_xxxes, xxx去掉末尾y+ies 等
    3. 反向匹配：如果表名是 t_users，字段 user_id 的 xxx 是 user，去掉 t_ 前缀后匹配
    """
    table_map = {}
    for m in models:
        name = m.get("name", "")
        if name and not m.get("_deleted"):
            table_map[name.lower()] = m

    def _find_matching_table(ref_name: str, current_table: str) -> str | None:
        """Try various naming conventions to find the referenced table."""
        candidates = []
        base = ref_name.lower()

        # Direct: user → user
        candidates.append(base)
        # Plural: user → users
        candidates.append(base + "s")
        # -es plural: category → categories (y → ies)
        if base.endswith("y") and len(base) > 1 and base[-2] not in "aeiou":
            candidates.append(base[:-1] + "ies")
        else:
            candidates.append(base.rstrip("e") + "es")
        # With t_ prefix: user → t_user, t_users
        candidates.append("t_" + base)
        candidates.append("t_" + base + "s")
        # Without t_ prefix: if ref_name starts with t_, try without
        if base.startswith("t_"):
            stripped = base[2:]
            candidates.append(stripped)
            candidates.append(stripped + "s")

        for candidate in candidates:
            if candidate in table_map and candidate != current_table.lower():
                # Return the actual key from table_map (preserving case)
                for tname in table_map:
                    if tname.lower() == candidate:
                        return tname
        return None

    relationships = []
    seen = set()

    for model in models:
        if model.get("_deleted"):
            continue
        table_name = model.get("name", "")
        for col in model.get("columns", []):
            col_name = col.get("name", "")
            # Match pattern: xxx_id -> table xxx.id
            if col_name.endswith("_id") and col_name != "id":
                ref_table_name = col_name[:-3]  # remove _id suffix
                matched_table = _find_matching_table(ref_table_name, table_name)

                if matched_table:
                    key = f"{table_name}.{col_name}->{matched_table}.id"
                    if key not in seen:
                        seen.add(key)
                        relationships.append({
                            "from_table": table_name,
                            "from_column": col_name,
                            "to_table": matched_table,
                            "to_column": "id",
                        })

    return relationships


def _infer_metrics(models: list[dict]) -> list[dict]:
    """根据表结构自动推断常用指标。

    规则：
    1. 如果表有 amount/total/price 类字段，生成 SUM 指标
    2. 如果表有 id 字段，生成 COUNT 指标
    """
    metrics = []
    seen_names = set()

    for model in models:
        if model.get("_deleted"):
            continue
        table_name = model.get("name", "")
        table_comment = model.get("comment") or model.get("description") or table_name
        columns = model.get("columns", [])
        col_names = {c.get("name", "").lower() for c in columns}

        # COUNT metric
        count_name = f"{table_comment}总数"
        if count_name not in seen_names:
            seen_names.add(count_name)
            metrics.append({
                "name": count_name,
                "expression": f"COUNT({table_name}.id)",
                "description": f"统计{table_comment}的总数",
            })

        # SUM metrics for amount/total/price columns
        for col in columns:
            col_name = col.get("name", "")
            col_lower = col_name.lower()
            col_comment = col.get("comment") or col_name
            if any(kw in col_lower for kw in ["amount", "total", "price", "cost", "fee", "money"]):
                sum_name = f"{table_comment}{col_comment}合计"
                if sum_name not in seen_names:
                    seen_names.add(sum_name)
                    metrics.append({
                        "name": sum_name,
                        "expression": f"SUM({table_name}.{col_name})",
                        "description": f"计算{table_comment}的{col_comment}合计",
                    })

    return metrics[:10]

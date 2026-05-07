import asyncio
import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession


def _iso(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

from app.db.session import get_db
from app.db.models import DataSource, MetadataConfig, MetadataConfigVersion
from app.core.security import get_current_user, require_role
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/data-models", tags=["数据模型"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


# ── Sync task state (Redis-backed) ──

SYNC_TASK_PREFIX = "sync_task:"
SYNC_TASK_TTL = 3600  # 1 hour


async def _create_sync_task(task_id: str, ds_id: str, tenant_id: str, mode: str) -> None:
    from app.core.redis_client import get_redis
    redis = await get_redis()
    task = {
        "task_id": task_id,
        "datasource_id": ds_id,
        "tenant_id": tenant_id,
        "mode": mode,
        "status": "pending",
        "progress": 0,
        "step": "准备中",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis.set(f"{SYNC_TASK_PREFIX}{task_id}", json.dumps(task, ensure_ascii=False), ex=SYNC_TASK_TTL)


async def _update_sync_task(task_id: str, **kwargs) -> None:
    from app.core.redis_client import get_redis
    redis = await get_redis()
    raw = await redis.get(f"{SYNC_TASK_PREFIX}{task_id}")
    if not raw:
        return
    task = json.loads(raw)
    task.update(kwargs)
    await redis.set(f"{SYNC_TASK_PREFIX}{task_id}", json.dumps(task, ensure_ascii=False), ex=SYNC_TASK_TTL)


async def _run_sync_background(task_id: str, ds_id: str, tenant_id: str, mode: str) -> None:
    """Background task that executes the full sync pipeline with progress updates."""
    from app.services.mysql_schema_scanner import scan_schema_raw, scan_mysql_schema
    from app.services.connection_pool import pool_manager
    from app.services.datasource_service import DataSourceService
    from app.db.session import async_session_factory

    try:
        # Step 1: Validate datasource
        await _update_sync_task(task_id, status="running", progress=5, step="验证数据源连接")
        async with async_session_factory() as session:
            service = DataSourceService(session, tenant_id=tenant_id)
            ds = await service.get_by_id(ds_id)
            if not ds:
                await _update_sync_task(task_id, status="failed", error="数据源不存在")
                return

            test_result = await service.test_connection(ds_id)
            if not test_result["success"]:
                await _update_sync_task(task_id, status="failed", error=test_result.get("error", "连接失败"))
                return

        # Step 2: Scan schema
        await _update_sync_task(task_id, progress=10, step="扫描表结构")
        engine = await pool_manager.get_pool(ds)

        if mode == "full":
            async with async_session_factory() as session:
                scan_result = await scan_mysql_schema(engine, session, ds)

            await _update_sync_task(task_id, progress=20, step="扫描完成，开始推断关联关系")

            # Step 3: Infer relationships
            async with async_session_factory() as session:
                result = await session.execute(
                    select(MetadataConfig).where(
                        MetadataConfig.datasource_id == uuid.UUID(ds_id),
                        MetadataConfig.tenant_id == uuid.UUID(tenant_id),
                    ).order_by(MetadataConfig.updated_at.desc()).limit(1)
                )
                full_config = result.scalar_one_or_none()

            if not full_config:
                await _update_sync_task(task_id, status="failed", error="扫描后未找到模型配置")
                return

            config_data = json.loads(full_config.config)
            models = config_data.get("models", [])

            await _update_sync_task(task_id, progress=30, step="推断关联关系（LLM）")
            from app.services.inference_service import infer_relationships, infer_metrics
            inferred_rels = await infer_relationships(models)

            # Step 4: Infer metrics
            await _update_sync_task(task_id, progress=50, step="推断业务指标（LLM）")
            inferred_metrics = await infer_metrics(models, inferred_rels)

            config_data["relationships"] = inferred_rels
            config_data["metrics"] = inferred_metrics
            config_data["version"] = "1.1"

            for model in models:
                model.pop("relationships", None)

            # Step 5: Save inferred results
            await _update_sync_task(task_id, progress=70, step="保存推断结果")
            async with async_session_factory() as session:
                result = await session.execute(
                    select(MetadataConfig).where(
                        MetadataConfig.datasource_id == uuid.UUID(ds_id),
                        MetadataConfig.tenant_id == uuid.UUID(tenant_id),
                    ).order_by(MetadataConfig.updated_at.desc()).limit(1)
                )
                full_config = result.scalar_one_or_none()
                if full_config:
                    full_config.config = json.dumps(config_data, ensure_ascii=False)
                    await session.commit()

            # Step 6: Generate suggested questions
            await _update_sync_task(task_id, progress=80, step="生成推荐问题（LLM）")
            try:
                from app.services.suggested_questions_service import generate_suggested_questions
                suggested = await generate_suggested_questions(models, inferred_rels, inferred_metrics)
                config_data["suggested_questions"] = suggested

                async with async_session_factory() as session:
                    result = await session.execute(
                        select(MetadataConfig).where(
                            MetadataConfig.datasource_id == uuid.UUID(ds_id),
                            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
                        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
                    )
                    full_config = result.scalar_one_or_none()
                    if full_config:
                        full_config.config = json.dumps(config_data, ensure_ascii=False)
                        await session.commit()
            except Exception as e:
                logger.warning("Suggested questions generation failed (non-fatal): %s", e)

            # Clear query cache for this datasource — metadata has changed
            from app.services.cache_service import cache_clear_datasource
            cleared = await cache_clear_datasource(ds_id, tenant_id)
            logger.info("Sync completed for datasource %s: cleared %d cache entries", ds_id, cleared)

            # Done
            await _update_sync_task(
                task_id,
                status="completed",
                progress=100,
                step="同步完成",
                result={
                    "mode": "full",
                    "relationships_count": len(inferred_rels),
                    "metrics_count": len(inferred_metrics),
                    "total_tables": len(models),
                },
            )

        else:  # incremental
            raw_schema = await scan_schema_raw(engine, ds)

            async with async_session_factory() as session:
                result = await session.execute(
                    select(MetadataConfig).where(
                        MetadataConfig.datasource_id == uuid.UUID(ds_id),
                        MetadataConfig.tenant_id == uuid.UUID(tenant_id),
                    ).order_by(MetadataConfig.updated_at.desc()).limit(1)
                )
                existing_config = result.scalar_one_or_none()

            existing_models = {}
            existing_data = {}
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
                await _update_sync_task(
                    task_id,
                    status="completed",
                    progress=100,
                    step="无需更新",
                    result={"mode": "incremental", "status": "up_to_date", "added": [], "removed": [], "changed": [], "total_tables": len(existing_models)},
                )
                return

            merged_models = []
            for model in raw_schema["models"]:
                tname = model["name"]
                if tname in existing_models:
                    merged = _merge_model(existing_models[tname], model)
                    merged_models.append(merged)
                else:
                    merged_models.append(model)

            for tname in removed:
                existing = existing_models[tname]
                existing["_deleted"] = True
                merged_models.append(existing)

            await _update_sync_task(task_id, progress=30, step="推断关联关系（LLM）")
            from app.services.inference_service import infer_relationships, infer_metrics
            if existing_config:
                inferred_rels = existing_data.get("relationships", [])
                inferred_metrics = existing_data.get("metrics", [])
            else:
                active_models_for_inference = [m for m in merged_models if not m.get("_deleted")]
                inferred_rels = await infer_relationships(active_models_for_inference)

                await _update_sync_task(task_id, progress=50, step="推断业务指标（LLM）")
                inferred_metrics = await infer_metrics(active_models_for_inference, inferred_rels)

            merged_metadata = {
                "version": "1.1",
                "database": raw_schema["database"],
                "models": merged_models,
                "relationships": inferred_rels,
                "metrics": inferred_metrics,
                "suggested_questions": existing_data.get("suggested_questions", []) if existing_config else [],
            }

            await _update_sync_task(task_id, progress=70, step="保存结果")
            async with async_session_factory() as session:
                if existing_config:
                    result = await session.execute(
                        select(MetadataConfig).where(
                            MetadataConfig.datasource_id == uuid.UUID(ds_id),
                            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
                        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
                    )
                    ec = result.scalar_one_or_none()
                    if ec:
                        ec.config = json.dumps(merged_metadata, ensure_ascii=False)
                else:
                    config = MetadataConfig(
                        tenant_id=uuid.UUID(tenant_id),
                        datasource_id=uuid.UUID(ds_id),
                        config=json.dumps(merged_metadata, ensure_ascii=False),
                    )
                    session.add(config)
                await session.commit()

            await _update_sync_task(task_id, progress=80, step="生成推荐问题（LLM）")
            try:
                from app.services.suggested_questions_service import generate_suggested_questions
                active_models_for_questions = [m for m in merged_models if not m.get("_deleted")]
                suggested = await generate_suggested_questions(
                    active_models_for_questions,
                    merged_metadata.get("relationships", []),
                    merged_metadata.get("metrics", []),
                )
                merged_metadata["suggested_questions"] = suggested

                async with async_session_factory() as session:
                    result = await session.execute(
                        select(MetadataConfig).where(
                            MetadataConfig.datasource_id == uuid.UUID(ds_id),
                            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
                        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
                    )
                    mc = result.scalar_one_or_none()
                    if mc:
                        mc.config = json.dumps(merged_metadata, ensure_ascii=False)
                    await session.commit()
            except Exception as e:
                logger.warning("Suggested questions generation failed (non-fatal): %s", e)

            # Clear query cache for this datasource — metadata has changed
            from app.services.cache_service import cache_clear_datasource
            cleared = await cache_clear_datasource(ds_id, tenant_id)
            logger.info("Incremental sync completed for datasource %s: cleared %d cache entries", ds_id, cleared)

            await _update_sync_task(
                task_id,
                status="completed",
                progress=100,
                step="同步完成",
                result={
                    "mode": "incremental",
                    "status": "synced",
                    "added": list(added),
                    "removed": list(removed),
                    "changed": list(changed),
                    "total_tables": len(merged_models),
                    "relationships_count": len(inferred_rels),
                    "metrics_count": len(inferred_metrics),
                },
            )

    except Exception as e:
        logger.error("Sync background task failed: %s", e)
        await _update_sync_task(task_id, status="failed", error=str(e))


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


@router.get("/sync-status/{task_id}")
async def get_sync_status(
    task_id: str,
    user=Depends(get_current_user),
):
    """查询异步同步任务的状态。"""
    from app.core.redis_client import get_redis

    redis = await get_redis()
    raw = await redis.get(f"{SYNC_TASK_PREFIX}{task_id}")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "任务不存在或已过期"),
        )
    task = json.loads(raw)
    # Verify tenant ownership
    if task.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_error("FORBIDDEN", "无权访问该任务"),
        )
    return task


@router.get("/sync-active/{ds_id}")
async def get_active_sync_task(
    ds_id: str,
    user=Depends(get_current_user),
):
    """查询指定数据源是否有进行中的同步任务，用于页面刷新后恢复进度。"""
    from app.core.redis_client import get_redis

    tenant_id = user["tenant_id"]
    redis = await get_redis()
    async for key in redis.scan_iter(f"{SYNC_TASK_PREFIX}*"):
        raw = await redis.get(key)
        if raw:
            task = json.loads(raw)
            if task.get("datasource_id") != ds_id or task.get("tenant_id") != tenant_id:
                continue
            if task.get("status") in ("pending", "running"):
                return task
            # Clean up finished tasks so user can sync again
            if task.get("status") in ("completed", "failed"):
                await redis.delete(key)
    return {"task_id": None, "status": "none"}


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
    old_config = row.config

    # Create version snapshot before update
    if "config" in data:
        new_config = json.dumps(data["config"], ensure_ascii=False)
        # Compute next version number
        ver_stmt = select(func.max(MetadataConfigVersion.version_number)).where(
            MetadataConfigVersion.config_id == config_id
        )
        ver_result = await db.execute(ver_stmt)
        next_ver = (ver_result.scalar_one_or_none() or 0) + 1

        version = MetadataConfigVersion(
            config_id=config_id,
            tenant_id=uuid.UUID(tenant_id),
            datasource_id=uuid.UUID(ds_id),
            version_number=next_ver,
            config_snapshot=old_config,
            change_summary=data.get("change_summary", f"手动更新 v{next_ver}"),
        )
        db.add(version)

        await db.execute(
            text("UPDATE metadata_configs SET config = :config, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"config": new_config, "id": str(config_id)},
        )
        await db.commit()
    else:
        new_config = old_config

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
    异步同步数据库表结构到语义层模型。

    立即返回 task_id，后台执行 LLM 推断（关联关系、指标、推荐问题）。
    前端通过 GET /data-models/sync-status/{task_id} 轮询进度。

    mode: "full" (全量覆盖) | "incremental" (增量合并，默认)
    """
    from app.core.redis_client import get_redis

    tenant_id = user["tenant_id"]
    mode = data.get("mode", "incremental")

    # Validate datasource
    from app.services.datasource_service import DataSourceService
    service = DataSourceService(db, tenant_id=tenant_id)
    ds = await service.get_by_id(ds_id)
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "数据源不存在"),
        )

    # Check for existing running task for this datasource (clean up finished ones)
    redis = await get_redis()
    async for key in redis.scan_iter(f"{SYNC_TASK_PREFIX}*"):
        raw = await redis.get(key)
        if raw:
            task = json.loads(raw)
            if task.get("datasource_id") != ds_id or task.get("tenant_id") != tenant_id:
                continue
            if task.get("status") in ("pending", "running"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=_error("SYNC_IN_PROGRESS", f"该数据源已有同步任务进行中: {task['task_id']}"),
                )
            # Clean up finished tasks so they don't block new syncs
            if task.get("status") in ("completed", "failed"):
                await redis.delete(key)

    # Create task and launch background work
    task_id = str(uuid.uuid4())
    await _create_sync_task(task_id, ds_id, tenant_id, mode)
    asyncio.create_task(_run_sync_background(task_id, ds_id, tenant_id, mode))

    return {"task_id": task_id, "status": "pending", "mode": mode}


# ── Metadata Version Management (3.20) ──

from sqlalchemy import func


@router.get("/{ds_id}/versions")
async def list_metadata_versions(
    ds_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出元数据配置的历史版本。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(MetadataConfigVersion)
        .where(
            MetadataConfigVersion.datasource_id == uuid.UUID(ds_id),
            MetadataConfigVersion.tenant_id == uuid.UUID(tenant_id),
        )
        .order_by(MetadataConfigVersion.version_number.desc())
    )
    versions = result.scalars().all()
    return [
        {
            "id": str(v.id),
            "version_number": v.version_number,
            "change_summary": v.change_summary,
            "created_at": _iso(v.created_at),
            "tables_count": len(json.loads(v.config_snapshot).get("models", [])),
        }
        for v in versions
    ]


@router.get("/{ds_id}/versions/{version_num}")
async def get_metadata_version(
    ds_id: str,
    version_num: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取指定版本的内容。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(MetadataConfigVersion).where(
            MetadataConfigVersion.datasource_id == uuid.UUID(ds_id),
            MetadataConfigVersion.tenant_id == uuid.UUID(tenant_id),
            MetadataConfigVersion.version_number == version_num,
        ).limit(1)
    )
    ver = result.scalar_one_or_none()
    if not ver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "版本不存在"),
        )
    return {
        "id": str(ver.id),
        "version_number": ver.version_number,
        "change_summary": ver.change_summary,
        "config": json.loads(ver.config_snapshot),
        "created_at": _iso(ver.created_at),
    }


@router.post("/{ds_id}/rollback")
async def rollback_metadata(
    ds_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """回滚到指定版本的元数据配置。"""
    tenant_id = user["tenant_id"]
    version_num = data.get("version_number")
    if not version_num:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "需要 version_number 字段"),
        )

    result = await db.execute(
        select(MetadataConfigVersion).where(
            MetadataConfigVersion.datasource_id == uuid.UUID(ds_id),
            MetadataConfigVersion.tenant_id == uuid.UUID(tenant_id),
            MetadataConfigVersion.version_number == version_num,
        ).limit(1)
    )
    ver = result.scalar_one_or_none()
    if not ver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "版本不存在"),
        )

    # Save current as version before rollback
    current = await db.execute(
        select(MetadataConfig.id, MetadataConfig.config).where(
            MetadataConfig.datasource_id == uuid.UUID(ds_id),
            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
        ).order_by(MetadataConfig.updated_at.desc()).limit(1)
    )
    current_row = current.one_or_none()
    if current_row:
        ver_stmt = select(func.max(MetadataConfigVersion.version_number)).where(
            MetadataConfigVersion.config_id == current_row.id
        )
        ver_result = await db.execute(ver_stmt)
        next_ver = (ver_result.scalar_one_or_none() or 0) + 1
        rollback_ver = MetadataConfigVersion(
            config_id=current_row.id,
            tenant_id=uuid.UUID(tenant_id),
            datasource_id=uuid.UUID(ds_id),
            version_number=next_ver,
            config_snapshot=current_row.config,
            change_summary=f"回滚前备份 v{next_ver}",
        )
        db.add(rollback_ver)

        await db.execute(
            text("UPDATE metadata_configs SET config = :config, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"config": ver.config_snapshot, "id": str(current_row.id)},
        )
        await db.commit()

    return {
        "id": ds_id,
        "rolled_back_to_version": version_num,
        "config": json.loads(ver.config_snapshot),
    }


# ── Metadata Auto-Refresh Config (3.16) ──

AUTO_REFRESH_PREFIX = "metadata_auto_refresh:"
AUTO_REFRESH_TTL = 86400 * 7  # 7 days


@router.get("/auto-refresh/config")
async def get_auto_refresh_config(
    admin: dict = Depends(require_role("admin")),
):
    """获取元数据自动刷新配置。"""
    from app.core.redis_client import get_redis
    redis = await get_redis()
    raw = await redis.get(f"{AUTO_REFRESH_PREFIX}config")
    if raw:
        return json.loads(raw)
    return {
        "enabled": settings.metadata_auto_refresh_enabled,
        "interval_minutes": settings.metadata_auto_refresh_interval_minutes,
        "datasources": settings.metadata_auto_refresh_datasources,
    }


@router.put("/auto-refresh/config")
async def set_auto_refresh_config(
    data: dict,
    admin: dict = Depends(require_role("admin")),
):
    """设置元数据自动刷新配置。"""
    from app.core.redis_client import get_redis
    redis = await get_redis()
    config = {
        "enabled": data.get("enabled", False),
        "interval_minutes": data.get("interval_minutes", 60),
        "datasources": data.get("datasources", []),
    }
    await redis.set(f"{AUTO_REFRESH_PREFIX}config", json.dumps(config), ex=AUTO_REFRESH_TTL)
    return config


@router.post("/auto-refresh/trigger")
async def trigger_auto_refresh(
    admin: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """手动触发一次自动刷新。扫描所有活跃数据源，检测表结构变更并自动同步。"""
    from app.core.redis_client import get_redis
    from app.services.datasource_service import DataSourceService
    from app.services.mysql_schema_scanner import scan_mysql_schema
    from app.services.connection_pool import pool_manager
    from app.db.session import async_session_factory

    # Get auto-refresh config
    redis = await get_redis()
    raw = await redis.get(f"{AUTO_REFRESH_PREFIX}config")
    config = json.loads(raw) if raw else {
        "enabled": settings.metadata_auto_refresh_enabled,
        "interval_minutes": settings.metadata_auto_refresh_interval_minutes,
        "datasources": settings.metadata_auto_refresh_datasources,
    }

    if not config.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("DISABLED", "自动刷新未启用，请先在配置中开启"),
        )

    tenant_id = admin["tenant_id"]
    # Get datasources to refresh
    if config.get("datasources"):
        ds_ids = config["datasources"]
    else:
        # All active datasources
        result = await db.execute(
            select(DataSource.id).where(
                DataSource.tenant_id == uuid.UUID(tenant_id),
                DataSource.is_active == True,
            )
        )
        ds_ids = [str(r.id) for r in result.scalars().all()]

    refreshed = []
    for ds_id in ds_ids:
        task_id = str(uuid.uuid4())
        await _create_sync_task(task_id, ds_id, tenant_id, "incremental")
        asyncio.create_task(_run_sync_background(task_id, ds_id, tenant_id, "incremental"))
        refreshed.append({"datasource_id": ds_id, "task_id": task_id})

    return {"triggered": len(refreshed), "tasks": refreshed}


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



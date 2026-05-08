"""
数据模型 API — 元数据同步、表/列推断、关联关系推断、版本管理

本文件是 ChatBI 语义层的核心 API，负责把数据库的「物理表结构」转化为 AI 可理解的「语义模型」。

核心概念：
  - 语义层（Semantic Layer）：在原始数据库表之上增加别名、描述、关联关系、业务指标等，
    让 AI 能理解"订单表"和"用户表"之间是"用户下单"的关系，而不仅仅是两个独立的表。
  - 元数据同步（Sync）：从真实数据库扫描表结构 → 用 LLM 推断关联关系和指标 → 保存到 MetadataConfig。
  - 增量 vs 全量：incremental 模式只合并新增/变更的表，保留用户自定义的别名和描述；
    full 模式完全重新扫描和推断，覆盖所有内容。

与其它文件的关系：
  - app/services/mysql_schema_scanner.py — 扫描 MySQL 数据库的表和列信息
  - app/services/inference_service.py — 调用 LLM 推断表间关联关系和业务指标
  - app/services/suggested_questions_service.py — 根据模型生成推荐的自然语言问题
  - app/services/cache_service.py — 同步完成后清除该数据源的查询缓存
  - app/services/chroma_service.py — 删除模型时清理向量数据库
  - app/db/models.py — MetadataConfig / MetadataConfigVersion 数据库模型
  - app/core/redis_client.py — Redis 客户端，用于存储同步任务状态

设计要点：
  - 同步是耗时操作（涉及 LLM 调用），因此采用异步任务模式：API 立即返回 task_id，
    前端通过轮询 sync-status 接口获取进度。
  - 任务状态存在 Redis 中（TTL 1小时），而非数据库，避免频繁写入。
  - 版本管理：每次更新模型配置时，自动创建版本快照，支持回滚。
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession


from app.api._helpers import api_error, iso_format

from app.db.session import get_db
from app.db.models import DataSource, MetadataConfig, MetadataConfigVersion
from app.core.security import get_current_user, require_role
from app.core.logging import get_logger

logger = get_logger(__name__)

# APIRouter 创建一个路由子应用，prefix 表示所有路由都以 /data-models 开头
# tags 用于 Swagger 文档分组
router = APIRouter(prefix="/data-models", tags=["数据模型"])


# ── 同步任务状态管理（基于 Redis） ──
# 为什么用 Redis 而不是数据库？同步任务状态更新频繁（每步都更新进度），
# 且是临时数据（1小时后自动过期），用 Redis 的 SET + TTL 比数据库更高效。

# Redis key 前缀，完整的 key 格式为 "sync_task:{task_id}"
SYNC_TASK_PREFIX = "sync_task:"
# 任务状态在 Redis 中的存活时间（秒），1小时后自动删除，避免残留
SYNC_TASK_TTL = 3600  # 1 hour


async def _create_sync_task(task_id: str, ds_id: str, tenant_id: str, mode: str) -> None:
    """在 Redis 中创建一条同步任务记录。

    参数：
        task_id: 任务的唯一标识（UUID）
        ds_id: 数据源 ID
        tenant_id: 租户 ID（多租户隔离）
        mode: 同步模式，"full" 全量 或 "incremental" 增量

    设计说明：
        - 延迟导入 get_redis（函数内部 import），避免模块加载时 Redis 还未初始化
        - json.dumps(ensure_ascii=False) 让中文字符直接存储，而非转义为 \\uXXXX
        - ex=SYNC_TASK_TTL 设置 Redis key 的过期时间，到期自动删除
    """
    from app.core.redis_client import get_redis
    redis = await get_redis()
    # 任务初始状态：pending（等待执行）、进度 0、步骤"准备中"
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
    """更新 Redis 中同步任务的部分字段（如进度、状态、步骤）。

    参数：
        task_id: 任务 ID
        **kwargs: 要更新的字段，如 status="running", progress=30, step="扫描表结构"

    Python 语法提示：
        **kwargs 把额外的关键字参数收集成一个字典，例如调用
        _update_sync_task(task_id, status="running", progress=10)
        则 kwargs = {"status": "running", "progress": 10}

    实现方式：读取 → 修改 → 写回（Redis 没有原生的"部分更新"命令）。
    在高并发场景下可能有竞态问题，但同步任务是单线程执行的，所以这里没问题。
    """
    from app.core.redis_client import get_redis
    redis = await get_redis()
    raw = await redis.get(f"{SYNC_TASK_PREFIX}{task_id}")
    if not raw:
        return
    task = json.loads(raw)
    # dict.update() 把 kwargs 中的键值对合并到 task 中，已有的键会被覆盖
    task.update(kwargs)
    await redis.set(f"{SYNC_TASK_PREFIX}{task_id}", json.dumps(task, ensure_ascii=False), ex=SYNC_TASK_TTL)


async def _run_sync_background(task_id: str, ds_id: str, tenant_id: str, mode: str) -> None:
    """后台同步任务：执行完整的元数据同步流水线，并实时更新进度。

    这是整个数据模型同步的核心逻辑，分为全量（full）和增量（incremental）两种模式。

    全量模式流水线（6 步）：
        1. 验证数据源连接是否可用
        2. 扫描数据库所有表和列的结构
        3. 用 LLM 推断表之间的关联关系（如：订单表.用户_id → 用户表.id）
        4. 用 LLM 推断业务指标（如：月度销售额、用户留存率）
        5. 将推断结果保存到数据库
        6. 用 LLM 生成推荐问题，并清除查询缓存

    增量模式流水线：
        1. 验证连接
        2. 扫描当前表结构，与已有配置对比，找出新增/删除/变更的表
        3. 合并：保留用户自定义的别名和描述，只更新结构变化的部分
        4. 对新增的表推断关联关系和指标（已有表保留原有推断）
        5. 保存合并结果
        6. 生成推荐问题，清除缓存

    参数：
        task_id: 任务 ID，用于在 Redis 中更新进度
        ds_id: 数据源 ID
        tenant_id: 租户 ID
        mode: "full" 或 "incremental"

    设计要点：
        - 每一步都调用 _update_sync_task 更新进度，前端可以实时展示
        - 使用 async_session_factory() 创建独立的数据库会话，
          因为这个函数在后台线程运行，不能复用请求的会话
        - 推荐问题生成失败不影响整体流程（non-fatal），用 try/except 包裹
    """
    from app.services.mysql_schema_scanner import scan_schema_raw, scan_mysql_schema
    from app.services.connection_pool import pool_manager
    from app.services.datasource_service import DataSourceService
    from app.db.session import async_session_factory

    try:
        # Step 1: 验证数据源连接是否可用
        await _update_sync_task(task_id, status="running", progress=5, step="验证数据源连接")
        # async with 会在代码块结束后自动关闭会话，即使发生异常也能正确释放资源
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

        # Step 2: 扫描数据库表结构
        await _update_sync_task(task_id, progress=10, step="扫描表结构")
        # 从连接池获取数据库引擎，避免每次同步都创建新连接
        engine = await pool_manager.get_pool(ds)

        if mode == "full":
            # ── 全量同步：重新扫描所有表，重新推断所有关系 ──
            async with async_session_factory() as session:
                # scan_mysql_schema 会读取数据库的 INFORMATION_SCHEMA，
                # 获取所有表名、列名、类型、注释等信息，并保存到 MetadataConfig
                scan_result = await scan_mysql_schema(engine, session, ds)

            await _update_sync_task(task_id, progress=20, step="扫描完成，开始推断关联关系")

            # Step 3: 用 LLM 推断表间关联关系
            async with async_session_factory() as session:
                # 取出刚扫描生成的配置（scan_mysql_schema 已保存到数据库）
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

            # config 字段在数据库中是 JSON 字符串，需要 json.loads 反序列化为 Python 字典
            config_data = json.loads(full_config.config)
            models = config_data.get("models", [])

            await _update_sync_task(task_id, progress=30, step="推断关联关系（LLM）")
            from app.services.inference_service import infer_relationships, infer_metrics
            # LLM 推断：分析表名、列名、外键等信息，猜测表之间的关系
            # 例如：orders.user_id → users.id（用户下单）
            inferred_rels = await infer_relationships(models)

            # Step 4: 用 LLM 推断业务指标
            await _update_sync_task(task_id, progress=50, step="推断业务指标（LLM）")
            # LLM 推断：基于表结构和关联关系，生成业务指标定义
            # 例如：月度订单量、平均客单价、用户留存率
            inferred_metrics = await infer_metrics(models, inferred_rels)

            # 将推断结果写入配置
            config_data["relationships"] = inferred_rels
            config_data["metrics"] = inferred_metrics
            config_data["version"] = "1.1"

            # 清除 model 中可能残留的 relationships 字段（已统一提升到顶层）
            for model in models:
                model.pop("relationships", None)

            # Step 5: 保存推断结果到数据库
            await _update_sync_task(task_id, progress=70, step="保存推断结果")
            async with async_session_factory() as session:
                # 重新查询配置（因为之前的 session 已关闭）
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

            # Step 6: 生成推荐问题
            await _update_sync_task(task_id, progress=80, step="生成推荐问题（LLM）")
            try:
                from app.services.suggested_questions_service import generate_suggested_questions
                # 根据模型、关系、指标，生成用户可能会问的自然语言问题
                # 例如："各月份的订单金额趋势如何？"
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
                # 推荐问题生成失败不影响同步结果，只记录警告
                logger.warning("Suggested questions generation failed (non-fatal): %s", e)

            # 同步完成后清除该数据源的查询缓存
            # 原因：元数据变更后，之前的 SQL 生成结果可能已经过时
            from app.services.cache_service import cache_clear_datasource
            cleared = await cache_clear_datasource(ds_id, tenant_id)
            logger.info("Sync completed for datasource %s: cleared %d cache entries", ds_id, cleared)

            # 完成
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

        else:  # incremental — 增量同步
            # ── 增量同步：只处理新增/删除/变更的表，保留用户自定义 ──
            # scan_schema_raw 只扫描表结构，不保存到数据库（与 scan_mysql_schema 的区别）
            raw_schema = await scan_schema_raw(engine, ds)

            async with async_session_factory() as session:
                result = await session.execute(
                    select(MetadataConfig).where(
                        MetadataConfig.datasource_id == uuid.UUID(ds_id),
                        MetadataConfig.tenant_id == uuid.UUID(tenant_id),
                    ).order_by(MetadataConfig.updated_at.desc()).limit(1)
                )
                existing_config = result.scalar_one_or_none()

            # 构建已有表的索引：{表名: 表配置}，方便快速查找
            existing_models = {}
            existing_data = {}
            if existing_config:
                existing_data = json.loads(existing_config.config)
                for model in existing_data.get("models", []):
                    existing_models[model["name"]] = model

            # 集合运算：找出新增、删除、变更的表
            # Python 的集合支持 -（差集）、&（交集）等运算，非常方便
            new_table_names = {m["name"] for m in raw_schema["models"]}
            existing_table_names = set(existing_models.keys())

            added = new_table_names - existing_table_names    # 新增的表
            removed = existing_table_names - new_table_names  # 已删除的表
            changed = set()                                   # 列发生变化的表

            # 对比交集部分的列名是否有变化
            for tname in new_table_names & existing_table_names:
                new_cols = {c["name"] for c in raw_schema["model_map"][tname]["columns"]}
                old_cols = {c["name"] for c in existing_models[tname].get("columns", [])}
                if new_cols != old_cols:
                    changed.add(tname)

            # 如果没有任何变化，直接返回"已是最新"
            if not added and not removed and not changed:
                await _update_sync_task(
                    task_id,
                    status="completed",
                    progress=100,
                    step="无需更新",
                    result={"mode": "incremental", "status": "up_to_date", "added": [], "removed": [], "changed": [], "total_tables": len(existing_models)},
                )
                return

            # 合并模型：已有表保留用户自定义，新表直接使用扫描结果
            merged_models = []
            for model in raw_schema["models"]:
                tname = model["name"]
                if tname in existing_models:
                    # 已有表：合并策略 — 保留用户的别名和描述，更新结构变化
                    merged = _merge_model(existing_models[tname], model)
                    merged_models.append(merged)
                else:
                    # 新增表：直接使用扫描结果
                    merged_models.append(model)

            # 已删除的表标记为 _deleted，而不是直接移除
            # 这样前端可以展示"已删除"状态，让用户确认后再真正删除
            for tname in removed:
                existing = existing_models[tname]
                existing["_deleted"] = True
                merged_models.append(existing)

            await _update_sync_task(task_id, progress=30, step="推断关联关系（LLM）")
            from app.services.inference_service import infer_relationships, infer_metrics
            if existing_config:
                # 已有配置：保留原有的关联关系和指标（增量模式不重新推断已有表）
                inferred_rels = existing_data.get("relationships", [])
                inferred_metrics = existing_data.get("metrics", [])
            else:
                # 首次同步：只对未删除的表推断关系和指标
                active_models_for_inference = [m for m in merged_models if not m.get("_deleted")]
                inferred_rels = await infer_relationships(active_models_for_inference)

                await _update_sync_task(task_id, progress=50, step="推断业务指标（LLM）")
                inferred_metrics = await infer_metrics(active_models_for_inference, inferred_rels)

            # 组装合并后的完整元数据
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
                    # 更新已有配置
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
                    # 首次创建配置
                    config = MetadataConfig(
                        tenant_id=uuid.UUID(tenant_id),
                        datasource_id=uuid.UUID(ds_id),
                        config=json.dumps(merged_metadata, ensure_ascii=False),
                    )
                    session.add(config)
                await session.commit()

            # 生成推荐问题（增量模式下也更新）
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

            # 清除查询缓存
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
        # 捕获所有未处理的异常，将任务标记为失败
        # 这样前端能看到失败状态和错误信息，而不是一直卡在"运行中"
        logger.error("Sync background task failed: %s", e)
        await _update_sync_task(task_id, status="failed", error=str(e))


@router.get("")
async def list_data_models(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前租户所有数据模型（按数据源分组）。

    参数：
        user: 通过 JWT 令牌解析出的当前用户信息（由 get_current_user 依赖注入提供）
        db: 异步数据库会话（由 get_db 依赖注入提供）

    返回：
        数据模型列表，每个元素包含：ID、数据源名称、表数量、是否有关系/指标、更新时间

    Python 语法提示：
        Depends(get_current_user) 是 FastAPI 的依赖注入机制，
        框架会在调用此函数前自动执行 get_current_user，提取并验证 JWT 令牌。
    """
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(MetadataConfig)
        .where(MetadataConfig.tenant_id == uuid.UUID(tenant_id))
        .order_by(MetadataConfig.updated_at.desc())
    )
    configs = result.scalars().all()

    # 批量查询数据源名称，避免 N+1 查询问题
    # N+1 问题：如果对每个 config 单独查数据源，N 条记录就需要 N+1 次查询
    # 用 IN 查询一次获取所有数据源，然后用字典映射，只需 2 次查询
    ds_ids = [str(c.datasource_id) for c in configs]
    ds_result = await db.execute(select(DataSource).where(DataSource.id.in_(ds_ids)))
    ds_map = {str(ds.id): ds.name for ds in ds_result.scalars().all()}

    # 列表推导式（list comprehension）：Python 中快速构建列表的语法
    # [表达式 for 变量 in 可迭代对象] — 对每个元素计算表达式，收集结果
    return [
        {
            "id": str(c.id),
            "datasource_id": str(c.datasource_id),
            "datasource_name": ds_map.get(str(c.datasource_id), "未知"),
            "tables_count": len(json.loads(c.config).get("models", [])),
            "has_relationships": bool(json.loads(c.config).get("relationships", [])),
            "has_metrics": bool(json.loads(c.config).get("metrics", [])),
            "updated_at": iso_format(c.updated_at),
        }
        for c in configs
    ]


@router.get("/sync-status/{task_id}")
async def get_sync_status(
    task_id: str,
    user=Depends(get_current_user),
):
    """查询异步同步任务的状态（前端轮询此接口获取进度）。

    参数：
        task_id: 同步任务的唯一标识（由 sync_data_model 接口返回）
        user: 当前用户信息

    返回：
        任务状态字典，包含 status（pending/running/completed/failed）、progress（0-100）、step（当前步骤）等

    安全设计：
        验证 tenant_id 一致性，防止用户查看其他租户的任务状态。
    """
    from app.core.redis_client import get_redis

    redis = await get_redis()
    raw = await redis.get(f"{SYNC_TASK_PREFIX}{task_id}")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=api_error("NOT_FOUND", "任务不存在或已过期"),
        )
    task = json.loads(raw)
    # 验证租户归属：防止通过遍历 task_id 查看其他租户的任务
    if task.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=api_error("FORBIDDEN", "无权访问该任务"),
        )
    return task


@router.get("/sync-active/{ds_id}")
async def get_active_sync_task(
    ds_id: str,
    user=Depends(get_current_user),
):
    """查询指定数据源是否有进行中的同步任务，用于页面刷新后恢复进度。

    场景：用户在同步页面刷新浏览器后，需要知道之前的同步是否还在进行，
    以便恢复进度条显示。

    参数：
        ds_id: 数据源 ID
        user: 当前用户信息

    返回：
        如果有进行中的任务，返回任务状态；否则返回 {"task_id": None, "status": "none"}

    设计要点：
        - 使用 Redis SCAN 命令遍历所有同步任务 key（scan_iter）
        - 已完成或失败的任务会被清理删除，这样用户可以重新发起同步
        - SCAN 比 KEYS 更安全：KEYS 会阻塞 Redis，SCAN 是增量式的
    """
    from app.core.redis_client import get_redis

    tenant_id = user["tenant_id"]
    redis = await get_redis()
    # scan_iter 逐批扫描 Redis key，避免一次性返回大量数据导致阻塞
    async for key in redis.scan_iter(f"{SYNC_TASK_PREFIX}*"):
        raw = await redis.get(key)
        if raw:
            task = json.loads(raw)
            if task.get("datasource_id") != ds_id or task.get("tenant_id") != tenant_id:
                continue
            if task.get("status") in ("pending", "running"):
                return task
            # 已完成的任务清理掉，让用户可以重新发起同步
            if task.get("status") in ("completed", "failed"):
                await redis.delete(key)
    return {"task_id": None, "status": "none"}


@router.get("/{ds_id}")
async def get_data_model(
    ds_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取指定数据源的语义层模型配置。

    返回完整的元数据 JSON，包括表定义、关联关系、业务指标、推荐问题等。
    前端的"数据模型编辑"页面调用此接口获取初始数据。

    参数：
        ds_id: 数据源 ID（URL 路径参数）
        user: 当前用户（JWT 解析结果）
        db: 异步数据库会话

    返回：
        包含 id、datasource_id、config（完整 JSON）、updated_at 的字典
    """
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
            detail=api_error("NOT_FOUND", "该数据源尚未配置数据模型"),
        )
    return {
        "id": str(config.id),
        "datasource_id": str(config.datasource_id),
        "config": json.loads(config.config),
        "updated_at": iso_format(config.updated_at),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_data_model(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    为数据源创建语义层模型配置（手动创建，一般由同步接口自动创建）。

    请求体格式：{ "datasource_id": "...", "config": { "models": [...], "relationships": [...], "metrics": [...] } }

    参数：
        data: 请求体，包含 datasource_id 和 config
        user: 当前用户信息
        db: 数据库会话

    返回：
        创建成功的配置信息

    注意：
        - 每个数据源只能有一个模型配置（一对一关系），重复创建返回 409
        - 正常流程中，模型配置由 sync_data_model 接口自动创建
        - db.refresh(config) 让 ORM 从数据库重新加载对象，获取自动生成的 id 和 updated_at
    """
    tenant_id = user["tenant_id"]
    ds_id = data.get("datasource_id")
    config_content = data.get("config")

    if not ds_id or not config_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=api_error("INVALID_INPUT", "需要 datasource_id 和 config 字段"),
        )

    # Verify datasource belongs to tenant — 验证数据源归属，防止越权操作
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
            detail=api_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )

    # Check if already exists — 每个数据源只允许一个模型配置
    existing = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == uuid.UUID(ds_id),
            MetadataConfig.tenant_id == uuid.UUID(tenant_id),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=api_error("ALREADY_EXISTS", "该数据源已存在模型配置，请使用 PUT 更新"),
        )

    config = MetadataConfig(
        tenant_id=uuid.UUID(tenant_id),
        datasource_id=uuid.UUID(ds_id),
        config=json.dumps(config_content, ensure_ascii=False),
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)  # 刷新 ORM 对象，获取数据库自动生成的 id、created_at 等字段

    return {
        "id": str(config.id),
        "datasource_id": str(config.datasource_id),
        "config": json.loads(config.config),
        "updated_at": iso_format(config.updated_at),
    }


@router.put("/{ds_id}")
async def update_data_model(
    ds_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新指定数据源的语义层模型配置（用户手动编辑后保存）。

    参数：
        ds_id: 数据源 ID（URL 路径参数）
        data: 请求体，包含 config（新的模型配置）和可选的 change_summary（变更说明）
        user: 当前用户信息
        db: 数据库会话

    返回：
        更新后的配置信息

    版本管理机制：
        更新前自动创建版本快照（MetadataConfigVersion），记录旧配置。
        版本号自增：先查当前最大版本号，然后 +1。
        这样用户可以随时回滚到任意历史版本。

    为什么用原生 SQL 更新而不是 ORM？
        因为 SQLAlchemy 的 ORM 更新有时不会立即刷新 updated_at，
        使用 text() 执行原生 SQL 可以确保 CURRENT_TIMESTAMP 正确设置。
    """
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
            detail=api_error("NOT_FOUND", "该数据源尚未配置数据模型"),
        )

    config_id = row.id
    old_config = row.config

    # Create version snapshot — 创建版本快照，保存当前配置到版本历史表
    # 这样即使更新出错，用户也可以回滚到之前的版本 before update
    if "config" in data:
        new_config = json.dumps(data["config"], ensure_ascii=False)
        # Compute next version number — 计算下一个版本号
        # 先查当前最大版本号，如果不存在则为 0，然后 +1
        # Python 技巧：or 0 处理 None 值，scalar_one_or_none() 在无结果时返回 None
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
    """删除指定数据源的语义层模型配置。

    参数：
        ds_id: 数据源 ID
        user: 当前用户信息
        db: 数据库会话

    返回：
        HTTP 204 No Content（删除成功无返回体）

    副作用：
        删除模型配置后，还会清理 Chroma 向量数据库中对应的 collection。
        Chroma 用于存储表结构的向量索引，删除模型后这些索引不再需要。
        清理失败不影响删除操作（non-fatal），只记录警告日志。
    """
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
            detail=api_error("NOT_FOUND", "该数据源尚未配置数据模型"),
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
            detail=api_error("NOT_FOUND", "数据源不存在"),
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
                    detail=api_error("SYNC_IN_PROGRESS", f"该数据源已有同步任务进行中: {task['task_id']}"),
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
            "created_at": iso_format(v.created_at),
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
            detail=api_error("NOT_FOUND", "版本不存在"),
        )
    return {
        "id": str(ver.id),
        "version_number": ver.version_number,
        "change_summary": ver.change_summary,
        "config": json.loads(ver.config_snapshot),
        "created_at": iso_format(ver.created_at),
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
            detail=api_error("INVALID_INPUT", "需要 version_number 字段"),
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
            detail=api_error("NOT_FOUND", "版本不存在"),
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
    """获取元数据自动刷新配置（仅管理员可访问）。

    自动刷新功能：定时检测数据库结构变化，自动增量同步到模型配置。
    配置存储在 Redis 中（TTL 7天），如果 Redis 中没有则使用默认值。

    参数：
        admin: 管理员用户信息（require_role("admin") 确保只有管理员能访问）

    返回：
        {"enabled": bool, "interval_minutes": int, "datasources": [str]}
    """
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
            detail=api_error("DISABLED", "自动刷新未启用，请先在配置中开启"),
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
    # 构建列名到列配置的映射，方便按列名查找
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



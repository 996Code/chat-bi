"""
T014: 数据源管理 API (DataSource CRUD + 扫描触发)

端点:
  POST   /data-sources          创建数据源 (admin) — 密码 Fernet 加密存储
  GET    /data-sources          列出当前租户的数据源
  GET    /data-sources/{id}     查看详情 (解密密码不返回)
  POST   /data-sources/{id}/scan  触发扫描 → 生成语义层 v1 (admin)

对标:
  - v1 #38: 密码加密存储 (Fernet)
  - v1 #48: 多租户隔离 (显式 tenant_filter)
  - v1 #41: 审计三态
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_admin, require_user, write_audit_log
from app.core.config import get_settings
from app.core.security import encrypt_password
from app.db.models import DataSource, SemanticModel
from app.db.session import get_db
from app.services.datasource_engine import datasource_to_url, get_engine_pool
from app.services.semantic_scanner import scan_data_source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


# ── 请求 DTO ──────────────────────────────────────────────────

class DataSourceCreate(BaseModel):
    name: str
    db_type: str  # "postgresql" | "mysql"
    host: str
    port: int
    database: str
    username: str
    password: str  # 明文, 存储前加密

    @field_validator("db_type")
    @classmethod
    def _validate_db_type(cls, v: str) -> str:
        if v not in ("postgresql", "mysql"):
            raise ValueError(f"不支持的数据库类型: {v} (仅 postgresql/mysql)")
        return v


class DataSourceOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    db_type: str
    host: str
    port: int
    database: str
    username: str
    is_active: bool
    # 扫描状态 (前端轮询展示进度条 + 步骤, 对标 V1 + 经验教训 #25)
    scan_status: str = "idle"
    scan_progress: int = 0
    scan_stage: str | None = None
    scan_error: str | None = None


def _to_out(ds: DataSource) -> DataSourceOut:
    """统一构造 DataSourceOut (避免各端点重复 + 漏字段)。"""
    return DataSourceOut(
        id=ds.id, tenant_id=ds.tenant_id, name=ds.name,
        db_type=ds.db_type, host=ds.host, port=ds.port,
        database=ds.database, username=ds.username, is_active=ds.is_active,
        scan_status=ds.scan_status, scan_progress=ds.scan_progress,
        scan_stage=ds.scan_stage, scan_error=ds.scan_error,
    )


# ── 端点 ──────────────────────────────────────────────────────

@router.post("", response_model=DataSourceOut, status_code=status.HTTP_201_CREATED)
async def create_data_source(
    body: DataSourceCreate,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """创建数据源。密码 Fernet 加密后存储 (对标 v1 #38)。"""
    ds = DataSource(
        tenant_id=user.tenant_id,
        name=body.name,
        db_type=body.db_type,
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
        encrypted_password=encrypt_password(body.password),
    )
    db.add(ds)
    await db.flush()
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="data_source", action="create", status="success",
        resource_id=ds.id, detail={"name": body.name},
    )
    await db.commit()
    await db.refresh(ds)
    return _to_out(ds)


@router.get("", response_model=list[DataSourceOut])
async def list_data_sources(
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前租户的数据源 (多租户隔离, 对标 v1 #48)。"""
    stmt = select(DataSource).where(
        DataSource.tenant_filter(user.tenant_id),
        DataSource.is_active == True,  # noqa: E712
    )
    result = await db.execute(stmt)
    return [_to_out(ds) for ds in result.scalars()]


@router.get("/{ds_id}", response_model=DataSourceOut)
async def get_data_source(
    ds_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(DataSource).where(
        DataSource.id == ds_id,
        DataSource.tenant_filter(user.tenant_id),
    )
    ds = (await db.execute(stmt)).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return _to_out(ds)


class DataSourceToggle(BaseModel):
    """数据源启停 (DSO-08)。禁用后不可查询, 对标 v1 经验教训 #18 Partial 陷阱。"""
    is_active: bool


@router.patch("/{ds_id}", response_model=DataSourceOut)
async def toggle_data_source(
    ds_id: str,
    body: DataSourceToggle,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """启用/禁用数据源 (admin only, DSO-08)。

    禁用后: 列表不返回 (已过滤), 查询/扫描拒绝 (build_agent_deps 校验)。
    审计三态: 记录 enable/disable 操作。
    """
    stmt = select(DataSource).where(
        DataSource.id == ds_id,
        DataSource.tenant_filter(user.tenant_id),
    )
    ds = (await db.execute(stmt)).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if ds.is_active == body.is_active:
        raise HTTPException(status_code=400, detail=f"数据源已是 {'启用' if body.is_active else '禁用'} 状态")
    ds.is_active = body.is_active
    await db.flush()
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="data_source",
        action="enable" if body.is_active else "disable",
        status="success", resource_id=ds.id, detail={"name": ds.name},
    )
    await db.commit()
    await db.refresh(ds)
    return _to_out(ds)


# ── DSO-02: 数据源健康检查 ────────────────────────────────────

@router.get("/{ds_id}/health")
async def check_datasource_health(
    ds_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """手动检查单个数据源健康状态 (DSO-02)。

    返回 {ok, latency_ms, error}。ping 失败不标记 error (只检查, 状态变更靠定时任务)。
    """
    ds = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == ds_id,
                DataSource.tenant_filter(user.tenant_id),
            )
        )
    ).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")

    from app.services.datasource_health import ping_datasource
    result = await ping_datasource(ds)
    return {
        "ok": result.ok,
        "latency_ms": result.latency_ms,
        "error": result.error,
    }


@router.post("/health-check/all")
async def check_all_health(
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """手动触发全量数据源健康检查 (admin, DSO-02)。

    M6: 限定本租户数据源 (跨租户隔离)。
    返回汇总 {checked, healthy, unhealthy, recovered, newly_error}。
    """
    from app.services.datasource_health import check_all_datasources_health
    summary = await check_all_datasources_health(db, tenant_id=user.tenant_id)
    await db.commit()
    return summary


@router.post("/refresh-metadata/all")
async def refresh_all_metadata(
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """手动触发全量元数据刷新 (admin, DSO-04)。

    M6: 限定本租户数据源 (跨租户隔离)。
    检测所有数据源表结构变更, 有变更自动写新版本。
    """
    from app.services.metadata_refresher import detect_and_refresh_metadata
    summary = await detect_and_refresh_metadata(db, tenant_id=user.tenant_id)
    await db.commit()
    return summary


@router.post("/{ds_id}/scan", status_code=status.HTTP_202_ACCEPTED)
async def scan_data_source_endpoint(
    ds_id: str,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """触发扫描 (异步任务模式, 对标 V1 + 经验教训 #25)。

    扫描耗时长 (连库 + LLM 推断 + 建索引), 不能阻塞 HTTP:
      - 立即返回 202 + scan_status=scanning
      - 后台 asyncio.create_task 跑全流程, 分阶段更新 scan_progress/scan_stage
      - 前端轮询 GET /data-sources/{id} 拿进度

    防重复: scan_status=scanning 时拒绝 (409), 避免并发扫描污染。
    """
    stmt = select(DataSource).where(
        DataSource.id == ds_id,
        DataSource.tenant_filter(user.tenant_id),
    )
    ds = (await db.execute(stmt)).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")

    # 防重复提交 (对标经验教训 #25: 同一资源不允许重复提交)
    if ds.scan_status == "scanning":
        raise HTTPException(status_code=409, detail="该数据源正在扫描中, 请等待完成")

    # 标记扫描中, 立即提交 (前端能立刻看到状态变化)
    ds.scan_status = "scanning"
    ds.scan_progress = 5
    ds.scan_stage = "排队中"
    ds.scan_error = None
    await db.commit()

    # 后台执行全流程 (独立 session, 不共享请求 session)
    asyncio.create_task(_run_scan_background(ds_id, user.tenant_id, user.user_id))

    return _to_out(ds)


# ── 扫描后台任务 ──────────────────────────────────────────────

async def _run_scan_background(ds_id: str, tenant_id: str, user_id: str) -> None:
    """后台跑扫描全流程 (独立 db session, 分阶段更新 DataSource 扫描状态)。

    阶段进度 (对标经验教训 #25 "进度按步骤百分比"):
      connecting 10% → scanning 35% → inferring 65% → enriching 85% → saving 95% → done 100%
    任一阶段失败 → scan_status=failed + scan_error, 不留半成品状态。
    """
    from app.db.session import get_async_session_factory
    factory = get_async_session_factory()

    async with factory() as session:
        ds = None  # 初始化, 防止 except 块引用未定义变量
        try:
            ds = (
                await session.execute(
                    select(DataSource).where(
                        DataSource.id == ds_id,
                        DataSource.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if ds is None:
                return  # 数据源被删了

            # ── Stage 1: 连库扫描 (10% → 35%) ──────────────────
            await _update_scan(session, ds, progress=10, stage="连接数据库...")
            pool = get_engine_pool()
            url = datasource_to_url(ds)
            inspector = pool.get_inspector(ds_id, url)
            await _update_scan(session, ds, progress=20, stage="扫描表结构...")
            content = scan_data_source(inspector)
            await _update_scan(session, ds, progress=35, stage=f"扫描到 {len(content.models)} 张表")

            # ── Stage 2: LLM 中文推断 (35% → 65%) ──────────────
            await _update_scan(session, ds, progress=45, stage="LLM 推断中文名...")
            try:
                import asyncio
                await asyncio.wait_for(_enrich_with_llm(content), timeout=float(get_settings().scan_llm_enrichment_timeout))
            except asyncio.TimeoutError:
                logger.warning("LLM 中文推断整体超时 10min, 退化列名")
            except Exception as e:
                logger.warning("LLM 推断失败, 退化列名: %s", e)

            # ── Stage 2b: LLM 指标推断 (65% → 70%) ──────────────
            await _update_scan(session, ds, progress=65, stage="LLM 推断业务指标...")
            try:
                from app.services.semantic_scanner import enrich_metrics
                await asyncio.wait_for(enrich_metrics(content), timeout=float(get_settings().scan_llm_enrichment_timeout))
            except asyncio.TimeoutError:
                logger.warning("LLM 指标推断整体超时, 跳过")
            except Exception as e:
                logger.warning("LLM 指标推断失败, 跳过: %s", e)

            # ── Stage 3: 知识图谱 + 示例问题 (65% → 85%) ────────
            await _update_scan(session, ds, progress=70, stage="推断表关系...")
            try:
                import asyncio
                from app.services.knowledge_graph import infer_knowledge_graph
                # 整体超时 10min (本地小模型生成速度慢, 多批次需足够时间)
                inferred_rels = await asyncio.wait_for(
                    infer_knowledge_graph(content, use_llm=True),
                    timeout=float(get_settings().scan_llm_enrichment_timeout),
                )
                if inferred_rels:
                    _apply_inferred_relationships(content, inferred_rels)
            except asyncio.TimeoutError:
                logger.warning("知识图谱推断整体超时 5min, 跳过 (name_pattern 关系已可用)")
            except Exception as e:
                logger.debug("知识图谱推断失败, 跳过: %s", e)

            await _update_scan(session, ds, progress=80, stage="生成示例问题...")
            try:
                from app.ai.question_generator import generate_sample_questions
                content.sample_questions = await generate_sample_questions(
                    content.models,
                )
            except Exception:
                pass  # 失败不阻塞 (sample_questions 留空)

            # ── Stage 4: 版本保存 (85% → 95%) ──────────────────
            await _update_scan(session, ds, progress=88, stage="保存语义层...")
            max_version = (
                await session.execute(
                    select(SemanticModel.version)
                    .where(
                        SemanticModel.tenant_filter(tenant_id),
                        SemanticModel.data_source_id == ds_id,
                    )
                    .order_by(SemanticModel.version.desc()).limit(1)
                )
            ).scalar_one_or_none() or 0
            new_version = max_version + 1

            if max_version > 0:
                old_currents = (
                    await session.execute(
                        select(SemanticModel).where(
                            SemanticModel.tenant_filter(tenant_id),
                            SemanticModel.data_source_id == ds_id,
                            SemanticModel.is_current == True,  # noqa: E712
                        )
                    )
                ).scalars().all()
                for old in old_currents:
                    old.is_current = False

            sm = SemanticModel(
                tenant_id=tenant_id,
                data_source_id=ds_id,
                version=new_version,
                content=content.model_dump(),
                is_current=True,
            )
            session.add(sm)
            await session.flush()
            await write_audit_log(
                session, tenant_id=tenant_id, user_id=user_id,
                resource_type="semantic_model", action="scan", status="success",
                resource_id=sm.id, detail={"version": new_version, "tables": len(content.models)},
            )
            await session.commit()

            # ── Stage 5: 建向量索引 (95% → 100%) ───────────────
            await _update_scan(session, ds, progress=95, stage="构建检索索引...")
            index_count = 0
            try:
                from app.services.indexer_update import rebuild_index
                from app.services.embedder import get_embedder
                from app.services.vector_store import get_vector_store
                result = await rebuild_index(
                    content=content,
                    data_source_id=ds_id,
                    store=get_vector_store(),
                    embedder=get_embedder(),
                )
                index_count = result.indexed_count
            except Exception as e:
                logger.warning("建索引失败, RAG 检索将降级: %s", e)

            # ── done ───────────────────────────────────────────
            await _finish_scan(
                session, ds,
                stage=f"完成: v{new_version}, {len(content.models)} 表, {index_count} 索引",
            )
            logger.info("扫描完成 ds=%s v%d (%d 表)", ds_id, new_version, len(content.models))

        except Exception as e:
            logger.exception("扫描后台任务失败 ds=%s", ds_id)
            await write_audit_log(
                session, tenant_id=tenant_id, user_id=user_id,
                resource_type="data_source", action="scan", status="fail",
                resource_id=ds_id, error_message=str(e)[:500],
            )
            try:
                if ds is not None:
                    await _fail_scan(session, ds, error=str(e)[:500])
            except Exception:
                pass


async def _update_scan(session: AsyncSession, ds: DataSource, progress: int, stage: str) -> None:
    """更新扫描进度 (不抛异常, 失败只记日志)。"""
    try:
        ds.scan_progress = progress
        ds.scan_stage = stage
        await session.commit()
    except Exception as e:
        logger.warning("更新扫描进度失败 ds=%s: %s", ds.id, e)


async def _finish_scan(session: AsyncSession, ds: DataSource, stage: str) -> None:
    """扫描完成: scan_status=done + progress=100 + scanned_at。"""
    ds.scan_status = "done"
    ds.scan_progress = 100
    ds.scan_stage = stage
    ds.scan_error = None
    ds.scanned_at = datetime.now(timezone.utc)
    await session.commit()


async def _fail_scan(session: AsyncSession, ds: DataSource, error: str) -> None:
    """扫描失败: scan_status=failed + scan_error (对标 fail-closed, 明确告知失败)。"""
    ds.scan_status = "failed"
    ds.scan_error = error
    ds.scan_progress = 0
    ds.scan_stage = "failed"
    await session.commit()


# ChatBI 自己的系统表 (元数据表), 扫描时不调 LLM 推断
# 集中定义在 config.system_tables, 此处延迟获取 (新增系统表只改 config)
def _get_system_tables() -> frozenset:
    return frozenset(get_settings().system_tables)


def _apply_inferred_relationships(content, inferred_rels) -> None:
    """把 LLM 推断的关系写回语义层 (原地 patch)。

    对标 ARC-05: 补充外键扫描发现不了的隐式关系。
    Relationship 的 name 格式为 "<from>_to_<to>", 从中解析来源表。
    """
    # 按 from_table 分组 (name 里解析: "<from>_to_<to>")
    by_model: dict[str, list] = {}
    for rel in inferred_rels:
        from_table = rel.name.split("_to_")[0] if "_to_" in rel.name else ""
        if from_table:
            by_model.setdefault(from_table, []).append(rel)
    # 写回对应 model 的 relationships (去重: target_model 不重复)
    for model in content.models:
        if model.name in by_model:
            existing_targets = {r.target_model for r in model.relationships}
            for rel in by_model[model.name]:
                if rel.target_model not in existing_targets:
                    model.relationships.append(rel)
                    existing_targets.add(rel.target_model)


async def _enrich_with_llm(content) -> None:
    """扫描后用 LLM 给无注释的列补中文 display_name (原地 patch)。

    对标 v1 经验教训 #15: 元数据质量是准确率根本。
    有注释的列不动 (source=manual), 只补 source=auto_inferred 且 confidence=0.5
    (退化列名) 的那些 → 补完后升 source=auto_inferred, confidence=0.8。

    优化:
      - 跳过系统表 (ChatBI 元数据表, 用户不查, 省 60%+ LLM 调用)
      - 优先一次性全量发送 (200K 上下文足够容纳 200+ 表的列名)
      - 若表数 > 200 则分批, 每批 100 张表
    不设每批超时: asyncio.wait_for 会断开 LLM 连接中断生成,
    靠外层整体超时兜底。LLM 失败静默降级 (退化列名已可用, 不阻塞扫描)。
    """
    from app.core.llm_client import llm_chat
    from app.core.llm_json import parse_json_response

    # 筛出需要推断的业务表 (跳过系统表 + 全有注释的表)
    tasks = []  # (model, needs_infer)
    _system_tables = _get_system_tables()
    for model in content.models:
        if model.name in _system_tables:
            continue
        needs_infer = [
            c for c in model.columns
            if c.source == "auto_inferred" and c.confidence == 0.5
        ]
        if needs_infer:
            tasks.append((model, needs_infer))

    if not tasks:
        return  # 没有需要推断的, 直接返回

    # 全量优先: 200 表以内一次性发送; 超过则分批 100 表
    batch_size = 200 if len(tasks) <= 200 else 100

    for batch_start in range(0, len(tasks), batch_size):
        batch = tasks[batch_start:batch_start + batch_size]
        tables_desc = []
        for model, needs_infer in batch:
            col_desc = ", ".join(
                f"{c.name}({c.data_type})" for c in needs_infer
            )
            tables_desc.append(f"表 {model.name}: {col_desc}")

        prompt = (
            "你是数据库语义推断助手。以下是多张表需要推断中文展示名的列。\n\n"
            + "\n".join(tables_desc)
            + "\n\n为每个列推断一个简洁的中文展示名（display_name）。"
            "只返回 JSON，格式: {\"表名\": {\"列名\": \"中文名\"}}，不要解释。"
        )

        try:
            # 不设每批超时: asyncio.wait_for 会断开 LLM 连接中断生成
            # 靠外层整体超时兜底
            result_text, _ = await llm_chat(
                messages=[{"role": "user", "content": prompt}],
            )
            result = parse_json_response(result_text)
            if not isinstance(result, dict):
                continue
        except Exception as e:
            logger.warning("_enrich_with_llm: LLM 批量推断失败, 降级: %s", e)
            continue

        # patch 结果
        for model, needs_infer in batch:
            table_result = result.get(model.name, {})
            if not isinstance(table_result, dict):
                continue
            name_to_col = {c.name: c for c in needs_infer}
            for col_name, display_name in table_result.items():
                if col_name in name_to_col:
                    col = name_to_col[col_name]
                    col.display_name = display_name
                    col.confidence = 0.8  # LLM 推断, 升级置信度

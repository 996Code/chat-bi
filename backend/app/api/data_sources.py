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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_admin, require_user, write_audit_log
from app.core.security import encrypt_password
from app.db.models import DataSource, SemanticModel
from app.db.session import get_db
from app.services.datasource_engine import datasource_to_url, get_engine_pool
from app.services.semantic_scanner import scan_data_source

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
    return DataSourceOut(
        id=ds.id, tenant_id=ds.tenant_id, name=ds.name,
        db_type=ds.db_type, host=ds.host, port=ds.port,
        database=ds.database, username=ds.username, is_active=ds.is_active,
    )


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
    return [
        DataSourceOut(
            id=ds.id, tenant_id=ds.tenant_id, name=ds.name,
            db_type=ds.db_type, host=ds.host, port=ds.port,
            database=ds.database, username=ds.username, is_active=ds.is_active,
        )
        for ds in result.scalars()
    ]


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
    return DataSourceOut(
        id=ds.id, tenant_id=ds.tenant_id, name=ds.name,
        db_type=ds.db_type, host=ds.host, port=ds.port,
        database=ds.database, username=ds.username, is_active=ds.is_active,
    )


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
    return DataSourceOut(
        id=ds.id, tenant_id=ds.tenant_id, name=ds.name,
        db_type=ds.db_type, host=ds.host, port=ds.port,
        database=ds.database, username=ds.username, is_active=ds.is_active,
    )


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

    返回汇总 {checked, healthy, unhealthy, recovered, newly_error}。
    """
    from app.services.datasource_health import check_all_datasources_health
    summary = await check_all_datasources_health(db)
    await db.commit()
    return summary


@router.post("/refresh-metadata/all")
async def refresh_all_metadata(
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """手动触发全量元数据刷新 (admin, DSO-04)。

    检测所有数据源表结构变更, 有变更自动写新版本。
    """
    from app.services.metadata_refresher import detect_and_refresh_metadata
    summary = await detect_and_refresh_metadata(db)
    await db.commit()
    return summary


@router.post("/{ds_id}/scan", status_code=status.HTTP_200_OK)
async def scan_data_source_endpoint(
    ds_id: str,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """触发扫描: 连接数据源 → inspect 表结构 → 生成语义层 v1。

    对标 T013 扫描 + T014 版本管理: 首次扫描创建 v1, 重新扫描创建新版本。
    """
    stmt = select(DataSource).where(
        DataSource.id == ds_id,
        DataSource.tenant_filter(user.tenant_id),
    )
    ds = (await db.execute(stmt)).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")

    # 连接业务库扫描 (用动态引擎池)
    pool = get_engine_pool()
    try:
        url = datasource_to_url(ds)
        inspector = pool.get_inspector(ds_id, url)
        # 同步扫描拿到结构 + 注释 (infer_llm 不在此传, 因为它是同步函数,
        # 而 LLM 调用是 async。LLM 推断在扫描后单独跑, 再 patch 回结果)
        content = scan_data_source(inspector)
    except Exception as e:
        await write_audit_log(
            db, tenant_id=user.tenant_id, user_id=user.user_id,
            resource_type="data_source", action="scan", status="fail",
            resource_id=ds_id, error_message=str(e)[:500],
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"扫描失败: {type(e).__name__}",
        )

    # LLM 中文推断: 给无注释的表/列补 display_name (对标 v1 #15 元数据质量)
    # scanner 是同步的, LLM 是 async, 所以扫描后单独跑再 patch 回 content
    try:
        await _enrich_with_llm(content)
    except Exception:
        pass  # LLM 推断失败不阻塞扫描 (宁缺毋滥, 退化列名已可用)

    # 知识图谱: LLM 推断实体关系 (对标 ARC-05: name_pattern + AI 推断)
    # 补充外键扫描发现不了的隐式关系 (如 orders.user_id → users.id), 失败不阻塞
    try:
        from app.services.knowledge_graph import infer_knowledge_graph
        from app.core.llm_client import get_llm_client
        inferred_rels = await infer_knowledge_graph(content, use_llm=True, llm_client=get_llm_client())
        if inferred_rels:
            # 写回各 model 的 relationships (去重: infer_knowledge_graph 已保证不重复)
            _apply_inferred_relationships(content, inferred_rels)
            logger.info("知识图谱: LLM 推断 %d 条新关系", len(inferred_rels))
    except Exception as e:
        logger.debug("知识图谱 LLM 推断失败, 跳过: %s", e)

    # 生成示例问题: 基于扫描到的表/列/关系, LLM 生成可问的 BI 问题
    # 对标 V1: 扫描完告诉用户这个数据源可以问什么 (fail-closed, LLM 失败有规则降级)
    try:
        from app.ai.question_generator import generate_sample_questions
        from app.core.llm_client import get_llm_client
        content.sample_questions = await generate_sample_questions(
            content.models, get_llm_client(),
        )
    except Exception:
        pass  # 失败不阻塞扫描 (sample_questions 留空, 前端用硬编码兜底)

    # 版本管理: 新版本号 = max(version)+1, 旧版本 is_current=False
    max_version = (
        await db.execute(
            select(SemanticModel.version)
            .where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == ds_id,
            )
            .order_by(SemanticModel.version.desc()).limit(1)
        )
    ).scalar_one_or_none() or 0
    new_version = max_version + 1

    # 旧版本置 is_current=False
    if max_version > 0:
        old_currents = (
            await db.execute(
                select(SemanticModel).where(
                    SemanticModel.tenant_filter(user.tenant_id),
                    SemanticModel.data_source_id == ds_id,
                    SemanticModel.is_current == True,  # noqa: E712
                )
            )
        ).scalars().all()
        for old in old_currents:
            old.is_current = False

    sm = SemanticModel(
        tenant_id=user.tenant_id,
        data_source_id=ds_id,
        version=new_version,
        content=content.model_dump(),
        is_current=True,
    )
    db.add(sm)
    await db.flush()
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="semantic_model", action="scan", status="success",
        resource_id=sm.id, detail={"version": new_version, "tables": len(content.models)},
    )
    await db.commit()

    # T020: 扫描后重建向量索引 (删旧+建新, 对标 RAG-001)
    # 用 rebuild_index 而非 build_index: 语义层内容可能变了 (表增删/LLM推断变化),
    # 旧索引要清掉, 否则已删表的索引残留 → 检索到不存在的表
    # 失败降级不阻塞扫描 (索引只是优化检索, 缺失时检索返回空)
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
        import logging
        logging.getLogger("app.api.data_sources").warning(
            "扫描后建索引失败, RAG 检索将降级: %s", e
        )

    return {
        "semantic_model_id": sm.id,
        "version": new_version,
        "table_count": len(content.models),
        "index_count": index_count,
        "models": [m.name for m in content.models],
    }


# ChatBI 自己的系统表 (元数据表), 扫描时不调 LLM 推断
# 这些表用户不会查, 跳过能省 60%+ LLM 调用
_SYSTEM_TABLES = frozenset({
    "tenants", "users", "data_sources", "semantic_models",
    "conversations", "saved_queries", "audit_logs", "feedback",
})


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
      - 并行调 LLM (asyncio.gather, N 张表并发而非串行)
    LLM 失败静默降级 (退化列名已可用, 不阻塞扫描)。
    """
    import asyncio
    from app.core.llm_client import infer_column_chinese

    # 筛出需要推断的业务表 (跳过系统表 + 全有注释的表)
    tasks = []  # (model, needs_infer)
    for model in content.models:
        if model.name in _SYSTEM_TABLES:
            continue
        needs_infer = [
            c for c in model.columns
            if c.source == "auto_inferred" and c.confidence == 0.5
        ]
        if needs_infer:
            cols_for_llm = [{"name": c.name, "data_type": c.data_type} for c in needs_infer]
            tasks.append((model, needs_infer, cols_for_llm))

    if not tasks:
        return  # 没有需要推断的, 直接返回

    # 并行调 LLM (所有业务表同时推断, 而非串行)
    results = await asyncio.gather(
        *(infer_column_chinese(m.name, cols) for m, _, cols in tasks),
        return_exceptions=True,  # 单个失败不影响其他
    )

    # patch 结果
    for (model, needs_infer, _), inferred in zip(tasks, results):
        if isinstance(inferred, Exception) or not inferred:
            continue  # 失败/空, 保持退化列名
        name_to_col = {c.name: c for c in needs_infer}
        for col_name, display_name in inferred.items():
            if col_name in name_to_col:
                col = name_to_col[col_name]
                col.display_name = display_name
                col.confidence = 0.8  # LLM 推断, 升级置信度

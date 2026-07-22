"""
T014: 语义层管理 API (查看 / 版本历史 / 回滚)

端点:
  GET    /semantic-models?data_source_id=   查看当前版本
  GET    /semantic-models/{id}/versions     版本历史
  POST   /semantic-models/{id}/rollback?to_version=N  回滚 (append-only)
  GET    /semantic-models/{id}/diff?from=&to=  简单 diff

对标:
  - SEM-004: 版本管理 + 回滚 + diff
  - 回滚 = 复制目标版本成新 version (append-only, 不改历史)
  - v1 #48: 多租户隔离, v1 #41: 审计
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, get_current_user, require_admin, require_user, write_audit_log
from app.db.models import SemanticModel
from app.db.session import get_db

router = APIRouter(prefix="/semantic-models", tags=["semantic-models"])


class SemanticModelOut(BaseModel):
    id: str
    tenant_id: str
    data_source_id: str
    version: int
    is_current: bool
    content: dict  # 对标 F3: 结构化为 SemanticModelContent 会导致 schema 变更; 保持 dict 兼容前端


class VersionSummary(BaseModel):
    id: str
    version: int
    is_current: bool


# ── 查看当前版本 ──────────────────────────────────────────────

@router.get("", response_model=SemanticModelOut | None)
async def get_current_semantic_model(
    data_source_id: str = Query(...),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查看指定数据源的当前语义层版本 (is_current=True)。"""
    stmt = select(SemanticModel).where(
        SemanticModel.tenant_filter(user.tenant_id),
        SemanticModel.data_source_id == data_source_id,
        SemanticModel.is_current == True,  # noqa: E712
    )
    sm = (await db.execute(stmt)).scalar_one_or_none()
    if sm is None:
        return None
    return SemanticModelOut(
        id=sm.id, tenant_id=sm.tenant_id, data_source_id=sm.data_source_id,
        version=sm.version, is_current=sm.is_current, content=sm.content,
    )


# ── 版本历史 ──────────────────────────────────────────────────

@router.get("/{sm_id}/versions", response_model=list[VersionSummary])
async def list_versions(
    sm_id: str,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出该语义层的所有版本 (含历史)。"""
    # 先找到这条记录拿到 data_source_id + tenant 校验
    stmt = select(SemanticModel).where(
        SemanticModel.id == sm_id,
        SemanticModel.tenant_filter(user.tenant_id),
    )
    sm = (await db.execute(stmt)).scalar_one_or_none()
    if sm is None:
        raise HTTPException(status_code=404, detail="语义层不存在")

    all_versions = (
        await db.execute(
            select(SemanticModel).where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == sm.data_source_id,
            ).order_by(SemanticModel.version.desc())
        )
    ).scalars().all()
    return [
        VersionSummary(id=v.id, version=v.version, is_current=v.is_current)
        for v in all_versions
    ]


# ── 回滚 (append-only: 复制目标版本成新 version) ──────────────

@router.post("/{sm_id}/rollback", response_model=SemanticModelOut)
async def rollback_to_version(
    sm_id: str,
    to_version: int = Query(..., description="回滚到哪个版本号"),
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """回滚 = 把目标版本的 content 复制成新 version (append-only, 不改历史)。

    对标 SEM-004 + 版本管理最佳实践: 历史版本不可变。
    """
    # 校验当前记录归属 (多租户)
    stmt = select(SemanticModel).where(
        SemanticModel.id == sm_id,
        SemanticModel.tenant_filter(user.tenant_id),
    )
    current = (await db.execute(stmt)).scalar_one_or_none()
    if current is None:
        raise HTTPException(status_code=404, detail="语义层不存在")

    # 找目标版本
    target = (
        await db.execute(
            select(SemanticModel).where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == current.data_source_id,
                SemanticModel.version == to_version,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=f"版本 {to_version} 不存在")

    if target.version == current.version and current.is_current:
        raise HTTPException(status_code=400, detail="目标版本已是当前版本")

    # 旧版本 is_current=False
    old_currents = (
        await db.execute(
            select(SemanticModel).where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == current.data_source_id,
                SemanticModel.is_current == True,  # noqa: E712
            )
        )
    ).scalars().all()
    for old in old_currents:
        old.is_current = False

    # 新版本 = 目标 content 的副本
    new_version = max(v.version for v in old_currents) + 1 if old_currents else current.version + 1
    # 更稳妥: 取全局 max
    max_v = (
        await db.execute(
            select(SemanticModel.version).where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == current.data_source_id,
            ).order_by(SemanticModel.version.desc()).limit(1)
        )
    ).scalar_one()
    new_version = max_v + 1

    new_sm = SemanticModel(
        tenant_id=user.tenant_id,
        data_source_id=current.data_source_id,
        version=new_version,
        content=target.content,  # 复制 (dict 引用, JSON 序列化时会复制)
        is_current=True,
    )
    db.add(new_sm)
    await db.flush()
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="semantic_model", action="rollback", status="success",
        resource_id=new_sm.id,
        detail={"from_current": current.version, "to_version": to_version, "new_version": new_version},
    )
    await db.commit()
    await db.refresh(new_sm)

    # T021: 语义层变更 → 重建向量索引 (删旧 + 建新)
    # 失败降级不阻塞回滚 (索引只是优化检索)
    try:
        from app.services.indexer_update import rebuild_index
        from app.services.embedder import get_embedder
        from app.services.vector_store import get_vector_store
        from app.schemas.semantic_layer import SemanticModelContent
        content = SemanticModelContent(**new_sm.content)
        await rebuild_index(
            content=content,
            data_source_id=new_sm.data_source_id,
            store=get_vector_store(),
            embedder=get_embedder(),
        )
    except Exception as e:
        import logging
        logging.getLogger("app.api.semantic_models").warning(
            "回滚后重建索引失败, RAG 检索将降级: %s", e
        )

    return SemanticModelOut(
        id=new_sm.id, tenant_id=new_sm.tenant_id, data_source_id=new_sm.data_source_id,
        version=new_sm.version, is_current=new_sm.is_current, content=new_sm.content,
    )


# ── 局部更新 (T015 行内编辑: 表/列语义 → append-only 新版本) ────

class SemanticPatch(BaseModel):
    """语义层局部更新 (只改人工语义标注, 不动结构)。

    支持: 表的 display_name/description; 列的 display_name/semantic_type/description。
    source 改为 manual, confidence=1.0 (人工标注权威)。
    """
    table_name: str
    display_name: str | None = None        # 表的中文名
    description: str | None = None         # 表的描述
    # 列级编辑 (任一非空则更新该列)
    column_name: str | None = None
    column_display_name: str | None = None
    column_semantic_type: str | None = None  # measure/dimension/key
    column_description: str | None = None


@router.patch("/{sm_id}", response_model=SemanticModelOut)
async def patch_semantic_model(
    sm_id: str,
    body: SemanticPatch,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """局部更新语义层 (T015) — 写成新版本 (append-only, 不改历史)。

    对标 SEM-004: 历史不可变, 编辑 = 复制当前版本 → 改字段 → 存为新版本。
    编辑后重建向量索引 (RAG-005 语义边界)。
    """
    import copy
    # 校验当前记录归属
    stmt = select(SemanticModel).where(
        SemanticModel.id == sm_id,
        SemanticModel.tenant_filter(user.tenant_id),
    )
    current = (await db.execute(stmt)).scalar_one_or_none()
    if current is None:
        raise HTTPException(status_code=404, detail="语义层不存在")

    # 深拷贝 content → 修改 (不污染原对象)
    new_content = copy.deepcopy(current.content)
    models = new_content.get("models", [])
    target_model = next((m for m in models if m.get("name") == body.table_name), None)
    if target_model is None:
        raise HTTPException(status_code=404, detail=f"表 '{body.table_name}' 不在语义层中")

    # 表级编辑
    changed = False
    if body.display_name is not None:
        target_model["display_name"] = body.display_name
        target_model["source"] = "manual"
        target_model["confidence"] = 1.0
        changed = True
    if body.description is not None:
        target_model["description"] = body.description
        target_model["source"] = "manual"
        target_model["confidence"] = 1.0
        changed = True

    # 列级编辑
    if body.column_name:
        columns = target_model.get("columns", [])
        target_col = next((c for c in columns if c.get("name") == body.column_name), None)
        if target_col is None:
            raise HTTPException(status_code=404, detail=f"列 '{body.column_name}' 不在表 '{body.table_name}' 中")
        if body.column_display_name is not None:
            target_col["display_name"] = body.column_display_name
        if body.column_semantic_type is not None:
            if body.column_semantic_type not in ("measure", "dimension", "key"):
                raise HTTPException(status_code=422, detail="column_semantic_type 必须是 measure/dimension/key")
            target_col["semantic_type"] = body.column_semantic_type
        if body.column_description is not None:
            target_col["description"] = body.column_description
        target_col["source"] = "manual"
        target_col["confidence"] = 1.0
        changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="未提供任何更新字段")

    # 旧版本 is_current=False
    old_currents = (
        await db.execute(
            select(SemanticModel).where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == current.data_source_id,
                SemanticModel.is_current == True,  # noqa: E712
            )
        )
    ).scalars().all()
    for old in old_currents:
        old.is_current = False

    # 新版本号 = 全局 max + 1
    max_v = (
        await db.execute(
            select(SemanticModel.version).where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == current.data_source_id,
            ).order_by(SemanticModel.version.desc()).limit(1)
        )
    ).scalar_one()
    new_version = max_v + 1

    new_sm = SemanticModel(
        tenant_id=user.tenant_id,
        data_source_id=current.data_source_id,
        version=new_version,
        content=new_content,
        is_current=True,
    )
    db.add(new_sm)
    await db.flush()
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="semantic_model", action="patch", status="success",
        resource_id=new_sm.id,
        detail={"table": body.table_name, "column": body.column_name, "from_version": current.version},
    )
    await db.commit()
    await db.refresh(new_sm)

    # T021: 编辑后重建向量索引 (语义层是 RAG 检索的安全边界)
    try:
        from app.services.indexer_update import rebuild_index
        from app.services.embedder import get_embedder
        from app.services.vector_store import get_vector_store
        from app.schemas.semantic_layer import SemanticModelContent
        content = SemanticModelContent(**new_sm.content)
        await rebuild_index(
            content=content,
            data_source_id=new_sm.data_source_id,
            store=get_vector_store(),
            embedder=get_embedder(),
        )
    except Exception as e:
        import logging
        logging.getLogger("app.api.semantic_models").warning(
            "编辑后重建索引失败, RAG 检索将降级: %s", e
        )

    return SemanticModelOut(
        id=new_sm.id, tenant_id=new_sm.tenant_id, data_source_id=new_sm.data_source_id,
        version=new_sm.version, is_current=new_sm.is_current, content=new_sm.content,
    )


# ── 指标局部更新 (编辑/新增/删除 → append-only 新版本) ──────────

class MetricPatch(BaseModel):
    """指标局部更新 (人工校正 → source=manual)。

    支持: 编辑现有指标 / 新增指标 / 删除指标。
    编辑后 source 改为 manual (人工校正权威)。
    """
    table_name: str
    metric_name: str | None = None              # 编辑时指定 (新增时为新的 name)
    metric_display_name: str | None = None
    metric_formula: str | None = None
    metric_type: str | None = None               # single / composite
    metric_condition: str | None = None
    metric_description: str | None = None
    metric_factor_metric_names: list[str] | None = None
    delete_metric: bool = False                  # True = 删除该指标


@router.patch("/{sm_id}/metric", response_model=SemanticModelOut)
async def patch_metric(
    sm_id: str,
    body: MetricPatch,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """指标局部更新 — 编辑/新增/删除 → 写成新版本 (append-only)。

    对标 SEM-004: 历史不可变, 编辑 = 复制当前版本 → 改指标 → 存为新版本。
    指标是数据模型的附属品, 归表所有 (GMV 属于 biz_orders)。
    """
    import copy
    # 校验当前记录归属
    stmt = select(SemanticModel).where(
        SemanticModel.id == sm_id,
        SemanticModel.tenant_filter(user.tenant_id),
    )
    current = (await db.execute(stmt)).scalar_one_or_none()
    if current is None:
        raise HTTPException(status_code=404, detail="语义层不存在")

    # 深拷贝 content → 修改 (不污染原对象)
    new_content = copy.deepcopy(current.content)
    models = new_content.get("models", [])
    target_model = next((m for m in models if m.get("name") == body.table_name), None)
    if target_model is None:
        raise HTTPException(status_code=404, detail=f"表 '{body.table_name}' 不在语义层中")

    metrics = target_model.setdefault("metrics", [])

    # 删除指标
    if body.delete_metric:
        if not body.metric_name:
            raise HTTPException(status_code=400, detail="删除指标需指定 metric_name")
        before = len(metrics)
        metrics[:] = [m for m in metrics if m.get("name") != body.metric_name]
        if len(metrics) == before:
            raise HTTPException(status_code=404, detail=f"指标 '{body.metric_name}' 不存在")
    elif body.metric_name and any(m.get("name") == body.metric_name for m in metrics):
        # 编辑现有指标
        target_metric = next(m for m in metrics if m.get("name") == body.metric_name)
        if body.metric_display_name is not None:
            target_metric["display_name"] = body.metric_display_name
        if body.metric_formula is not None:
            target_metric["formula"] = body.metric_formula
        if body.metric_type is not None:
            if body.metric_type not in ("single", "composite"):
                raise HTTPException(status_code=422, detail="metric_type 必须是 single/composite")
            target_metric["type"] = body.metric_type
        if body.metric_condition is not None:
            target_metric["condition"] = body.metric_condition or None
        if body.metric_description is not None:
            target_metric["description"] = body.metric_description or None
        if body.metric_factor_metric_names is not None:
            target_metric["factor_metric_names"] = body.metric_factor_metric_names
        # 人工校正 → source=manual
        target_metric["source"] = "manual"
    else:
        # 新增指标 (需提供完整定义)
        if not body.metric_name:
            raise HTTPException(status_code=400, detail="新增指标需指定 metric_name")
        if not body.metric_display_name or not body.metric_formula:
            raise HTTPException(status_code=400, detail="新增指标需提供 display_name 和 formula")
        metric_type = body.metric_type or "single"
        if metric_type not in ("single", "composite"):
            raise HTTPException(status_code=422, detail="metric_type 必须是 single/composite")
        new_metric = {
            "name": body.metric_name,
            "display_name": body.metric_display_name,
            "formula": body.metric_formula,
            "type": metric_type,
            "condition": body.metric_condition or None,
            "description": body.metric_description or None,
            "factor_metric_names": body.metric_factor_metric_names,
            "co_occurrence": 0,
            "source": "manual",
        }
        # composite 必须有 factor_metric_names (SEM-005)
        if metric_type == "composite" and not body.metric_factor_metric_names:
            raise HTTPException(
                status_code=422,
                detail="composite 指标必须提供 factor_metric_names (SEM-005)",
            )
        metrics.append(new_metric)

    # 旧版本 is_current=False
    old_currents = (
        await db.execute(
            select(SemanticModel).where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == current.data_source_id,
                SemanticModel.is_current == True,  # noqa: E712
            )
        )
    ).scalars().all()
    for old in old_currents:
        old.is_current = False

    # 新版本号 = 全局 max + 1
    max_v = (
        await db.execute(
            select(SemanticModel.version).where(
                SemanticModel.tenant_filter(user.tenant_id),
                SemanticModel.data_source_id == current.data_source_id,
            ).order_by(SemanticModel.version.desc()).limit(1)
        )
    ).scalar_one()
    new_version = max_v + 1

    new_sm = SemanticModel(
        tenant_id=user.tenant_id,
        data_source_id=current.data_source_id,
        version=new_version,
        content=new_content,
        is_current=True,
    )
    db.add(new_sm)
    await db.flush()
    await write_audit_log(
        db, tenant_id=user.tenant_id, user_id=user.user_id,
        resource_type="semantic_model", action="patch_metric", status="success",
        resource_id=new_sm.id,
        detail={
            "table": body.table_name,
            "metric": body.metric_name,
            "delete": body.delete_metric,
            "from_version": current.version,
        },
    )
    await db.commit()
    await db.refresh(new_sm)

    return SemanticModelOut(
        id=new_sm.id, tenant_id=new_sm.tenant_id, data_source_id=new_sm.data_source_id,
        version=new_sm.version, is_current=new_sm.is_current, content=new_sm.content,
    )

@router.get("/{sm_id}/diff")
async def diff_versions(
    sm_id: str,
    frm: int = Query(..., alias="from"),
    to: int = Query(...),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """对比两个版本的 content 差异 (表级增删 + 模型名变化)。"""
    # 校验归属
    stmt = select(SemanticModel).where(
        SemanticModel.id == sm_id,
        SemanticModel.tenant_filter(user.tenant_id),
    )
    anchor = (await db.execute(stmt)).scalar_one_or_none()
    if anchor is None:
        raise HTTPException(status_code=404, detail="语义层不存在")

    async def _get(version: int) -> dict | None:
        row = (
            await db.execute(
                select(SemanticModel).where(
                    SemanticModel.tenant_filter(user.tenant_id),
                    SemanticModel.data_source_id == anchor.data_source_id,
                    SemanticModel.version == version,
                )
            )
        ).scalar_one_or_none()
        return row.content if row else None

    from_content = await _get(frm)
    to_content = await _get(to)
    if from_content is None:
        raise HTTPException(status_code=404, detail=f"版本 {frm} 不存在")
    if to_content is None:
        raise HTTPException(status_code=404, detail=f"版本 {to} 不存在")

    # 复用语义层 diff 工具 (DSO-04 元数据刷新也用它)
    from app.services.semantic_diff import diff_semantic_contents
    result = diff_semantic_contents(from_content, to_content)
    result["from_version"] = frm
    result["to_version"] = to
    return result

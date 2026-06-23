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
    content: dict


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
    return SemanticModelOut(
        id=new_sm.id, tenant_id=new_sm.tenant_id, data_source_id=new_sm.data_source_id,
        version=new_sm.version, is_current=new_sm.is_current, content=new_sm.content,
    )


# ── diff (简单: 对比两个版本的 content JSON) ──────────────────

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

    from_models = {m["name"]: m for m in from_content.get("models", [])}
    to_models = {m["name"]: m for m in to_content.get("models", [])}
    added = sorted(set(to_models) - set(from_models))
    removed = sorted(set(from_models) - set(to_models))
    common = sorted(set(from_models) & set(to_models))
    changed = [n for n in common if from_models[n] != to_models[n]]

    return {
        "from_version": frm,
        "to_version": to,
        "added_models": added,
        "removed_models": removed,
        "changed_models": changed,
        "unchanged_models": [n for n in common if n not in changed],
    }

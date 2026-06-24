"""
T050/T052: 可观测性 + 审计日志 API

对标:
  - T050: 可观测性面板 (审计日志查询 + 系统状态)
  - T052: 查询历史 + 审计日志页面

API:
  GET /audit-logs         审计日志列表 (admin 看全部, 分页)
  GET /conversations       对话列表 (从 StateStore)
  GET /conversations/{id}  对话详情 (所有轮次)
  GET /health/detail       系统状态详情 (T050 可观测)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_admin, require_user
from app.core.config import get_settings
from app.db.models import AuditLog
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["observability"])


class AuditLogOut(BaseModel):
    id: str
    user_id: str | None = None
    resource_type: str | None = None
    action: str | None = None
    status: str | None = None
    sql_text: str | None = None
    error_message: str | None = None
    created_at: str | None = None


@router.get("/audit-logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    resource_type: str | None = None,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """审计日志列表 (T052, admin only)。"""
    stmt = (
        select(AuditLog)
        .where(AuditLog.tenant_filter(user.tenant_id))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    result = await db.execute(stmt)
    return [
        AuditLogOut(
            id=log.id, user_id=log.user_id,
            resource_type=log.resource_type, action=log.action,
            status=log.status, sql_text=log.sql_text,
            error_message=log.error_message,
            created_at=log.created_at.isoformat() if log.created_at else None,
        )
        for log in result.scalars()
    ]


@router.get("/conversations")
async def list_conversations(
    user: AuthUser = Depends(require_user),
):
    """对话列表 (T052, 从 StateStore 文件)。"""
    from app.ai.state_store import StateStore
    store = StateStore()
    states_dir = Path(store._base_dir) / user.tenant_id
    if not states_dir.exists():
        return []
    conversations = []
    for f in sorted(states_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        lines = f.read_text(encoding="utf-8").strip().split("\n")
        if not lines or not lines[0]:
            continue
        try:
            last = json.loads(lines[-1])
            conv_id = f.stem
            state = last.get("state", {})
            conversations.append({
                "conversation_id": conv_id,
                "turn_count": len(lines),
                "last_sql": state.get("current_sql", "")[:60],
                "last_tables": state.get("current_tables", []),
                "timestamp": last.get("timestamp", ""),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return conversations


@router.get("/conversations/{conv_id}")
async def get_conversation_detail(
    conv_id: str,
    user: AuthUser = Depends(require_user),
):
    """对话详情 — 所有轮次 (T052)。"""
    from app.ai.state_store import StateStore
    store = StateStore()
    return store.list_turns(user.tenant_id, conv_id)


@router.get("/health/detail")
async def health_detail(
    user: AuthUser = Depends(require_admin),
):
    """系统状态详情 (T050 可观测性面板)。"""
    settings = get_settings()
    # 检查各组件状态
    components = {}

    # Milvus
    try:
        from app.core.milvus_client import get_milvus_client
        get_milvus_client()
        components["milvus"] = "ok"
    except Exception:
        components["milvus"] = "unavailable (degraded)"

    # Redis
    try:
        from app.core.redis_client import get_redis
        redis = await get_redis()
        components["redis"] = "ok" if redis else "unavailable (degraded)"
    except Exception:
        components["redis"] = "unavailable (degraded)"

    # Embedder
    try:
        from app.services.embedder import get_embedder
        emb = get_embedder()
        components["embedder"] = f"ok (dim={emb.dim})"
    except Exception as e:
        components["embedder"] = f"unavailable: {e}"

    # Skills
    try:
        from app.services.skills_loader import get_skills_loader
        skills = get_skills_loader().load_all()
        components["skills"] = f"{len(skills)} loaded"
    except Exception:
        components["skills"] = "0 (none)"

    return {
        "status": "ok",
        "components": components,
        "config": {
            "llm_model": settings.llm_model,
            "embedding_backend": settings.embedding_backend,
            "vector_store_backend": settings.vector_store_backend,
            "debug": settings.debug,
        },
    }

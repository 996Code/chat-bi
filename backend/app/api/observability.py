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
from sqlalchemy import case, func, select
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
    # DSO-07: 慢查询标记
    duration_ms: int | None = None
    is_slow: bool = False
    # 指标命中信息 (含 source + type)
    detail: dict | None = None


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
            duration_ms=log.duration_ms,
            is_slow=log.is_slow,
            detail=log.detail,
        )
        for log in result.scalars()
    ]


@router.get("/slow-queries", response_model=list[AuditLogOut])
async def list_slow_queries(
    limit: int = Query(50, le=200),
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """慢查询列表 (DSO-07, admin only) — is_slow=True 的审计记录, 按耗时倒序。

    阈值由 config.sql_slow_query_threshold 控制 (默认 10s)。
    """
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.tenant_filter(user.tenant_id),
            AuditLog.is_slow == True,  # noqa: E712
        )
        .order_by(AuditLog.duration_ms.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        AuditLogOut(
            id=log.id, user_id=log.user_id,
            resource_type=log.resource_type, action=log.action,
            status=log.status, sql_text=log.sql_text,
            error_message=log.error_message,
            created_at=log.created_at.isoformat() if log.created_at else None,
            duration_ms=log.duration_ms,
            is_slow=log.is_slow,
            detail=log.detail,
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
            first = json.loads(lines[0])
            last = json.loads(lines[-1])
            conv_id = f.stem
            state = last.get("state", {})
            first_state = first.get("state", {})
            # 标题: 优先用 title, 其次 first_question, 最后 last_sql 截断
            title = state.get("title") or first_state.get("first_question") or state.get("current_sql", "")[:40]
            if not title:
                title = "新对话"
            # 汇总全对话 token (从各轮 prompts 字段累加)
            total_prompt_tokens = 0
            total_completion_tokens = 0
            for line in lines:
                if not line:
                    continue
                entry = json.loads(line)
                prompts = entry.get("state", {}).get("prompts")
                if prompts:
                    total_prompt_tokens += sum(p.get("prompt_tokens", 0) for p in prompts)
                    total_completion_tokens += sum(p.get("completion_tokens", 0) for p in prompts)
            conversations.append({
                "conversation_id": conv_id,
                "title": title,
                "turn_count": len(lines),
                "last_sql": state.get("current_sql", "")[:60],
                "last_tables": state.get("current_tables", []),
                "timestamp": last.get("timestamp", ""),
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            })
        except (json.JSONDecodeError, KeyError):
            continue
    # 按时间倒序 (最新对话在前, 与 ChatGPT/微信等聊天应用一致)
    # T050 的 token 排序需求由 ObservabilityView 的 Top 50 高消耗对话面板满足
    conversations.sort(key=lambda c: c["timestamp"], reverse=True)
    return conversations


@router.get("/conversations/{conv_id}")
async def get_conversation_detail(
    conv_id: str,
    user: AuthUser = Depends(require_user),
):
    """对话详情 — 所有轮次 (T052)。"""
    from app.ai.state_store import StateStore
    store = StateStore()
    turns = store.list_turns(user.tenant_id, conv_id)
    # 注: 骨架行 + 完整行故意复用同一 turn 号 (start 落骨架, 末尾落完整, 复用 turn)
    # 前端 loadConversation 按 question 合并连续同 turn 行, 不需要这里重新编号
    return turns


@router.get("/conversations/{conv_id}/title")
async def get_conversation_title(
    conv_id: str,
    user: AuthUser = Depends(require_user),
):
    """对话标题 — 轻量接口, 供记忆页显示来源对话名称。

    只读 StateStore 最后一轮的 title 字段, 不拉全量轮次。
    """
    from app.ai.state_store import StateStore
    store = StateStore()
    turns = store.list_turns(user.tenant_id, conv_id)
    if not turns:
        return {"id": conv_id, "title": ""}
    last = turns[-1]
    state = last.get("state", {})
    first_state = turns[0].get("state", {})
    title = state.get("title") or first_state.get("first_question") or "新对话"
    return {"id": conv_id, "title": title}


@router.get("/conversations/{conv_id}/trace")
async def get_conversation_trace(
    conv_id: str,
    user: AuthUser = Depends(require_admin),
):
    """对话 trace 导出 (T050 dump-prompts) — 各轮 LLM prompt + token 统计。

    仅 admin 可访问 (prompt 可能含敏感 schema/数据)。
    DEBUG 模式才持久化 prompt 文本, 非 DEBUG 时 prompts 字段为 null。
    """
    from app.ai.state_store import StateStore
    store = StateStore()
    turns = store.list_turns(user.tenant_id, conv_id)
    # 自愈: 如果所有 turn 值相同 (历史脏数据全为 1), 按行序重新编号
    turn_values = [t.get("turn", 0) for t in turns]
    if turns and len(set(turn_values)) == 1:
        for i, t in enumerate(turns):
            t["turn"] = i + 1
    trace = []
    total_prompt = 0
    total_completion = 0
    for t in turns:
        state = t.get("state", {})
        prompts = state.get("prompts")
        # 汇总本轮 token
        turn_prompt_tokens = sum(p.get("prompt_tokens", 0) for p in prompts) if prompts else 0
        turn_completion_tokens = sum(p.get("completion_tokens", 0) for p in prompts) if prompts else 0
        total_prompt += turn_prompt_tokens
        total_completion += turn_completion_tokens
        trace.append({
            "turn": t.get("turn", 0),
            "timestamp": t.get("timestamp", ""),
            "question": state.get("question", ""),
            "sql": state.get("current_sql", ""),
            "prompts": prompts,
            "prompt_tokens": turn_prompt_tokens,
            "completion_tokens": turn_completion_tokens,
        })
    return {
        "conversation_id": conv_id,
        "turns": trace,
        "summary": {
            "total_turns": len(turns),
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
        },
    }


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
        from app.services.skills_loader import SkillsLoader
        skills = SkillsLoader(base_dir=f"skills/{user.tenant_id}").load_all()
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


@router.get("/datasource-metrics")
async def get_datasource_metrics(
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """数据源状态监控 (DSO-05, admin) — 按数据源聚合最近 24h 查询统计。

    返回 [{data_source_id, name, query_count, avg_duration_ms, error_rate, slow_count}]。
    数据源名称从 data_sources 表 JOIN (无 data_source_id 的审计记录归为 unknown)。
    """
    from datetime import datetime, timedelta, timezone
    from app.db.models import DataSource

    since = datetime.now(timezone.utc) - timedelta(hours=24)

    # 聚合统计 (GROUP BY data_source_id)
    stmt = (
        select(
            AuditLog.data_source_id,
            func.count(AuditLog.id).label("query_count"),
            func.avg(AuditLog.duration_ms).label("avg_duration_ms"),
            func.sum(case((AuditLog.status == "fail", 1), else_=0)).label("error_count"),
            func.sum(case((AuditLog.is_slow == True, 1), else_=0)).label("slow_count"),  # noqa: E712
        )
        .where(
            AuditLog.tenant_filter(user.tenant_id),
            AuditLog.resource_type == "chat",
            AuditLog.data_source_id.isnot(None),
            AuditLog.created_at >= since,
        )
        .group_by(AuditLog.data_source_id)
    )
    rows = (await db.execute(stmt)).all()

    # 取数据源名称 (避免 N+1, 一次查全)
    ds_ids = [r.data_source_id for r in rows]
    ds_names: dict[str, str] = {}
    if ds_ids:
        ds_rows = (
            await db.execute(
                select(DataSource.id, DataSource.name).where(DataSource.id.in_(ds_ids))
            )
        ).all()
        ds_names = {r.id: r.name for r in ds_rows}

    return [
        {
            "data_source_id": r.data_source_id,
            "name": ds_names.get(r.data_source_id, "unknown"),
            "query_count": r.query_count,
            "avg_duration_ms": round(r.avg_duration_ms) if r.avg_duration_ms else 0,
            "error_rate": round(r.error_count / r.query_count * 100, 1) if r.query_count else 0.0,
            "slow_count": r.slow_count,
        }
        for r in rows
    ]

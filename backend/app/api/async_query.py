"""
PERF-03: 异步查询 — 大查询不阻塞对话流

对标 V1 设计 (BackgroundTasks + task_id 轮询):
  - POST /async-query: 提交 → 创建 pending Task → asyncio.create_task 后台跑 Agent
  - GET /async-query/{id}: 轮询状态/进度/结果
  - DELETE /async-query/{id}: 取消 (标记 cancelled)

设计:
  - 后台任务用独立 db session (async sessionmaker 新建, 不共享请求 session)
  - 进度映射 Agent 阶段: intent 10% / schema 30% / sql 50% / execute 70% / chart 90% / done 100%
  - 防重复: 同 user 有 pending/running 的同 question → 409
  - 取消: 标记 cancelled, 后台任务检查标志位退出
  - 结果采样: 前 50 行 + chart_option (防 OOM)
  - 定时清理: cleanup_expired_tasks (scheduler 每小时调)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_user, write_audit_log
from app.db.models import QueryTask
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/async-query", tags=["async-query"])

# 运行中任务的取消标志 (内存, 单进程; key=task_id)
# 后台任务定期检查, 设了就退出
_cancel_flags: set[str] = set()

# Agent 阶段 → 进度映射
_STAGE_PROGRESS = {
    "intent": 10,
    "schema": 30,
    "sql": 50,
    "execute": 70,
    "chart": 90,
}


# ── 请求 DTO ──────────────────────────────────────────────────

class AsyncQueryRequest(BaseModel):
    question: str
    data_source_id: str


class AsyncTaskOut(BaseModel):
    id: str
    status: str
    progress: int
    current_stage: str | None = None
    result: dict | None = None
    error: str | None = None
    created_at: str | None = None


# ── 端点 ──────────────────────────────────────────────────────

@router.post("", response_model=AsyncTaskOut, status_code=status.HTTP_201_CREATED)
async def create_async_query(
    body: AsyncQueryRequest,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """提交异步查询 (PERF-03)。

    防重复: 同 user 有 pending/running 的同 question → 409。
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question 不能为空")

    # 防重复: 检查是否已有进行中的相同查询
    existing = (
        await db.execute(
            select(QueryTask).where(
                QueryTask.tenant_filter(user.tenant_id),
                QueryTask.user_id == user.user_id,
                QueryTask.question == question,
                QueryTask.status.in_(["pending", "running"]),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="已有进行中的相同查询, 请等待完成或取消后重试")

    # 创建 pending 任务
    task = QueryTask(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        data_source_id=body.data_source_id,
        question=question,
        status="pending",
        progress=0,
    )
    db.add(task)
    await db.flush()
    task_id = task.id
    await db.commit()

    # 后台执行 (不阻塞响应)
    asyncio.create_task(_run_agent_background(task_id, user.tenant_id, body.data_source_id, question))

    return AsyncTaskOut(
        id=task_id, status="pending", progress=0, current_stage=None,
        created_at=task.created_at.isoformat() if task.created_at else None,
    )


@router.get("/{task_id}", response_model=AsyncTaskOut)
async def get_async_query(
    task_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """轮询异步查询状态/结果 (PERF-03)。"""
    task = (
        await db.execute(
            select(QueryTask).where(
                QueryTask.tenant_filter(user.tenant_id),
                QueryTask.id == task_id,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    return AsyncTaskOut(
        id=task.id, status=task.status, progress=task.progress,
        current_stage=task.current_stage, result=task.result_json,
        error=task.error,
        created_at=task.created_at.isoformat() if task.created_at else None,
    )


@router.delete("/{task_id}")
async def cancel_async_query(
    task_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """取消异步查询 (PERF-03)。标记 cancelled, 后台任务检查标志退出。"""
    task = (
        await db.execute(
            select(QueryTask).where(
                QueryTask.tenant_filter(user.tenant_id),
                QueryTask.id == task_id,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"任务已结束 ({task.status}), 无法取消")

    # 设取消标志 (后台任务检查退出)
    _cancel_flags.add(task_id)
    task.status = "cancelled"
    task.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"cancelled": task_id}


# ── 后台任务执行 ──────────────────────────────────────────────

async def _run_agent_background(task_id: str, tenant_id: str, data_source_id: str, question: str) -> None:
    """后台跑 Agent 全流程 (独立 db session, 更新进度/状态)。

    用 run_agent 状态机 + 进度映射。失败/取消都更新 task 状态。
    """
    from app.db.session import get_async_session_factory

    factory = get_async_session_factory()
    async with factory() as session:
        try:
            # 检查取消
            if await _is_cancelled(session, task_id):
                await _finish_task(session, task_id, "cancelled")
                return

            await _update_task(session, task_id, status="running", progress=5, stage="starting")

            # 装配 Agent 依赖 (复用 chat.py build_agent_deps)
            from app.api.chat import build_agent_deps
            try:
                deps, content = await build_agent_deps(data_source_id, tenant_id, session)
            except Exception as e:
                await _finish_task(session, task_id, "failed", error=f"Agent 初始化失败: {e}")
                return

            # 运行 Agent (run_agent 状态机)
            from app.ai.agent import AgentState, run_agent
            state = AgentState(question=question, semantic_content=content)

            # 分阶段更新进度 (run_agent 内部不回调, 我们在调用前后更新主要阶段)
            await _update_task(session, task_id, progress=10, stage="intent")

            if await _is_cancelled(session, task_id):
                await _finish_task(session, task_id, "cancelled")
                return

            state = await run_agent(state, deps)

            # 取结果
            exec_result = state.execute_result
            result_json = None
            if state.success:
                # 结果采样 (前 50 行防 OOM)
                from app.api.chat_stream import _normalize_value
                rows_sample = []
                cols = []
                if exec_result and hasattr(exec_result, "rows"):
                    rows_sample = [
                        [_normalize_value(v) for v in r] for r in (exec_result.rows or [])[:50]
                    ]
                    cols = list(exec_result.columns) if hasattr(exec_result, "columns") else []
                result_json = {
                    "reply": state.reply or None,
                    "sql": state.sql or None,
                    "columns": cols,
                    "rows": rows_sample,
                    "row_count": len(exec_result.rows) if exec_result and hasattr(exec_result, "rows") else 0,
                    "chart": state.chart_option,
                }

            if await _is_cancelled(session, task_id):
                await _finish_task(session, task_id, "cancelled")
                return

            if state.success:
                await _finish_task(session, task_id, "done", progress=100, result=result_json)
            else:
                await _finish_task(session, task_id, "failed", error=state.error or "查询失败")

        except Exception as e:
            logger.exception("异步任务 %s 执行异常", task_id)
            try:
                await _finish_task(session, task_id, "failed", error=f"执行异常: {e}")
            except Exception:
                pass
        finally:
            _cancel_flags.discard(task_id)


async def _update_task(session: AsyncSession, task_id: str, status: str | None = None, progress: int | None = None, stage: str | None = None) -> None:
    """更新任务进度 (不抛异常)。"""
    try:
        task = (
            await session.execute(select(QueryTask).where(QueryTask.id == task_id))
        ).scalar_one_or_none()
        if task is None:
            return
        if status is not None:
            task.status = status
        if progress is not None:
            task.progress = progress
        if stage is not None:
            task.current_stage = stage
        await session.commit()
    except Exception as e:
        logger.warning("更新任务 %s 进度失败: %s", task_id, e)


async def _finish_task(session: AsyncSession, task_id: str, status: str, progress: int | None = None, result: dict | None = None, error: str | None = None) -> None:
    """结束任务 (写最终状态 + completed_at)。

    不覆盖已被取消的任务 (高-5 修复: 用户取消后后台任务不应再覆盖为 done)。
    """
    task = (
        await session.execute(select(QueryTask).where(QueryTask.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        return
    # 已被取消 (用户主动) → 不覆盖 (避免后台跑完又写回 done)
    if task.status == "cancelled" and status != "cancelled":
        logger.info("任务 %s 已取消, 跳过状态更新 (%s)", task_id, status)
        return
    task.status = status
    if progress is not None:
        task.progress = progress
    if result is not None:
        task.result_json = result
    if error is not None:
        task.error = error
    task.completed_at = datetime.now(timezone.utc)
    await session.commit()


async def _is_cancelled(session: AsyncSession, task_id: str) -> bool:
    """从 DB 查任务是否已取消 (高-4 修复: 多 worker 下内存标志不可靠, 改查 DB)。

    仍兼容内存标志 (单 worker 快路径)。
    """
    if task_id in _cancel_flags:
        return True
    task = (
        await session.execute(select(QueryTask.status).where(QueryTask.id == task_id))
    ).scalar_one_or_none()
    return task == "cancelled"


async def cleanup_expired_tasks(session: AsyncSession) -> int:
    """清理过期已完成任务 (scheduler 定时调, 返回清理数)。"""
    from datetime import timedelta
    from app.core.config import get_settings
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.async_task_retention_hours)

    tasks = (
        await session.execute(
            select(QueryTask).where(
                QueryTask.status.in_(["done", "failed", "cancelled"]),
                QueryTask.completed_at < cutoff,
            )
        )
    ).scalars().all()
    for t in tasks:
        await session.delete(t)
    if tasks:
        logger.info("清理 %d 个过期异步任务", len(tasks))
    return len(tasks)

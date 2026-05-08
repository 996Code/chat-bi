"""查询保存与历史 API。"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, or_

from app.api._helpers import api_error, iso_format

from app.db.session import get_db
from app.db.models import SavedQuery
from app.core.security import get_current_user
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/queries", tags=["查询历史"])


@router.get("", response_model=dict)
async def list_queries(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cursor: str | None = Query(None, description="游标：上次返回的最后一条记录的 created_at"),
    page_size: int = Query(20, ge=1, le=100),
    text: str | None = Query(None, description="搜索问题文本"),
    datasource_id: str | None = Query(None, description="按数据源筛选"),
    status: str | None = Query(None, description="按状态筛选: success/error"),
):
    """列出当前用户的查询历史（cursor-based 分页 + 搜索筛选）。"""
    tenant_id = user["tenant_id"]
    conditions = [
        SavedQuery.tenant_id == tenant_id,
        SavedQuery.user_id == user["user_id"],
    ]

    if text:
        conditions.append(SavedQuery.query_text.ilike(f"%{text}%"))
    if datasource_id:
        conditions.append(SavedQuery.datasource_id == datasource_id)
    if status == "success":
        conditions.append(SavedQuery.success == True)  # noqa: E712
    elif status == "error":
        conditions.append(SavedQuery.success == False)  # noqa: E712

    query = (
        select(SavedQuery)
        .where(and_(*conditions))
        .order_by(desc(SavedQuery.created_at))
        .limit(page_size + 1)
    )

    if cursor:
        query = query.where(SavedQuery.created_at < cursor)

    result = await db.execute(query)
    queries = list(result.scalars().all())

    has_next = len(queries) > page_size
    if has_next:
        queries = queries[:page_size]

    next_cursor = str(queries[-1].created_at) if queries and has_next else None

    return {
        "data": [
            {
                "id": str(q.id),
                "name": q.name,
                "query_text": q.query_text,
                "generated_sql": q.generated_sql,
                "datasource_id": str(q.datasource_id),
                "success": q.success,
                "execution_time_ms": q.execution_time_ms,
                "row_count": q.row_count,
                "error": q.error,
                "chart_type": q.chart_type,
                "created_at": iso_format(q.created_at),
            }
            for q in queries
        ],
        "next_cursor": next_cursor,
        "has_more": has_next,
    }


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def save_query(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """保存当前查询（手动收藏）。"""
    tenant_id = user["tenant_id"]
    name = data.get("name", "").strip()
    query_text = data.get("query_text", "")
    generated_sql = data.get("generated_sql", "")
    datasource_id = data.get("datasource_id")

    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=api_error("INVALID_INPUT", "查询名称不能为空"),
        )
    if not datasource_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=api_error("INVALID_INPUT", "数据源 ID 不能为空"),
        )

    sq = SavedQuery(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user["user_id"],
        name=name,
        query_text=query_text,
        generated_sql=generated_sql,
        datasource_id=datasource_id,
    )
    db.add(sq)
    await db.commit()
    await db.refresh(sq)

    from app.services.analytics_service import track_event, EVENT_QUERY_SAVE
    await track_event(db, tenant_id, user["user_id"], EVENT_QUERY_SAVE, {"name": sq.name})
    await db.commit()

    return {
        "id": str(sq.id),
        "name": sq.name,
        "query_text": sq.query_text,
        "generated_sql": sq.generated_sql,
        "datasource_id": str(sq.datasource_id),
        "created_at": iso_format(sq.created_at),
    }


@router.get("/{query_id}", response_model=dict)
async def get_query(
    query_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个查询详情。"""
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == query_id,
            SavedQuery.tenant_id == user["tenant_id"],
            SavedQuery.user_id == user["user_id"],
        )
    )
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=api_error("NOT_FOUND", "查询不存在"),
        )

    return {
        "id": str(sq.id),
        "name": sq.name,
        "query_text": sq.query_text,
        "generated_sql": sq.generated_sql,
        "datasource_id": str(sq.datasource_id),
        "success": sq.success,
        "execution_time_ms": sq.execution_time_ms,
        "row_count": sq.row_count,
        "error": sq.error,
        "chart_type": sq.chart_type,
        "created_at": iso_format(sq.created_at),
    }


@router.delete("/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_query(
    query_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除查询历史。"""
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == query_id,
            SavedQuery.tenant_id == user["tenant_id"],
            SavedQuery.user_id == user["user_id"],
        )
    )
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=api_error("NOT_FOUND", "查询不存在"),
        )

    await db.delete(sq)
    await db.commit()
    return None


@router.post("/{query_id}/re-run", response_model=dict)
async def re_run_query(
    query_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """重新运行保存的查询。"""
    result = await db.execute(
        select(SavedQuery).where(
            SavedQuery.id == query_id,
            SavedQuery.tenant_id == user["tenant_id"],
            SavedQuery.user_id == user["user_id"],
        )
    )
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=api_error("NOT_FOUND", "查询不存在"),
        )

    return {
        "question": sq.query_text,
        "sql": sq.generated_sql,
        "datasource_id": str(sq.datasource_id),
    }
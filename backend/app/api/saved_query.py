"""查询保存与历史 API。"""
import json
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.db.session import get_db
from app.db.models import SavedQuery
from app.core.security import get_current_user
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/queries", tags=["查询历史"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.get("", response_model=list[dict])
async def list_queries(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """列出当前用户的查询历史。"""
    offset = (page - 1) * page_size

    result = await db.execute(
        select(SavedQuery)
        .where(SavedQuery.user_id == user["user_id"])
        .order_by(desc(SavedQuery.created_at))
        .offset(offset)
        .limit(page_size)
    )
    queries = result.scalars().all()

    return [
        {
            "id": str(q.id),
            "name": q.name,
            "query_text": q.query_text,
            "generated_sql": q.generated_sql,
            "datasource_id": str(q.datasource_id),
            "created_at": str(q.created_at),
        }
        for q in queries
    ]


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def save_query(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """保存当前查询。"""
    name = data.get("name", "").strip()
    query_text = data.get("query_text", "")
    generated_sql = data.get("generated_sql", "")
    datasource_id = data.get("datasource_id")

    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "查询名称不能为空"),
        )
    if not datasource_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "数据源 ID 不能为空"),
        )

    import uuid
    sq = SavedQuery(
        id=uuid.uuid4(),
        tenant_id=user["tenant_id"],
        user_id=user["user_id"],
        name=name,
        query_text=query_text,
        generated_sql=generated_sql,
        datasource_id=datasource_id,
    )
    db.add(sq)
    await db.commit()
    await db.refresh(sq)

    # Analytics
    from app.services.analytics_service import track_event, EVENT_QUERY_SAVE
    await track_event(db, user["tenant_id"], user["user_id"], EVENT_QUERY_SAVE, {"name": sq.name})
    await db.commit()

    return {
        "id": str(sq.id),
        "name": sq.name,
        "query_text": sq.query_text,
        "generated_sql": sq.generated_sql,
        "datasource_id": str(sq.datasource_id),
        "created_at": str(sq.created_at),
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
            SavedQuery.user_id == user["user_id"],
        )
    )
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "查询不存在"),
        )

    return {
        "id": str(sq.id),
        "name": sq.name,
        "query_text": sq.query_text,
        "generated_sql": sq.generated_sql,
        "datasource_id": str(sq.datasource_id),
        "created_at": str(sq.created_at),
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
            SavedQuery.user_id == user["user_id"],
        )
    )
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "查询不存在"),
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
            SavedQuery.user_id == user["user_id"],
        )
    )
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "查询不存在"),
        )

    return {
        "question": sq.query_text,
        "sql": sq.generated_sql,
        "datasource_id": str(sq.datasource_id),
    }

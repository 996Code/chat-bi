import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import Conversation
from app.core.security import get_current_user
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["会话"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.get("")
async def list_conversations(
    datasource_id: str | None = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户的会话列表，按更新时间倒序。可按数据源过滤。"""
    tenant_id = user["tenant_id"]
    q = select(Conversation).where(
        Conversation.tenant_id == uuid.UUID(tenant_id),
        Conversation.user_id == uuid.UUID(user["user_id"]),
    )
    if datasource_id:
        q = q.where(Conversation.datasource_id == datasource_id)
    result = await db.execute(q.order_by(Conversation.updated_at.desc()))
    convs = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "title": c.title or "新对话",
            "datasource_id": c.datasource_id,
            "message_count": len(json.loads(c.messages or "[]")),
            "created_at": str(c.created_at),
            "updated_at": str(c.updated_at),
        }
        for c in convs
    ]


@router.get("/{conv_id}")
async def get_conversation(
    conv_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取单个会话详情（包含完整消息）。"""
    try:
        conv_uuid = uuid.UUID(conv_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的会话 ID")
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_uuid,
            Conversation.tenant_id == uuid.UUID(tenant_id),
            Conversation.user_id == uuid.UUID(user["user_id"]),
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return {
        "id": str(conv.id),
        "title": conv.title or "新对话",
        "datasource_id": conv.datasource_id,
        "messages": json.loads(conv.messages or "[]"),
        "created_at": str(conv.created_at),
        "updated_at": str(conv.updated_at),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新会话。"""
    tenant_id = user["tenant_id"]
    conv = Conversation(
        tenant_id=uuid.UUID(tenant_id),
        user_id=uuid.UUID(user["user_id"]),
        title=data.get("title", "新对话"),
        datasource_id=data.get("datasource_id", ""),
        messages=json.dumps(data.get("messages", []), ensure_ascii=False),
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return {
        "id": str(conv.id),
        "title": conv.title,
        "datasource_id": conv.datasource_id,
        "message_count": len(json.loads(conv.messages)),
        "created_at": str(conv.created_at),
        "updated_at": str(conv.updated_at),
    }


@router.put("/{conv_id}")
async def update_conversation(
    conv_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新会话（追加消息/更新标题）。"""
    try:
        conv_uuid = uuid.UUID(conv_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的会话 ID")
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_uuid,
            Conversation.tenant_id == uuid.UUID(tenant_id),
            Conversation.user_id == uuid.UUID(user["user_id"]),
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")

    if "title" in data:
        conv.title = data["title"]
    if "messages" in data:
        conv.messages = json.dumps(data["messages"], ensure_ascii=False)
    if "datasource_id" in data:
        conv.datasource_id = data["datasource_id"]

    await db.commit()
    return {"id": str(conv.id), "title": conv.title, "message_count": len(json.loads(conv.messages))}


@router.delete("/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conv_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除会话。"""
    try:
        conv_uuid = uuid.UUID(conv_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的会话 ID")
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conv_uuid,
            Conversation.tenant_id == uuid.UUID(tenant_id),
            Conversation.user_id == uuid.UUID(user["user_id"]),
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    await db.delete(conv)
    await db.commit()
    return None

"""
Agent Memory API — 记忆管理 (T045)

对标 Claude Code MemoryFileSelector: 查看/编辑/删除 agent 记忆。
记忆是 agent 跨会话保留的事实/偏好 (文件存储 + MEMORY.md 索引)。

安全: 仅 admin 可操作。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryOut(BaseModel):
    name: str
    description: str
    type: str
    content: str


class MemorySave(BaseModel):
    name: str
    description: str
    content: str
    type: str = "project"


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    user=Depends(require_admin),
):
    """列出所有记忆 (T045)。"""
    from app.core.agent_memory import get_agent_memory_store
    store = get_agent_memory_store()
    memories = []
    for m in store.list_memories():
        full = store.read_memory(m["name"]) or ""
        # 去掉 frontmatter, 只返回正文
        content = full
        if full.startswith("---"):
            parts = full.split("---", 2)
            content = parts[2].strip() if len(parts) > 2 else full
        memories.append(MemoryOut(
            name=m["name"], description=m["description"],
            type=m["type"], content=content,
        ))
    return memories


@router.put("", response_model=MemoryOut)
async def save_memory(
    body: MemorySave,
    user=Depends(require_admin),
):
    """创建或更新记忆 (T045)。"""
    from app.core.agent_memory import get_agent_memory_store
    store = get_agent_memory_store()
    store.save_memory(
        name=body.name, description=body.description,
        content=body.content, memory_type=body.type,
    )
    logger.info("记忆已保存: %s (by %s)", body.name, user.user_id)
    return MemoryOut(name=body.name, description=body.description, type=body.type, content=body.content)


@router.delete("/{name}")
async def delete_memory(
    name: str,
    user=Depends(require_admin),
):
    """删除记忆 (T045)。"""
    from app.core.agent_memory import get_agent_memory_store
    store = get_agent_memory_store()
    if not store.delete_memory(name):
        raise HTTPException(status_code=404, detail=f"记忆 '{name}' 不存在")
    logger.info("记忆已删除: %s (by %s)", name, user.user_id)
    return {"deleted": name}

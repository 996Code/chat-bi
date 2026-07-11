"""
Agent Memory API — 记忆管理 (T045)

对标 Claude Code MemoryFileSelector: 查看/编辑/删除 agent 记忆。
记忆是 agent 跨会话保留的事实/偏好 (文件存储 + MEMORY.md 索引)。

安全: 仅 admin 可操作。
多租户隔离: 按 tenant_id 隔离到 memory/{tenant_id}/ 子目录 (对标 S1)。
数据源隔离: 记忆按 data_source_id 隔离 (不同数据源的业务约定不同,
            避免跨库召回错误知识)。

标识: 每条记忆有不可变的 UUID id (文件名), name 是可编辑的标题。

整理: 异步任务模式 (对标数据源扫描), POST 返回 202, 前端轮询状态。
"""
from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

# 路径穿越防护: memory name 是显示标题, 允许安全字符 + 空格 + 中文标点
# (id 才是文件名, name 不直接用于文件操作, 所以可以宽松)
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fff\s\u3000-\u303f\uff00-\uffef]+$")


def _validate_memory_name(name: str) -> str:
    """校验 memory name 不含路径穿越字符。"""
    if not name or not _SAFE_NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="记忆名称只允许字母、数字、下划线、连字符和中文",
        )
    return name


def _validate_id(value: str, name: str) -> str:
    """校验租户/数据源 ID 不含路径穿越字符。"""
    if not value or ".." in value or "/" in value or "\\" in value:
        raise HTTPException(status_code=400, detail=f"无效的{name}")
    return value


def _validate_mem_id(mem_id: str) -> str:
    """校验记忆 ID (UUID 或旧格式 slug 文件名) — 严格白名单防路径穿越。

    允许: UUID (xxxxxxxx-xxxx-...) 和旧格式 slug (a-z0-9_-)
    禁止: .., /, \\, 空格, 换行等一切可能用于路径操作或 frontmatter 注入的字符。
    """
    if not mem_id or not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$|^[a-zA-Z0-9_\-]+$", mem_id):
        raise HTTPException(status_code=400, detail="无效的记忆标识")
    return mem_id


def _get_ds_store(tenant_id: str, data_source_id: str):
    """获取数据源隔离的 memory store (memory/{tenant_id}/{data_source_id}/)。

    首次访问时自动从全局模板目录 (memory/_template/) 复制种子记忆,
    确保新数据源不会看到空白页面。
    """
    from pathlib import Path
    from app.core.agent_memory import AgentMemoryStore
    _validate_id(tenant_id, "租户标识")
    _validate_id(data_source_id, "数据源标识")
    mem_dir = Path(f"memory/{tenant_id}/{data_source_id}")
    mem_dir.mkdir(parents=True, exist_ok=True)
    # 种子数据: 目录为空时从 _template/ 复制默认记忆
    _ensure_seed_memories(mem_dir)
    return AgentMemoryStore(base_dir=str(mem_dir))


def _ensure_seed_memories(target_dir):
    """目录为空时从 _template/ 复制种子记忆 (不覆盖已有内容)。

    种子记忆包含电商场景常见业务约定, 用户可自由编辑或删除。
    """
    import shutil
    from pathlib import Path
    target_dir = Path(target_dir)
    # 已有记忆 → 不覆盖 (排除 MEMORY.md 索引文件)
    if any(f for f in target_dir.glob("*.md") if f.name != "MEMORY.md"):
        return
    # _template 在 memory/ 根目录下
    template_dir = target_dir.parent.parent / "_template"
    if not template_dir.is_dir():
        return
    try:
        for item in template_dir.iterdir():
            if item.is_file() and item.suffix == ".md" and item.name != "MEMORY.md":
                dest = target_dir / item.name
                if not dest.exists():
                    shutil.copy2(item, dest)
        logger.info("种子 Memory 已初始化到 %s", target_dir)
    except Exception as e:
        logger.warning("种子 Memory 初始化失败 (不阻塞): %s", e)


class MemoryOut(BaseModel):
    id: str
    name: str
    description: str
    type: str
    content: str
    consolidated: bool = False


class MemorySave(BaseModel):
    """创建或更新记忆。id 为空时创建 (后端生成 UUID), 有 id 时更新。"""
    id: str | None = None  # None = 新建, 有值 = 更新
    name: str
    description: str
    content: str
    type: str = "project"


class ConsolidateRequest(BaseModel):
    """整理记忆请求。ids=None 整理全部, 有值只整理指定的。"""
    ids: list[str] | None = None


class ConsolidateStatus(BaseModel):
    """整理任务状态 (内存 dict, 不存 DB)。"""
    status: str = "idle"          # idle | running | done | failed
    progress: int = 0             # 0-100
    stage: str = ""               # 当前步骤文字
    result: dict | None = None    # 完成后的结果
    error: str | None = None      # 失败原因


# 内存状态: {(tenant_id, ds_id): ConsolidateStatus}
_consolidate_status: dict[tuple[str, str], ConsolidateStatus] = {}


def _get_consolidate_status(tenant_id: str, ds_id: str) -> ConsolidateStatus:
    """获取整理状态 (无记录时返回 idle)。"""
    return _consolidate_status.get((tenant_id, ds_id), ConsolidateStatus())


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    data_source_id: str = Query(..., description="数据源 ID"),
    include_consolidated: bool = Query(False, description="是否包含已整理的记忆"),
    user=Depends(require_admin),
):
    """列出所有记忆 — 按 tenant_id + data_source_id 隔离。

    默认不返回已整理的记忆 (consolidated=true), 开启 include_consolidated 后返回。
    """
    store = _get_ds_store(user.tenant_id, data_source_id)
    memories = []
    for m in store.list_memories():
        # 默认隐藏已整理的记忆
        if m.get("consolidated") and not include_consolidated:
            continue
        full = store.read_memory(m["id"]) or ""
        # 去掉 frontmatter, 只返回正文
        content = full
        if full.startswith("---"):
            parts = full.split("---", 2)
            content = parts[2].strip() if len(parts) > 2 else full
        memories.append(MemoryOut(
            id=m["id"], name=m["name"], description=m["description"],
            type=m["type"], content=content,
            consolidated=m.get("consolidated", False),
        ))
    return memories


@router.put("", response_model=MemoryOut)
async def save_memory(
    body: MemorySave,
    data_source_id: str = Query(..., description="数据源 ID"),
    user=Depends(require_admin),
):
    """创建或更新记忆 — 按 tenant_id + data_source_id 隔离。

    body.id 为空时创建新记忆 (生成 UUID), 有值时更新已有记忆。
    """
    _validate_memory_name(body.name)  # name 是标题, 不直接用于文件操作
    if body.id:
        _validate_mem_id(body.id)  # id 用于文件名, 严格白名单校验
    store = _get_ds_store(user.tenant_id, data_source_id)
    # 更新模式: id 对应的文件必须已存在, 否则拒绝 (防幽灵记忆)
    if body.id and not store.read_memory(body.id):
        raise HTTPException(status_code=404, detail=f"记忆 '{body.id}' 不存在, 无法更新")
    file_path = store.save_memory(
        name=body.name, description=body.description,
        content=body.content, memory_type=body.type,
        mem_id=body.id,
    )
    # 从文件读回 id (filename stem) + 实际 consolidated 状态
    mem_id = file_path.stem
    memories_on_disk = store.list_memories()
    consolidated = next((m.get("consolidated", False) for m in memories_on_disk if m["id"] == mem_id), False)
    logger.info("记忆已保存: %s (id=%s, by %s, tenant=%s, ds=%s)", body.name, mem_id, user.user_id, user.tenant_id, data_source_id)
    return MemoryOut(id=mem_id, name=body.name, description=body.description, type=body.type, content=body.content, consolidated=consolidated)


@router.delete("/{mem_id}")
async def delete_memory(
    mem_id: str,
    data_source_id: str = Query(..., description="数据源 ID"),
    user=Depends(require_admin),
):
    """删除记忆 — 按 tenant_id + data_source_id 隔离。"""
    _validate_mem_id(mem_id)
    store = _get_ds_store(user.tenant_id, data_source_id)
    if not store.delete_memory(mem_id):
        raise HTTPException(status_code=404, detail=f"记忆 '{mem_id}' 不存在")
    logger.info("记忆已删除: %s (by %s, tenant=%s, ds=%s)", mem_id, user.user_id, user.tenant_id, data_source_id)
    return {"deleted": mem_id}


@router.post("/consolidate", status_code=status.HTTP_202_ACCEPTED)
async def consolidate_memories(
    data_source_id: str = Query(..., description="数据源 ID"),
    body: ConsolidateRequest | None = None,
    user=Depends(require_admin),
):
    """整理记忆 — 异步任务模式 (对标数据源扫描)。

    POST 返回 202, 后台 asyncio.create_task 跑 LLM 整理。
    前端轮询 GET /memory/consolidate/status 拿进度。
    防重复: running 时拒绝 (409)。
    """
    key = (user.tenant_id, data_source_id)
    current = _get_consolidate_status(user.tenant_id, data_source_id)
    if current.status == "running":
        raise HTTPException(status_code=409, detail="该数据源正在整理中, 请等待完成")

    # 标记运行中
    cs = ConsolidateStatus(status="running", progress=5, stage="准备中...")
    _consolidate_status[key] = cs

    ids = body.ids if body else None
    asyncio.create_task(_run_consolidate_background(user.tenant_id, data_source_id, ids))

    return cs


@router.get("/consolidate/status", response_model=ConsolidateStatus)
async def get_consolidate_status(
    data_source_id: str = Query(..., description="数据源 ID"),
    user=Depends(require_admin),
):
    """查询整理任务状态 — 前端轮询用。"""
    return _get_consolidate_status(user.tenant_id, data_source_id)


async def _run_consolidate_background(tenant_id: str, ds_id: str, ids: list[str] | None) -> None:
    """后台跑整理全流程 (更新内存状态, 不依赖 DB session)。"""
    key = (tenant_id, ds_id)
    cs = _consolidate_status[key]

    def on_progress(pct: int, stage: str):
        cs.progress = pct
        cs.stage = stage

    try:
        store = _get_ds_store(tenant_id, ds_id)
        from app.ai.recall import consolidate_memories as _consolidate
        result = await _consolidate(store, memory_dir=f"memory/{tenant_id}/{ds_id}", ids=ids, on_progress=on_progress)
        cs.status = "done"
        cs.progress = 100
        cs.stage = "完成"
        cs.result = result
        logger.info("记忆整理完成: tenant=%s ds=%s result=%s", tenant_id, ds_id, result.get("detail", ""))
    except Exception as e:
        cs.status = "failed"
        cs.error = str(e)
        cs.stage = "失败"
        logger.warning("记忆整理失败: tenant=%s ds=%s error=%s", tenant_id, ds_id, e)

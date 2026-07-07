"""
Skills API — 业务规则在线管理 (T041)

对标 SKL-001/002/003: Skills 是注入 SQL 生成 prompt 的业务规则。
本端点提供查看/编辑/保存/删除 Skills 的 HTTP 接口。

安全: 仅 admin 可操作 (Skills 影响全局 SQL 生成)。
热更新: 保存后 invalidate 缓存, 下次查询自动生效。

SEC NOTE: 当前 Skills Store 是全局共享的, 无租户隔离。
单租户部署环境下安全; 多租户部署需改为 skills/{tenant_id}/ 目录结构
+ API 层按 tenant_id 过滤。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_admin, write_audit_log
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillOut(BaseModel):
    name: str
    description: str
    version: str
    content: str
    references: dict[str, str] = {}  # T060: reference 子文件 (key=文件名, value=内容)


class SkillSave(BaseModel):
    name: str
    description: str = ""
    version: str = "1"
    content: str


@router.get("", response_model=list[SkillOut])
async def list_skills(
    user=Depends(require_admin),
):
    """列出所有 Skills (T041)。"""
    from app.services.skills_loader import get_skills_loader
    loader = get_skills_loader()
    return [
        SkillOut(name=s.name, description=s.description, version=s.version,
                 content=s.content, references=s.references)
        for s in loader.load_all()
    ]


@router.get("/{name}", response_model=SkillOut)
async def get_skill(
    name: str,
    user=Depends(require_admin),
):
    """查看单个 Skill 详情。"""
    from app.services.skills_loader import get_skills_loader
    loader = get_skills_loader()
    for s in loader.load_all():
        if s.name == name:
            return SkillOut(name=s.name, description=s.description, version=s.version,
                            content=s.content, references=s.references)
    raise HTTPException(status_code=404, detail=f"Skill '{name}' 不存在")


@router.put("", response_model=SkillOut)
async def save_skill(
    body: SkillSave,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """创建或更新 Skill (T041 在线编辑)。

    保存后 invalidate 缓存, 下次查询自动热更新。
    """
    from pathlib import Path
    from app.services.skills_loader import get_skills_loader, reset_skills_loader
    loader = get_skills_loader()
    base = Path(loader._base_dir)
    skill_dir = base / body.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    # 组装 SKILL.md (frontmatter + 正文)
    fm = (
        f"---\n"
        f"name: {body.name}\n"
        f"description: {body.description}\n"
        f"version: {body.version}\n"
        f"---\n\n"
        f"{body.content}\n"
    )
    skill_file.write_text(fm, encoding="utf-8")
    # 热更新: 失效缓存
    reset_skills_loader()
    logger.info("Skill 已保存: %s (by %s)", body.name, user.user_id)
    # SEC-006: Skills 修改影响全局 SQL 生成, 必须审计
    try:
        await write_audit_log(
            db, tenant_id=user.tenant_id, user_id=user.user_id,
            resource_type="skill", action="save",
            status="success", resource_id=body.name,
        )
        await db.commit()
    except Exception as e:
        logger.warning("Skill 审计日志失败 (不阻塞): %s", e)
    return SkillOut(name=body.name, description=body.description, version=body.version,
                    content=body.content, references={})


@router.delete("/{name}")
async def delete_skill(
    name: str,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """删除 Skill (含目录)。"""
    import shutil
    from pathlib import Path
    from app.services.skills_loader import get_skills_loader, reset_skills_loader
    loader = get_skills_loader()
    base = Path(loader._base_dir)
    skill_dir = base / name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{name}' 不存在")
    shutil.rmtree(skill_dir)
    reset_skills_loader()
    logger.info("Skill 已删除: %s (by %s)", name, user.user_id)
    # SEC-006: Skills 删除必须审计
    try:
        await write_audit_log(
            db, tenant_id=user.tenant_id, user_id=user.user_id,
            resource_type="skill", action="delete",
            status="success", resource_id=name,
        )
        await db.commit()
    except Exception as e:
        logger.warning("Skill 审计日志失败 (不阻塞): %s", e)
    return {"deleted": name}


class SkillPreviewRequest(BaseModel):
    """Skills 预览请求 — 用给定规则对示例问题跑一次 SQL 生成 (T041)。

    不走完整 Agent, 只验证规则文本能否被 LLM 正确理解 (轻量, 不污染对话历史)。
    """
    content: str                 # 规则正文 (SKILL.md 编辑中的草稿)
    test_question: str = "各分类的销售数量统计"  # 示例问题


class SkillPreviewResponse(BaseModel):
    generated_sql: str | None = None
    error: str | None = None
    usage: dict | None = None


@router.post("/preview", response_model=SkillPreviewResponse)
async def preview_skill(
    body: SkillPreviewRequest,
    user: AuthUser = Depends(require_admin),
):
    """预览 Skill 规则效果 (T041) — 用规则拼一个最小 prompt 调一次 LLM。

    对标 SKL-002: Skills 注入 SQL 生成 prompt。预览验证规则文本有效性,
    不执行生成的 SQL (仅看 LLM 是否按规则产出)。
    """
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="规则内容不能为空")

    from app.core.llm_client import llm_chat

    # 最小 prompt: 模拟 SQL 生成场景 (规则 + 示例 schema + 示例问题)
    system_prompt = (
        "你是 Text-to-SQL 专家, 只返回 SELECT 语句。\n\n"
        "【示例 schema】\n"
        "categories(id, name)\n"
        "orders(id, category_id, amount, status, created_at)\n\n"
        "【业务规则 (Skills)】\n"
        f"{body.content}\n\n"
        "只返回 SQL, 不要解释。"
    )
    try:
        sql, resp = await llm_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": body.test_question},
            ],
            temperature=0.0,
        )
        sql = sql.strip()
        # 简单校验: 至少像 SQL
        if sql and not sql.upper().startswith("SELECT"):
            return SkillPreviewResponse(generated_sql=sql, error="生成内容非 SELECT 语句 (规则可能需要调整)")
        return SkillPreviewResponse(
            generated_sql=sql,
            usage={
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
                "total_tokens": getattr(resp.usage, "total_tokens", 0),
            },
        )
    except Exception as e:
        logger.warning("Skill 预览失败: %s", e)
        return SkillPreviewResponse(error=f"预览失败: {e}")

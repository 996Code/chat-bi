"""
Skills API — 业务规则在线管理 (T041)

对标 SKL-001/002/003: Skills 是注入 SQL 生成 prompt 的业务规则。
本端点提供查看/编辑/保存/删除 Skills 的 HTTP 接口。

安全: 仅 admin 可操作 (Skills 影响全局 SQL 生成)。
热更新: 保存后 invalidate 缓存, 下次查询自动生效。
多租户隔离: 按 tenant_id 隔离到 skills/{tenant_id}/ 子目录 (对标 S1)。
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

# 路径穿越防护: skill name 只允许字母/数字/下划线/连字符
# 安全设计: 白名单校验, 只允许安全字符, 拒绝一切特殊字符
# 路径穿越攻击如 "../../etc/passwd" 会被拦截
import re
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_skill_name(name: str) -> str:
    """校验 skill name 不含路径穿越字符 (../ 等)。"""
    if not name or not _SAFE_NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="Skill 名称只允许字母、数字、下划线和连字符",
        )
    return name


def _get_tenant_skills_dir(tenant_id: str):
    """获取租户隔离的 skills 目录 (skills/{tenant_id}/)。

    首次访问时自动从全局模板目录 (skills/_template/) 复制种子规则,
    确保新租户不会看到空白页面。

    防御纵深: 即使 tenant_id 来自 JWT (理论上安全), 仍做路径穿越校验。
    原因: JWT 解析逻辑可能在未来被修改, 或者 tenant_id 可能来自其他来源。
    """
    from pathlib import Path
    import shutil
    from app.core.config import get_settings
    # tenant_id 来自 JWT, 理论上安全; 但防御纵深: 防路径穿越
    if not tenant_id or ".." in tenant_id or "/" in tenant_id or "\\" in tenant_id:
        raise HTTPException(status_code=400, detail="无效的租户标识")
    settings = get_settings()
    base = Path(getattr(settings, "skills_dir", "skills")) / tenant_id
    base.mkdir(parents=True, exist_ok=True)
    # 种子数据: 租户目录为空时, 从 _template/ 复制默认规则
    # 设计决策: 种子数据只复制一次, 不覆盖已有内容
    # 这样用户修改后不会被下次重启覆盖
    _ensure_seed_skills(base)
    return base


def _ensure_seed_skills(tenant_dir: Path):
    """租户 skills 目录为空时, 从 _template/ 复制种子规则。

    种子规则包含电商场景常见业务约定 (GMV/状态枚举/字段别名),
    用户可自由编辑或删除, 不影响其他租户。
    """
    # 已有规则 → 不覆盖
    if any(tenant_dir.glob("*/SKILL.md")):
        return
    template_dir = tenant_dir.parent / "_template"
    if template_dir.is_dir():
        try:
            for item in template_dir.iterdir():
                if item.is_dir() and (item / "SKILL.md").exists():
                    dest = tenant_dir / item.name
                    if not dest.exists():
                        shutil.copytree(item, dest)
            logger.info("种子 Skills 已初始化到 %s", tenant_dir)
        except Exception as e:
            logger.warning("种子 Skills 初始化失败 (不阻塞): %s", e)


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
    """列出所有 Skills (T041) — 按 tenant_id 隔离。

    admin 权限: Skills 影响全局 SQL 生成逻辑, 非 admin 不可查看。
    返回包含 reference 文件 (T060) 的完整内容, 前端用于渲染编辑界面。
    """
    from app.services.skills_loader import SkillsLoader
    base = _get_tenant_skills_dir(user.tenant_id)
    loader = SkillsLoader(base_dir=str(base))
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
    """查看单个 Skill 详情 — 按 tenant_id 隔离。"""
    _validate_skill_name(name)  # 防路径穿越
    from app.services.skills_loader import SkillsLoader
    base = _get_tenant_skills_dir(user.tenant_id)
    loader = SkillsLoader(base_dir=str(base))
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
    """创建或更新 Skill (T041 在线编辑) — 按 tenant_id 隔离。

    保存后 invalidate 缓存, 下次查询自动热更新。
    admin 权限: Skills 修改影响全局 SQL 生成, 必须审计 (SEC-006)。

    热更新机制: reset_skills_loader() 清除 SkillsLoader 的缓存,
    下次 load_all() 时重新从磁盘读取, 无需重启服务。
    """
    _validate_skill_name(body.name)  # 对标防御纵深: 防路径穿越
    base = _get_tenant_skills_dir(user.tenant_id)
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
    # 热更新: 失效租户级缓存
    from app.services.skills_loader import reset_skills_loader
    reset_skills_loader()
    logger.info("Skill 已保存: %s (by %s, tenant=%s)", body.name, user.user_id, user.tenant_id)
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
    """删除 Skill (含目录) — 按 tenant_id 隔离。"""
    import shutil
    _validate_skill_name(name)  # 防路径穿越
    base = _get_tenant_skills_dir(user.tenant_id)
    skill_dir = base / name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{name}' 不存在")
    shutil.rmtree(skill_dir)
    from app.services.skills_loader import reset_skills_loader
    reset_skills_loader()
    logger.info("Skill 已删除: %s (by %s, tenant=%s)", name, user.user_id, user.tenant_id)
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

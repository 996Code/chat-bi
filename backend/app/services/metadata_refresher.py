"""
DSO-04: 元数据自动刷新

对标 V1 设计:
  - 定时 (6h) 检测数据源表结构变更
  - 全量扫描 → 与当前语义层 diff → 有变更写新版本 (append-only)
  - 无变更跳过 (不产空版本, 宁缺毋滥)
  - 失败 fail-closed: 记审计 fail, 不阻塞下次

机制:
  - 复用 scan_data_source (结构扫描) + diff_semantic_contents (差异对比)
  - 变更时复用 data_sources 扫描流水线的版本管理 + 索引重建
  - 不做 LLM 推断 (已存在表的推断是幂等的, 不会产生 diff; 新表靠下次手动 scan 补注释)
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import DataSource, SemanticModel

logger = logging.getLogger(__name__)


async def detect_and_refresh_metadata(db_session) -> dict:
    """定时任务: 检测所有数据源的元数据变更并刷新。

    遍历所有 active 数据源 → 全量扫描 → diff 当前语义层 → 有变更写新版本。
    返回汇总 dict。不抛异常 (定时任务容错)。
    """
    summary = {"checked": 0, "refreshed": 0, "unchanged": 0, "failed": 0}

    # 查所有 active 数据源
    result = await db_session.execute(
        select(DataSource).where(DataSource.is_active == True)  # noqa: E712
    )
    datasources = result.scalars().all()

    for ds in datasources:
        summary["checked"] += 1
        try:
            refreshed = await _refresh_single_datasource(db_session, ds)
            if refreshed:
                summary["refreshed"] += 1
            else:
                summary["unchanged"] += 1
        except Exception as e:
            summary["failed"] += 1
            logger.warning("数据源 %s (%s) 元数据刷新失败: %s", ds.id, ds.name, e)
            # rollback 否则 session 脏了, 后续数据源全部失败 (高-6 修复)
            await db_session.rollback()
            try:
                await _audit_refresh(db_session, ds, "fail", error=str(e)[:500])
                await db_session.commit()
            except Exception:
                await db_session.rollback()

    return summary


async def _refresh_single_datasource(db_session, ds: DataSource) -> bool:
    """刷新单个数据源元数据。有变更返回 True (已写新版本), 无变更 False。

    流程: 全量扫描结构 → 取当前语义层 → diff → 有变更写新版本 + 重建索引。
    """
    from app.services.datasource_engine import datasource_to_url, get_engine_pool
    from app.services.semantic_scanner import scan_data_source

    # 1. 全量扫描结构 (同步 inspector, 复用连接池)
    pool = get_engine_pool()
    url = datasource_to_url(ds)
    inspector = pool.get_inspector(ds.id, url)
    new_content = scan_data_source(inspector)  # SemanticModelContent

    # 2. 取当前语义层版本
    current_sm = (
        await db_session.execute(
            select(SemanticModel).where(
                SemanticModel.tenant_filter(ds.tenant_id),
                SemanticModel.data_source_id == ds.id,
                SemanticModel.is_current == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()

    # 无当前版本 (从未扫描过) → 不自动建 (需手动 scan 触发首次, 避免定时任务意外建)
    if current_sm is None:
        logger.debug("数据源 %s 无当前语义层, 跳过自动刷新", ds.id)
        return False

    # 3. diff (新扫描 vs 当前语义层)
    from app.services.semantic_diff import diff_semantic_contents
    diff = diff_semantic_contents(current_sm.content, new_content.model_dump())
    if not diff["has_changes"]:
        logger.debug("数据源 %s 元数据无变更", ds.id)
        return False

    # 4. 有变更 → 写新版本 (复用 data_sources 扫描流水线的版本逻辑)
    logger.info(
        "数据源 %s (%s) 检测到元数据变更: +%d -%d ~%d 表",
        ds.id, ds.name,
        len(diff["added_models"]), len(diff["removed_models"]), len(diff["changed_models"]),
    )

    # 旧版本 is_current=False
    old_currents = (
        await db_session.execute(
            select(SemanticModel).where(
                SemanticModel.tenant_filter(ds.tenant_id),
                SemanticModel.data_source_id == ds.id,
                SemanticModel.is_current == True,  # noqa: E712
            )
        )
    ).scalars().all()
    for old in old_currents:
        old.is_current = False

    # 新版本号
    max_v = (
        await db_session.execute(
            select(SemanticModel.version).where(
                SemanticModel.tenant_filter(ds.tenant_id),
                SemanticModel.data_source_id == ds.id,
            ).order_by(SemanticModel.version.desc()).limit(1)
        )
    ).scalar_one()
    new_version = max_v + 1

    # 新版本 content: 保留当前版本的 LLM 注释/sample_questions (人工标注不丢),
    # 只更新结构部分。简化处理: 用新扫描结构 + 合并旧版本的非结构字段
    merged_content = _merge_content(current_sm.content, new_content.model_dump())

    new_sm = SemanticModel(
        tenant_id=ds.tenant_id,
        data_source_id=ds.id,
        version=new_version,
        content=merged_content,
        is_current=True,
    )
    db_session.add(new_sm)
    await db_session.flush()

    await _audit_refresh(
        db_session, ds, "success",
        detail={"version": new_version, "diff": {
            "added": diff["added_models"],
            "removed": diff["removed_models"],
            "changed": [c["table"] for c in diff["changed_models"]],
        }},
    )

    # 重建向量索引 (RAG 边界, 失败降级)
    try:
        from app.services.indexer_update import rebuild_index
        from app.services.embedder import get_embedder
        from app.services.vector_store import get_vector_store
        from app.schemas.semantic_layer import SemanticModelContent
        content = SemanticModelContent(**merged_content)
        await rebuild_index(
            content=content, data_source_id=ds.id,
            store=get_vector_store(), embedder=get_embedder(),
        )
    except Exception as e:
        logger.warning("元数据刷新后重建索引失败 (降级): %s", e)

    return True


def _merge_content(old_content: dict, new_structure: dict) -> dict:
    """合并: 保留旧版本的 LLM 注释/sample_questions, 更新结构。

    元数据刷新只关心表/列结构变化 (增删改), 保留人工/LLM 标注的
    display_name/description/sample_questions (这些结构扫描会重新生成退化值,
    合并旧版本的更准确)。
    """
    old_models = {m["name"]: m for m in old_content.get("models", [])}
    new_models = {m["name"]: m for m in new_structure.get("models", [])}

    # 新扫描的表列表是权威 (结构变了), 但对每个表保留旧版本的标注
    merged_models = []
    for name, new_m in new_models.items():
        old_m = old_models.get(name)
        if old_m:
            # 保留旧 display_name/description (人工/LLM 标注), 用新结构 (列变化)
            merged = dict(new_m)
            merged["display_name"] = old_m.get("display_name", new_m.get("display_name"))
            merged["description"] = old_m.get("description", new_m.get("description"))
            # 列: 新结构权威, 但保留旧列的 display_name/description/semantic_type
            old_cols = {c["name"]: c for c in old_m.get("columns", [])}
            merged_cols = []
            for c in new_m.get("columns", []):
                old_c = old_cols.get(c["name"])
                if old_c:
                    merged_c = dict(c)
                    merged_c["display_name"] = old_c.get("display_name", c.get("display_name"))
                    merged_c["description"] = old_c.get("description", c.get("description"))
                    merged_c["semantic_type"] = old_c.get("semantic_type", c.get("semantic_type"))
                    merged_c["source"] = old_c.get("source", c.get("source"))
                    merged_c["confidence"] = old_c.get("confidence", c.get("confidence"))
                    merged_cols.append(merged_c)
                else:
                    merged_cols.append(c)  # 新列, 用扫描值
            merged["columns"] = merged_cols
            merged_models.append(merged)
        else:
            merged_models.append(new_m)  # 新表, 用扫描值

    return {
        "version": new_structure.get("version", 1),
        "models": merged_models,
        # 保留旧版本的 sample_questions (扫描不生成, LLM 才生成)
        "sample_questions": old_content.get("sample_questions", []),
    }


async def _audit_refresh(db_session, ds: DataSource, status: str, detail: dict | None = None, error: str | None = None) -> None:
    """元数据刷新结果记审计 (不抛异常)。"""
    try:
        from app.core.auth import write_audit_log
        await write_audit_log(
            db_session, tenant_id=ds.tenant_id, user_id=None,
            resource_type="semantic_model", action="auto_refresh",
            status=status, resource_id=ds.id,
            detail=detail, error_message=error,
        )
    except Exception as e:
        logger.warning("元数据刷新审计失败 (不阻塞): %s", e)

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

设计决策:
  - 为什么全量扫描而非增量: 元数据变更频率低 (6h 一次), 全量扫描简单可靠,
    增量检测需要维护表结构指纹, 复杂度高且易出错。
  - 为什么 append-only 而非原地更新: 保留历史版本用于回滚和审计。
    每次自动刷新都生成新版本, 旧版本 is_current=False 但保留在 DB 中。
  - 为什么首次扫描不自动触发: 新数据源接入后的首次扫描应该由用户手动触发
    (scan 端点), 让用户确认扫描结果。定时任务只做自动检测已知变更。
  - 为什么重建索引失败降级: 索引是 RAG 的辅助功能, 元数据版本管理是核心功能。
    索引重建失败不应影响元数据版本写入 (fail-closed 的变体: 核心功能 fail-closed,
    辅助功能 fail-open)。
"""

# 版本号递增策略: 每次自动刷新发现变更时, 取当前最大版本号 +1
# 不依赖时间戳: 避免时钟不同步导致的版本混乱
# 不依赖 UUID: 版本号需可读可排序, 方便人工排查
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import DataSource, SemanticModel

logger = logging.getLogger(__name__)


async def detect_and_refresh_metadata(db_session, tenant_id: str | None = None) -> dict:
    """定时任务: 检测所有数据源的元数据变更并刷新。

    遍历所有 active 数据源 → 全量扫描 → diff 当前语义层 → 有变更写新版本。
    M6: tenant_id 限定本租户 (定时任务不传=全量, API 调用传=租户隔离)。
    返回汇总 dict。不抛异常 (定时任务容错)。

    数据流:
    DB 查所有 active 数据源 → 逐个扫描结构 → diff → 有变更?
      ├─ 是 → 写新版本 (append-only) → 重建索引 (降级) → 记审计
      └─ 否 → 跳过 (不产空版本)

    错误处理:
    - 单个数据源刷新失败 → 记审计 fail, 继续下一个
    - session 脏了 → rollback 再继续 (高-6 修复)
    - 索引重建失败 → 只 WARNING, 不阻断版本写入
    """
    summary = {"checked": 0, "refreshed": 0, "unchanged": 0, "failed": 0}

    # 查所有 active 数据源 (M6: 加租户过滤)
    # 只查 active 数据源: 非 active 数据源的表结构不参与查询, 不需要刷新
    query = select(DataSource).where(DataSource.is_active == True)  # noqa: E712
    if tenant_id:
        query = query.where(DataSource.tenant_id == tenant_id)
    result = await db_session.execute(query)
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
            # 场景: 某个数据源刷新过程中抛异常, session 进入 error 状态,
            # 后续所有数据库操作都失败。必须 rollback 清除 error 状态。
            await db_session.rollback()
            try:
                await _audit_refresh(db_session, ds, "fail", error=str(e)[:500])
                await db_session.commit()
            except Exception:
                # 审计日志写入失败也不能阻塞, 继续下一个数据源
                await db_session.rollback()

    return summary


async def _refresh_single_datasource(db_session, ds: DataSource) -> bool:
    """刷新单个数据源元数据。有变更返回 True (已写新版本), 无变更 False。

    流程: 全量扫描结构 → 取当前语义层 → diff → 有变更写新版本 + 重建索引。

    为什么不同步到 DB 事务:
    整个流程 (扫描+diff+写版本+重建索引) 不在单个 DB 事务中,
    因为扫描和重建索引是外部操作 (连接业务库 / 调用向量库),
    不应持有 DB 事务锁。版本写入和审计各自独立提交。

    边界情况:
    - 数据源表结构无变化 → 跳过, 不产空版本
    - 当前无语义层 → 跳过 (定时任务不触发首次扫描)
    - 索引重建失败 → 降级 (WARNING), 不影响版本写入
    """
    from app.services.datasource_engine import datasource_to_url, get_engine_pool
    from app.services.semantic_scanner import scan_data_source

    # 1. 全量扫描结构 (同步 inspector, 复用连接池)
    # 使用 DataSourceEnginePool 的 inspector 而非新建引擎,
    # 复用已有连接池, 避免重复创建连接
    pool = get_engine_pool()
    url = datasource_to_url(ds)
    inspector = pool.get_inspector(ds.id, url)
    new_content = scan_data_source(inspector)  # SemanticModelContent

    # 2. 取当前语义层版本
    # 只取 is_current=True 的版本, 与当前版本对比
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
    # 设计决策: 如果用户从未手动触发扫描, 自动刷新不应自动创建第一个版本。
    # 因为用户可能还没准备好接受默认元数据, 或者想先配置表过滤规则。
    if current_sm is None:
        logger.debug("数据源 %s 无当前语义层, 跳过自动刷新", ds.id)
        return False

    # 3. diff (新扫描 vs 当前语义层)
    # 对比的是新扫描的结构和当前版本的 content (已包含 LLM 标注)
    # 注意: 新扫描的结构不含 LLM 标注 (display_name/description 是扫描的退化值)
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
    # 可能有多个 is_current=True 的版本 (异常情况), 全部置 False
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
    # 取当前最大版本号 +1, 保证版本号单调递增
    # 如果某次手动触发扫描产生了版本号 5, 自动刷新检测到变更后生成 6
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
    # 因为元数据 content 变了, 向量索引也需要同步更新
    # 使用 rebuild_index 实现删旧建新
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
        # 降级: 索引重建失败只 WARNING, 不影响版本写入
        # 后续用户手动触发索引重建即可修复
        logger.warning("元数据刷新后重建索引失败 (降级): %s", e)

    return True


def _merge_content(old_content: dict, new_structure: dict) -> dict:
    """合并: 保留旧版本的 LLM 注释/sample_questions, 更新结构。

    元数据刷新只关心表/列结构变化 (增删改), 保留人工/LLM 标注的
    display_name/description/sample_questions (这些结构扫描会重新生成退化值,
    合并旧版本的更准确)。

    合并策略:
    - 表级别: 以新扫描的表列表为准 (增删的表), 对每个表保留旧版本的标注
    - 列级别: 以新扫描的列列表为准 (增删的列), 对每列保留旧版本的标注
    - 顶层字段: 保留旧版本的 sample_questions (LLM 生成, 扫描不产生)

    为什么保留旧版本标注:
    结构扫描 (scan_data_source) 只获取表名/列名/类型, 不做 LLM 推断。
    display_name/description/semantic_type 由 LLM enrich 生成, 如果直接用
    新扫描结果覆盖, 这些标注会丢失。所以需要新旧合并。

    边界情况:
    - 删除的表: 旧版本中删除的表的标注不再保留 (数据已从业务库删除)
    - 新增的表: 没有旧标注, 使用新扫描的退化值 (等待下次 LLM enrich)
    - 新增的列: 同上, 使用新扫描值
    - 旧版本 sample_questions 为空: 保留空列表, 不影响新版本
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
            # 优先使用旧版本的标注, 没有则用新扫描的退化值
            merged["display_name"] = old_m.get("display_name", new_m.get("display_name"))
            merged["description"] = old_m.get("description", new_m.get("description"))
            # 列: 新结构权威, 但保留旧列的 display_name/description/semantic_type
            # 这里列的顺序以新扫描为准 (增删改后列的排列)
            old_cols = {c["name"]: c for c in old_m.get("columns", [])}
            merged_cols = []
            for c in new_m.get("columns", []):
                old_c = old_cols.get(c["name"])
                if old_c:
                    # 旧列: 保留旧版本的标注, 用新版本的结构 (data_type 可能变化)
                    merged_c = dict(c)
                    merged_c["display_name"] = old_c.get("display_name", c.get("display_name"))
                    merged_c["description"] = old_c.get("description", c.get("description"))
                    merged_c["semantic_type"] = old_c.get("semantic_type", c.get("semantic_type"))
                    merged_c["source"] = old_c.get("source", c.get("source"))
                    merged_c["confidence"] = old_c.get("confidence", c.get("confidence"))
                    merged_cols.append(merged_c)
                else:
                    # 新列: 没有旧标注, 用新扫描的退化值
                    # 后续用户手动触发 LLM enrich 后会补充
                    merged_cols.append(c)
            merged["columns"] = merged_cols
            merged_models.append(merged)
        else:
            # 新表: 用新扫描值, 没有旧标注可保留
            merged_models.append(new_m)

    # 保留旧版本的 sample_questions (扫描不生成, LLM 才生成)
    # 注意: 这里只保留旧版本的 sample_questions, 不合并新版本的
    # 因为新扫描不产生 sample_questions, 合并没有意义
    return {
        "version": new_structure.get("version", 1),
        "models": merged_models,
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

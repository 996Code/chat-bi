"""
图谱 API: 知识图谱可视化 + 图分析端点

端点:
  GET  /graph              — 全图数据 (G6 渲染)
  GET  /graph/subgraph     — 子图 (聚焦某表)
  GET  /graph/communities  — 社区列表
  GET  /graph/hubs         — 枢纽表
  GET  /graph/impact       — 影响分析
  GET  /graph/join-path    — JOIN 路径
  POST /graph/relationship — 新增关系
  DELETE /graph/relationship — 删除关系

所有端点通过 data_source_id 参数获取语义层, 复用现有鉴权模式。
SchemaGraph 从语义层懒构建, 无额外存储。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, get_current_user, require_user, write_audit_log
from app.db.models import SemanticModel
from app.db.session import get_db
from app.schemas.semantic_layer import Relationship, SemanticModelContent
from app.services.graph_service import SchemaGraph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/graph", tags=["graph"])


# ── 请求/响应模型 ──────────────────────────────────────────────

class OnCondition(BaseModel):
    """单个 ON 条件。"""
    source_column: str = Field(description="源表列名")
    target_column: str = Field(description="目标表列名")


class AddRelationshipRequest(BaseModel):
    """新增关系请求体。"""
    from_table: str = Field(description="源表名")
    name: str = Field(description="关系名称, 如 biz_orders_to_biz_users")
    target_model: str = Field(description="目标表名")
    join_type: str = Field(default="LEFT", description="JOIN 类型: INNER/LEFT/RIGHT/FULL")
    on: str | None = Field(default=None, description="ON 条件文本 (兼容旧版, 优先使用 on_conditions)")
    on_conditions: list[OnCondition] | None = Field(default=None, description="结构化 ON 条件列表 (推荐)")
    type: str = Field(default="N:1", description="基数: N:1/1:N/1:1/N:N")
    source: str = Field(default="manual", description="来源: manual/foreign_key/name_pattern/ai_inferred")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度 0-1")


class JoinPathResponse(BaseModel):
    """JOIN 路径响应。"""
    tables: list[str]
    on_conditions: list[str]
    join_types: list[str]
    confidences: list[float]
    total_weight: float


# ── 内部工具 ──────────────────────────────────────────────────

async def _get_graph(
    data_source_id: str,
    user: AuthUser,
    db: AsyncSession,
) -> tuple[SchemaGraph, SemanticModel | None]:
    """获取 SchemaGraph 实例 + 原始 SemanticModel 行 (用于写回)。

    Returns:
        (SchemaGraph, SemanticModel | None)
        SemanticModel 为 None 表示无语义层 (graph 为空图)
    """
    stmt = select(SemanticModel).where(
        SemanticModel.tenant_filter(user.tenant_id),
        SemanticModel.data_source_id == data_source_id,
        SemanticModel.is_current == True,
    )
    sm = (await db.execute(stmt)).scalar_one_or_none()

    if sm is None:
        return SchemaGraph(), None

    content = SemanticModelContent(**sm.content) if sm.content else SemanticModelContent()
    return SchemaGraph(content), sm


# ── 端点 ──────────────────────────────────────────────────────

@router.get("")
async def get_full_graph(
    data_source_id: str = Query(..., description="数据源 ID"),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取全图数据 (G6 渲染格式)。

    SchemaGraph 从语义层懒构建, 无额外存储。
    返回格式兼容 G6 图可视化引擎: nodes + edges 结构。
    如果数据源无语义层, 返回空图 (nodes=[], edges=[])。
    """
    graph, _ = await _get_graph(data_source_id, user, db)
    return graph.to_vis_data()


@router.get("/subgraph")
async def get_subgraph(
    data_source_id: str = Query(..., description="数据源 ID"),
    center: str = Query(..., description="中心表名"),
    depth: int = Query(default=2, ge=1, le=5, description="邻居深度"),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取子图数据 (聚焦某表及其 depth-hop 邻居)。"""
    graph, _ = await _get_graph(data_source_id, user, db)
    return graph.to_vis_subgraph(center, depth)


@router.get("/communities")
async def get_communities(
    data_source_id: str = Query(..., description="数据源 ID"),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取社区列表 (表按业务域聚类)。

    社区检测: 基于图连通性和关系权重, 自动识别业务域 (如: 订单域、商品域、用户域)。
    用于前端展示"业务域"概念, 帮助用户理解表结构。
    """
    graph, _ = await _get_graph(data_source_id, user, db)
    communities = graph.get_communities()
    return {"communities": communities, "count": len(communities)}


@router.get("/hubs")
async def get_hub_tables(
    data_source_id: str = Query(..., description="数据源 ID"),
    top_k: int = Query(default=10, ge=1, le=100, description="返回前 k 个枢纽表"),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取枢纽表 (度中心度最高的表)。"""
    graph, _ = await _get_graph(data_source_id, user, db)
    hubs = graph.get_hub_tables(top_k)
    return {"hubs": [{"table": t, "centrality": round(c, 4)} for t, c in hubs]}


@router.get("/impact")
async def get_impact(
    data_source_id: str = Query(..., description="数据源 ID"),
    table: str = Query(..., description="表名"),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """影响分析: 从给定表可达的所有下游表。

    用途: 当用户需要修改某表结构时, 可查看哪些下游表会受影响。
    基于图遍历算法 (BFS/DFS), 沿关系方向传播。
    """
    graph, _ = await _get_graph(data_source_id, user, db)
    if not graph.has_node(table):
        raise HTTPException(status_code=404, detail=f"表 '{table}' 不在图谱中")
    impact = graph.get_impact(table)
    return {"table": table, "impacted_tables": impact, "count": len(impact)}


@router.get("/reverse-relationships")
async def get_reverse_relationships(
    data_source_id: str = Query(..., description="数据源 ID"),
    table: str = Query(..., description="表名"),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取指向该表的反向关系 (其他表引用了此表)。"""
    graph, _ = await _get_graph(data_source_id, user, db)
    if not graph.has_node(table):
        raise HTTPException(status_code=404, detail=f"表 '{table}' 不在图谱中")
    rels = graph.get_reverse_relationships(table)
    return {"table": table, "relationships": rels, "count": len(rels)}


@router.get("/join-path")
async def get_join_path(
    data_source_id: str = Query(..., description="数据源 ID"),
    source: str = Query(..., description="起始表名"),
    target: str = Query(..., description="目标表名"),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取两表之间的最短 JOIN 路径。"""
    graph, _ = await _get_graph(data_source_id, user, db)
    if not graph.has_node(source):
        raise HTTPException(status_code=404, detail=f"表 '{source}' 不在图谱中")
    if not graph.has_node(target):
        raise HTTPException(status_code=404, detail=f"表 '{target}' 不在图谱中")
    paths = graph.find_join_paths(source, target)
    if not paths:
        return {"source": source, "target": target, "paths": [], "found": False}
    return {
        "source": source,
        "target": target,
        "paths": [JoinPathResponse(
            tables=p.tables,
            on_conditions=p.on_conditions,
            join_types=p.join_types,
            confidences=p.confidences,
            total_weight=p.total_weight,
        ).model_dump() for p in paths],
        "found": True,
    }


@router.post("/relationship")
async def add_relationship(
    req: AddRelationshipRequest,
    data_source_id: str = Query(..., description="数据源 ID"),
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """新增关系 (持久化到语义层, 创建新版本)。需要 user 或 admin 权限。

    流程: 校验源表/目标表存在 → 构建 ON 条件 → 更新语义层 content → 创建新版本 → 写审计日志。
    注意: 这里直接修改 sm.content 并递增版本号 (不创建新 SemanticModel 行),
    区别于 semantic_models.py 的 append-only 策略。
    这是因为 graph 端点直接操作 content dict, 语义层模块操作完整版本。
    """
    graph, sm = await _get_graph(data_source_id, user, db)
    if sm is None:
        raise HTTPException(status_code=404, detail="该数据源无语义层")

    # 校验源表/目标表存在
    if not graph.has_node(req.from_table):
        raise HTTPException(status_code=400, detail=f"源表 '{req.from_table}' 不在图谱中")
    if not graph.has_node(req.target_model):
        raise HTTPException(status_code=400, detail=f"目标表 '{req.target_model}' 不在图谱中")

    # 构建 ON 条件字符串: 优先用 on_conditions (结构化), 否则用 on (文本)
    on_str = req.on
    if req.on_conditions:
        parts = [f"{req.from_table}.{oc.source_column} = {req.target_model}.{oc.target_column}" for oc in req.on_conditions]
        on_str = " AND ".join(parts)
    if not on_str:
        raise HTTPException(status_code=400, detail="必须提供 on 或 on_conditions")

    # 持久化: 更新语义层 content (创建新版本)
    content = SemanticModelContent(**sm.content) if sm.content else SemanticModelContent()
    for model in content.models:
        if model.name == req.from_table:
            # 检查是否已存在同目标的关系 (先检查再加图, 避免图对象被无谓修改)
            existing_targets = {r.target_model for r in model.relationships}
            if req.target_model in existing_targets:
                raise HTTPException(
                    status_code=409,
                    detail=f"{req.from_table} → {req.target_model} 关系已存在",
                )
            rel = Relationship(
                name=req.name,
                target_model=req.target_model,
                join_type=req.join_type,
                on=on_str,
                type=req.type,
                source=req.source,
                confidence=req.confidence,
            )
            model.relationships.append(rel)
            # 添加到图 (用于即时返回)
            graph.add_relationship(req.from_table, rel)
            break
    else:
        raise HTTPException(status_code=400, detail=f"源表 '{req.from_table}' 不在语义层中")

    # 创建新版本
    sm.content = content.model_dump()
    sm.version += 1
    await db.commit()

    await write_audit_log(
        db=db, tenant_id=user.tenant_id, user_id=user.user_id,
        action="graph_add_relationship",
        resource_type="semantic_model", resource_id=str(sm.id),
        details={"from": req.from_table, "to": req.target_model, "on": req.on},
    )

    return {"success": True, "graph": graph.to_vis_data()}


@router.delete("/relationship")
async def delete_relationship(
    data_source_id: str = Query(..., description="数据源 ID"),
    from_table: str = Query(..., description="源表名", alias="from"),
    target: str = Query(..., description="目标表名"),
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """删除关系 (持久化到语义层, 创建新版本)。需要 user 或 admin 权限。

    同时删除正向和反向关系: 如果 target 表也有指向 from_table 的关系, 也一并删除。
    这是为了保持图的一致性 — 双向关系只需一次删除操作。
    """
    graph, sm = await _get_graph(data_source_id, user, db)
    if sm is None:
        raise HTTPException(status_code=404, detail="该数据源无语义层")

    if not graph.has_edge(from_table, target) and not graph.has_edge(target, from_table):
        raise HTTPException(
            status_code=404,
            detail=f"{from_table} → {target} 关系不存在",
        )

    # 持久化: 从语义层 content 中删除关系
    # 同时从 from_table 和 target 两个方向清除, 保持图对称性
    content = SemanticModelContent(**sm.content) if sm.content else SemanticModelContent()
    removed = False
    for model in content.models:
        if model.name == from_table:
            original_len = len(model.relationships)
            model.relationships = [
                r for r in model.relationships if r.target_model != target
            ]
            if len(model.relationships) < original_len:
                removed = True
        # 反向: 如果 target 表有指向 from_table 的关系, 也删除
        if model.name == target:
            model.relationships = [
                r for r in model.relationships if r.target_model != from_table
            ]

    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"{from_table} → {target} 关系在语义层中不存在",
        )

    # 创建新版本
    sm.content = content.model_dump()
    sm.version += 1
    await db.commit()

    await write_audit_log(
        db=db, tenant_id=user.tenant_id, user_id=user.user_id,
        action="graph_delete_relationship",
        resource_type="semantic_model", resource_id=str(sm.id),
        details={"from": from_table, "to": target},
    )

    return {"success": True}


@router.get("/table-columns")
async def get_table_columns(
    data_source_id: str = Query(..., description="数据源 ID"),
    table: str = Query(..., description="表名"),
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取指定表的列信息 (用于 ON 条件下拉框)。"""
    graph, sm = await _get_graph(data_source_id, user, db)
    if sm is None:
        raise HTTPException(status_code=404, detail="该数据源无语义层")

    content = SemanticModelContent(**sm.content) if sm.content else SemanticModelContent()
    for model in content.models:
        if model.name == table:
            columns = []
            for col in model.columns:
                columns.append({
                    "name": col.name,
                    "display_name": col.display_name,
                    "data_type": col.data_type,
                    "semantic_type": col.semantic_type,
                    "label": f"{col.display_name} ({col.name}) [{col.data_type}]",
                })
            return {"table": table, "columns": columns}

    raise HTTPException(status_code=404, detail=f"表 '{table}' 不在语义层中")

"""
看板 — Dashboard + Widget CRUD + 实时查询

对标 V1 dashboard.py:
  - 多看板 (GET/POST/PUT/DELETE /dashboards)
  - Widget CRUD (POST/DELETE /dashboards/{id}/widgets, PUT .../refresh)
  - Widget 只保存 SQL + 数据源 (不存结果快照), 打开/刷新时实时重跑 SQL
  - refresh: 实时查询 + AI 生成图表 (基于 chart_agent 全量数据注入)
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_user
from app.db.models import Dashboard, DashboardWidget, DataSource
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


# ── DTO ─────────────────────────────────────────────────────

class DashboardOut(BaseModel):
    id: str
    name: str
    created_at: str | None = None
    updated_at: str | None = None

class WidgetOut(BaseModel):
    id: str
    dashboard_id: str
    question: str
    query_sql: str | None = None
    datasource_id: str
    chart_type: str = "table"
    columns: list = []
    rows: list = []
    row_count: int | None = None
    position_x: int = 0
    position_y: int = 0
    width: int = 6
    height: int = 4
    created_at: str | None = None
    updated_at: str | None = None


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _calc_next_position(
    existing: list[tuple[int, int, int]],
) -> tuple[int, int]:
    """根据已有 widget 布局, 计算新 widget 的 (x, y) 位置。

    策略: 网格填充, 每行最多 2 个 (列宽 6, 总宽 12), 按行优先排列。
    找到已有 widget 占据的最大 y 行, 在该行找空列; 该行满则换到下一行。
    """
    if not existing:
        return 0, 0

    # 按 y 分组, 每行记录已占的 x 范围
    rows: dict[int, list[tuple[int, int]]] = {}  # y -> [(x_start, x_end)]
    max_y = 0
    for x, y, w in existing:
        max_y = max(max_y, y)
        if y not in rows:
            rows[y] = []
        rows[y].append((x, x + (w or 6)))

    # 从 y=0 开始, 找第一个能放 width=6 widget 的行
    COLS = 12
    WIDGET_WIDTH = 6
    for y in range(max_y + 2):
        occupied = rows.get(y, [])
        # 计算该行已占的列
        occupied_cols = set()
        for x_start, x_end in occupied:
            for c in range(x_start, x_end):
                occupied_cols.add(c)
        # 找连续 6 列的空位
        for x in range(0, COLS - WIDGET_WIDTH + 1, WIDGET_WIDTH):
            slot = set(range(x, x + WIDGET_WIDTH))
            if not slot & occupied_cols:
                return x, y

    # 兜底: 放到最大行下方
    return 0, max_y + 4


def _widget_to_dict(w: DashboardWidget) -> dict:
    cols = []
    rows = []
    try:
        if w.columns: cols = json.loads(w.columns)
    except (json.JSONDecodeError, TypeError): pass
    try:
        if w.rows: rows = json.loads(w.rows)
    except (json.JSONDecodeError, TypeError): pass
    return {
        "id": w.id,
        "dashboard_id": w.dashboard_id,
        "question": w.question,
        "query_sql": w.query_sql,
        "datasource_id": w.datasource_id,
        "chart_type": w.chart_type,
        "columns": cols,
        "rows": rows,
        "row_count": w.row_count,
        "position_x": w.position_x,
        "position_y": w.position_y,
        "width": w.width,
        "height": w.height,
        "created_at": _iso(w.created_at),
        "updated_at": _iso(w.updated_at),
    }


# ── Dashboard CRUD ─────────────────────────────────────────

@router.get("")
async def list_dashboards(
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前租户的看板。"""
    rows = (
        await db.execute(
            select(Dashboard)
            .where(Dashboard.tenant_filter(user.tenant_id))
            .order_by(Dashboard.updated_at.desc())
        )
    ).scalars().all()
    return [
        DashboardOut(
            id=d.id, name=d.name,
            created_at=_iso(d.created_at), updated_at=_iso(d.updated_at),
        )
        for d in rows
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    data: dict,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """新建看板。"""
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="看板名称不能为空")
    dash = Dashboard(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        name=name,
    )
    db.add(dash)
    await db.commit()
    await db.refresh(dash)
    return DashboardOut(
        id=dash.id, name=dash.name,
        created_at=_iso(dash.created_at), updated_at=_iso(dash.updated_at),
    )


@router.put("/{dashboard_id}")
async def update_dashboard(
    dashboard_id: str,
    data: dict,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """更新看板名称。"""
    dash = (
        await db.execute(
            select(Dashboard).where(
                Dashboard.id == dashboard_id,
                Dashboard.tenant_filter(user.tenant_id),
            )
        )
    ).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="看板不存在")
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="看板名称不能为空")
    dash.name = name
    await db.commit()
    await db.refresh(dash)
    return DashboardOut(
        id=dash.id, name=dash.name,
        created_at=_iso(dash.created_at), updated_at=_iso(dash.updated_at),
    )


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """删除看板 (级联删 widget)。"""
    dash = (
        await db.execute(
            select(Dashboard).where(
                Dashboard.id == dashboard_id,
                Dashboard.tenant_filter(user.tenant_id),
            )
        )
    ).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="看板不存在")
    await db.execute(
        DashboardWidget.__table__.delete().where(DashboardWidget.dashboard_id == dashboard_id)
    )
    await db.delete(dash)
    await db.commit()
    return None


# ── Widget CRUD ────────────────────────────────────────────

@router.get("/{dashboard_id}")
async def get_dashboard(
    dashboard_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """获取看板详情 (含所有 widget)。"""
    dash = (
        await db.execute(
            select(Dashboard).where(
                Dashboard.id == dashboard_id,
                Dashboard.tenant_filter(user.tenant_id),
            )
        )
    ).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="看板不存在")
    widgets = (
        await db.execute(
            select(DashboardWidget)
            .where(DashboardWidget.dashboard_id == dashboard_id)
            .order_by(DashboardWidget.position_y, DashboardWidget.position_x)
        )
    ).scalars().all()
    return {
        "id": dash.id,
        "name": dash.name,
        "widgets": [_widget_to_dict(w) for w in widgets],
        "created_at": _iso(dash.created_at),
        "updated_at": _iso(dash.updated_at),
    }


@router.post("/{dashboard_id}/widgets", status_code=status.HTTP_201_CREATED)
async def add_widget(
    dashboard_id: str,
    data: dict,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """向看板添加 widget (保存 SQL + 数据源 + 图表类型 + 图表配置缓存)。

    实时查询模式: columns/rows 不入库; refresh 时用缓存的图表配置 + 实时数据 inject_data。
    保存时跑一次 SQL + generate_chart 生成图表配置缓存 (避免每次 refresh 重跑 LLM)。
    """
    # 校验看板归属
    dash = (
        await db.execute(
            select(Dashboard).where(
                Dashboard.id == dashboard_id,
                Dashboard.tenant_filter(user.tenant_id),
            )
        )
    ).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="看板不存在")

    question = (data.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="组件名称不能为空")

    datasource_id = data.get("datasource_id") or ""
    query_sql = data.get("query_sql") or ""
    if not datasource_id:
        raise HTTPException(status_code=400, detail="数据源不能为空")
    if not query_sql:
        raise HTTPException(status_code=400, detail="SQL 不能为空 (看板需基于 SQL 实时查询)")

    # 保存时跑一次 SQL, 用于生成图表配置缓存 (避免每次 refresh 都调 LLM)
    chart_config = None
    try:
        ds = (
            await db.execute(
                select(DataSource).where(
                    DataSource.id == datasource_id,
                    DataSource.tenant_filter(user.tenant_id),
                )
            )
        ).scalar_one_or_none()
        if ds and ds.is_active:
            from app.services.datasource_engine import datasource_to_url, get_engine_pool
            from app.services.sql_executor import execute_sql
            url = datasource_to_url(ds)
            result = await execute_sql(
                sql=query_sql, datasource_id=datasource_id, url=url,
                engine_pool=get_engine_pool(),
            )
            if not result.error:
                cols = list(result.columns) if hasattr(result, "columns") else []
                rows_data = [list(r) for r in (result.rows or [])]
                # 生成图表配置 (LLM 选型, 一次性), 缓存 config 供 refresh 复用
                from app.ai.chart_agent import generate_chart
                chart_result = await generate_chart(
                    question=question,
                    columns=cols,
                    rows=rows_data,
                    chart_type_hint=data.get("chart_type", "bar"),
                )
                if chart_result.ok and chart_result.config:
                    chart_config = chart_result.config
    except Exception as e:
        logger.warning("看板 widget 图表配置生成失败 (不影响保存): %s", e)

    # 自动布局: 根据已有 widget 计算新 widget 的位置, 避免全部堆在 (0,0)
    # 策略: 按行填充, 每行最多 2 个 widget (列宽 6, 总宽 12), 超出换行
    existing = (
        await db.execute(
            select(DashboardWidget.position_x, DashboardWidget.position_y, DashboardWidget.width)
            .where(DashboardWidget.dashboard_id == dashboard_id)
            .order_by(DashboardWidget.position_y, DashboardWidget.position_x)
        )
    ).all()
    auto_position_x, auto_position_y = _calc_next_position(existing)

    widget = DashboardWidget(
        dashboard_id=dashboard_id,
        tenant_id=user.tenant_id,
        question=question,
        query_sql=query_sql,
        datasource_id=datasource_id,
        chart_type=data.get("chart_type", "bar"),
        columns="",
        rows="",
        row_count=0,
        chart_option=chart_config,  # 缓存 {chart_type, dim_col, measure_cols}
        position_x=int(data.get("position_x", auto_position_x)),
        position_y=int(data.get("position_y", auto_position_y)),
        width=int(data.get("width", 6)),
        height=int(data.get("height", 4)),
    )
    db.add(widget)
    await db.commit()
    await db.refresh(widget)
    return _widget_to_dict(widget)


@router.delete("/{dashboard_id}/widgets/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(
    dashboard_id: str,
    widget_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 widget。"""
    w = (
        await db.execute(
            select(DashboardWidget).where(
                DashboardWidget.id == widget_id,
                DashboardWidget.dashboard_id == dashboard_id,
                DashboardWidget.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Widget 不存在")
    await db.delete(w)
    await db.commit()
    return None


@router.put("/{dashboard_id}/widgets/layout")
async def update_widget_layout(
    dashboard_id: str,
    items: list[dict],
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """批量更新 widget 布局 (拖拽/缩放后保存 position_x/y/width/height)。"""
    # 校验看板归属
    dash = (
        await db.execute(
            select(Dashboard).where(
                Dashboard.id == dashboard_id,
                Dashboard.tenant_filter(user.tenant_id),
            )
        )
    ).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="看板不存在")

    updated = 0
    for item in items:
        wid = item.get("id")
        if not wid:
            continue
        w = (
            await db.execute(
                select(DashboardWidget).where(
                    DashboardWidget.id == wid,
                    DashboardWidget.dashboard_id == dashboard_id,
                    DashboardWidget.tenant_id == user.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if not w:
            continue
        w.position_x = int(item.get("x", w.position_x))
        w.position_y = int(item.get("y", w.position_y))
        w.width = int(item.get("w", w.width))
        w.height = int(item.get("h", w.height))
        updated += 1
    await db.commit()
    return {"updated": updated}


@router.put("/{dashboard_id}/widgets/{widget_id}/refresh")
async def refresh_widget(
    dashboard_id: str,
    widget_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """刷新 widget: 实时重跑 SQL 并生成图表 (不入库快照, 直接返回)。

    实时查询模式: 看板数据始终基于最新 SQL 执行结果。
    """
    w = (
        await db.execute(
            select(DashboardWidget).where(
                DashboardWidget.id == widget_id,
                DashboardWidget.dashboard_id == dashboard_id,
                DashboardWidget.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Widget 不存在")
    if not w.query_sql:
        raise HTTPException(status_code=400, detail="该组件无保存的 SQL，无法刷新")
    if not w.datasource_id:
        raise HTTPException(status_code=400, detail="该组件无数据源，无法刷新")

    # 从 DB 查出完整 DataSource 行 (修复原 bug: 不能只传 id)
    ds = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == w.datasource_id,
                DataSource.tenant_filter(user.tenant_id),
            )
        )
    ).scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not ds.is_active:
        raise HTTPException(status_code=403, detail="数据源已禁用")

    # 重跑 SQL
    from app.services.datasource_engine import datasource_to_url, get_engine_pool
    from app.services.sql_executor import execute_sql
    url = datasource_to_url(ds)
    result = await execute_sql(
        sql=w.query_sql, datasource_id=w.datasource_id, url=url,
        engine_pool=get_engine_pool(),
    )
    if result.error:
        raise HTTPException(status_code=500, detail=f"查询执行失败: {result.error}")

    columns = list(result.columns) if hasattr(result, "columns") else []
    rows = [list(r) for r in (result.rows or [])]

    # 图表: 用缓存的 chart_config + 实时数据 inject_data (毫秒级, 不调 LLM)
    chart_option = None
    if w.chart_option:
        try:
            from app.ai.chart_agent import inject_data
            # 缓存的是 {chart_type, dim_col, measure_cols}, 用实时 rows 注入
            chart_option = inject_data(w.chart_option, columns, rows)
        except Exception as e:
            logger.warning("看板图表缓存注入失败: %s", e)

    # 缓存缺失或注入失败 → 规则推断降级 (不调 LLM, 保证速度)
    if chart_option is None:
        try:
            from app.ai.chart_agent import infer_chart_by_rule
            chart_option = infer_chart_by_rule(columns, rows)
        except Exception as e:
            logger.warning("看板图表规则推断失败: %s", e)

    # 不入库快照, 直接返回实时数据 + 图表
    return {
        "id": w.id,
        "dashboard_id": w.dashboard_id,
        "question": w.question,
        "query_sql": w.query_sql,
        "datasource_id": w.datasource_id,
        "chart_type": w.chart_type,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "chart_option": chart_option,
        "position_x": w.position_x,
        "position_y": w.position_y,
        "width": w.width,
        "height": w.height,
        "created_at": _iso(w.created_at),
        "updated_at": _iso(w.updated_at),
    }

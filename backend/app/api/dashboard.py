"""Dashboard layout and persistence API."""
import asyncio
import json
import secrets
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import Dashboard, DashboardShare, DashboardWidget, DataSource
from app.core.security import get_current_user, hash_password, verify_password
from app.core.logging import get_logger
from app.ai.nodes.execution import execute_sql
from app.ai.chart_type import infer_chart_type

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboards", tags=["仪表盘"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


COL_COUNT = 12  # 12-column grid


async def _auto_position(session, dashboard_id: str, w: int, h: int, tenant_id: str) -> tuple[int, int]:
    """Calculate next available grid position. Row-by-row scan, finds first gap that fits w*h."""
    w = min(w, COL_COUNT)
    result = await session.execute(
        select(DashboardWidget)
        .where(DashboardWidget.dashboard_id == dashboard_id)
        .order_by(DashboardWidget.position_y, DashboardWidget.position_x)
    )
    existing = result.scalars().all()

    placed: list[dict] = [
        {"x": e.position_x, "y": e.position_y, "w": max(e.width, 1), "h": max(e.height, 1)}
        for e in existing
    ]
    max_y = max((p["y"] + p["h"] for p in placed), default=0) if placed else 0

    def _collides(x: int, y: int) -> bool:
        return any(
            x < p["x"] + p["w"] and x + w > p["x"] and
            y < p["y"] + p["h"] and y + h > p["y"]
            for p in placed
        )

    for y in range(max_y + 10):
        for x in range(COL_COUNT - w + 1):
            if not _collides(x, y):
                return x, y
    return 0, max_y


def _iso(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_layout_config(val):
    """Parse layout_config from JSON string to dict, or return None."""
    if val is None:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


@router.get("", response_model=dict)
async def list_dashboards(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户的仪表盘（不包含 widget 详情）。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(Dashboard)
        .where(Dashboard.tenant_id == tenant_id)
        .order_by(desc(Dashboard.updated_at))
    )
    dashboards = result.scalars().all()
    return {
        "data": [
            {
                "id": str(d.id),
                "name": d.name,
                "datasource_id": str(d.datasource_id) if d.datasource_id else None,
                "layout_config": _parse_layout_config(d.layout_config),
                "created_at": _iso(d.created_at),
                "updated_at": _iso(d.updated_at),
            }
            for d in dashboards
        ],
        "total": len(dashboards),
    }


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建新仪表盘。"""
    tenant_id = user["tenant_id"]
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "仪表盘名称不能为空"),
        )

    datasource_id = data.get("datasource_id")
    if not datasource_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "请选择数据源"),
        )
    # Verify datasource exists and belongs to tenant
    ds_result = await db.execute(
        select(DataSource).where(
            DataSource.id == datasource_id,
            DataSource.tenant_id == tenant_id,
        )
    )
    ds = ds_result.scalar_one_or_none()
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )

    dashboard = Dashboard(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user["user_id"],
        name=name,
        datasource_id=datasource_id,
    )
    db.add(dashboard)
    await db.commit()
    await db.refresh(dashboard)

    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "datasource_id": str(dashboard.datasource_id) if dashboard.datasource_id else None,
        "layout_config": _parse_layout_config(dashboard.layout_config),
        "created_at": _iso(dashboard.created_at),
        "updated_at": _iso(dashboard.updated_at),
    }


# ===== Public share access (no auth required) =====
# MUST be registered before /{dashboard_id} to avoid "shared" being parsed as dashboard_id

@router.get("/shared/{share_token}", response_model=dict)
async def access_shared_dashboard(
    share_token: str,
    password: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """通过分享 token 访问仪表盘（无需认证）。"""
    result = await db.execute(
        select(DashboardShare).where(
            DashboardShare.share_token == share_token,
            DashboardShare.is_active == True,
        )
    )
    share = result.scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=404, detail=_error("NOT_FOUND", "分享链接不存在或已失效"))

    if share.expires_at and share.expires_at < datetime.now():
        raise HTTPException(status_code=410, detail=_error("EXPIRED", "分享链接已过期"))

    if share.password:
        if not password:
            raise HTTPException(status_code=401, detail=_error("PASSWORD_REQUIRED", "此分享链接需要密码"))
        if not verify_password(password, share.password):
            raise HTTPException(status_code=401, detail=_error("WRONG_PASSWORD", "密码错误"))

    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == share.dashboard_id)
    )
    dashboard = dash_result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail=_error("NOT_FOUND", "仪表盘不存在"))

    widgets_result = await db.execute(
        select(DashboardWidget)
        .where(DashboardWidget.dashboard_id == str(dashboard.id))
        .order_by(DashboardWidget.position_y, DashboardWidget.position_x)
    )
    widgets = widgets_result.scalars().all()

    widget_list = []
    for w in widgets:
        try:
            cols = json.loads(w.columns) if w.columns else []
        except (json.JSONDecodeError, TypeError):
            cols = []
        try:
            rows = json.loads(w.rows) if w.rows else []
        except (json.JSONDecodeError, TypeError):
            rows = []
        widget_list.append({
            "id": str(w.id),
            "question": w.question,
            "query_sql": w.query_sql,
            "chart_type": w.chart_type,
            "columns": cols,
            "rows": rows,
            "row_count": w.row_count,
            "position_x": w.position_x,
            "position_y": w.position_y,
            "width": w.width,
            "height": w.height,
        })

    return {
        "name": dashboard.name,
        "layout_config": _parse_layout_config(dashboard.layout_config),
        "widgets": widget_list,
        "shared_at": _iso(share.created_at),
        "expires_at": _iso(share.expires_at),
    }


@router.get("/{dashboard_id}", response_model=dict)
async def get_dashboard(
    dashboard_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取仪表盘详情，包含完整 widget 列表（自动执行 SQL 获取最新数据）。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(Dashboard).where(
            Dashboard.id == dashboard_id,
            Dashboard.tenant_id == tenant_id,
        )
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "仪表盘不存在"),
        )

    widgets_result = await db.execute(
        select(DashboardWidget)
        .where(DashboardWidget.dashboard_id == dashboard_id)
        .order_by(DashboardWidget.position_y, DashboardWidget.position_x)
    )
    widgets = widgets_result.scalars().all()

    async def _widget_to_dict(w):
        d = {
            "id": str(w.id),
            "question": w.question,
            "query_sql": w.query_sql,
            "datasource_id": str(w.datasource_id),
            "chart_type": w.chart_type,
            "row_count": w.row_count,
            "position_x": w.position_x,
            "position_y": w.position_y,
            "width": w.width,
            "height": w.height,
            "created_at": _iso(w.created_at),
            "updated_at": _iso(w.updated_at),
        }
        # Execute SQL to get fresh data
        if w.query_sql:
            # Check if datasource still exists
            ds_result = await db.execute(
                select(DataSource).where(
                    DataSource.id == w.datasource_id,
                    DataSource.tenant_id == tenant_id,
                )
            )
            ds = ds_result.scalar_one_or_none()
            if not ds:
                d["columns"] = []
                d["rows"] = []
                d["error"] = "DATASOURCE_DELETED"
                d["error_msg"] = "该组件绑定的数据源已删除，请重新配置"
            else:
                try:
                    exec_result = await execute_sql(w.query_sql, str(w.datasource_id), tenant_id=str(tenant_id))
                    if exec_result.get("success"):
                        d["columns"] = exec_result.get("columns", [])
                        d["rows"] = exec_result.get("rows", [])
                        d["row_count"] = exec_result.get("row_count", 0)
                        # Update cached data in DB
                        w.columns = json.dumps(d["columns"], ensure_ascii=False)
                        w.rows = json.dumps(d["rows"], ensure_ascii=False, default=str)
                        w.row_count = d["row_count"]
                    else:
                        # Fall back to cached data
                        try:
                            d["columns"] = json.loads(w.columns) if w.columns else []
                        except (json.JSONDecodeError, TypeError):
                            d["columns"] = []
                        try:
                            d["rows"] = json.loads(w.rows) if w.rows else []
                        except (json.JSONDecodeError, TypeError):
                            d["rows"] = []
                        d["error"] = "QUERY_FAILED"
                        d["error_msg"] = exec_result.get("error", "查询执行失败")
                except Exception:
                    # Fall back to cached data on execution error
                    try:
                        d["columns"] = json.loads(w.columns) if w.columns else []
                    except (json.JSONDecodeError, TypeError):
                        d["columns"] = []
                    try:
                        d["rows"] = json.loads(w.rows) if w.rows else []
                    except (json.JSONDecodeError, TypeError):
                        d["rows"] = []
                    d["error"] = "QUERY_FAILED"
                    d["error_msg"] = "查询执行异常"
        else:
            try:
                d["columns"] = json.loads(w.columns) if w.columns else []
            except (json.JSONDecodeError, TypeError):
                d["columns"] = []
            try:
                d["rows"] = json.loads(w.rows) if w.rows else []
            except (json.JSONDecodeError, TypeError):
                d["rows"] = []

        return d

    widget_dicts = await asyncio.gather(*[_widget_to_dict(w) for w in widgets])
    # Commit any cached data updates
    await db.commit()

    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "datasource_id": str(dashboard.datasource_id) if dashboard.datasource_id else None,
        "layout_config": _parse_layout_config(dashboard.layout_config),
        "widgets": widget_dicts,
        "created_at": _iso(dashboard.created_at),
        "updated_at": _iso(dashboard.updated_at),
    }


@router.put("/{dashboard_id}", response_model=dict)
async def update_dashboard(
    dashboard_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新仪表盘（名称、布局配置）。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(Dashboard).where(
            Dashboard.id == dashboard_id,
            Dashboard.tenant_id == tenant_id,
        )
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "仪表盘不存在"),
        )

    if "name" in data:
        name = data["name"].strip() if data["name"] else ""
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_INPUT", "仪表盘名称不能为空"),
            )
        dashboard.name = name

    if "datasource_id" in data:
        ds_id = data["datasource_id"]
        if ds_id:
            # Verify datasource exists
            ds_result = await db.execute(
                select(DataSource).where(
                    DataSource.id == ds_id,
                    DataSource.tenant_id == tenant_id,
                )
            )
            ds = ds_result.scalar_one_or_none()
            if not ds:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
                )
            dashboard.datasource_id = ds_id
        else:
            dashboard.datasource_id = None

    if "layout_config" in data:
        layout = data["layout_config"]
        if isinstance(layout, dict):
            dashboard.layout_config = json.dumps(layout, ensure_ascii=False)
        elif layout is None:
            dashboard.layout_config = None
        else:
            dashboard.layout_config = str(layout)

    await db.commit()
    await db.refresh(dashboard)

    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "datasource_id": str(dashboard.datasource_id) if dashboard.datasource_id else None,
        "layout_config": _parse_layout_config(dashboard.layout_config),
        "created_at": _iso(dashboard.created_at),
        "updated_at": _iso(dashboard.updated_at),
    }


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除仪表盘（级联删除所有 widget）。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(Dashboard).where(
            Dashboard.id == dashboard_id,
            Dashboard.tenant_id == tenant_id,
        )
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "仪表盘不存在"),
        )

    # Delete all widgets first
    await db.execute(
        DashboardWidget.__table__.delete().where(
            DashboardWidget.dashboard_id == dashboard_id,
        )
    )
    await db.delete(dashboard)
    await db.commit()
    return None


@router.post("/{dashboard_id}/widgets", response_model=dict, status_code=status.HTTP_201_CREATED)
async def add_widget(
    dashboard_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """向仪表盘添加 widget。"""
    tenant_id = user["tenant_id"]

    # Verify dashboard ownership
    result = await db.execute(
        select(Dashboard).where(
            Dashboard.id == dashboard_id,
            Dashboard.tenant_id == tenant_id,
        )
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "仪表盘不存在"),
        )

    question = data.get("question", "").strip()
    datasource_id = data.get("datasource_id")
    chart_type = data.get("chart_type", "table")
    query_sql = data.get("query_sql", data.get("sql", "")).strip()

    if not question:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "问题不能为空"),
        )
    if not datasource_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "数据源 ID 不能为空"),
        )

    # Verify datasource belongs to tenant
    ds_result = await db.execute(
        select(DataSource).where(
            DataSource.id == datasource_id,
            DataSource.tenant_id == tenant_id,
        )
    )
    ds = ds_result.scalar_one_or_none()
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )

    # Check datasource is active
    if not ds.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("DATASOURCE_INACTIVE", "数据源已禁用，请联系管理员"),
        )

    # Validate chart_type
    VALID_CHART_TYPES = {"table", "line", "bar", "pie", "metric", "area", "scatter"}
    chart_type = data.get("chart_type", "table")
    if chart_type not in VALID_CHART_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", f"不支持的图表类型: {chart_type}"),
        )

    # Validate position/dimension fields
    try:
        pos_x = int(data.get("position_x", -1))
        pos_y = int(data.get("position_y", -1))
        w = int(data.get("width", 0)) or 6
        h = int(data.get("height", 0)) or 3
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "位置和尺寸必须为整数"),
        )

    # Auto-calculate position if not specified (pos_x=-1 or position is (0,0) collision)
    if pos_x < 0 or pos_y < 0:
        pos_x, pos_y = await _auto_position(db, dashboard_id, w, h, tenant_id)

    # Generate SQL and execute to get initial data
    # Only store question + query_sql + chart_type — data is re-executed on load/refresh
    if query_sql:
        # SQL provided — use it directly
        pass
    else:
        # Run AI pipeline to generate SQL from natural language
        from app.ai.graph import build_graph
        graph = build_graph()
        state = await graph.ainvoke({
            "question": question,
            "datasource_id": str(datasource_id),
            "tenant_id": str(tenant_id),
        })
        query_sql = state.get("sql")
        if not state.get("success") or not query_sql:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("SQL_GEN_FAILED", state.get("error", "SQL 生成失败")),
            )
        if chart_type == "table":
            chart_type = infer_chart_type_from_sql(query_sql) or "table"

    # Execute SQL to get initial data for the response
    exec_result = await execute_sql(query_sql, str(datasource_id), tenant_id=str(tenant_id))
    if exec_result.get("success"):
        columns = exec_result.get("columns", [])
        rows = exec_result.get("rows", [])
        row_count = exec_result.get("row_count", 0)
        if chart_type == "table":
            chart_type = infer_chart_type(columns, rows)
    else:
        columns = []
        rows = []
        row_count = 0

    widget = DashboardWidget(
        id=uuid.uuid4(),
        dashboard_id=dashboard_id,
        tenant_id=tenant_id,
        question=question,
        query_sql=query_sql,
        datasource_id=datasource_id,
        chart_type=chart_type,
        columns=json.dumps(columns, ensure_ascii=False),
        rows=json.dumps(rows, ensure_ascii=False, default=str),
        row_count=row_count,
        position_x=pos_x,
        position_y=pos_y,
        width=w,
        height=h,
    )
    db.add(widget)
    await db.commit()
    await db.refresh(widget)

    return {
        "id": str(widget.id),
        "question": widget.question,
        "query_sql": widget.query_sql,
        "datasource_id": str(widget.datasource_id),
        "chart_type": widget.chart_type,
        "columns": json.loads(widget.columns) if widget.columns else [],
        "rows": json.loads(widget.rows) if widget.rows else [],
        "row_count": widget.row_count,
        "position_x": widget.position_x,
        "position_y": widget.position_y,
        "width": widget.width,
        "height": widget.height,
        "created_at": _iso(widget.created_at),
        "updated_at": _iso(widget.updated_at),
    }


@router.put("/{dashboard_id}/widgets/positions", response_model=dict)
async def batch_update_widget_positions(
    dashboard_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批量更新 widget 位置和尺寸。"""
    tenant_id = user["tenant_id"]

    # Verify dashboard ownership
    result = await db.execute(
        select(Dashboard).where(
            Dashboard.id == dashboard_id,
            Dashboard.tenant_id == tenant_id,
        )
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "仪表盘不存在"),
        )

    widgets_data = data.get("widgets", [])
    if not isinstance(widgets_data, list) or not widgets_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "widgets 必须为非空列表"),
        )

    updated = []
    for item in widgets_data:
        widget_id = item.get("id")
        if not widget_id:
            continue

        w_result = await db.execute(
            select(DashboardWidget).where(
                DashboardWidget.id == widget_id,
                DashboardWidget.dashboard_id == dashboard_id,
                DashboardWidget.tenant_id == tenant_id,
            )
        )
        widget = w_result.scalar_one_or_none()
        if not widget:
            continue

        if "position_x" in item:
            try:
                widget.position_x = int(item["position_x"])
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=_error("INVALID_INPUT", f"widget {widget_id}: position_x 必须为整数"),
                )
        if "position_y" in item:
            try:
                widget.position_y = int(item["position_y"])
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=_error("INVALID_INPUT", f"widget {widget_id}: position_y 必须为整数"),
                )
        if "width" in item:
            try:
                widget.width = int(item["width"])
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=_error("INVALID_INPUT", f"widget {widget_id}: width 必须为整数"),
                )
        if "height" in item:
            try:
                widget.height = int(item["height"])
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=_error("INVALID_INPUT", f"widget {widget_id}: height 必须为整数"),
                )

        updated.append({
            "id": str(widget.id),
            "position_x": widget.position_x,
            "position_y": widget.position_y,
            "width": widget.width,
            "height": widget.height,
        })

    await db.commit()

    return {"updated": updated, "count": len(updated)}


@router.put("/{dashboard_id}/widgets/{widget_id}", response_model=dict)
async def update_widget(
    dashboard_id: str,
    widget_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 widget 属性（如 chart_type），不重新执行查询。"""
    tenant_id = user["tenant_id"]

    result = await db.execute(
        select(DashboardWidget).where(
            DashboardWidget.id == widget_id,
            DashboardWidget.dashboard_id == dashboard_id,
            DashboardWidget.tenant_id == tenant_id,
        )
    )
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "Widget 不存在"),
        )

    VALID_CHART_TYPES = {"table", "line", "bar", "pie", "metric", "area", "scatter"}
    if "chart_type" in data:
        ct = data["chart_type"]
        if ct not in VALID_CHART_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_INPUT", f"不支持的图表类型: {ct}"),
            )
        widget.chart_type = ct

    if "question" in data:
        question = data["question"].strip() if data["question"] else ""
        if not question:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_error("INVALID_INPUT", "组件名称不能为空"),
            )
        widget.question = question

    if "position_x" in data:
        try:
            widget.position_x = int(data["position_x"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", "position_x 必须为整数"))
    if "position_y" in data:
        try:
            widget.position_y = int(data["position_y"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", "position_y 必须为整数"))
    if "width" in data:
        try:
            widget.width = int(data["width"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", "width 必须为整数"))
    if "height" in data:
        try:
            widget.height = int(data["height"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", "height 必须为整数"))

    await db.commit()
    await db.refresh(widget)

    return {
        "id": str(widget.id),
        "question": widget.question,
        "query_sql": widget.query_sql,
        "datasource_id": str(widget.datasource_id),
        "chart_type": widget.chart_type,
        "columns": json.loads(widget.columns) if widget.columns else [],
        "rows": json.loads(widget.rows) if widget.rows else [],
        "row_count": widget.row_count,
        "position_x": widget.position_x,
        "position_y": widget.position_y,
        "width": widget.width,
        "height": widget.height,
        "created_at": _iso(widget.created_at),
        "updated_at": _iso(widget.updated_at),
    }


@router.put("/{dashboard_id}/widgets/{widget_id}/position", response_model=dict)
async def update_widget_position(
    dashboard_id: str,
    widget_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新 widget 的位置和尺寸。"""
    tenant_id = user["tenant_id"]

    result = await db.execute(
        select(DashboardWidget).where(
            DashboardWidget.id == widget_id,
            DashboardWidget.dashboard_id == dashboard_id,
            DashboardWidget.tenant_id == tenant_id,
        )
    )
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "Widget 不存在"),
        )

    if "position_x" in data:
        try: widget.position_x = int(data["position_x"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", "position_x 必须为整数"))
    if "position_y" in data:
        try: widget.position_y = int(data["position_y"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", "position_y 必须为整数"))
    if "width" in data:
        try: widget.width = int(data["width"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", "width 必须为整数"))
    if "height" in data:
        try: widget.height = int(data["height"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", "height 必须为整数"))

    await db.commit()
    await db.refresh(widget)

    return {
        "id": str(widget.id),
        "position_x": widget.position_x,
        "position_y": widget.position_y,
        "width": widget.width,
        "height": widget.height,
    }


@router.post("/{dashboard_id}/widgets/{widget_id}/refresh", response_model=dict)
async def refresh_widget(
    dashboard_id: str,
    widget_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """重新执行 widget 的查询，更新数据。"""
    tenant_id = user["tenant_id"]

    result = await db.execute(
        select(DashboardWidget).where(
            DashboardWidget.id == widget_id,
            DashboardWidget.dashboard_id == dashboard_id,
            DashboardWidget.tenant_id == tenant_id,
        )
    )
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "Widget 不存在"),
        )

    if not widget.query_sql:
        # No saved SQL — cannot refresh
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("NO_SQL", "该组件无保存的 SQL，无法刷新"),
        )

    # Re-execute the saved SQL directly
    exec_result = await execute_sql(widget.query_sql, str(widget.datasource_id), tenant_id=str(tenant_id))

    if exec_result.get("success"):
        widget.columns = json.dumps(exec_result.get("columns", []), ensure_ascii=False)
        widget.rows = json.dumps(exec_result.get("rows", []), ensure_ascii=False, default=str)
        widget.row_count = exec_result.get("row_count", 0)
    # On failure, keep existing data

    await db.commit()
    await db.refresh(widget)

    return {
        "id": str(widget.id),
        "question": widget.question,
        "query_sql": widget.query_sql,
        "datasource_id": str(widget.datasource_id),
        "chart_type": widget.chart_type,
        "columns": json.loads(widget.columns) if widget.columns else [],
        "rows": json.loads(widget.rows) if widget.rows else [],
        "row_count": widget.row_count,
        "position_x": widget.position_x,
        "position_y": widget.position_y,
        "width": widget.width,
        "height": widget.height,
        "created_at": _iso(widget.created_at),
        "updated_at": _iso(widget.updated_at),
    }


@router.delete("/{dashboard_id}/widgets/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(
    dashboard_id: str,
    widget_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 widget。"""
    tenant_id = user["tenant_id"]

    result = await db.execute(
        select(DashboardWidget).where(
            DashboardWidget.id == widget_id,
            DashboardWidget.dashboard_id == dashboard_id,
            DashboardWidget.tenant_id == tenant_id,
        )
    )
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "Widget 不存在"),
        )

    await db.delete(widget)
    await db.commit()
    return None


# ===== Share endpoints =====

_SHARE_EXPIRY_OPTIONS = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "never": None,
}


@router.post("/{dashboard_id}/shares", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_share(
    dashboard_id: str,
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建看板分享链接。"""
    tenant_id = user["tenant_id"]

    # Verify dashboard ownership
    result = await db.execute(
        select(Dashboard).where(
            Dashboard.id == dashboard_id,
            Dashboard.tenant_id == tenant_id,
        )
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail=_error("NOT_FOUND", "仪表盘不存在"))

    # Parse expiry
    expires_in = data.get("expires_in", "7d")
    delta = _SHARE_EXPIRY_OPTIONS.get(expires_in)
    if delta is None and expires_in != "never":
        raise HTTPException(status_code=400, detail=_error("INVALID_INPUT", f"不支持的过期时间: {expires_in}"))

    expires_at = datetime.now() + delta if delta else None

    # Optional password
    password = data.get("password", "").strip()
    password_hash = None
    if password:
        password_hash = hash_password(password)

    share = DashboardShare(
        id=uuid.uuid4(),
        dashboard_id=dashboard_id,
        tenant_id=tenant_id,
        share_token=secrets.token_urlsafe(32),
        created_by=user["user_id"],
        expires_at=expires_at,
        password=password_hash,
        is_active=True,
    )
    db.add(share)
    await db.commit()
    await db.refresh(share)

    return {
        "id": str(share.id),
        "share_token": share.share_token,
        "expires_at": _iso(share.expires_at),
        "has_password": share.password is not None,
        "created_at": _iso(share.created_at),
    }


@router.get("/{dashboard_id}/shares", response_model=dict)
async def list_shares(
    dashboard_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出租户仪表盘的所有分享链接。"""
    tenant_id = user["tenant_id"]

    # Verify dashboard ownership
    result = await db.execute(
        select(Dashboard).where(
            Dashboard.id == dashboard_id,
            Dashboard.tenant_id == tenant_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=_error("NOT_FOUND", "仪表盘不存在"))

    shares_result = await db.execute(
        select(DashboardShare)
        .where(DashboardShare.dashboard_id == dashboard_id)
        .order_by(desc(DashboardShare.created_at))
    )
    shares = shares_result.scalars().all()

    now = datetime.now()
    return {
        "data": [
            {
                "id": str(s.id),
                "share_token": s.share_token,
                "expires_at": _iso(s.expires_at),
                "has_password": s.password is not None,
                "is_active": s.is_active,
                "is_expired": s.expires_at is not None and s.expires_at < now if s.expires_at else False,
                "created_at": _iso(s.created_at),
            }
            for s in shares
        ]
    }


@router.delete("/{dashboard_id}/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share(
    dashboard_id: str,
    share_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """撤销分享链接。"""
    tenant_id = user["tenant_id"]
    result = await db.execute(
        select(DashboardShare).where(
            DashboardShare.id == share_id,
            DashboardShare.dashboard_id == dashboard_id,
            DashboardShare.tenant_id == tenant_id,
        )
    )
    share = result.scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=404, detail=_error("NOT_FOUND", "分享链接不存在"))
    share.is_active = False
    await db.commit()
    return None

"""Dashboard layout and persistence API."""
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import Dashboard, DashboardWidget, DataSource
from app.core.security import get_current_user
from app.core.logging import get_logger
from app.ai.nodes.execution import execute_sql
from app.ai.chart_type import infer_chart_type

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboards", tags=["仪表盘"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


def _iso(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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

    dashboard = Dashboard(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user["user_id"],
        name=name,
    )
    db.add(dashboard)
    await db.commit()
    await db.refresh(dashboard)

    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "created_at": _iso(dashboard.created_at),
        "updated_at": _iso(dashboard.updated_at),
    }


@router.get("/{dashboard_id}", response_model=dict)
async def get_dashboard(
    dashboard_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取仪表盘详情，包含完整 widget 列表。"""
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

    def _widget_to_dict(w):
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
        try:
            d["columns"] = json.loads(w.columns) if w.columns else []
        except (json.JSONDecodeError, TypeError):
            d["columns"] = []
        try:
            d["rows"] = json.loads(w.rows) if w.rows else []
        except (json.JSONDecodeError, TypeError):
            d["rows"] = []
        return d

    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "widgets": [_widget_to_dict(w) for w in widgets],
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
    """更新仪表盘名称。"""
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

    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "仪表盘名称不能为空"),
        )

    dashboard.name = name
    await db.commit()
    await db.refresh(dashboard)

    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
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
        pos_x = int(data.get("position_x", 0))
        pos_y = int(data.get("position_y", 0))
        w = int(data.get("width", 6))
        h = int(data.get("height", 4))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "位置和尺寸必须为整数"),
        )

    # Use columns/rows from request if provided (frontend already has query results)
    # Otherwise run AI pipeline to generate them from natural language question
    if data.get("columns") is not None and data.get("rows") is not None:
        # Frontend already executed — use provided results directly
        columns_json = json.dumps(data["columns"], ensure_ascii=False)
        rows_json = json.dumps(data["rows"], ensure_ascii=False, default=str)
        row_count = len(data["rows"])
        if chart_type == "table":
            inferred = infer_chart_type(data["columns"], data["rows"])
            chart_type = inferred
    elif query_sql:
        # SQL provided — execute directly
        exec_result = await execute_sql(query_sql, str(datasource_id), tenant_id=str(tenant_id))
        if exec_result.get("success"):
            columns_json = json.dumps(exec_result.get("columns", []), ensure_ascii=False)
            rows_json = json.dumps(exec_result.get("rows", []), ensure_ascii=False, default=str)
            row_count = exec_result.get("row_count", 0)
            if chart_type == "table":
                inferred_chart = infer_chart_type(
                    exec_result.get("columns", []),
                    exec_result.get("rows", []),
                )
                chart_type = inferred_chart
        else:
            columns_json = json.dumps([])
            rows_json = json.dumps([])
            row_count = 0
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
        if state.get("success") and query_sql:
            exec_result = await execute_sql(query_sql, str(datasource_id), tenant_id=str(tenant_id))
        else:
            exec_result = {"success": False, "error": state.get("error", "SQL 生成失败")}

        if exec_result.get("success"):
            columns_json = json.dumps(exec_result.get("columns", []), ensure_ascii=False)
            rows_json = json.dumps(exec_result.get("rows", []), ensure_ascii=False, default=str)
            row_count = exec_result.get("row_count", 0)
            if chart_type == "table":
                inferred_chart = infer_chart_type(
                    exec_result.get("columns", []),
                    exec_result.get("rows", []),
                )
                chart_type = inferred_chart
        else:
            columns_json = json.dumps([])
            rows_json = json.dumps([])
            row_count = 0

    widget = DashboardWidget(
        id=uuid.uuid4(),
        dashboard_id=dashboard_id,
        tenant_id=tenant_id,
        question=question,
        query_sql=query_sql or None,
        datasource_id=datasource_id,
        chart_type=chart_type,
        columns=columns_json,
        rows=rows_json,
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

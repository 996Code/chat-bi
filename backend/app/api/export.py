"""查询结果 CSV 导出 API。"""
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import get_current_user
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/export", tags=["导出"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


def _sanitize_csv_cell(value: str) -> str:
    """Prevent CSV formula injection by prefixing with single quote if starts with dangerous char."""
    if not isinstance(value, str):
        return value
    if value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


@router.post("/csv")
async def export_csv(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出查询结果为 CSV。"""
    columns = data.get("columns", [])
    rows = data.get("rows", [])

    # Extract column names (support both list of strings and list of objects)
    col_names = []
    for col in columns:
        if isinstance(col, dict):
            col_names.append(col.get("name", col.get("label", "")))
        else:
            col_names.append(str(col))

    if not col_names or not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NO_DATA", "无数据可导出"),
        )

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for row in rows:
        writer.writerow([_sanitize_csv_cell(str(row.get(col, ""))) for col in col_names])

    csv_content = output.getvalue()
    output.close()

    # Analytics
    from app.services.analytics_service import track_event, EVENT_QUERY_EXPORT_CSV
    await track_event(db, user["tenant_id"], user["user_id"], EVENT_QUERY_EXPORT_CSV, {
        "row_count": len(rows),
    })
    await db.commit()

    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=export.csv",
        },
    )

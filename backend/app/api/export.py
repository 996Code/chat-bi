"""查询结果 CSV 导出 API。"""
import csv
import io
from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import get_current_user
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/export", tags=["导出"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.post("/csv")
async def export_csv(
    data: dict,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出查询结果为 CSV。"""
    columns = data.get("columns", [])
    rows = data.get("rows", [])

    if not columns or not rows:
        return {"error": "无数据可导出"}

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(col, "") for col in columns])

    csv_content = output.getvalue()
    output.close()

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=export.csv",
            "Content-Encoding": "utf-8",
        },
    )

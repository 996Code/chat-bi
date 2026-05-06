"""Evaluation API — run Text-to-SQL accuracy tests."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import get_current_user, require_role
from app.services.evaluation_service import run_evaluation

router = APIRouter(prefix="/evaluation", tags=["评估"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.post("/run")
async def run_eval(
    data: dict,
    admin: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """运行评估测试集。

    请求体: {
        "datasource_id": "...",
        "dataset": [
            {"question": "...", "expected_sql": "SELECT ...", "expected_columns": ["col1", "col2"]}
        ]
    }
    """
    tenant_id = admin["tenant_id"]
    datasource_id = data.get("datasource_id")
    dataset = data.get("dataset", [])

    if not datasource_id or not dataset:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "需要 datasource_id 和 dataset 字段"),
        )

    result = await run_evaluation(db, tenant_id, datasource_id, dataset)
    return result

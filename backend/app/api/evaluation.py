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
            {"question": "各城市订单数量"}
        ]
    }

    评估管线和正常对话查询完全一致：
    缓存检查 → 意图识别 → Schema 选择 → SQL 生成 → 执行查询 → 图表推断
    缓存命中时只复用 SQL，查询结果仍从数据库实时获取。
    非数据查询意图会显示友好提示，和对话界面一致。
    """
    tenant_id = admin["tenant_id"]
    datasource_id = data.get("datasource_id")
    dataset = data.get("dataset", [])

    if not datasource_id or not dataset:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("INVALID_INPUT", "需要 datasource_id 和 dataset 字段"),
        )

    result = await run_evaluation(db, tenant_id, datasource_id, dataset, admin["user_id"])
    return result

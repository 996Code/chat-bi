import asyncio
import time
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import DataSource, MetadataConfig
from app.core.security import get_current_user
from app.core.logging import get_logger
from app.schemas.query import QueryRequest, QueryResponse
from app.ai.graph import build_graph
from app.services.connection_pool import pool_manager

logger = get_logger(__name__)

router = APIRouter(prefix="/query", tags=["查询"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


@router.post("", response_model=QueryResponse)
async def create_query(
    data: QueryRequest,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    start = time.monotonic()
    tenant_id = user["tenant_id"]

    # Verify datasource belongs to tenant
    from sqlalchemy import select

    result = await db.execute(
        select(DataSource).where(
            DataSource.id == data.datasource_id,
            DataSource.tenant_id == tenant_id,
        )
    )
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("DATASOURCE_NOT_FOUND", "数据源不存在或无权访问"),
        )

    # Get schema context from metadata_configs
    config_result = await db.execute(
        select(MetadataConfig).where(
            MetadataConfig.datasource_id == data.datasource_id,
            MetadataConfig.tenant_id == tenant_id,
        )
    )
    config = config_result.scalar_one_or_none()
    schema_context = config.config if config else ""

    # Build graph and execute
    try:
        graph = build_graph()
        initial_state = {
            "question": data.question,
            "datasource_id": data.datasource_id,
            "schema_context": schema_context,
        }

        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state),
            timeout=35.0,  # Slightly above the 30s SQL timeout
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        response = QueryResponse(
            success=final_state.get("success", False),
            intent=final_state.get("intent"),
            sql=final_state.get("sql"),
            columns=final_state.get("columns", []),
            rows=final_state.get("rows", []),
            row_count=final_state.get("row_count", 0),
            error=final_state.get("error"),
            execution_time_ms=final_state.get("execution_time_ms") or elapsed_ms,
        )
        return response

    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return QueryResponse(
            success=False,
            error="查询超时（35秒限制）",
            execution_time_ms=elapsed_ms,
        )
    except Exception as e:
        logger.exception("Query pipeline error: %s", e)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return QueryResponse(
            success=False,
            error=f"查询失败: {e}",
            execution_time_ms=elapsed_ms,
        )

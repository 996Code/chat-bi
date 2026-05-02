from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import DataSource
from app.core.security import get_current_user
from app.core.logging import get_logger
from app.schemas.datasource import (
    DataSourceCreate,
    DataSourceUpdate,
    DataSourceResponse,
)
from app.services.datasource_service import DataSourceService
from app.services.mysql_schema_scanner import scan_mysql_schema
from app.services.connection_pool import pool_manager

logger = get_logger(__name__)

router = APIRouter(prefix="/datasources", tags=["数据源"])


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "details": None}


def _to_response(ds: DataSource) -> DataSourceResponse:
    return DataSourceResponse(
        id=str(ds.id),
        name=ds.name,
        type=ds.db_type,
        host=ds.host,
        port=ds.port,
        database_name=ds.database_name,
        status="active" if ds.is_active else "inactive",
        last_health_check=str(ds.last_health_check) if ds.last_health_check else None,
        health_check_error=None,
    )


@router.get("", response_model=list[DataSourceResponse])
async def list_datasources(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    service = DataSourceService(db, tenant_id=user["tenant_id"])
    datasources = await service.list_all()
    return [_to_response(ds) for ds in datasources]


@router.post("", response_model=DataSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_datasource(
    data: DataSourceCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DataSourceService(db, tenant_id=user["tenant_id"])
    ds = await service.create(data)

    # Audit log
    try:
        from app.services.audit_service import log_action
        await log_action(
            db, user["tenant_id"], user["user_id"],
            "DATASOURCE_CREATE", "datasource", str(ds.id),
            f"name={ds.name} type={ds.db_type}",
        )
        await db.commit()
    except Exception:
        # Don't fail the request if audit log fails
        await db.rollback()

    return _to_response(ds)


@router.post("/{ds_id}/test", response_model=dict)
async def test_datasource(
    ds_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DataSourceService(db, tenant_id=user["tenant_id"])
    result = await service.test_connection(ds_id)
    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("CONNECTION_FAILED", result.get("error", "连接失败")),
        )
    return result


@router.post("/{ds_id}/scan", response_model=dict)
async def scan_datasource(
    ds_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DataSourceService(db, tenant_id=user["tenant_id"])
    ds = await service.get_by_id(ds_id)
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "数据源不存在"),
        )

    # Test connection first
    test_result = await service.test_connection(ds_id)
    if not test_result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_error("CONNECTION_FAILED", test_result.get("error", "连接失败")),
        )

    # Scan schema
    engine = await pool_manager.get_pool(ds)
    result = await scan_mysql_schema(engine, db, ds)
    return result


@router.put("/{ds_id}", response_model=DataSourceResponse)
async def update_datasource(
    ds_id: str,
    data: DataSourceUpdate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DataSourceService(db, tenant_id=user["tenant_id"])
    ds = await service.update(ds_id, data)
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "数据源不存在"),
        )
    return _to_response(ds)


@router.delete("/{ds_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_datasource(
    ds_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DataSourceService(db, tenant_id=user["tenant_id"])
    deleted = await service.delete(ds_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "数据源不存在"),
        )
    # Close pool
    await pool_manager.close_pool(ds_id)
    return None


@router.get("/{ds_id}/health", response_model=dict)
async def health_check_datasource(
    ds_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DataSourceService(db, tenant_id=user["tenant_id"])
    ds = await service.get_by_id(ds_id)
    if not ds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("NOT_FOUND", "数据源不存在"),
        )

    result = await pool_manager.health_check(ds_id, ds)

    # Update health check in DB
    ds.last_health_check = datetime.now(timezone.utc)
    if result["healthy"]:
        ds.is_active = True
    else:
        ds.is_active = False
    await db.commit()

    return {"healthy": result["healthy"], "error": result["error"]}

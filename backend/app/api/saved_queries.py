"""
T051: Saved Query API — 看板页数据源 (查询历史/收藏)

对标 ARC-05: SavedQuery 是历史查询挖掘的输入数据源。
本端点提供查询历史的列表/详情读取, 供前端看板页渲染已保存查询的图表。

注意 (已知技术债): SavedQuery 无 data_source_id 字段 (models.py:171)，
前端列表通过 conversation_id 关联兜底显示来源, 不擅自改模型 (迁移风险)。
"""
from __future__ import annotations

import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_user
from app.db.models import DataSource, SavedQuery
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/saved-queries", tags=["saved-queries"])

# CSV 导出最大行数 (防超大结果 OOM, 对标 sql_max_rows 上限)
_CSV_EXPORT_MAX_ROWS = 50000


class SavedQueryOut(BaseModel):
    id: str
    user_id: str | None = None
    conversation_id: str | None = None
    question: str
    sql_text: str
    result_summary: str | None = None
    chart_config: dict | None = None
    created_at: str | None = None


@router.get("", response_model=list[SavedQueryOut])
async def list_saved_queries(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """已保存查询列表 (T051, 分页, 按 created_at 倒序)。"""
    stmt = (
        select(SavedQuery)
        .where(SavedQuery.tenant_filter(user.tenant_id))
        .order_by(SavedQuery.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return [
        SavedQueryOut(
            id=q.id, user_id=q.user_id, conversation_id=q.conversation_id,
            question=q.question, sql_text=q.sql_text,
            result_summary=q.result_summary, chart_config=q.chart_config,
            created_at=q.created_at.isoformat() if q.created_at else None,
        )
        for q in result.scalars()
    ]


@router.get("/{sq_id}", response_model=SavedQueryOut)
async def get_saved_query(
    sq_id: str,
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """已保存查询详情 (含 chart_config)。"""
    q = (
        await db.execute(
            select(SavedQuery).where(
                SavedQuery.tenant_filter(user.tenant_id),
                SavedQuery.id == sq_id,
            )
        )
    ).scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="查询记录不存在")
    return SavedQueryOut(
        id=q.id, user_id=q.user_id, conversation_id=q.conversation_id,
        question=q.question, sql_text=q.sql_text,
        result_summary=q.result_summary, chart_config=q.chart_config,
        created_at=q.created_at.isoformat() if q.created_at else None,
    )


@router.get("/{sq_id}/export")
async def export_saved_query_csv(
    sq_id: str,
    data_source_id: str = Query(..., description="数据源 id (SavedQuery 无此字段, 需前端传入)"),
    user: AuthUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """导出已保存查询为 CSV (UX-08)。

    重跑 SavedQuery 的 sql_text 拿全量结果 (受 _CSV_EXPORT_MAX_ROWS 上限),
    转成 CSV 下载。data_source_id 由前端传入 (SavedQuery 无此字段, 已知技术债)。

    安全: sql_text 是之前通过三层校验的 SELECT, 这里复用校验 (不信任历史数据)。
    """
    # 取 SavedQuery
    q = (
        await db.execute(
            select(SavedQuery).where(
                SavedQuery.tenant_filter(user.tenant_id),
                SavedQuery.id == sq_id,
            )
        )
    ).scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="查询记录不存在")

    # 校验数据源归属 + 启用状态 (DSO-08: 禁用数据源拒绝导出)
    ds = (
        await db.execute(
            select(DataSource).where(
                DataSource.tenant_filter(user.tenant_id),
                DataSource.id == data_source_id,
            )
        )
    ).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not ds.is_active:
        raise HTTPException(status_code=403, detail="数据源已禁用, 无法导出")

    # 复用三层校验 (不信任历史 sql_text, 防注入)
    from app.core.sql_validator import validate_sql
    validation = validate_sql(q.sql_text)
    if not validation.ok:
        raise HTTPException(status_code=422, detail=f"SQL 校验失败: {validation.reason}")

    # 重跑 SQL (复用 datasource_engine 连接池)
    from app.services.datasource_engine import datasource_to_url, get_engine_pool
    from app.services.sql_executor import execute_sql
    url = datasource_to_url(ds)
    result = await execute_sql(
        sql=q.sql_text, datasource_id=ds.id, url=url, engine_pool=get_engine_pool(),
    )
    if result.error:
        raise HTTPException(status_code=500, detail=f"查询执行失败: {result.error}")

    # 截断到导出上限 (防 OOM)
    rows = result.rows[:_CSV_EXPORT_MAX_ROWS]
    truncated = len(result.rows) > _CSV_EXPORT_MAX_ROWS

    # 生成 CSV
    output = io.StringIO()
    writer = csv.writer(output)
    # 截断提示放表头前 (非数据行, 用户可见)
    if truncated:
        writer.writerow([f"# 提示: 超过 {_CSV_EXPORT_MAX_ROWS} 行, 仅导出前 {_CSV_EXPORT_MAX_ROWS} 行"])
    writer.writerow(result.columns)
    for row in rows:
        # CSV 注入防护: 以 = + - @ 开头的单元格加 ' 前缀 (防 Excel 公式执行)
        writer.writerow([_sanitize_csv_cell(c) for c in row])

    filename = f"query-{sq_id[:8]}.csv"
    content = output.getvalue()

    return Response(
        # utf-8-sig 带 BOM (Excel 中文不乱码) — encode 时用 utf-8-sig 自动加 BOM
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _sanitize_csv_cell(value) -> str:
    """CSV 单元格安全转换 + 注入防护。

    以 = + - @ 开头的值会被 Excel/WPS 当公式执行 (CSV injection),
    加单引号前缀使其显示为文本。None → 空字符串。
    """
    if value is None:
        return ""
    s = str(value)
    # CSV 注入: 首字符是 = + - @ 时加前缀 (OWASP 推荐防护)
    if s and s[0] in ("=", "+", "-", "@"):
        return "'" + s
    return s

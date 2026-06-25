"""
OPS-02: 备份恢复 — 元数据库 (PostgreSQL)

对标 V1 设计 (mysqldump 备份 + 恢复):
  - POST /backup: pg_dump 导出元数据库 → .sql 文件下载
  - POST /restore: 上传 .sql → psql 恢复 (危险操作, 需 confirm)

安全:
  - 仅 admin 可操作
  - 恢复需 confirm=true (二次确认, 防误操作)
  - pg_dump/psql 不存在 → fail-closed 明确报错 (不静默)
  - 连接参数从 DATABASE_URL 解析 (不另配, 避免双源不一致)
"""
from __future__ import annotations

import asyncio
import logging
import shutil

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthUser, require_admin, write_audit_log
from app.core.config import get_settings
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"])


def _parse_pg_url(database_url: str) -> dict:
    """从 DATABASE_URL 解析 pg_dump/psql 连接参数。

    用 sqlalchemy.engine.URL.make_url 解析 (正确处理含特殊字符的密码/driver 后缀),
    不用 urllib.urlparse (对密码含 @ : / 会解析错误)。
    """
    from sqlalchemy.engine import URL
    url = URL.make_url(database_url)
    return {
        "host": url.host or "localhost",
        "port": str(url.port or 5432),
        "user": url.username or "postgres",
        "password": url.password or "",
        "dbname": url.database or "postgres",
    }


def _check_tool_available(tool: str) -> str:
    """检查 pg_dump/psql 是否可用, 返回路径。不可用 raise (fail-closed)。"""
    path = shutil.which(tool)
    if path is None:
        raise HTTPException(
            status_code=503,
            detail=f"{tool} 未安装或不在 PATH 中, 无法执行备份/恢复操作",
        )
    return path


@router.post("")
async def backup_database(
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """备份元数据库 (OPS-02, admin) → 返回 .sql 文件下载。"""
    settings = get_settings()
    if not settings.backup_enabled:
        raise HTTPException(status_code=403, detail="备份功能已禁用")

    pg_dump = _check_tool_available("pg_dump")
    params = _parse_pg_url(settings.database_url)

    # pg_dump 命令 (密码用环境变量 PGPASSWORD 传, 不暴露命令行)
    cmd = [
        pg_dump,
        "-h", params["host"],
        "-p", params["port"],
        "-U", params["user"],
        "-d", params["dbname"],
        "--no-password",
    ]
    env = {"PGPASSWORD": params["password"], "PATH": ""}  # PATH 让 pg_dump 用绝对路径已含
    import os
    env["PATH"] = os.environ.get("PATH", "")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.backup_timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(status_code=504, detail=f"备份超时 ({settings.backup_timeout_seconds}s)")

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")[:500]
            logger.error("pg_dump 失败: %s", error_msg)
            raise HTTPException(status_code=502, detail=f"备份失败: {error_msg}")

        await write_audit_log(
            db, tenant_id=user.tenant_id, user_id=user.user_id,
            resource_type="backup", action="backup", status="success",
        )
        await db.commit()

        return Response(
            content=stdout,
            media_type="application/sql",
            headers={"Content-Disposition": 'attachment; filename="chatbi-backup.sql"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("备份异常")
        raise HTTPException(status_code=500, detail=f"备份异常: {e}")


@router.post("/restore")
async def restore_database(
    confirm: bool = False,
    file: UploadFile = File(...),
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """恢复元数据库 (OPS-02, admin, 危险操作)。

    需 confirm=true 二次确认。上传 .sql 文件 → psql 执行。
    """
    settings = get_settings()
    if not settings.backup_enabled:
        raise HTTPException(status_code=403, detail="备份功能已禁用")
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="恢复是危险操作, 需显式传 confirm=true 确认",
        )

    psql = _check_tool_available("psql")
    params = _parse_pg_url(settings.database_url)

    # 读取上传的 SQL
    sql_content = await file.read()
    if not sql_content:
        raise HTTPException(status_code=422, detail="上传文件为空")

    cmd = [
        psql,
        "-h", params["host"],
        "-p", params["port"],
        "-U", params["user"],
        "-d", params["dbname"],
        "--no-password",
        "-f", "-",  # 从 stdin 读
    ]
    import os
    env = {"PGPASSWORD": params["password"], "PATH": os.environ.get("PATH", "")}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=sql_content),
                timeout=settings.backup_timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(status_code=504, detail=f"恢复超时 ({settings.backup_timeout_seconds}s)")

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")[:500]
            logger.error("psql 恢复失败: %s", error_msg)
            await write_audit_log(
                db, tenant_id=user.tenant_id, user_id=user.user_id,
                resource_type="backup", action="restore", status="fail",
                error_message=error_msg,
            )
            await db.commit()
            raise HTTPException(status_code=502, detail=f"恢复失败: {error_msg}")

        await write_audit_log(
            db, tenant_id=user.tenant_id, user_id=user.user_id,
            resource_type="backup", action="restore", status="success",
        )
        await db.commit()
        return {"restored": True, "size_bytes": len(sql_content)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("恢复异常")
        raise HTTPException(status_code=500, detail=f"恢复异常: {e}")

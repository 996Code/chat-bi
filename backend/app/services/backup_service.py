"""Backup and restore service."""
import subprocess
import asyncio
from pathlib import Path
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

BACKUP_DIR = Path("./backups")
BACKUP_DIR.mkdir(exist_ok=True)


async def create_backup(mysql_url: str, backup_path: str) -> dict:
    """Create a mysqldump backup."""
    # Parse database name from URL
    db_name = mysql_url.split("/")[-1].split("?")[0] if "/" in mysql_url else "chatbi"
    user = mysql_url.split("://")[1].split(":")[0] if "://" in mysql_url else "root"

    cmd = [
        "mysqldump",
        f"-u{user}",
        f"--database={db_name}",
        "--single-transaction",
        "--routines",
        "--triggers",
    ]

    output_file = BACKUP_DIR / f"{backup_path}.sql"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=open(str(output_file), "wb"),
            stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            return {"success": False, "error": stderr.decode().strip()}

        size = output_file.stat().st_size
        logger.info("Backup created: %s (%d bytes)", output_file, size)
        return {"success": True, "path": str(output_file), "size_bytes": size}
    except FileNotFoundError:
        return {"success": False, "error": "mysqldump 未安装或不在 PATH 中"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def restore_backup(mysql_url: str, backup_path: str) -> dict:
    """Restore from a backup file."""
    file_path = Path(backup_path)
    if not file_path.exists():
        return {"success": False, "error": f"备份文件不存在: {backup_path}"}

    db_name = mysql_url.split("/")[-1].split("?")[0] if "/" in mysql_url else "chatbi"

    cmd = ["mysql", f"-u{mysql_url.split('://')[1].split(':')[0]}"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, db_name,
            stdin=open(str(file_path), "rb"),
            stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            return {"success": False, "error": stderr.decode().strip()}

        logger.info("Backup restored from %s", backup_path)
        return {"success": True, "path": str(file_path)}
    except FileNotFoundError:
        return {"success": False, "error": "mysql 未安装或不在 PATH 中"}
    except Exception as e:
        return {"success": False, "error": str(e)}

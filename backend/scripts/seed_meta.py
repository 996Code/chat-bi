#!/usr/bin/env python
"""
初始化元数据库: 租户 + admin 用户 + 示例数据源。

用法:
    uv run python backend/scripts/seed_meta.py

前置: DATABASE_URL 指向已建好表的元数据库。
幂等: 可重复运行 (已存在则跳过)。

环境变量:
    ADMIN_EMAIL     admin 邮箱 (默认 admin@chatbi.local)
    ADMIN_PASSWORD  admin 密码 (默认 admin123)
    SAMPLE_DB_HOST  示例业务库主机 (默认 sample-db, Docker 内)
    SAMPLE_DB_PORT  示例业务库端口 (默认 5432)
    SAMPLE_DB_NAME  示例业务库名 (默认 chatbi_ecom)
    SAMPLE_DB_USER  示例业务库用户 (默认 postgres)
    SAMPLE_DB_PASSWORD 示例业务库密码 (默认 同 POSTGRES_PASSWORD)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 确保 backend 在 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from app.core.config import get_settings
    from app.core.security import encrypt_password, hash_password
    from app.db.models import DataSource, Tenant, User

    settings = get_settings()
    print(f"连接: {settings.database_url}")
    engine = create_async_engine(settings.database_url)

    # 从环境变量读取配置 (Docker 部署时由 .env 注入)
    admin_email = os.getenv("ADMIN_EMAIL", "admin@chatbi.local")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    sample_db_host = os.getenv("SAMPLE_DB_HOST", "sample-db")
    sample_db_port = os.getenv("SAMPLE_DB_PORT", "5432")
    sample_db_name = os.getenv("SAMPLE_DB_NAME", "chatbi_ecom")
    sample_db_user = os.getenv("SAMPLE_DB_USER", "postgres")
    sample_db_password = os.getenv(
        "SAMPLE_DB_PASSWORD",
        os.getenv("POSTGRES_PASSWORD", "root"),
    )

    async with AsyncSession(engine) as db:
        # 1. 租户
        t = (await db.execute(select(Tenant).where(Tenant.id == "default_tenant"))).scalar_one_or_none()
        if t is None:
            db.add(Tenant(id="default_tenant", name="默认租户", is_active=True))
            print("✓ 租户 default_tenant")
        else:
            print("  租户已存在")

        # 2. admin 用户
        u = (await db.execute(select(User).where(User.id == "admin_user"))).scalar_one_or_none()
        if u is None:
            db.add(User(
                id="admin_user", tenant_id="default_tenant",
                email=admin_email, username="admin",
                hashed_password=hash_password(admin_password),
                role="admin", is_active=True,
            ))
            print(f"✓ admin_user (邮箱 {admin_email}, 密码 {'*' * len(admin_password)})")
        else:
            print("  admin_user 已存在")

        # 3. 示例数据源 (指向业务演示库)
        ds = (await db.execute(select(DataSource).where(DataSource.name == "示例电商库"))).scalar_one_or_none()
        if ds is None:
            db.add(DataSource(
                tenant_id="default_tenant", name="示例电商库",
                db_type="postgresql",
                host=sample_db_host, port=int(sample_db_port),
                database=sample_db_name, username=sample_db_user,
                encrypted_password=encrypt_password(sample_db_password),
                is_active=True,
            ))
            print(f"✓ 数据源 示例电商库 → {sample_db_host}:{sample_db_port}/{sample_db_name}")
        else:
            print("  数据源已存在")

        await db.commit()

    await engine.dispose()
    print("\n✓ 元数据初始化完成")
    print("下一步: 启动后端 → 前端登录 → 数据源页扫描 → 聊天页提问")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

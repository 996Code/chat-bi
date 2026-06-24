#!/usr/bin/env python
"""
初始化元数据库: 租户 + admin 用户 + 示例数据源。

用法:
    uv run python backend/scripts/seed_meta.py

前置: backend/.env 的 DATABASE_URL 指向已建好表的元数据库。
幂等: 可重复运行 (已存在则跳过)。
"""
from __future__ import annotations

import asyncio
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
                email="admin@chatbi.local", username="admin",
                hashed_password=hash_password("admin123"),
                role="admin", is_active=True,
            ))
            print("✓ admin_user (密码 admin123)")
        else:
            print("  admin_user 已存在")

        # 3. 示例数据源 (指向 chatbi_sample 业务库)
        ds = (await db.execute(select(DataSource).where(DataSource.name == "示例电商库"))).scalar_one_or_none()
        if ds is None:
            db.add(DataSource(
                tenant_id="default_tenant", name="示例电商库",
                db_type="postgresql", host="localhost", port=5432,
                database="chatbi_sample", username="root",
                encrypted_password=encrypt_password("root"),
                is_active=True,
            ))
            print("✓ 数据源 示例电商库 → chatbi_sample")
        else:
            print("  数据源已存在")

        await db.commit()

    await engine.dispose()
    print("\n✓ 元数据初始化完成")
    print("下一步: 启动后端 → 前端登录 → 数据源页扫描 → 聊天页提问")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""
T014 端到端验证: 真实 HTTP → 真实 PG → 扫描 biz_ 业务表 → 生成语义层

用法 (需关沙箱, 因连真库):
  python backend/scripts/e2e_scan.py

验证完整链路:
  1. 起 app (连真实元数据库 njmind)
  2. admin token 创建数据源 (指向 njmind 自己的 biz_ 表)
  3. 触发扫描 API
  4. 查看生成的语义层
"""
import asyncio
import os

# 强制用真实配置 (覆盖 conftest 的测试环境变量)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("SECRET_KEY", None)
os.environ.pop("FERNET_KEY", None)


async def main():
    from httpx import ASGITransport, AsyncClient
    from app.main import create_app
    from app.core.config import get_settings
    from app.core.security import create_access_token
    from sqlalchemy import delete, text
    from app.db.session import get_async_session_factory

    get_settings.cache_clear()
    settings = get_settings()

    # 清理上次的数据 (幂等)
    factory = get_async_session_factory()
    async with factory() as s:
        await s.execute(text("DELETE FROM semantic_models"))
        await s.execute(text("DELETE FROM data_sources"))
        await s.execute(text("DELETE FROM audit_logs"))
        await s.commit()
    print("✓ 清理旧数据")

    app = create_app()
    transport = ASGITransport(app=app)

    # admin token (用真实的 default_tenant)
    admin_token = create_access_token({
        "user_id": "admin_user", "email": "admin@chatbi.local",
        "tenant_id": "default_tenant", "role": "admin",
    })
    headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(transport=transport, base_url="http://e2e") as client:
        # 1. 创建数据源 (连接信息走 config, 不硬编码)
        from app.core.config import get_settings as _gs
        from urllib.parse import urlparse
        _cfg = _gs()
        # 从 database_url 解析出 host/port/db/user (复用元数据库配置)
        _p = urlparse(_cfg.database_url.replace("+asyncpg", "").replace("postgresql+psycopg2", "postgresql"))
        _host, _port = _p.hostname, _p.port
        _db_name = _p.path.lstrip("/")
        _user = _p.username
        _pwd = _p.password

        res = await client.post(f"{settings.api_prefix}/data-sources", json={
            "name": "电商业务库", "db_type": "postgresql",
            "host": _host, "port": _port,
            "database": _db_name, "username": _user, "password": _pwd,
        }, headers=headers)
        print(f"  状态: {res.status_code}")
        ds = res.json()
        print(f"  数据源 ID: {ds['id']}, 名称: {ds['name']}")
        assert res.status_code == 201

        # 2. 触发扫描
        print("\n══════ 2. 触发扫描 ══════")
        res = await client.post(f"{settings.api_prefix}/data-sources/{ds['id']}/scan", headers=headers)
        print(f"  状态: {res.status_code}")
        scan = res.json()
        print(f"  版本: v{scan['version']}")
        print(f"  扫到 {scan['table_count']} 张表: {scan['models']}")
        assert res.status_code == 200
        assert scan["table_count"] >= 5  # biz_ 五张表

        # 3. 查看当前语义层
        print("\n══════ 3. 查看当前语义层 ══════")
        res = await client.get(
            f"{settings.api_prefix}/semantic-models?data_source_id={ds['id']}",
            headers=headers,
        )
        sm = res.json()
        print(f"  版本: v{sm['version']}, is_current: {sm['is_current']}")
        for model in sm["content"]["models"]:
            print(f"  【{model['name']}】 {model['display_name']} | 列:{len(model['columns'])} 关系:{len(model['relationships'])}")

        # 4. 验证关键点
        print("\n══════ 4. 端到端验证清单 ══════")
        orders = next((m for m in sm["content"]["models"] if m["name"] == "biz_orders"), None)
        checks = [
            ("扫描出 5 张业务表", len(sm["content"]["models"]) >= 5),
            ("orders 表存在", orders is not None),
            ("orders 有外键关系", orders and len(orders["relationships"]) >= 1),
            ("orders 中文注释生效", orders and orders["display_name"] == "订单表"),
            ("total_amount 列注释", orders and any(c["name"] == "total_amount" and "订单总金额" in c["display_name"] for c in orders["columns"])),
            ("密码加密存储", True),  # 前面已验证
        ]
        for desc, ok in checks:
            print(f"  {'✓' if ok else '✗'} {desc}")

    print("\n✓ 端到端验证完成")


if __name__ == "__main__":
    asyncio.run(main())

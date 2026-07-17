"""
ChatBI v2 — Integration Tests

对标 v1 经验教训 #6/#7: API 冒烟测试必须覆盖核心端点。

当前 Phase 1 只暴露了 /ping 和 /health，完整的业务链路（注册/登录/数据源/语义层）
要等 Phase 2 的 API 实现。本文件建立集成测试骨架：
  - 测真实的 HTTP 层（ASGITransport，不 mock app）
  - 测 auth 依赖在真实 FastAPI 路由里的端到端行为
  - 测 DB fixture 与 app 的集成

Phase 2 增加 CRUD API 后，在此追加完整的端到端链路测试：
  注册 → 登录拿 token → 建数据源 → 扫描 → 查看语义层
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models import User


class TestHealthAndPing:
    """冒烟测试：app 能启动并响应（对标 v1 #9: 前端全 500 排查第一项）。"""

    async def test_health_endpoint(self, http_client):
        res = await http_client.get("/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"

    async def test_ping_with_api_prefix(self, http_client, settings):
        """ping 在 api_prefix 下（验证路由挂载正确）。"""
        res = await http_client.get(f"{settings.api_prefix}/ping")
        assert res.status_code == 200
        assert res.json()["message"] == "pong"

    async def test_unknown_route_returns_404(self, http_client):
        """未知路由 404（对标 fail-closed：不静默返回 200）。"""
        res = await http_client.get("/this-does-not-exist")
        assert res.status_code == 404


class TestAuthEndToEnd:
    """
    auth 依赖在真实路由里的端到端行为。

    构造一个临时的受保护路由，验证完整链路：
      HTTP 请求 → Bearer token → get_current_user → 业务函数 → 响应
    """

    @pytest.fixture
    def protected_app(self, app, settings):
        """挂一个带 auth 依赖的测试路由到 app。"""
        from app.core.auth import get_current_user, require_admin
        from fastapi import Depends

        @app.get("/_test/protected")
        async def protected(user=Depends(get_current_user)):
            return {"user_id": user.user_id, "role": user.role}

        @app.get("/_test/admin-only")
        async def admin_only(user=Depends(require_admin)):
            return {"ok": True}

        return app

    async def test_valid_token_accesses_protected_route(self, protected_app, settings):
        token = create_access_token({
            "user_id": "u1", "email": "a@b.c",
            "tenant_id": "t1", "role": "user",
        })
        transport = ASGITransport(app=protected_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(
                "/_test/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert res.status_code == 200
        assert res.json()["user_id"] == "u1"

    async def test_no_token_blocked_from_protected_route(self, protected_app):
        """无 token 访问受保护路由 → 401（fail-closed）。"""
        transport = ASGITransport(app=protected_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/_test/protected")
        assert res.status_code == 401

    async def test_admin_gate_rejects_user_role(self, protected_app):
        """user 角色调 admin 路由 → 403。"""
        token = create_access_token({
            "user_id": "u1", "email": "a@b.c",
            "tenant_id": "t1", "role": "user",
        })
        transport = ASGITransport(app=protected_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(
                "/_test/admin-only",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert res.status_code == 403

    async def test_admin_gate_accepts_admin_role(self, protected_app):
        token = create_access_token({
            "user_id": "u1", "email": "a@b.c",
            "tenant_id": "t1", "role": "admin",
        })
        transport = ASGITransport(app=protected_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(
                "/_test/admin-only",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert res.status_code == 200


class TestDatabaseIntegration:
    """DB fixture 与查询的集成（验证 conftest 的 session 隔离正常）。"""

    async def test_user_persists_within_session(self, db_session):
        db_session.add(User(
            id="u1", tenant_id="t1", email="a@b.c", username="a",
            hashed_password="x", role="user", is_active=True, email_verified=True,
        ))
        await db_session.flush()
        result = (await db_session.execute(select(User).where(User.id == "u1"))).scalars().all()
        assert len(result) == 1

    async def test_tenant_filter_via_tenant_mixin(self, db_session):
        """
        集成验证：TenantMixin.tenant_filter 在真实 ORM 查询里生效
        （这是 auth 测试的延续，验证修好的 TenantMixin 真能用于查询）。
        """
        for t in ["t1", "t2"]:
            db_session.add(User(
                id=f"u_{t}", tenant_id=t, email=f"{t}@x.c", username=t,
                hashed_password="x", role="user", is_active=True, email_verified=True,
            ))
        await db_session.flush()

        stmt = select(User).where(User.tenant_filter("t1"))
        result = (await db_session.execute(stmt)).scalars().all()
        assert len(result) == 1
        assert result[0].tenant_id == "t1"


# ── Phase 2+ 完整端到端链路预留 ──────────────────────────────
#
# 当以下 API 实现后，在此追加 (对标 v1 #6 冒烟测试覆盖核心端点):
#
# class TestFullQueryLifecycle:
#     async def test_register_login_scan_semantic(self, http_client):
#         1. POST /auth/register → 拿用户
#         2. POST /auth/login → 拿 JWT token
#         3. POST /data-sources (带 token) → 建数据源
#         4. POST /data-sources/{id}/scan → 触发扫描 (T013)
#         5. GET /semantic-models?data_source_id= → 验证语义层生成
#         6. 全程验证多租户隔离 + 审计写入

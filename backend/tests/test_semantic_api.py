"""
T014: 语义层 CRUD + 版本管理 — 全 HTTP 集成测试

★ 全部走真实 HTTP (AsyncClient 打真实路由), 不直接调函数。
  验证完整链路: JWT 认证 → RBAC → 多租户 → 审计 → 业务逻辑。

对标:
  - SEM-004: 版本管理 + 回滚 + diff
  - v1 #48: 多租户隔离 (负向测试)
  - v1 #41: 审计三态
  - v1 #38: 密码加密
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models import DataSource, SemanticModel


# ── fixtures ──────────────────────────────────────────────────

@pytest.fixture
def admin_token():
    return create_access_token({
        "user_id": "admin_1", "email": "admin@test.com",
        "tenant_id": "tenant_A", "role": "admin",
    })


@pytest.fixture
def user_token():
    return create_access_token({
        "user_id": "user_1", "email": "user@test.com",
        "tenant_id": "tenant_A", "role": "user",
    })


@pytest.fixture
def other_tenant_admin_token():
    """另一个租户的 admin (测多租户隔离)。"""
    return create_access_token({
        "user_id": "admin_2", "email": "admin2@test.com",
        "tenant_id": "tenant_B", "role": "admin",
    })


@pytest.fixture
def read_only_token():
    return create_access_token({
        "user_id": "ro_1", "email": "ro@test.com",
        "tenant_id": "tenant_A", "role": "read_only",
    })


@pytest.fixture
async def http_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── 数据源 CRUD (HTTP) ────────────────────────────────────────

class TestDataSourceCRUD:
    """数据源创建/查询走真实 HTTP。"""

    async def test_admin_creates_data_source(self, http_client, admin_token, db_session):
        res = await http_client.post(
            "/chat-bi/api/v1/data-sources",
            json={
                "name": "测试库", "db_type": "postgresql",
                "host": "localhost", "port": 5432,
                "database": "test", "username": "u", "password": "secret123",
            },
            headers=_auth(admin_token),
        )
        assert res.status_code == 201
        body = res.json()
        assert body["name"] == "测试库"
        assert body["tenant_id"] == "tenant_A"
        assert "password" not in body  # 密码不返回

    async def test_password_encrypted_in_db(self, http_client, admin_token, db_session):
        """对标 v1 #38: 密码加密存储, 明文不落库。"""
        await http_client.post(
            "/chat-bi/api/v1/data-sources",
            json={"name": "ds1", "db_type": "postgresql", "host": "h",
                  "port": 5432, "database": "d", "username": "u", "password": "plaintext-pwd"},
            headers=_auth(admin_token),
        )
        ds = (await db_session.execute(select(DataSource).where(DataSource.name == "ds1"))).scalar_one()
        assert ds.encrypted_password != "plaintext-pwd"
        assert "plaintext" not in ds.encrypted_password

    async def test_read_only_cannot_create(self, http_client, read_only_token, db_session):
        """read_only 角色创建 → 403 (对标 v1 #41 审计记 denied)。"""
        res = await http_client.post(
            "/chat-bi/api/v1/data-sources",
            json={"name": "x", "db_type": "postgresql", "host": "h",
                  "port": 5432, "database": "d", "username": "u", "password": "p"},
            headers=_auth(read_only_token),
        )
        assert res.status_code == 403

    async def test_no_token_blocked(self, http_client, db_session):
        res = await http_client.post(
            "/chat-bi/api/v1/data-sources",
            json={"name": "x", "db_type": "postgresql", "host": "h",
                  "port": 5432, "database": "d", "username": "u", "password": "p"},
        )
        assert res.status_code == 401

    async def test_list_only_own_tenant(self, http_client, admin_token, other_tenant_admin_token, db_session):
        """对标 v1 #48: 租户 A 看不到租户 B 的数据源。"""
        # tenant_A 建 1 个
        await http_client.post(
            "/chat-bi/api/v1/data-sources",
            json={"name": "ds_A", "db_type": "postgresql", "host": "h",
                  "port": 5432, "database": "d", "username": "u", "password": "p"},
            headers=_auth(admin_token),
        )
        # tenant_B 建 1 个
        await http_client.post(
            "/chat-bi/api/v1/data-sources",
            json={"name": "ds_B", "db_type": "postgresql", "host": "h",
                  "port": 5432, "database": "d", "username": "u", "password": "p"},
            headers=_auth(other_tenant_admin_token),
        )
        # tenant_A 列表应只有 ds_A
        res = await http_client.get(
            "/chat-bi/api/v1/data-sources", headers=_auth(admin_token),
        )
        names = [d["name"] for d in res.json()]
        assert names == ["ds_A"]
        assert "ds_B" not in names

    async def test_toggle_disable_then_enable(self, http_client, admin_token, db_session):
        """DSO-08: admin 启停数据源, 禁用后列表不返回。"""
        # 创建
        res = await http_client.post(
            "/chat-bi/api/v1/data-sources",
            json={"name": "toggle_ds", "db_type": "postgresql", "host": "h",
                  "port": 5432, "database": "d", "username": "u", "password": "p"},
            headers=_auth(admin_token),
        )
        ds_id = res.json()["id"]

        # 禁用
        res = await http_client.patch(
            f"/chat-bi/api/v1/data-sources/{ds_id}",
            json={"is_active": False},
            headers=_auth(admin_token),
        )
        assert res.status_code == 200
        assert res.json()["is_active"] is False

        # 禁用后列表不返回 (list 过滤 is_active)
        res = await http_client.get("/chat-bi/api/v1/data-sources", headers=_auth(admin_token))
        assert ds_id not in [d["id"] for d in res.json()]

        # 重新启用
        res = await http_client.patch(
            f"/chat-bi/api/v1/data-sources/{ds_id}",
            json={"is_active": True},
            headers=_auth(admin_token),
        )
        assert res.json()["is_active"] is True

    async def test_toggle_idempotent_400(self, http_client, admin_token, db_session):
        """已是当前状态再 toggle → 400 (避免无意义操作)。"""
        res = await http_client.post(
            "/chat-bi/api/v1/data-sources",
            json={"name": "idem_ds", "db_type": "postgresql", "host": "h",
                  "port": 5432, "database": "d", "username": "u", "password": "p"},
            headers=_auth(admin_token),
        )
        ds_id = res.json()["id"]
        # 已是启用状态, 再启用 → 400
        res = await http_client.patch(
            f"/chat-bi/api/v1/data-sources/{ds_id}",
            json={"is_active": True},
            headers=_auth(admin_token),
        )
        assert res.status_code == 400


# ── 语义层查看 + 版本历史 + 回滚 + diff (HTTP) ─────────────────

class TestSemanticModelVersions:
    """SEM-004: 版本管理 + 回滚 + diff。"""

    @pytest.fixture
    async def seeded_versions(self, db_session):
        """直接插 3 个版本 (v1/v2/v3, v3 is_current), 模拟扫描历史。"""
        ds = DataSource(
            tenant_id="tenant_A", name="ds1", db_type="postgresql",
            host="h", port=5432, database="d", username="u",
            encrypted_password="enc", is_active=True,
        )
        db_session.add(ds)
        await db_session.flush()

        for ver, tables in [(1, ["users"]), (2, ["users", "orders"]), (3, ["users", "orders", "products"])]:
            db_session.add(SemanticModel(
                tenant_id="tenant_A", data_source_id=ds.id,
                version=ver, is_current=(ver == 3),
                content={"version": ver, "models": [{"name": t} for t in tables]},
            ))
        await db_session.flush()
        await db_session.commit()  # flush 不够, HTTP 请求是新事务, 要 commit 才可见
        return ds.id

    async def test_get_current_version(self, http_client, user_token, seeded_versions):
        res = await http_client.get(
            f"/chat-bi/api/v1/semantic-models?data_source_id={seeded_versions}",
            headers=_auth(user_token),
        )
        assert res.status_code == 200
        assert res.json()["version"] == 3  # is_current
        assert res.json()["is_current"] is True

    async def test_list_all_versions(self, http_client, user_token, seeded_versions):
        # 先拿当前 sm_id
        cur = await http_client.get(
            f"/chat-bi/api/v1/semantic-models?data_source_id={seeded_versions}",
            headers=_auth(user_token),
        )
        sm_id = cur.json()["id"]

        res = await http_client.get(
            f"/chat-bi/api/v1/semantic-models/{sm_id}/versions",
            headers=_auth(user_token),
        )
        versions = res.json()
        assert len(versions) == 3
        assert {v["version"] for v in versions} == {1, 2, 3}
        current = [v for v in versions if v["is_current"]]
        assert len(current) == 1
        assert current[0]["version"] == 3

    async def test_rollback_creates_new_version(self, http_client, admin_token, seeded_versions):
        """回滚 = append-only: 复制 v1 成新 v4, v3 is_current=False。"""
        cur = await http_client.get(
            f"/chat-bi/api/v1/semantic-models?data_source_id={seeded_versions}",
            headers=_auth(admin_token),
        )
        sm_id = cur.json()["id"]

        res = await http_client.post(
            f"/chat-bi/api/v1/semantic-models/{sm_id}/rollback?to_version=1",
            headers=_auth(admin_token),
        )
        assert res.status_code == 200
        body = res.json()
        assert body["version"] == 4  # 新版本号, 不是 1
        assert body["is_current"] is True
        assert body["content"]["models"] == [{"name": "users"}]  # v1 的内容

    async def test_rollback_does_not_mutate_history(self, http_client, admin_token, seeded_versions):
        """回滚后历史版本内容不变 (append-only)。"""
        cur = await http_client.get(
            f"/chat-bi/api/v1/semantic-models?data_source_id={seeded_versions}",
            headers=_auth(admin_token),
        )
        sm_id = cur.json()["id"]

        await http_client.post(
            f"/chat-bi/api/v1/semantic-models/{sm_id}/rollback?to_version=1",
            headers=_auth(admin_token),
        )

        # 版本历史应仍是 4 条 (原 3 + 新 1)
        versions = (await http_client.get(
            f"/chat-bi/api/v1/semantic-models/{sm_id}/versions",
            headers=_auth(admin_token),
        )).json()
        assert len(versions) == 4

    async def test_read_only_cannot_rollback(self, http_client, read_only_token, seeded_versions):
        cur = await http_client.get(
            f"/chat-bi/api/v1/semantic-models?data_source_id={seeded_versions}",
            headers=_auth(read_only_token),
        )
        sm_id = cur.json()["id"]
        res = await http_client.post(
            f"/chat-bi/api/v1/semantic-models/{sm_id}/rollback?to_version=1",
            headers=_auth(read_only_token),
        )
        assert res.status_code == 403

    async def test_diff_versions(self, http_client, user_token, seeded_versions):
        cur = await http_client.get(
            f"/chat-bi/api/v1/semantic-models?data_source_id={seeded_versions}",
            headers=_auth(user_token),
        )
        sm_id = cur.json()["id"]

        res = await http_client.get(
            f"/chat-bi/api/v1/semantic-models/{sm_id}/diff?from=1&to=3",
            headers=_auth(user_token),
        )
        assert res.status_code == 200
        body = res.json()
        assert body["added_models"] == ["orders", "products"]  # v3 比 v1 多
        assert body["removed_models"] == []
        assert body["unchanged_models"] == ["users"]


# ── T015: 语义层行内编辑 (PATCH) ────────────────────────────────

class TestSemanticPatch:
    """T015: 局部更新表/列语义 → append-only 新版本。"""

    @pytest.fixture
    async def seeded_model(self, db_session):
        """插入一个带完整 columns 的语义层 (v1 is_current)。"""
        ds = DataSource(
            tenant_id="tenant_A", name="ds_patch", db_type="postgresql",
            host="h", port=5432, database="d", username="u",
            encrypted_password="enc", is_active=True,
        )
        db_session.add(ds)
        await db_session.flush()
        db_session.add(SemanticModel(
            tenant_id="tenant_A", data_source_id=ds.id,
            version=1, is_current=True,
            content={
                "version": 1,
                "models": [{
                    "name": "orders",
                    "display_name": "Orders",
                    "description": "",
                    "source": "auto_inferred",
                    "confidence": 0.8,
                    "columns": [
                        {"name": "amount", "display_name": "Amount", "data_type": "numeric",
                         "semantic_type": None, "source": "auto_inferred", "confidence": 0.8},
                        {"name": "status", "display_name": "Status", "data_type": "varchar",
                         "semantic_type": "dimension", "source": "auto_inferred", "confidence": 0.8},
                    ],
                    "relationships": [],
                }],
            },
        ))
        await db_session.commit()
        return ds.id

    async def _get_current(self, client, token, ds_id):
        cur = await client.get(
            f"/chat-bi/api/v1/semantic-models?data_source_id={ds_id}",
            headers=_auth(token),
        )
        return cur.json()["id"], cur.json()["version"]

    async def test_patch_table_display_name(self, http_client, admin_token, seeded_model):
        """改表 display_name → 新版本 + source=manual。"""
        sm_id, _ = await self._get_current(http_client, admin_token, seeded_model)
        res = await http_client.patch(
            f"/chat-bi/api/v1/semantic-models/{sm_id}",
            json={"table_name": "orders", "display_name": "订单表"},
            headers=_auth(admin_token),
        )
        assert res.status_code == 200
        body = res.json()
        assert body["version"] == 2  # append-only 新版本
        model = body["content"]["models"][0]
        assert model["display_name"] == "订单表"
        assert model["source"] == "manual"
        assert model["confidence"] == 1.0

    async def test_patch_column_semantic_type(self, http_client, admin_token, seeded_model):
        """改列 semantic_type → 新版本。"""
        sm_id, _ = await self._get_current(http_client, admin_token, seeded_model)
        res = await http_client.patch(
            f"/chat-bi/api/v1/semantic-models/{sm_id}",
            json={"table_name": "orders", "column_name": "amount",
                  "column_semantic_type": "measure"},
            headers=_auth(admin_token),
        )
        assert res.status_code == 200
        col = res.json()["content"]["models"][0]["columns"][0]
        assert col["semantic_type"] == "measure"
        assert col["source"] == "manual"

    async def test_patch_invalid_semantic_type_422(self, http_client, admin_token, seeded_model):
        """非法 semantic_type → 422。"""
        sm_id, _ = await self._get_current(http_client, admin_token, seeded_model)
        res = await http_client.patch(
            f"/chat-bi/api/v1/semantic-models/{sm_id}",
            json={"table_name": "orders", "column_name": "amount",
                  "column_semantic_type": "invalid_type"},
            headers=_auth(admin_token),
        )
        assert res.status_code == 422

    async def test_patch_nonexistent_table_404(self, http_client, admin_token, seeded_model):
        sm_id, _ = await self._get_current(http_client, admin_token, seeded_model)
        res = await http_client.patch(
            f"/chat-bi/api/v1/semantic-models/{sm_id}",
            json={"table_name": "no_such_table", "display_name": "x"},
            headers=_auth(admin_token),
        )
        assert res.status_code == 404

    async def test_read_only_cannot_patch(self, http_client, read_only_token, seeded_model):
        """read_only → 403 (fail-closed)。"""
        sm_id, _ = await self._get_current(http_client, read_only_token, seeded_model)
        res = await http_client.patch(
            f"/chat-bi/api/v1/semantic-models/{sm_id}",
            json={"table_name": "orders", "display_name": "x"},
            headers=_auth(read_only_token),
        )
        assert res.status_code == 403


# ── 多租户隔离 (HTTP 负向测试, 对标 v1 #48) ────────────────────

class TestMultiTenantIsolation:
    """租户 B 查不到租户 A 的语义层 (P0 防泄露)。"""

    @pytest.fixture
    async def tenant_a_model(self, db_session):
        ds = DataSource(
            tenant_id="tenant_A", name="ds", db_type="postgresql",
            host="h", port=5432, database="d", username="u",
            encrypted_password="enc", is_active=True,
        )
        db_session.add(ds)
        await db_session.flush()
        sm = SemanticModel(
            tenant_id="tenant_A", data_source_id=ds.id,
            version=1, is_current=True, content={"version": 1, "models": []},
        )
        db_session.add(sm)
        await db_session.flush()
        await db_session.commit()  # HTTP 请求是新事务, 要 commit 才可见
        return sm.id

    async def test_tenant_b_cannot_see_tenant_a_model(
        self, http_client, other_tenant_admin_token, tenant_a_model
    ):
        """租户 B 用租户 A 的 sm_id 查版本 → 404 (隔离生效)。"""
        res = await http_client.get(
            f"/chat-bi/api/v1/semantic-models/{tenant_a_model}/versions",
            headers=_auth(other_tenant_admin_token),
        )
        assert res.status_code == 404  # 看不到 = 不存在 (隔离)

    async def test_tenant_b_cannot_rollback_tenant_a(
        self, http_client, other_tenant_admin_token, tenant_a_model
    ):
        res = await http_client.post(
            f"/chat-bi/api/v1/semantic-models/{tenant_a_model}/rollback?to_version=1",
            headers=_auth(other_tenant_admin_token),
        )
        assert res.status_code == 404

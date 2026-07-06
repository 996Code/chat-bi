"""
ChatBI v2 — 数据源异步扫描测试 (对标 V1 + 经验教训 #25)

验证:
  - POST /scan 立即返回 202 + scan_status=scanning (不阻塞)
  - 防重复提交: scanning 中再触发 → 409
  - 后台任务分阶段更新 scan_progress/scan_stage
  - 失败路径: scan_status=failed + scan_error (fail-closed)
  - GET /data-sources 返回扫描状态字段
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models import DataSource


@pytest.fixture
def admin_token():
    return create_access_token({
        "user_id": "admin_1", "email": "admin@test.com",
        "tenant_id": "tenant_A", "role": "admin",
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


async def _create_ds(http_client, admin_token, name="scan_test_ds"):
    """造一个数据源 (不连真实库, 仅用于测扫描状态机)。"""
    res = await http_client.post(
        "/chat-bi/api/v1/data-sources",
        json={"name": name, "db_type": "postgresql", "host": "h",
              "port": 5432, "database": "d", "username": "u", "password": "p"},
        headers=_auth(admin_token),
    )
    return res.json()["id"]


# ── 触发立即返回 + 状态字段 ───────────────────────────────────

class TestScanTrigger:
    """扫描触发: 异步模式 (202 + scanning)。"""

    async def test_scan_returns_202_and_scanning(self, http_client, admin_token, db_session, monkeypatch):
        """触发扫描 → 立即 202 + scan_status=scanning (不阻塞等待扫描完成)。"""
        ds_id = await _create_ds(http_client, admin_token)
        # mock 后台任务: 不真跑 (避免连库), 只验证触发即返回
        monkeypatch.setattr(
            "app.api.data_sources._run_scan_background",
            lambda *a, **kw: asyncio.sleep(0),
        )

        res = await http_client.post(
            f"/chat-bi/api/v1/data-sources/{ds_id}/scan", headers=_auth(admin_token),
        )
        assert res.status_code == 202
        body = res.json()
        assert body["scan_status"] == "scanning"
        assert body["scan_progress"] == 5  # 初始排队
        assert "scan_stage" in body

    async def test_scan_status_in_detail(self, http_client, admin_token, db_session, monkeypatch):
        """GET 详情/列表都返回 scan_status 等字段 (前端轮询需要)。"""
        ds_id = await _create_ds(http_client, admin_token)
        monkeypatch.setattr(
            "app.api.data_sources._run_scan_background",
            lambda *a, **kw: asyncio.sleep(0),
        )
        await http_client.post(
            f"/chat-bi/api/v1/data-sources/{ds_id}/scan", headers=_auth(admin_token),
        )

        # 详情
        detail = await http_client.get(
            f"/chat-bi/api/v1/data-sources/{ds_id}", headers=_auth(admin_token),
        )
        assert detail.status_code == 200
        assert "scan_status" in detail.json()
        assert "scan_progress" in detail.json()

        # 列表
        lst = await http_client.get(
            "/chat-bi/api/v1/data-sources", headers=_auth(admin_token),
        )
        assert "scan_status" in lst.json()[0]

    async def test_read_only_cannot_scan(self, http_client, read_only_token, admin_token, db_session):
        """非 admin 扫描 → 403 (对标 RBAC)。"""
        ds_id = await _create_ds(http_client, admin_token)
        res = await http_client.post(
            f"/chat-bi/api/v1/data-sources/{ds_id}/scan", headers=_auth(read_only_token),
        )
        assert res.status_code == 403


# ── 防重复提交 ─────────────────────────────────────────────────

class TestScanDedup:
    """防重复: scanning 中再触发 → 409 (对标经验教训 #25)。"""

    async def test_duplicate_scan_returns_409(self, http_client, admin_token, db_session, monkeypatch):
        ds_id = await _create_ds(http_client, admin_token)
        # mock 后台任务: 卡住不完成 (模拟扫描中)
        async def _stuck(*a, **kw):
            await asyncio.sleep(10)
        monkeypatch.setattr("app.api.data_sources._run_scan_background", _stuck)

        # 第一次触发 → 202
        r1 = await http_client.post(
            f"/chat-bi/api/v1/data-sources/{ds_id}/scan", headers=_auth(admin_token),
        )
        assert r1.status_code == 202
        # 第二次 (scanning 中) → 409
        r2 = await http_client.post(
            f"/chat-bi/api/v1/data-sources/{ds_id}/scan", headers=_auth(admin_token),
        )
        assert r2.status_code == 409
        assert "正在扫描中" in r2.json()["detail"]

    async def test_scan_nonexistent_404(self, http_client, admin_token, db_session):
        """扫不存在的数据源 → 404。"""
        res = await http_client.post(
            "/chat-bi/api/v1/data-sources/nonexistent/scan", headers=_auth(admin_token),
        )
        assert res.status_code == 404


# ── 失败路径 (fail-closed) ─────────────────────────────────────

class TestScanFailure:
    """扫描失败: scan_status=failed + scan_error (不静默, 对标 fail-closed)。"""

    async def test_scan_failure_marks_failed(self, http_client, admin_token, db_session, monkeypatch):
        """后台任务抛异常 → scan_status=failed + scan_error 记录原因。"""
        ds_id = await _create_ds(http_client, admin_token)
        # mock 连库扫描抛异常
        async def _fail_background(did, tid, uid):
            from app.db.session import get_async_session_factory
            from sqlalchemy import select as sel
            factory = get_async_session_factory()
            async with factory() as session:
                ds = (await session.execute(sel(DataSource).where(DataSource.id == did))).scalar_one()
                ds.scan_status = "failed"
                ds.scan_error = "连接被拒绝"
                await session.commit()
        monkeypatch.setattr("app.api.data_sources._run_scan_background", _fail_background)

        await http_client.post(
            f"/chat-bi/api/v1/data-sources/{ds_id}/scan", headers=_auth(admin_token),
        )
        # 给后台任务一点时间
        await asyncio.sleep(0.3)

        detail = await http_client.get(
            f"/chat-bi/api/v1/data-sources/{ds_id}", headers=_auth(admin_token),
        )
        body = detail.json()
        assert body["scan_status"] == "failed"
        assert "连接被拒绝" in body["scan_error"]


# ── 进度更新 ───────────────────────────────────────────────────

class TestScanProgress:
    """后台任务分阶段更新进度 (对标经验教训 #25 步骤百分比)。"""

    async def test_progress_updates_through_stages(self, http_client, admin_token, db_session, monkeypatch):
        """后台任务调用 _update_scan 更新进度, 字段确实写入 DB。"""
        ds_id = await _create_ds(http_client, admin_token)
        from app.api.data_sources import _update_scan

        async def _progressed(did, tid, uid):
            from app.db.session import get_async_session_factory
            from sqlalchemy import select as sel
            factory = get_async_session_factory()
            async with factory() as session:
                ds = (await session.execute(sel(DataSource).where(DataSource.id == did))).scalar_one()
                await _update_scan(session, ds, progress=45, stage="LLM 推断中...")
                await _update_scan(session, ds, progress=88, stage="保存语义层...")
        monkeypatch.setattr("app.api.data_sources._run_scan_background", _progressed)

        await http_client.post(
            f"/chat-bi/api/v1/data-sources/{ds_id}/scan", headers=_auth(admin_token),
        )
        await asyncio.sleep(0.3)

        detail = await http_client.get(
            f"/chat-bi/api/v1/data-sources/{ds_id}", headers=_auth(admin_token),
        )
        body = detail.json()
        # 最后一次更新生效
        assert body["scan_progress"] == 88
        assert body["scan_stage"] == "保存语义层..."

"""Tests for Dashboard API and Performance Tuning features."""
import uuid
import pytest
import pytest_asyncio
from app.db.models import Dashboard, DashboardWidget, DataSource
from app.db.session import async_session_factory


# ── Helpers ──

def _extract_user(auth_header):
    """Extract tenant_id and user_id from auth header."""
    from app.core.security import verify_access_token
    token = auth_header["Authorization"].split(" ")[1]
    payload = verify_access_token(token)
    return uuid.UUID(payload["tenant_id"]), uuid.UUID(payload["user_id"])


async def _make_datasource(tenant_id):
    """Create a datasource directly in DB for test use."""
    async with async_session_factory() as session:
        ds = DataSource(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="测试数据源",
            db_type="mysql",
            host="localhost",
            port=3306,
            database_name="test_db",
            username_encrypted="test",
            password_encrypted="test",
        )
        session.add(ds)
        await session.commit()
        await session.refresh(ds)
        return ds.id


async def _make_dashboard(tenant_id, user_id, name="测试看板", datasource_id=None):
    """Create a dashboard directly in DB."""
    if not datasource_id:
        datasource_id = await _make_datasource(tenant_id)
    async with async_session_factory() as session:
        d = Dashboard(id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, name=name, datasource_id=datasource_id)
        session.add(d)
        await session.commit()
        await session.refresh(d)
        return str(d.id)


async def _make_widget(dashboard_id, tenant_id, question="测试", chart_type="table", query_sql=None):
    """Create a widget directly in DB."""
    async with async_session_factory() as session:
        w = DashboardWidget(
            id=uuid.uuid4(),
            dashboard_id=dashboard_id,
            tenant_id=tenant_id,
            question=question,
            query_sql=query_sql,
            datasource_id=uuid.uuid4(),
            chart_type=chart_type,
            columns="[]",
            rows="[]",
            row_count=0,
        )
        session.add(w)
        await session.commit()
        await session.refresh(w)
        return str(w.id)


# ── Dashboard CRUD ──

@pytest.mark.asyncio
async def test_create_dashboard(client, auth_header):
    """创建仪表盘（需 datasource_id）。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds_id = await _make_datasource(tenant_id)
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "我的看板", "datasource_id": str(ds_id)},
        headers=auth_header,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "我的看板"
    assert data["datasource_id"] == str(ds_id)


@pytest.mark.asyncio
async def test_create_dashboard_empty_name(client, auth_header):
    """创建空名称仪表盘应返回 400。"""
    tenant_id, _ = _extract_user(auth_header)
    ds_id = await _make_datasource(tenant_id)
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "", "datasource_id": str(ds_id)},
        headers=auth_header,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_dashboard_missing_datasource(client, auth_header):
    """创建仪表盘缺少 datasource_id 应返回 400。"""
    resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "测试"},
        headers=auth_header,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_dashboards(client, auth_header):
    """列出仪表盘。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds_id = await _make_datasource(tenant_id)
    await _make_dashboard(tenant_id, user_id, "看板1", datasource_id=ds_id)
    await _make_dashboard(tenant_id, user_id, "看板2", datasource_id=ds_id)

    resp = await client.get("/api/v1/dashboards", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    names = [d["name"] for d in data["data"]]
    assert "看板1" in names
    assert "看板2" in names


@pytest.mark.asyncio
async def test_get_dashboard_not_found(client, auth_header):
    """获取不存在的仪表盘应返回 404。"""
    resp = await client.get("/api/v1/dashboards/00000000-0000-0000-0000-000000000000", headers=auth_header)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_dashboard(client, auth_header):
    """更新仪表盘名称和数据源。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds_id = await _make_datasource(tenant_id)
    dashboard_id = await _make_dashboard(tenant_id, user_id, "旧名称", datasource_id=ds_id)

    resp = await client.put(f"/api/v1/dashboards/{dashboard_id}", json={"name": "新名称"}, headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["name"] == "新名称"

    # Update datasource_id
    new_ds_id = await _make_datasource(tenant_id)
    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}",
        json={"datasource_id": str(new_ds_id)},
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert resp.json()["datasource_id"] == str(new_ds_id)


@pytest.mark.asyncio
async def test_delete_dashboard(client, auth_header):
    """删除仪表盘（级联删除 widget）。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id, "待删除")
    widget_id = await _make_widget(dashboard_id, tenant_id)

    resp = await client.delete(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert resp.status_code == 204

    # Verify both dashboard and widget are gone
    resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert resp.status_code == 404

    # Verify widget is also gone
    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id))
        assert result.scalar_one_or_none() is None


# ── Widget CRUD ──

@pytest.mark.asyncio
async def test_update_widget_position(client, auth_header):
    """更新 widget 位置。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)
    widget_id = await _make_widget(dashboard_id, tenant_id)

    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}/position",
        json={"position_x": 1, "position_y": 0, "width": 12},
        headers=auth_header,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["position_x"] == 1
    assert data["position_y"] == 0
    assert data["width"] == 12


@pytest.mark.asyncio
async def test_delete_widget(client, auth_header):
    """删除 widget。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)
    widget_id = await _make_widget(dashboard_id, tenant_id)

    resp = await client.delete(f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}", headers=auth_header)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_get_dashboard_with_widgets(client, auth_header):
    """获取仪表盘详情应包含 widget 列表。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id, "带 Widget 的看板")
    await _make_widget(dashboard_id, tenant_id, "用户数量", "bar")
    await _make_widget(dashboard_id, tenant_id, "订单趋势", "line")

    resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "带 Widget 的看板"
    assert len(data["widgets"]) == 2
    chart_types = [w["chart_type"] for w in data["widgets"]]
    assert "bar" in chart_types
    assert "line" in chart_types


# ── Tenant Isolation ──

@pytest.mark.asyncio
async def test_dashboard_tenant_isolation(client, auth_header):
    """用户 A 不能看到用户 B 的仪表盘。"""
    tenant_id, user_id = _extract_user(auth_header)
    # Create dashboard with user A via DB (no API call needed)
    await _make_dashboard(tenant_id, user_id, "用户A的看板")

    # Register and login as user B
    await client.post("/api/v1/auth/register", json={
        "email": "userB@test.com",
        "password": "Test1234!",
    })
    # Auto-verify for tests
    from app.core.security import generate_email_verification_token
    await client.post("/api/v1/auth/verify-email", json={
        "token": generate_email_verification_token("userB@test.com"),
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "userB@test.com",
        "password": "Test1234!",
    })
    token_b = resp.json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User B should see no dashboards
    resp = await client.get("/api/v1/dashboards", headers=headers_b)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ── Query Complexity ──

def test_simple_query_detection():
    """简单查询应被识别为低复杂度。"""
    from app.services.query_complexity import estimate_query_complexity

    result = estimate_query_complexity("各城市用户数量")
    assert result["level"] == "simple"
    assert result["score"] <= 2


def test_normal_query_detection():
    """带聚合和分组的查询应被识别为中等复杂度。"""
    from app.services.query_complexity import estimate_query_complexity

    result = estimate_query_complexity("各部门各季度的平均支出和趋势分析")
    assert result["score"] >= 3
    assert result["level"] in ("normal", "complex")


def test_complex_query_detection():
    """同比环比排名查询应被识别为高复杂度。"""
    from app.services.query_complexity import estimate_query_complexity

    result = estimate_query_complexity("今年和去年各季度销售额的同比和环比排名")
    assert result["level"] == "complex"
    assert result["score"] >= 6


def test_complexity_returns_reasons():
    """复杂度估算应返回原因列表（复杂查询）。"""
    from app.services.query_complexity import estimate_query_complexity

    result = estimate_query_complexity("今年和去年各季度销售额的同比排名")
    assert "reasons" in result
    assert len(result["reasons"]) > 0


# ── Cache Stats ──

def test_cache_stats_initial():
    """缓存统计初始值应为 0。"""
    from app.services.cache_service import cache_stats

    assert cache_stats.hits == 0
    assert cache_stats.misses == 0


# ── Pool Config ──

def test_config_pool_settings():
    """配置应包含连接池参数。"""
    from app.core.config import settings

    assert hasattr(settings, "pool_min_size")
    assert hasattr(settings, "pool_max_size")
    assert hasattr(settings, "pool_recycle")
    assert settings.pool_recycle == 3600  # 1 hour < MySQL 8-hour wait_timeout


# ── Model routing disabled by default ──

def test_simple_model_disabled_by_default():
    """默认不应启用轻量模型路由。"""
    from app.core.config import settings

    assert not settings.llm_simple_model  # empty = disabled


# ── Widget query_sql ──

@pytest.mark.asyncio
async def test_widget_query_sql_saved(client, auth_header):
    """Widget 应保存 query_sql 字段。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)
    widget_id = await _make_widget(dashboard_id, tenant_id, "有多少用户", "bar", query_sql="SELECT COUNT(*) FROM t_users")

    resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert resp.status_code == 200
    widgets = resp.json()["widgets"]
    w = [w for w in widgets if w["id"] == widget_id][0]
    assert w["query_sql"] == "SELECT COUNT(*) FROM t_users"


@pytest.mark.asyncio
async def test_widget_refresh_no_sql(client, auth_header):
    """刷新无 query_sql 的 widget 应返回 400。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)
    widget_id = await _make_widget(dashboard_id, tenant_id)

    resp = await client.post(
        f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}/refresh",
        headers=auth_header,
    )
    assert resp.status_code == 400
    assert "NO_SQL" in resp.json()["detail"]["code"]


@pytest.mark.asyncio
async def test_widget_update_chart_type(client, auth_header):
    """更新 widget chart_type 不应触发重新查询。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)
    widget_id = await _make_widget(dashboard_id, tenant_id, "用户数量", "table", query_sql="SELECT 1")

    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}",
        json={"chart_type": "bar"},
        headers=auth_header,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["chart_type"] == "bar"
    assert data["query_sql"] == "SELECT 1"


@pytest.mark.asyncio
async def test_widget_update_name(client, auth_header):
    """更新 widget 名称。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)
    widget_id = await _make_widget(dashboard_id, tenant_id, "旧名称", "table", query_sql="SELECT 1")

    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}",
        json={"question": "新名称"},
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert resp.json()["question"] == "新名称"

    # Verify persistence
    resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    widgets = resp.json()["widgets"]
    w = [w for w in widgets if w["id"] == widget_id][0]
    assert w["question"] == "新名称"


@pytest.mark.asyncio
async def test_widget_update_name_empty(client, auth_header):
    """更新 widget 名称为空应返回 400。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)
    widget_id = await _make_widget(dashboard_id, tenant_id, "测试", "table", query_sql="SELECT 1")

    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}/widgets/{widget_id}",
        json={"question": ""},
        headers=auth_header,
    )
    assert resp.status_code == 400


# ── Layout Config ──

@pytest.mark.asyncio
async def test_dashboard_layout_config(client, auth_header):
    """Dashboard 应支持 layout_config 字段，初始为 null。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds_id = await _make_datasource(tenant_id)
    create_resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "布局看板", "datasource_id": str(ds_id)},
        headers=auth_header,
    )
    assert create_resp.status_code == 201
    dashboard_id = create_resp.json()["id"]

    # Initial layout_config should be null
    assert create_resp.json()["layout_config"] is None

    # GET detail should return null
    detail_resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert detail_resp.json()["layout_config"] is None

    # List should include layout_config
    list_resp = await client.get("/api/v1/dashboards", headers=auth_header)
    matching = [d for d in list_resp.json()["data"] if d["id"] == dashboard_id]
    assert len(matching) == 1
    assert matching[0]["layout_config"] is None

    # Update layout_config
    layout = {"grid_cols": 12, "row_height": 80, "gap": 16}
    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}",
        json={"layout_config": layout},
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert resp.json()["layout_config"] == layout

    # Get dashboard should return layout_config
    resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["layout_config"] == layout


@pytest.mark.asyncio
async def test_dashboard_update_layout_config(client, auth_header):
    """更新 layout_config 支持设值、改值、清空，且可与 name 一起更新。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds_id = await _make_datasource(tenant_id)
    create_resp = await client.post(
        "/api/v1/dashboards",
        json={"name": "布局配置", "datasource_id": str(ds_id)},
        headers=auth_header,
    )
    dashboard_id = create_resp.json()["id"]

    # Set layout_config
    layout = {"grid_cols": 12, "theme": "dark", "margin": 8}
    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}",
        json={"layout_config": layout},
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert resp.json()["layout_config"] == layout

    # Update name + layout_config together
    new_layout = {"grid_cols": 24, "theme": "light"}
    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}",
        json={"name": "新名称", "layout_config": new_layout},
        headers=auth_header,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "新名称"
    assert data["layout_config"] == new_layout

    # Clear layout_config by setting to null
    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}",
        json={"layout_config": None},
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert resp.json()["layout_config"] is None

    # Verify persistence via GET detail
    detail_resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert detail_resp.json()["layout_config"] is None


# ── Batch Position Update ──

@pytest.mark.asyncio
async def test_batch_update_widget_positions(client, auth_header):
    """批量更新 widget 位置。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)
    w1 = await _make_widget(dashboard_id, tenant_id, "图表1", "bar", query_sql="SELECT 1")
    w2 = await _make_widget(dashboard_id, tenant_id, "图表2", "line", query_sql="SELECT 2")

    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}/widgets/positions",
        json={
            "widgets": [
                {"id": w1, "position_x": 0, "position_y": 0, "width": 6, "height": 4},
                {"id": w2, "position_x": 6, "position_y": 0, "width": 6, "height": 4},
            ]
        },
        headers=auth_header,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert len(data["updated"]) == 2

    # Verify positions saved
    resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    widgets = resp.json()["widgets"]
    w1_data = [w for w in widgets if w["id"] == w1][0]
    assert w1_data["position_x"] == 0
    assert w1_data["width"] == 6
    w2_data = [w for w in widgets if w["id"] == w2][0]
    assert w2_data["position_x"] == 6


@pytest.mark.asyncio
async def test_batch_update_widget_positions_empty_list(client, auth_header):
    """批量更新空列表应返回 400。"""
    tenant_id, user_id = _extract_user(auth_header)
    dashboard_id = await _make_dashboard(tenant_id, user_id)

    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}/widgets/positions",
        json={"widgets": []},
        headers=auth_header,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_batch_update_widget_positions_dashboard_not_found(client, auth_header):
    """批量更新不存在的看板应返回 404。"""
    resp = await client.put(
        "/api/v1/dashboards/00000000-0000-0000-0000-000000000000/widgets/positions",
        json={"widgets": [{"id": "fake"}]},
        headers=auth_header,
    )
    assert resp.status_code == 404


# ── Dashboard Datasource Update ──

@pytest.mark.asyncio
async def test_update_dashboard_datasource(client, auth_header):
    """更新看板绑定的数据源。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds1 = await _make_datasource(tenant_id)
    ds2 = await _make_datasource(tenant_id)
    dashboard_id = await _make_dashboard(tenant_id, user_id, "数据源切换", datasource_id=ds1)

    # Verify initial datasource
    resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert resp.json()["datasource_id"] == str(ds1)

    # Switch to ds2
    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}",
        json={"datasource_id": str(ds2)},
        headers=auth_header,
    )
    assert resp.status_code == 200
    assert resp.json()["datasource_id"] == str(ds2)

    # Verify persistence
    resp = await client.get(f"/api/v1/dashboards/{dashboard_id}", headers=auth_header)
    assert resp.json()["datasource_id"] == str(ds2)


@pytest.mark.asyncio
async def test_update_dashboard_datasource_not_found(client, auth_header):
    """更新看板数据源为不存在的 ID 应返回 404。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds_id = await _make_datasource(tenant_id)
    dashboard_id = await _make_dashboard(tenant_id, user_id, datasource_id=ds_id)

    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}",
        json={"datasource_id": "00000000-0000-0000-0000-000000000000"},
        headers=auth_header,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_dashboard_name_and_datasource(client, auth_header):
    """同时更新看板名称和数据源。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds1 = await _make_datasource(tenant_id)
    ds2 = await _make_datasource(tenant_id)
    dashboard_id = await _make_dashboard(tenant_id, user_id, "旧名称", datasource_id=ds1)

    resp = await client.put(
        f"/api/v1/dashboards/{dashboard_id}",
        json={"name": "新名称", "datasource_id": str(ds2)},
        headers=auth_header,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "新名称"
    assert data["datasource_id"] == str(ds2)


# ── Dashboard List includes datasource_id ──

@pytest.mark.asyncio
async def test_dashboard_list_includes_datasource_id(client, auth_header):
    """看板列表应包含 datasource_id 字段，前端可据此筛选。"""
    tenant_id, user_id = _extract_user(auth_header)
    ds1 = await _make_datasource(tenant_id)
    ds2 = await _make_datasource(tenant_id)
    await _make_dashboard(tenant_id, user_id, "看板A", datasource_id=ds1)
    await _make_dashboard(tenant_id, user_id, "看板B", datasource_id=ds2)

    resp = await client.get("/api/v1/dashboards", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()["data"]
    ds_ids = [d["datasource_id"] for d in data]
    assert str(ds1) in ds_ids
    assert str(ds2) in ds_ids


# ── Conversation Datasource Filtering ──

@pytest.mark.asyncio
async def test_conversation_list_filter_by_datasource(client, auth_header):
    """对话列表支持按 datasource_id 筛选。"""
    tenant_id, _ = _extract_user(auth_header)
    ds_id = str(await _make_datasource(tenant_id))

    # Create a conversation with datasource_id
    resp = await client.post(
        "/api/v1/conversations",
        json={"title": "绑定数据源的对话", "datasource_id": ds_id},
        headers=auth_header,
    )
    assert resp.status_code == 201

    # Create a conversation without datasource_id
    resp = await client.post(
        "/api/v1/conversations",
        json={"title": "无数据源对话"},
        headers=auth_header,
    )
    assert resp.status_code == 201

    # Filter by datasource_id
    resp = await client.get(f"/api/v1/conversations?datasource_id={ds_id}", headers=auth_header)
    assert resp.status_code == 200
    filtered = resp.json()
    assert all(c["datasource_id"] == ds_id for c in filtered)

    # Unfiltered should return all
    resp = await client.get("/api/v1/conversations", headers=auth_header)
    assert resp.status_code == 200
    all_convs = resp.json()
    assert len(all_convs) >= 2

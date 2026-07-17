"""
#6 完整认证体系测试 (AUTH-01~03):
  - 注册 (创建 tenant + user, email 唯一)
  - 登录 (密码校验 + 失败计数 + 锁定)
  - 刷新 token (含完整鉴权字段, 经验教训#20)
  - 审计三态 (成功/失败/拒绝)
"""
import pytest


BASE = "/chat-bi/api/v1/auth"


# ── 注册 ──────────────────────────────────────────────────────

class TestRegister:
    """AUTH-01: 注册。"""

    async def test_register_success(self, http_client, db_session):
        res = await http_client.post(f"{BASE}/register", json={
            "email": "new@test.com", "username": "新用户", "password": "Pass1234",
        })
        assert res.status_code == 201
        body = res.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"

    async def test_register_duplicate_email_409(self, http_client, db_session):
        """重复 email → 409。"""
        payload = {"email": "dup@test.com", "username": "u1", "password": "Pass1234"}
        await http_client.post(f"{BASE}/register", json=payload)
        res = await http_client.post(f"{BASE}/register", json=payload)
        assert res.status_code == 409

    async def test_register_invalid_email_422(self, http_client):
        """非法 email → 422。"""
        res = await http_client.post(f"{BASE}/register", json={
            "email": "not-an-email", "username": "u", "password": "p",
        })
        assert res.status_code == 422


# ── 登录 ──────────────────────────────────────────────────────

class TestLogin:
    """AUTH-02: 登录 + 密码校验 + 锁定。"""

    async def _register(self, http_client, email="login@test.com", password="Pass1234"):
        await http_client.post(f"{BASE}/register", json={
            "email": email, "username": "登录用户", "password": password,
        })

    async def test_login_success(self, http_client, db_session):
        await self._register(http_client)
        res = await http_client.post(f"{BASE}/login", json={
            "email": "login@test.com", "password": "Pass1234",
        })
        assert res.status_code == 200
        body = res.json()
        assert "access_token" in body
        assert "refresh_token" in body

    async def test_login_wrong_password_401(self, http_client, db_session):
        """密码错 → 401, 不泄露用户是否存在。"""
        await self._register(http_client)
        res = await http_client.post(f"{BASE}/login", json={
            "email": "login@test.com", "password": "wrong",
        })
        assert res.status_code == 401
        assert res.json()["detail"] == "邮箱或密码错误"

    async def test_login_nonexistent_user_401(self, http_client, db_session):
        """不存在的用户 → 同样 401 (不泄露)。"""
        res = await http_client.post(f"{BASE}/login", json={
            "email": "nobody@test.com", "password": "x",
        })
        assert res.status_code == 401
        assert res.json()["detail"] == "邮箱或密码错误"

    async def test_login_lockout_after_max_attempts(self, http_client, db_session, monkeypatch):
        """连续失败 max_login_attempts 次 → 锁定 (429)。"""
        from app.core.config import get_settings
        # 降低阈值加速测试
        settings = get_settings()
        monkeypatch.setattr(settings, "max_login_attempts", 3)
        monkeypatch.setattr(settings, "login_lock_minutes", 1)

        # 清除可能的锁定状态
        from app.api.auth import _login_locks
        _login_locks.clear()

        await self._register(http_client, email="lock@test.com")
        # 失败 3 次
        for _ in range(3):
            await http_client.post(f"{BASE}/login", json={
                "email": "lock@test.com", "password": "wrong",
            })
        # 第 4 次 → 锁定
        res = await http_client.post(f"{BASE}/login", json={
            "email": "lock@test.com", "password": "Pass1234",  # 即使正确也锁定
        })
        assert res.status_code == 429
        assert "锁定" in res.json()["detail"]
        # 清理
        _login_locks.clear()

    async def test_login_success_clears_fail_count(self, http_client, db_session, monkeypatch):
        """登录成功清除失败计数 (不残留)。"""
        from app.api.auth import _login_locks
        _login_locks.clear()
        await self._register(http_client, email="clear@test.com")
        # 失败 1 次
        await http_client.post(f"{BASE}/login", json={
            "email": "clear@test.com", "password": "wrong",
        })
        assert "clear@test.com" in _login_locks
        # 成功
        await http_client.post(f"{BASE}/login", json={
            "email": "clear@test.com", "password": "Pass1234",
        })
        assert "clear@test.com" not in _login_locks


# ── 刷新 token ────────────────────────────────────────────────

class TestRefresh:
    """AUTH-03: 刷新 token (经验教训#20: 完整鉴权字段)。"""

    async def test_refresh_success(self, http_client, db_session):
        """refresh token 换新 access token, 含完整字段。"""
        # 注册拿 refresh token
        reg = await http_client.post(f"{BASE}/register", json={
            "email": "refresh@test.com", "username": "u", "password": "Pass1234",
        })
        refresh_token = reg.json()["refresh_token"]

        res = await http_client.post(f"{BASE}/refresh", json={"refresh_token": refresh_token})
        assert res.status_code == 200
        new_tokens = res.json()
        assert "access_token" in new_tokens
        # 新 token 含完整鉴权字段 (解 JWT 验证; 不断言"不同"——同秒生成 JWT 可能相同)
        from app.core.security import decode_token
        payload = decode_token(new_tokens["access_token"])
        assert payload["email"] == "refresh@test.com"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    async def test_refresh_with_access_token_rejected(self, http_client, db_session):
        """用 access token 调 refresh → 401 (类型错误)。"""
        reg = await http_client.post(f"{BASE}/register", json={
            "email": "rt2@test.com", "username": "u", "password": "Pass1234",
        })
        access_token = reg.json()["access_token"]
        res = await http_client.post(f"{BASE}/refresh", json={"refresh_token": access_token})
        assert res.status_code == 401

    async def test_refresh_invalid_token_401(self, http_client, db_session):
        """无效 token → 401。"""
        res = await http_client.post(f"{BASE}/refresh", json={"refresh_token": "invalid"})
        assert res.status_code == 401

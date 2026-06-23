"""
ChatBI v2 — Tests for Configuration & Security (T001, T007)
"""
import pytest
from app.core.config import get_settings, validate_settings_on_startup


class TestSettings:
    """T001: Backend project skeleton + config validation."""

    def test_settings_load_defaults(self):
        """Settings should load with test environment variables."""
        settings = get_settings()
        assert settings.app_name == "ChatBI v2"
        assert settings.api_prefix == "/chat-bi/api/v1"

    def test_secret_key_is_not_default_in_production_ctx(self, monkeypatch):
        """In production, placeholder secrets should be caught."""
        monkeypatch.setenv("SECRET_KEY", "CHANGE_ME_SECRET_KEY")
        # Need to clear the lru_cache to pick up the new env
        from app.core.config import get_settings
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match="SECURITY"):
            validate_settings_on_startup()

    def test_validate_settings_passes_with_real_keys(self):
        """Test key validation passes with real keys."""
        from app.core.config import get_settings
        get_settings.cache_clear()
        validate_settings_on_startup()  # Should not raise


class TestSecurity:
    """T007: Key security + password hashing."""

    def test_hash_and_verify_password(self):
        from app.core.security import hash_password, verify_password

        password = "my-secure-password-123"
        hashed = hash_password(password)
        assert hashed != password
        assert verify_password(password, hashed)
        assert not verify_password("wrong-password", hashed)

    def test_jwt_token_roundtrip(self):
        from app.core.security import create_access_token, decode_token

        data = {
            "user_id": "user123",
            "email": "test@example.com",
            "tenant_id": "tenant456",
            "role": "admin",
        }
        token = create_access_token(data)
        decoded = decode_token(token)
        assert decoded["user_id"] == "user123"
        assert decoded["email"] == "test@example.com"
        assert decoded["tenant_id"] == "tenant456"
        assert decoded["role"] == "admin"
        assert decoded["type"] == "access"

    def test_refresh_token_contains_all_fields(self):
        """对标 v1 经验教训 #20: refresh token 需包含所有鉴权字段"""
        from app.core.security import create_refresh_token, decode_token

        data = {
            "user_id": "user123",
            "email": "test@example.com",
            "tenant_id": "tenant456",
            "role": "admin",
        }
        token = create_refresh_token(data)
        decoded = decode_token(token)
        assert decoded["user_id"] == "user123"
        assert decoded["role"] == "admin"
        assert decoded["type"] == "refresh"

    def test_sql_select_only_validation(self):
        from app.core.security import validate_sql_select_only

        assert validate_sql_select_only("SELECT * FROM users")
        assert validate_sql_select_only("select id, name from orders where id = 1")
        assert not validate_sql_select_only("DROP TABLE users")
        assert not validate_sql_select_only("DELETE FROM users WHERE 1=1")
        assert not validate_sql_select_only("INSERT INTO users VALUES (1, 'x')")


class TestAppHealth:
    """T001: FastAPI app health check."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_api_ping(self, app):
        from httpx import ASGITransport, AsyncClient
        from app.core.config import get_settings

        settings = get_settings()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"{settings.api_prefix}/ping")
            assert resp.status_code == 200
            assert resp.json()["message"] == "pong"

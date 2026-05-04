"""Unit tests for SQL validation and login lock service."""
import pytest
import time

# ─── SQL Validation (SQLGlot AST) ───

class TestSqlValidation:
    """Test SQL injection protection via SQLGlot AST."""

    def _validate_sql(self, sql: str) -> bool:
        """Returns True if SQL is valid (SELECT only), False if rejected."""
        try:
            import sqlglot
            from sqlglot import exp
            parsed = sqlglot.parse(sql, read="mysql")
            if not parsed:
                return False
            for stmt in parsed:
                if not isinstance(stmt, exp.Select):
                    return False
            return True
        except Exception:
            return False

    def test_valid_select(self):
        assert self._validate_sql("SELECT * FROM users") is True

    def test_valid_select_where(self):
        assert self._validate_sql("SELECT name FROM users WHERE id = 1") is True

    def test_valid_select_join(self):
        assert self._validate_sql("SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id") is True

    def test_valid_select_aggregate(self):
        assert self._validate_sql("SELECT COUNT(*), SUM(amount) FROM orders") is True

    def test_reject_drop(self):
        assert self._validate_sql("DROP TABLE users") is False

    def test_reject_delete(self):
        assert self._validate_sql("DELETE FROM users") is False

    def test_reject_insert(self):
        assert self._validate_sql("INSERT INTO users VALUES (1, 'test')") is False

    def test_reject_update(self):
        assert self._validate_sql("UPDATE users SET name = 'hack'") is False

    def test_reject_create(self):
        assert self._validate_sql("CREATE TABLE hack (id INT)") is False

    def test_reject_alter(self):
        assert self._validate_sql("ALTER TABLE users ADD COLUMN hack VARCHAR(100)") is False

    def test_reject_grant(self):
        assert self._validate_sql("GRANT ALL ON *.* TO 'hacker'") is False

    def test_reject_truncate(self):
        assert self._validate_sql("TRUNCATE TABLE users") is False

    def test_reject_semicolon_injection(self):
        """SELECT followed by DROP via semicolon."""
        assert self._validate_sql("SELECT * FROM users; DROP TABLE users") is False

    def test_reject_union_injection(self):
        """UNION SELECT is rejected — SQLGlot parses as Union node, not Select."""
        result = self._validate_sql("SELECT * FROM users UNION SELECT password FROM admin")
        assert result is False, "UNION SELECT should be rejected (parsed as Union, not Select)"


# ─── Login Lock Service ───

class TestLoginLock:
    """Test login lock service."""

    @pytest.mark.asyncio
    async def setup_method(self):
        """Reset login attempts before each test."""
        from app.services import login_lock_service
        login_lock_service._fallback.clear()

    @pytest.mark.asyncio
    async def test_no_lock_initial(self):
        from app.services.login_lock_service import check_lock
        assert await check_lock("test@example.com") is False

    @pytest.mark.asyncio
    async def test_lock_after_5_failures(self):
        from app.services.login_lock_service import record_failure, check_lock
        email = "lock@example.com"
        for _ in range(5):
            await record_failure(email)
        assert await check_lock(email) is True

    @pytest.mark.asyncio
    async def test_not_locked_after_4_failures(self):
        from app.services.login_lock_service import record_failure, check_lock
        email = "notlocked@example.com"
        for _ in range(4):
            await record_failure(email)
        assert await check_lock(email) is False

    @pytest.mark.asyncio
    async def test_reset_unlocks(self):
        from app.services.login_lock_service import record_failure, check_lock, reset
        email = "reset@example.com"
        for _ in range(5):
            await record_failure(email)
        assert await check_lock(email) is True
        await reset(email)
        assert await check_lock(email) is False

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        from app.services.login_lock_service import record_failure, check_lock
        await record_failure("User@Example.com")
        await record_failure("user@example.com")
        await record_failure("USER@EXAMPLE.COM")
        await record_failure("uSeR@eXaMpLe.CoM")
        await record_failure("User@example.com")
        assert await check_lock("USER@EXAMPLE.COM") is True

    @pytest.mark.asyncio
    async def test_lock_expiry(self):
        import time
        from app.services.login_lock_service import record_failure, check_lock, _fallback, LOCK_DURATION
        email = "expiry@example.com"
        for _ in range(5):
            await record_failure(email)
        assert await check_lock(email) is True

        # Simulate time passing
        key = f"login_lock:{email}"
        _fallback[key]["locked_until"] = time.time() - 10  # Expired
        assert await check_lock(email) is False

    @pytest.mark.asyncio
    async def test_different_users_independent(self):
        from app.services.login_lock_service import record_failure, check_lock
        for _ in range(5):
            await record_failure("user1@example.com")
        assert await check_lock("user1@example.com") is True
        assert await check_lock("user2@example.com") is False

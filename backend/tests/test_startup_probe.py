"""
ChatBI v2 — Tests for Startup Probe (fail-fast, 对标 SEC-004/SEC-005)

验证:
  - 必需服务 (默认 PG) 可用 → 正常通过, 不抛异常
  - 必需服务挂了 → RuntimeError 拒绝启动 (fail-closed)
  - 可选服务 (Redis/Milvus) 失败 → 降级 WARNING, 不阻塞启动
  - 配置驱动: startup_required_services 增减生效
  - probe 不打印密钥 (SEC-004: 日志不含凭证)
"""
import pytest

from app.core.config import get_settings
from app.core.startup_probe import (
    ProbeResult,
    check_optional_services,
    check_required_services,
    probe_postgres,
    probe_redis,
    run_startup_probes,
    _redact,
)


# ── 单 probe 行为 ──────────────────────────────────────────────

class TestProbePostgres:
    """probe_postgres: SELECT 1 真实连库 + 认证校验。"""

    async def test_pg_available_returns_ok(self):
        """PG 在线 → ok=True, detail 是脱敏地址 (真实 chatbi_test 库)。"""
        result = await probe_postgres(timeout=5)
        assert result.service == "postgres"
        assert result.ok is True
        # detail 含地址但不裸露密码
        assert "chatbi_test" in result.detail or "postgres" in result.detail

    async def test_pg_down_returns_fail(self, monkeypatch):
        """PG 不可达 → ok=False, detail 含错误类型 (不抛异常, 策略由调用方定)。"""
        settings = get_settings()
        monkeypatch.setattr(
            settings, "database_url",
            "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/nonexistent",
        )
        result = await probe_postgres(timeout=2)
        assert result.ok is False
        assert "postgres" in result.service
        assert len(result.detail) > 0  # 有错误原因


class TestProbeRedis:
    """probe_redis: PING 校验。"""

    async def test_redis_available_returns_ok(self):
        """Redis 在线 (docker infra) → ok=True。"""
        result = await probe_redis(timeout=5)
        assert result.service == "redis"
        assert result.ok is True

    async def test_redis_down_returns_fail(self, monkeypatch):
        """Redis 不可达 → ok=False (不抛异常)。"""
        settings = get_settings()
        monkeypatch.setattr(
            settings, "redis_url", "redis://nobody:nopass@127.0.0.1:1/0",
        )
        result = await probe_redis(timeout=2)
        assert result.ok is False


# ── 脱敏 (SEC-004) ─────────────────────────────────────────────

class TestRedact:
    """SEC-004: 日志/探测结果不暴露凭证。"""

    def test_password_masked(self):
        url = "postgresql+asyncpg://root:secret@localhost:5432/chatbi_test"
        masked = _redact(url)
        assert "secret" not in masked
        assert "***" in masked
        # user/host/db 保留 (排查需要)
        assert "root" in masked
        assert "localhost" in masked
        assert "chatbi_test" in masked

    def test_redis_password_masked(self):
        url = "redis://default:mypass@host:6379/0"
        masked = _redact(url)
        assert "mypass" not in masked
        assert "***" in masked

    def test_redis_password_only_masked(self):
        """Redis 仅密码格式 scheme://:pass@host 也要脱敏 (无 user 段)。"""
        url = "redis://:redis_pass@host:6379/0"
        masked = _redact(url)
        assert "redis_pass" not in masked
        assert "***" in masked
        assert "host" in masked

    def test_redact_handles_leaked_url_in_error_string(self):
        """防御性: 异常消息里混进完整连接串也要脱敏 (SEC-004)。

        不依赖"某库当前不回显密码"的脆弱假设 — 任何库任何时候
        把连接串放进异常消息, _redact 都应兜住。
        """
        leaked = "ConnectError: postgresql+asyncpg://root:wrongpass@localhost:5432/chatbi"
        masked = _redact(leaked)
        assert "wrongpass" not in masked
        assert "***" in masked


# ── 编排: 必需 fail-fast ───────────────────────────────────────

class TestCheckRequired:
    """check_required_services: 必需服务任一挂 → RuntimeError。"""

    async def test_default_pg_required_passes(self):
        """默认 startup_required_services=['postgres'], PG 在线 → 不抛。"""
        results = await check_required_services()
        assert len(results) == 1
        assert results[0].service == "postgres"
        assert all(r.ok for r in results)

    async def test_required_pg_down_raises(self, monkeypatch):
        """必需的 PG 挂了 → RuntimeError 含 'STARTUP ABORTED' (fail-closed)。"""
        settings = get_settings()
        monkeypatch.setattr(settings, "startup_required_services", ["postgres"])
        monkeypatch.setattr(
            settings, "database_url",
            "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/nonexistent",
        )
        with pytest.raises(RuntimeError, match="STARTUP ABORTED"):
            await check_required_services()

    async def test_unknown_required_service_raises(self, monkeypatch):
        """未知 service 名进必需集 → RuntimeError (配置错误不静默)。"""
        settings = get_settings()
        monkeypatch.setattr(settings, "startup_required_services", ["nonexistent_svc"])
        with pytest.raises(RuntimeError, match="STARTUP ABORTED"):
            await check_required_services()

    async def test_configurable_required_set(self, monkeypatch):
        """required 集可配: 加 redis 进必需, 两者都在线 → 通过。"""
        settings = get_settings()
        monkeypatch.setattr(settings, "startup_required_services", ["postgres", "redis"])
        results = await check_required_services()
        assert len(results) == 2
        assert all(r.ok for r in results)


# ── 编排: 可选降级 ─────────────────────────────────────────────

class TestCheckOptional:
    """check_optional_services: 失败只 WARNING, 不抛异常 (SEC-005)。"""

    async def test_optional_never_raises_on_failure(self, monkeypatch):
        """可选服务全挂 → 不抛异常 (降级), 返回失败结果。"""
        settings = get_settings()
        # PG 在必需集 (默认), Redis/Milvus 进可选集; 把它们都搞挂
        monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
        monkeypatch.setattr(settings, "milvus_url", "http://127.0.0.1:1")
        # 不应抛异常
        results = await check_optional_services()
        assert len(results) >= 1
        # 可选集 = 全部 probe 减去必需集
        optional_names = {r.service for r in results}
        assert "postgres" not in optional_names

    async def test_optional_success_logged_at_info(self, caplog):
        """可选服务在线 → INFO 日志 (不是 WARNING)。"""
        import logging
        caplog.set_level(logging.INFO)
        await check_optional_services()
        # 至少有一条 INFO 级 startup probe 日志 (Redis 在线)
        probe_logs = [r for r in caplog.records if "Startup probe" in r.getMessage()]
        assert len(probe_logs) >= 1


# ── 端到端: run_startup_probes ──────────────────────────────────

class TestRunStartupProbes:
    """run_startup_probes: 必需通过后继续查可选。"""

    async def test_all_probes_run_when_required_ok(self):
        """必需通过 → 返回 (required_results, optional_results) 两组。"""
        required, optional = await run_startup_probes()
        assert len(required) >= 1  # 至少 postgres
        assert all(r.ok for r in required)
        assert len(optional) >= 0  # Redis/Milvus (取决于 required 集)

    async def test_aborts_before_optional_when_required_fails(self, monkeypatch):
        """必需失败 → 抛异常, 不查可选 (fail-fast 优先)。"""
        settings = get_settings()
        monkeypatch.setattr(
            settings, "database_url",
            "postgresql+asyncpg://nobody:nopass@127.0.0.1:1/nonexistent",
        )
        with pytest.raises(RuntimeError, match="STARTUP ABORTED"):
            await run_startup_probes()


# ── ProbeResult 表示 ───────────────────────────────────────────

class TestProbeResultStr:
    def test_ok_str_has_check(self):
        r = ProbeResult("postgres", True, "localhost:5432/db")
        s = str(r)
        assert "✅" in s
        assert "postgres" in s

    def test_fail_str_has_x(self):
        r = ProbeResult("redis", False, "connection refused")
        s = str(r)
        assert "❌" in s
        assert "connection refused" in s

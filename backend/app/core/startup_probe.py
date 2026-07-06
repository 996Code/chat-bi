"""
ChatBI v2 — Startup Probe (fail-fast)

对标:
  - SEC-004: 启动时检测 → 必需项缺失拒绝启动
  - SEC-005: 可选项降级 → WARNING 告警 (不阻塞启动)
  - v1 根本模式: 安全 Fail-Closed, 不静默放行

设计:
  - 每个外部依赖一个独立 probe (postgres / redis / milvus), 互不影响
  - probe 返回 ProbeResult(ok, detail), 不抛异常 (异常隔离)
  - check_required_services(): 必需服务任一失败 → raise RuntimeError 拒绝启动
  - check_optional_services(): 可选服务失败 → log WARNING 降级, 不阻塞

为什么 probe 不直接抛异常:
  - 要同时支持"必需(抛)"和"可选(降级)"两种语义, 抛异常会让降级路径复杂化
  - probe 只负责"能不能连", 策略 (必需/可选) 由调用方决定 → 关注点分离
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """单个服务的探测结果。"""
    service: str
    ok: bool
    detail: str  # 成功时是地址摘要, 失败时是错误原因

    def __str__(self) -> str:
        status = "✅" if self.ok else "❌"
        return f"{status} {self.service}: {self.detail}"


def _redact(url: str) -> str:
    """脱敏连接串中的凭证 (对标 SEC-004: 日志不打印密钥)。

    覆盖两种格式:
      scheme://user:pass@host   → scheme://user:***@host
      scheme://:pass@host       → scheme://:***@host  (Redis 仅密码)
    """
    import re
    # [^:@/]+ 匹配 user (不含冒号/斜杠); 整段匹配 user:pass 或 :pass
    return re.sub(r"://([^:@/]*):[^@/]*@", r"://\1:***@", url)


# ── 各服务独立 probe ──────────────────────────────────────────

async def probe_postgres(timeout: int) -> ProbeResult:
    """探测 PostgreSQL: 执行 SELECT 1 (真实连库 + 认证校验)。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    settings = get_settings()
    url = settings.database_url
    try:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with asyncio.timeout(timeout):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()
        return ProbeResult("postgres", True, _redact(url))
    except Exception as e:
        # 异常消息统一脱敏 (SEC-004: 不依赖某库当前不回显密码的脆弱假设)
        return ProbeResult("postgres", False, _redact(f"{type(e).__name__}: {e}"))


async def probe_redis(timeout: int) -> ProbeResult:
    """探测 Redis: PING (对标 redis_client 健康检查)。"""
    import redis.asyncio as aioredis

    settings = get_settings()
    url = settings.redis_url
    client = None
    try:
        client = aioredis.from_url(
            url, encoding="utf-8", decode_responses=True,
            socket_connect_timeout=timeout,
        )
        async with asyncio.timeout(timeout):
            await client.ping()
        return ProbeResult("redis", True, _redact(url))
    except Exception as e:
        return ProbeResult("redis", False, _redact(f"{type(e).__name__}: {e}"))
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


async def probe_milvus(timeout: int) -> ProbeResult:
    """探测 Milvus: 列 collections (真实连通 + 认证校验)。"""
    settings = get_settings()
    url = settings.milvus_url
    client = None
    try:
        # pymilvus 连接是同步的, 用 to_thread 避免阻塞 event loop
        from pymilvus import MilvusClient

        def _connect():
            return MilvusClient(uri=url, token=settings.milvus_token)

        async with asyncio.timeout(timeout):
            client = await asyncio.to_thread(_connect)
            # list_collections 触发真实请求
            await asyncio.to_thread(client.list_collections)
        return ProbeResult("milvus", True, _redact(url))
    except Exception as e:
        return ProbeResult("milvus", False, _redact(f"{type(e).__name__}: {e}"))
    finally:
        if client is not None:
            try:
                await asyncio.to_thread(client.close)
            except Exception:
                pass


# probe 注册表: service name → probe function
_PROBES = {
    "postgres": probe_postgres,
    "redis": probe_redis,
    "milvus": probe_milvus,
}


async def _probe(service: str, timeout: int) -> ProbeResult:
    """运行单个 probe, 未知 service 返回失败结果 (不 KeyError 崩溃)。"""
    fn = _PROBES.get(service)
    if fn is None:
        return ProbeResult(service, False, f"unknown service '{service}'")
    return await fn(timeout)


# ── 编排: 必需 (fail-fast) + 可选 (降级) ──────────────────────

async def check_required_services() -> list[ProbeResult]:
    """探测必需服务, 任一失败 → raise RuntimeError 拒绝启动 (fail-closed)。

    必需服务集来自 config.startup_required_services。
    """
    settings = get_settings()
    timeout = settings.startup_probe_timeout
    required = settings.startup_required_services

    results = [await _probe(svc, timeout) for svc in required]
    failed = [r for r in results if not r.ok]

    if failed:
        lines = [str(r) for r in results]
        failed_names = ", ".join(r.service for r in failed)
        msg = (
            "\n" + "=" * 60 + "\n"
            f"🚫 STARTUP ABORTED: required service(s) unavailable: {failed_names}\n"
            + "=" * 60 + "\n"
            + "\n".join(lines) + "\n"
            + "=" * 60 + "\n"
            "必需服务不可用, 拒绝启动 (fail-closed)。请检查:\n"
            "  1. 服务进程是否运行 (docker compose ps)\n"
            "  2. 连接配置是否正确 (DATABASE_URL / REDIS_URL / MILVUS_URL)\n"
            f"  3. 调整必需集: STARTUP_REQUIRED_SERVICES (当前: {required})\n"
            + "=" * 60
        )
        raise RuntimeError(msg)

    for r in results:
        logger.info("Startup probe (required): %s", r)
    return results


async def check_optional_services() -> list[ProbeResult]:
    """探测可选服务, 失败 → WARNING 降级, 不阻塞启动 (对标 SEC-005)。

    可选 = 所有已知 probe 中不在 required 集里的。
    """
    settings = get_settings()
    timeout = settings.startup_probe_timeout
    required = set(settings.startup_required_services)
    optional = [s for s in _PROBES if s not in required]

    results = [await _probe(svc, timeout) for svc in optional]
    for r in results:
        if r.ok:
            logger.info("Startup probe (optional): %s", r)
        else:
            logger.warning(
                "Startup probe (optional): %s — 降级运行, 相关功能将受限", r,
            )
    return results


async def run_startup_probes() -> tuple[list[ProbeResult], list[ProbeResult]]:
    """启动探测入口: 先查必需 (fail-fast), 再查可选 (降级)。

    Returns:
        (required_results, optional_results)
    Raises:
        RuntimeError: 必需服务任一不可用。
    """
    required_results = await check_required_services()
    optional_results = await check_optional_services()
    return required_results, optional_results

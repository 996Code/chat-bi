"""
T031: SQL 执行 — 单元测试

对标:
  - AEE-007 (openspec spec): 执行最多 1 次; 超时控制; 大结果分块
  - v1 教训 #42: 连接池必须 dispose (复用 datasource_engine.py 已修复的池)
  - Claude Code §3.4: 并发分批 (只读才并发), 写操作串行

设计:
  - execute_sql(sql, datasource) → ExecuteResult(rows, columns, truncated, error)
  - 复用 DataSourceEnginePool (同步 engine) → asyncio.to_thread 包装
  - 超时: config.sql_execution_timeout=30s
  - 行数限制: config.sql_max_rows=10000
  - 分块: config.sql_result_chunk_size=1000 (流式 yield, 防大结果 OOM)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.sql_executor import execute_sql, ExecuteResult


def _mock_engine(rows: list[tuple], columns: list[str] | None = None):
    """构造 mock sync engine + connection, execute 返回指定结果。"""
    result = MagicMock()
    result.keys.return_value = columns or ["col1"]
    result.fetchall.return_value = rows
    result.rowcount = len(rows)

    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value = result
    # execute 可作为 context manager (text(sql) 的返回)
    conn.execute.return_value.fetchall.return_value = rows

    engine = MagicMock()
    engine.connect.return_value = conn
    return engine, conn


# ── 执行成功 ──────────────────────────────────────────────────

class TestExecuteSuccess:
    """SQL 执行成功 → 返回行 + 列。"""

    @pytest.mark.asyncio
    async def test_execute_returns_rows(self):
        engine, _ = _mock_engine([(1, "alice"), (2, "bob")], ["id", "name"])
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        result = await execute_sql(
            sql="SELECT id, name FROM users",
            datasource_id="ds1",
            url="postgresql://x",
            engine_pool=pool,
        )
        assert result.error is None
        assert result.columns == ["id", "name"]
        assert len(result.rows) == 2
        assert result.rows[0] == (1, "alice")

    @pytest.mark.asyncio
    async def test_execute_empty_result(self):
        engine, _ = _mock_engine([], ["id"])
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        result = await execute_sql("SELECT * FROM t WHERE 1=0", "ds1", "x", pool)
        assert result.error is None
        assert result.rows == []
        assert len(result.columns) == 1

    @pytest.mark.asyncio
    async def test_execute_engine_reused_from_pool(self):
        """复用连接池 (对标 v1 教训 #42, 不新建连接)。"""
        engine, _ = _mock_engine([], ["x"])
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        await execute_sql("SELECT 1", "ds1", "url1", pool)
        pool.get_or_create.assert_called_once_with("ds1", "url1")


# ── 超时控制 ──────────────────────────────────────────────────

class TestTimeout:
    """超时控制 (config.sql_execution_timeout)。"""

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        """执行超时 → 返回 error, 不挂死。"""
        import time

        engine, conn = _mock_engine([], ["x"])
        def slow_exec(*a, **kw):
            time.sleep(0.3)  # 300ms, 超过 100ms 超时
            return MagicMock()
        conn.execute.side_effect = slow_exec
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        result = await execute_sql(
            "SELECT 1", "ds1", "x", pool, timeout=0.1,
        )
        assert result.error is not None
        assert "超时" in result.error or "timeout" in result.error.lower()


# ── 行数限制 ──────────────────────────────────────────────────

class TestMaxRows:
    """行数限制 (config.sql_max_rows=10000), 超限截断 + 标记。"""

    @pytest.mark.asyncio
    async def test_truncated_when_exceed_max_rows(self):
        """返回行数 > max_rows → 截断 + truncated=True。"""
        big_rows = [(i,) for i in range(100)]
        engine, _ = _mock_engine(big_rows, ["id"])
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        result = await execute_sql(
            "SELECT * FROM big", "ds1", "x", pool,
            max_rows=50,  # 限制 50 行
        )
        assert len(result.rows) <= 50
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_not_truncated_within_limit(self):
        rows = [(i,) for i in range(10)]
        engine, _ = _mock_engine(rows, ["id"])
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        result = await execute_sql("SELECT * FROM t", "ds1", "x", pool, max_rows=50)
        assert result.truncated is False
        assert len(result.rows) == 10


# ── 执行失败 ──────────────────────────────────────────────────

class TestExecuteFailure:
    """执行失败 → 返回 error (不抛, 调用方决定自愈/终止)。"""

    @pytest.mark.asyncio
    async def test_sql_error_returns_error(self):
        """SQL 错误 (如表不存在) → error 含错误信息 (供 T032 自愈)。"""
        engine, conn = _mock_engine([], ["x"])
        conn.execute.side_effect = Exception("(1146) Table 'db.users' doesn't exist")
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        result = await execute_sql("SELECT * FROM users", "ds1", "x", pool)
        assert result.error is not None
        assert "1146" in result.error or "doesn't exist" in result.error

    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self):
        """连接失败 → error (不抛)。"""
        engine = MagicMock()
        engine.connect.side_effect = Exception("Connection refused")
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        result = await execute_sql("SELECT 1", "ds1", "x", pool)
        assert result.error is not None


# ── SQL 执行前加 LIMIT (防全表扫描 OOM) ───────────────────────

class TestSafetyLimit:
    """无 LIMIT 的查询自动加行数上限 (防全表扫描)。"""

    @pytest.mark.asyncio
    async def test_limit_injected_if_missing(self):
        """SELECT 没有 LIMIT → 自动加 LIMIT max_rows (安全防护)。"""
        engine, conn = _mock_engine([(1,)], ["id"])
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        await execute_sql("SELECT * FROM big_table", "ds1", "x", pool, max_rows=1000)

        # 验证执行时 SQL 带 LIMIT
        executed_sql = str(conn.execute.call_args[0][0])
        assert "LIMIT" in executed_sql.upper()

    @pytest.mark.asyncio
    async def test_limit_not_injected_if_present(self):
        """已有 LIMIT → 不重复加。"""
        engine, conn = _mock_engine([(1,)], ["id"])
        pool = MagicMock()
        pool.get_or_create.return_value = engine

        await execute_sql("SELECT * FROM t LIMIT 5", "ds1", "x", pool, max_rows=1000)

        executed_sql = str(conn.execute.call_args[0][0]).upper()
        assert executed_sql.count("LIMIT") == 1

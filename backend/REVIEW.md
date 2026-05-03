---
phase: 01-code-review
reviewed: 2026-05-03T00:00:00Z
depth: deep
files_reviewed: 42
files_reviewed_list:
  - backend/app/main.py
  - backend/app/core/config.py
  - backend/app/core/security.py
  - backend/app/core/encryption.py
  - backend/app/core/logging.py
  - backend/app/core/rate_limiter.py
  - backend/app/db/base.py
  - backend/app/db/models.py
  - backend/app/db/session.py
  - backend/app/db/types.py
  - backend/app/schemas/__init__.py
  - backend/app/schemas/auth.py
  - backend/app/schemas/common.py
  - backend/app/schemas/datasource.py
  - backend/app/schemas/query.py
  - backend/app/api/__init__.py
  - backend/app/api/auth.py
  - backend/app/api/datasource.py
  - backend/app/api/query.py
  - backend/app/api/conversation.py
  - backend/app/api/saved_query.py
  - backend/app/api/export.py
  - backend/app/api/audit.py
  - backend/app/api/feedback.py
  - backend/app/api/data_model.py
  - backend/app/api/v1/__init__.py
  - backend/app/ai/__init__.py
  - backend/app/ai/graph.py
  - backend/app/ai/chart_type.py
  - backend/app/ai/nodes/__init__.py
  - backend/app/ai/nodes/execution.py
  - backend/app/ai/nodes/generation.py
  - backend/app/ai/nodes/intent.py
  - backend/app/ai/nodes/self_heal.py
  - backend/app/ai/prompts/__init__.py
  - backend/app/ai/prompts/query_prompt.py
  - backend/app/services/__init__.py
  - backend/app/services/analytics_service.py
  - backend/app/services/audit_service.py
  - backend/app/services/connection_pool.py
  - backend/app/services/datasource_service.py
  - backend/app/services/email_service.py
  - backend/app/services/login_lock_service.py
  - backend/app/services/mysql_schema_scanner.py
  - backend/app/services/rag_schema_service.py
  - backend/seed.py
  - backend/alembic/env.py
  - backend/tests/test_auth.py
  - backend/tests/test_unit.py
  - backend/tests/test_additional.py
  - backend/tests/test_datasource_query.py
  - backend/tests/test_e2e.py
findings:
  critical: 6
  warning: 16
  info: 10
  total: 32
status: issues_found
---

# Phase 01: Comprehensive Code Review Report

**Reviewed:** 2026-05-03T00:00:00Z
**Depth:** deep
**Files Reviewed:** 42
**Status:** issues_found

## Summary

This review covers the complete backend codebase of the ChatBI system -- a natural-language-to-SQL BI platform supporting MySQL and PostgreSQL datasources. The system is well-structured with clear separation of concerns (API routes, services, AI/LLM integration, schemas, database models). The SQLGlot-based SQL validation is a solid defense layer, credential encryption with Fernet is appropriate, and the login lock mechanism is well-designed.

However, I identified **6 critical issues** including security vulnerabilities (SQL injection via UNION bypass, broken datasource connection pool after test, JWT token revocation absence), **16 warnings** covering race conditions, unhandled exceptions, authorization gaps, and input validation gaps, plus **10 info items** for code quality improvements.

---

## Critical Issues

### CR-01: SQLGlot `parse_one` silently ignores additional statements -- injection bypass risk

**File:** `backend/app/ai/nodes/execution.py:21`
**Issue:** The `validate_sql` function uses `sqlglot.parse_one(sql, dialect=dialect)` which only parses the **first** SQL statement. Any statements after a semicolon are silently discarded and never validated. An attacker (via prompt injection in the user question) could craft:

```sql
SELECT 1; DROP TABLE users; --
```

`parse_one` returns only the `Select` node for `SELECT 1`. The `DROP TABLE` statement is never parsed, never validated, and never executed by SQLAlchemy's `text()` either (since SQLAlchemy does not support multi-statement execution by default). So the attack is neutralized by the execution layer, but **the validation layer gives a false sense of security** -- it reports the SQL as valid without acknowledging that additional statements exist. More critically, if the underlying driver ever supports multi-statement execution (e.g., `multi=True` in some MySQL drivers), the validation bypass becomes a full SQL injection vulnerability.

Additionally, `sqlglot.parse_one` for `UNION SELECT` returns a `Union` node (not `Select`), so it is correctly rejected. But `INTERSECT` and `EXCEPT` produce `SetOperation` nodes that are also not `Select`, which is correct but should be explicitly tested.

**Fix:** Parse all statements and reject multi-statement input explicitly:
```python
def validate_sql(sql: str, dialect: str = "mysql") -> tuple[bool, str]:
    try:
        statements = sqlglot.parse(sql, dialect=dialect)  # parse ALL statements
    except ParseError as e:
        return False, f"SQL syntax error: {e}"

    if len(statements) > 1:
        return False, "Multiple SQL statements are not allowed"

    parsed = statements[0]
    if not isinstance(parsed, sqlglot.exp.Select):
        return False, "Only SELECT queries are allowed"
    # ... rest of validation unchanged
```

### CR-02: `test_datasource` disposes engine but leaves it in pool manager -- all subsequent queries fail

**File:** `backend/app/services/datasource_service.py:93-95`
**Issue:** The `test_connection` method creates a connection pool, tests it, then calls `await engine.dispose()`. However, the disposed engine remains in `pool_manager._pools[ds_id]`. Any subsequent query using this datasource will retrieve the disposed engine and fail with a connection error.

```python
# Line 93-95:
async with engine.connect() as conn:
    await conn.execute(text("SELECT 1"))
await engine.dispose()  # Engine disposed but still in pool_manager._pools
```

**Fix:** Remove the engine from the pool after disposal:
```python
await engine.dispose()
await pool_manager.close_pool(str(ds.id))  # Remove from pool dict
```
Or simply do not dispose the engine after a successful test -- let it remain in the pool for reuse.

### CR-03: Login lock mechanism is in-memory only -- ineffective in multi-worker deployments

**File:** `backend/app/services/login_lock_service.py:6`
**Issue:** `_login_attempts` is a plain Python dictionary shared only within a single process. In production with multiple Uvicorn workers (or behind a load balancer), each worker maintains its own independent counter. An attacker can distribute failed login attempts across workers to bypass the lock entirely. Additionally, if the process restarts, all lock state is lost -- locked accounts are immediately unlocked.

**Fix:** Persist login attempt counts to the database (the `User` model already has `failed_login_attempts`, `is_locked`, and `lock_until` columns that are defined but **never used** by the lock service):
```python
# In record_failure(), use the DB:
user.failed_login_attempts += 1
if user.failed_login_attempts >= MAX_ATTEMPTS:
    user.is_locked = True
    user.lock_until = datetime.now(timezone.utc) + timedelta(seconds=LOCK_DURATION)
await db.commit()

# In check_lock(), query the DB:
user = await db.execute(select(User).where(User.email == email))
if user and user.is_locked and user.lock_until > datetime.now(timezone.utc):
    return True
```

### CR-04: `list_feedback` endpoint is scoped to tenant, not user -- any tenant member can see all feedback

**File:** `backend/app/api/feedback.py:52-53`
**Issue:** The `list_feedback` endpoint queries `Feedback.tenant_id == user["tenant_id"]`, returning ALL feedback from ALL users in the tenant. Feedback may contain sensitive comments about query results. There is no user-scoping filter.

**Fix:** Scope to the current user:
```python
result = await db.execute(
    select(Feedback)
    .where(Feedback.user_id == uuid.UUID(user["user_id"]))  # Changed from tenant_id
    .order_by(Feedback.created_at.desc())
    .offset(offset)
    .limit(page_size)
)
```
Or keep tenant-scoped but restrict to admin-only access.

### CR-05: JWT refresh endpoint does not validate user still exists or is active -- stolen tokens remain usable

**File:** `backend/app/api/auth.py:138-154`
**Issue:** The `/auth/refresh` endpoint only verifies the JWT signature and creates new tokens from the payload. It does not check whether the user still exists, is active, or whether the token should be revoked. A stolen refresh token remains valid until it naturally expires (7 days). There is no token revocation mechanism (blacklist/denylist).

**Fix:** Add a token revocation table or at minimum verify the user still exists and is active:
```python
@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = verify_refresh_token(req.refresh_token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, ...)

    # Verify user still exists and is active
    result = await db.execute(select(User).where(User.id == uuid.UUID(payload["user_id"])))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, ...)

    # Check token hasn't been revoked (requires a TokenBlacklist model)
    # ...
```

### CR-06: `reset-password/confirm` leaks user enumeration via error message

**File:** `backend/app/api/auth.py:169-190`
**Issue:** The password reset confirmation endpoint returns different error codes for invalid token vs. user not found. While the initial reset endpoint correctly returns the same message regardless of user existence, the confirmation endpoint gives away whether a user exists when the token is valid but the user is deleted:

```python
email = verify_password_reset_token(req.token)  # Line 171
if not email:
    raise HTTPException(..., detail=_error("INVALID_TOKEN", ...))  # Line 175

result = await db.execute(select(User).where(User.email == email))
user = result.scalar_one_or_none()
if not user:
    raise HTTPException(..., detail=_error("INVALID_TOKEN", ...))  # Same error code, but this branch is only reached if token is valid
```

The error code is the same (`INVALID_TOKEN`), but the timing difference (DB lookup vs. early return) could be exploited. More importantly, if the error messages ever diverge, this becomes a full user enumeration vector.

**Fix:** Use a constant-time lookup or combine both checks:
```python
email = verify_password_reset_token(req.token)
result = await db.execute(select(User).where(User.email == email))
user = result.scalar_one_or_none()
if not email or not user:  # Combined check
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=_error("INVALID_TOKEN", "重置链接已过期或无效"),
    )
```

---

## Warnings

### WR-01: `get_current_user` does not validate required JWT payload fields -- `KeyError` on malformed tokens

**File:** `backend/app/core/security.py:104-118`
**Issue:** After JWT signature verification, `get_current_user` returns the raw payload without validating that required keys (`user_id`, `tenant_id`, `email`) exist. A manually crafted token with a valid signature but missing fields will cause `KeyError` crashes in all downstream route handlers that access `user["tenant_id"]` or `user["user_id"]`.

**Fix:**
```python
async def get_current_user(request: Request) -> dict:
    # ... existing token verification ...
    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(...)
    for key in ("user_id", "tenant_id", "email"):
        if key not in payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "令牌缺少必要字段", "details": None},
            )
    return payload
```

### WR-02: Multiple endpoints accept raw `dict` without Pydantic validation -- no input sanitization

**File:** `backend/app/api/conversation.py:79-80`, `backend/app/api/saved_query.py:54-55`, `backend/app/api/feedback.py:17-18`, `backend/app/api/export.py:30-31`, `backend/app/api/data_model.py:83-84`, `backend/app/api/data_model.py:146-147`
**Issue:** Six endpoints accept `data: dict` as the request body parameter instead of typed Pydantic models. This means:
- No automatic input validation (type checking, length limits, required fields)
- No automatic error responses for malformed input
- Missing fields silently default to `None` or cause `KeyError` crashes
- `datasource_id` can be any string including SQL injection payloads

Specifically, `create_conversation` (line 79) uses `data.get("datasource_id", "")` which is stored directly without UUID validation, and `create_data_model` (line 83) stores `config_content` as JSON without structural validation.

**Fix:** Define Pydantic request schemas for each endpoint. Example for conversation:
```python
class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", max_length=200)
    datasource_id: str

class ConversationUpdate(BaseModel):
    title: str | None = Field(None, max_length=200)
    messages: list[dict] | None = None
    datasource_id: str | None = None
```

### WR-03: `sync_data_model` mutable default argument and UnboundLocalError risk

**File:** `backend/app/api/data_model.py:215`
**Issue:** The `sync_data_model` endpoint uses `data: dict = {}` as a mutable default argument. While FastAPI typically creates new instances per request, this is an anti-pattern that can cause subtle bugs. Additionally, `existing_data` is referenced at lines 318-319 but only defined inside the `if existing_config:` block at line 275. If `existing_config` is `None`, the code reaches lines 318-319 and raises `UnboundLocalError`.

**Fix:**
```python
async def sync_data_model(
    ds_id: str,
    data: dict = Body(default={}),  # Use Body for clarity
    ...
):
    ...
    existing_data = {}  # Initialize default
    if existing_config:
        existing_data = json.loads(existing_config.config)
    ...
    # Now existing_data is always defined
    merged_metadata = {
        ...
        "relationships": existing_data.get("relationships", []),
        "metrics": existing_data.get("metrics", []),
    }
```

### WR-04: `update_data_model` uses raw SQL with string ID -- type mismatch risk

**File:** `backend/app/api/data_model.py:171-174`
**Issue:** The UPDATE uses `text("UPDATE metadata_configs SET config = :config ... WHERE id = :id")` and passes `str(config_id)` as the `id` parameter. The `id` column is a UUID type. On PostgreSQL, the UUID type may not accept a bare string representation, causing a type coercion error or silently failing to match.

**Fix:** Use ORM instead of raw SQL:
```python
result = await db.execute(select(MetadataConfig).where(MetadataConfig.id == config_id))
config = result.scalar_one()
if "config" in data:
    config.config = json.dumps(data["config"], ensure_ascii=False)
await db.commit()
```

### WR-05: `_login_attempts` grows unbounded -- memory leak in long-running process

**File:** `backend/app/core/rate_limiter.py:11`, `backend/app/services/login_lock_service.py:6`
**Issue:** Both `_rate_limits` and `_login_attempts` are global dictionaries that grow without bound. Failed login entries for non-existent emails accumulate forever (only locked entries are cleaned on expiration check). Similarly, rate limit timestamps are only cleaned when `check_rate_limit` is called for that specific key, and entries for IPs that stop making requests are never cleaned.

**Fix:** Add periodic cleanup or use bounded data structures (e.g., `cachetools.TTLCache`):
```python
from cachetools import TTLCache
_rate_limits: dict[str, list[float]] = TTLCache(maxsize=10000, ttl=120)
```

### WR-06: `test_connection` swallows real error message -- returns generic "连接失败"

**File:** `backend/app/services/datasource_service.py:103-107`
**Issue:** The `test_connection` method catches all exceptions but returns a generic error message `"连接失败，请检查配置"` regardless of the actual error. The real exception is only logged. While this is arguably good for security (not leaking infrastructure details), it makes debugging connection issues difficult for legitimate users.

**Fix:** Return categorized error types:
```python
except asyncio.TimeoutError:
    return {"success": False, "error": "连接超时，请检查网络"}
except OSError:
    return {"success": False, "error": "无法连接到服务器，请检查主机和端口"}
except Exception as e:
    logger.error("Connection test failed for %s: %s", ds_id, e)
    return {"success": False, "error": "连接失败，请检查配置"}
```

### WR-07: `get_rag_schema` fallback returns raw metadata JSON -- potential context overflow

**File:** `backend/app/services/rag_schema_service.py:161-165`
**Issue:** When `metadata_json` is not valid JSON, the function falls back to returning the raw string to the LLM. If the raw string is very large (e.g., full schema dump), this can exceed the LLM context window limit and cause the entire query to fail. Additionally, the raw string may contain sensitive table names not intended for the current user.

**Fix:**
```python
def get_rag_schema(question: str, metadata_json: str, max_tables: int = 5) -> str:
    try:
        metadata = json.loads(metadata_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse metadata JSON")
        return ""  # Return empty context rather than raw string
```

### WR-08: `login` endpoint double `commit()` -- redundant and potentially confusing

**File:** `backend/app/api/auth.py:110, 124`
**Issue:** After `reset(req.email)` and `user.failed_login_attempts = 0`, the first `await db.commit()` persists the changes. Then audit logging and analytics are added (which just do `db.add()` without commit), and a second `await db.commit()` is called. This is redundant -- the second commit has nothing new to persist since the audit/analytics services don't commit. While not a bug per se, it creates confusion about transaction boundaries.

**Fix:** Remove the first commit and keep only the final one:
```python
reset(req.email)
user.failed_login_attempts = 0

# Audit log
await log_action(db, ...)
await track_event(db, ...)

await db.commit()  # Single commit for all changes
```

### WR-09: `stream_query` commits before streaming begins -- audit event persists even if query fails

**File:** `backend/app/api/query.py:198-208`
**Issue:** The `stream_query` endpoint commits the audit/analytics events BEFORE the SSE generator starts yielding. If the query subsequently fails or the client disconnects, the audit trail records `QUERY_EXECUTE_STREAM` but there may be no corresponding success/failure event, creating incomplete audit records.

**Fix:** Move audit logging inside the generator, after the query completes:
```python
async def event_stream():
    try:
        # ... execute query ...
        # After completion, log audit in a separate DB session
        async with async_session_factory() as audit_db:
            await log_action(audit_db, ...)
            await track_event(audit_db, ...)
            await audit_db.commit()
    ...
```

### WR-10: `DataSourceService.test_connection` disposes engine without removing from pool

**File:** `backend/app/services/datasource_service.py:95`
**Issue:** Related to CR-02. After `engine.dispose()`, the engine object is still referenced in `pool_manager._pools[ds_id]`. The next call to `pool_manager.get_pool(ds)` will return the disposed engine. Any attempt to use it (e.g., in a query) will raise `InvalidRequestError: This connection is closed`.

**Fix:** Either don't dispose the engine after test, or remove it from the pool:
```python
async with engine.connect() as conn:
    await conn.execute(text("SELECT 1"))
# Don't dispose -- let the pool manager keep it for reuse
# await engine.dispose()  # REMOVED
```

### WR-11: SQL error messages returned to client may leak schema details

**File:** `backend/app/ai/nodes/execution.py:136-139`
**Issue:** The error handling returns `str(e)[:500]` to the client. Database error messages often contain table names, column names, and schema details that could aid an attacker in mapping the database structure. For example: `Table 'orders_db.user_profiles' doesn't exist` leaks table names.

**Fix:** Sanitize error messages before returning:
```python
except Exception as e:
    logger.exception("SQL execution failed: %s", sql[:200])
    # Map to generic error categories
    error_str = str(e).lower()
    if "doesn't exist" in error_str or "unknown column" in error_str:
        return {"success": False, "error": "查询引用的表或列不存在，请检查字段名"}
    elif "access denied" in error_str:
        return {"success": False, "error": "数据库访问权限不足"}
    else:
        return {"success": False, "error": "SQL 执行失败，请稍后重试"}
```

### WR-12: `conversation.py` uses raw `uuid.UUID()` without validation -- crashes on invalid input

**File:** `backend/app/api/conversation.py:60, 117, 147`
**Issue:** Multiple endpoints call `uuid.UUID(conv_id)` on user-provided input. If `conv_id` is not a valid UUID string (e.g., `"invalid"` or `"../../etc/passwd"`), `uuid.UUID()` raises `ValueError` which is not caught, resulting in a 500 Internal Server Error instead of a proper 400/404 response.

**Fix:** Wrap UUID parsing in try/except:
```python
try:
    conv_uuid = uuid.UUID(conv_id)
except ValueError:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的会话 ID 格式")
```
Or use a Pydantic path parameter with UUID type.

### WR-13: `_scan_postgres_schema` uses `regclass` cast which can fail on special table names

**File:** `backend/app/services/mysql_schema_scanner.py:190`
**Issue:** The PostgreSQL schema scan uses `(schemaname || '.' || tablename)::regclass` to get table comments. If a table name contains special characters or is a reserved word, the `regclass` cast will fail with a SQL error, crashing the entire scan.

**Fix:** Use `quote_ident()` or `format()` with `%I` for safe identifier quoting:
```python
tables_result = await conn.execute(text("""
    SELECT tablename,
        obj_description(format('%I.%I', schemaname, tablename)::regclass, 'pg_class') as comment
    FROM pg_tables
    WHERE schemaname = 'public'
"""))
```

### WR-14: `create_datasource` commit logic is fragile -- audit failure can cause double-commit attempt

**File:** `backend/app/api/datasource.py:59-78`
**Issue:** The create datasource endpoint commits, flushes for audit, then commits again. If the first commit succeeds but audit logging fails, the outer try/except attempts a second commit on an already-committed session. While the inner try/except catches this, the logic is fragile and depends on SQLAlchemy session state behavior.

**Fix:** Perform audit/analytics in a separate DB session after the primary operation:
```python
ds = await service.create(data)
await db.commit()  # Primary operation committed

# Audit in separate session
try:
    from app.services.audit_service import log_action
    async with async_session_factory() as audit_db:
        await log_action(audit_db, ...)
        await audit_db.commit()
except Exception:
    logger.exception("Audit failed for datasource %s", ds.id)
```

### WR-15: `_build_url` uses `render_as_string(hide_password=False)` -- password visible in engine URL repr

**File:** `backend/app/services/connection_pool.py:54`
**Issue:** While the password is necessary for the connection URL, calling `render_as_string(hide_password=False)` means that any logging or debugging that prints the engine URL will expose the plaintext database password. SQLAlchemy's default engine repr also shows the URL.

**Fix:** Use `hide_password=True` in production and only unhide for debugging:
```python
engine = create_async_engine(
    url,  # Pass URL object directly, not string
    pool_size=5,
    ...
)
```
SQLAlchemy accepts URL objects directly. This avoids stringification entirely.

### WR-16: `_merge_model` does not handle missing `existing_col_map` column gracefully

**File:** `backend/app/api/data_model.py:354`
**Issue:** In `_merge_model`, if a scanned column does not exist in `existing_col_map`, the code proceeds normally. But if an existing column was deleted in the scanned schema, the `merged_columns` list simply omits it. This could silently drop user customizations for columns that were renamed in the database.

**Fix:** Log a warning when columns are dropped:
```python
missing_cols = set(existing_col_map.keys()) - {c["name"] for c in scanned.get("columns", [])}
if missing_cols:
    logger.warning("Columns dropped during merge for %s: %s", scanned["name"], missing_cols)
```

---

## Info

### IN-01: Unused `Conversation` and `Feedback` models in alembic imports

**File:** `backend/app/alembic/env.py:18-25`
**Issue:** The alembic env imports `Tenant`, `User`, `DataSource`, `MetadataConfig`, `AuditLog`, `SavedQuery` but not `Conversation` or `Feedback`. If `Base.metadata.create_all` is used (as in `main.py`), all models are included. But for Alembic migrations, the missing imports mean new tables added via `Base.metadata.create_all` won't be tracked by Alembic. The `Conversation` and `Feedback` tables exist in the model but are not in the Alembic import list.

**Fix:** Add missing model imports:
```python
from app.db.models import (
    Tenant, User, DataSource, MetadataConfig, AuditLog, SavedQuery,
    Feedback, AnalyticsEvent, Conversation,  # Added
)
```

### IN-02: `mask_sensitive` logging function only masks emails, not passwords or tokens

**File:** `backend/app/core/logging.py:23-34`
**Issue:** The `mask_sensitive` function checks for `@` to detect emails but returns `***` for anything else. It does not handle passwords, API keys, tokens, or other sensitive patterns that might appear in log messages. The function is also never called in the codebase.

**Fix:** Expand to handle common sensitive patterns or integrate with structured logging:
```python
def mask_sensitive(value: str) -> str:
    patterns = [
        (r'"password"\s*:\s*"[^"]*"', '"password": "***"'),
        (r'"api_key"\s*:\s*"[^"]*"', '"api_key": "***"'),
        (r'"token"\s*:\s*"[^"]*"', '"token": "***"'),
    ]
    result = value
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result)
    # ... existing email masking ...
    return result
```

### IN-03: Duplicate code between `scan_mysql_schema_raw` and `scan_mysql_schema`

**File:** `backend/app/services/mysql_schema_scanner.py:13-84` vs `87-182`
**Issue:** The two functions have nearly identical SQL queries and data structure building logic. The column mapping, relationship building, and model assembly code is duplicated. DRY violation -- changes to schema scanning logic must be made in two places.

**Fix:** Extract the common query + structure building into a shared function:
```python
async def _scan_schema_queries(conn, db_name: str) -> dict:
    # Common query logic
    ...

async def scan_mysql_schema_raw(engine: AsyncEngine) -> dict:
    async with engine.connect() as conn:
        data = await _scan_schema_queries(conn, None)  # None uses DATABASE()
    return _build_metadata(data)

async def scan_mysql_schema(engine: AsyncEngine, db: AsyncSession, ds: DataSource) -> dict:
    async with engine.connect() as conn:
        data = await _scan_schema_queries(conn, ds.database_name)
    metadata = _build_metadata(data)
    # Save to DB
    ...
```

### IN-04: `stream_query` logs raw SQL without sanitization

**File:** `backend/app/api/query.py:252`
**Issue:** `logger.info("[%s] SQL generated: %s", trace_id, sql[:100] if sql else 'empty')` logs the generated SQL which may contain sensitive column names, table names, or data patterns. In production, log files should not contain database schema details.

**Fix:** Log only success/failure, or hash the SQL for correlation:
```python
import hashlib
sql_hash = hashlib.sha256(sql.encode()).hexdigest()[:12]
logger.info("[%s] SQL generated (hash=%s, len=%d)", trace_id, sql_hash, len(sql))
```

### IN-05: `login_lock_service` failure counter not persisted to DB -- redundant with User model fields

**File:** `backend/app/services/login_lock_service.py`
**Issue:** The `User` model has `failed_login_attempts`, `is_locked`, and `lock_until` columns defined in `models.py` (lines 33-35), but the `login_lock_service` uses an in-memory dictionary instead. The DB columns are dead code -- they are never read or written.

**Fix:** Either use the DB columns (as recommended in CR-03) or remove them from the model.

### IN-06: Seed script contains hardcoded credentials

**File:** `backend/seed.py:53, 57, 61`
**Issue:** The seed script contains hardcoded database passwords (`root123`) and user passwords (`Test1234!`). While this is a development script, it should not be committed with real credentials.

**Fix:** Use environment variables or generate random passwords:
```python
import os
ds_password = os.environ.get("SEED_DB_PASSWORD", "root123")
user_password = os.environ.get("SEED_USER_PASSWORD", "Test1234!")
```

### IN-07: `test_connection` uses unusual `__import__` pattern for datetime

**File:** `backend/app/services/datasource_service.py:98`
**Issue:** The code uses `__import__('datetime', globals(), locals(), ['datetime', 'timezone'], 0).datetime.now(...)` instead of a standard `from datetime import datetime, timezone` at the top of the file. This is an unusual pattern that hurts readability.

**Fix:** Add `from datetime import datetime, timezone` at the top of the file (already imported at line 2) and use `datetime.now(timezone.utc)`.

### IN-08: No test coverage for `self_heal` node

**File:** `backend/app/ai/nodes/self_heal.py`
**Issue:** The self-healing SQL retry logic is a critical path for the system's resilience, but there are no unit tests for it. The test suite only tests SQL validation, not the self-healing behavior.

**Fix:** Add tests that mock the LLM to return fixed SQL and verify the retry logic works correctly.

### IN-09: No test coverage for `stream_query` endpoint

**File:** `backend/app/api/query.py:149-298`
**Issue:** The SSE streaming query endpoint is a significant code path (~150 lines) with complex async generator logic, timeout handling, caching, and self-healing. No test covers this endpoint.

**Fix:** Add integration tests using `httpx.AsyncClient` with SSE parsing to verify the streaming behavior.

### IN-10: `Conversation.datasource_id` is String but should be UUID

**File:** `backend/app/db/models.py:128`
**Issue:** The `Conversation` model defines `datasource_id` as `Mapped[str] = mapped_column(String(100), nullable=False)`, while all other foreign key references (in `DataSource`, `MetadataConfig`, `SavedQuery`, etc.) use `GUID` (UUID) type. This inconsistency could cause issues if foreign key constraints are ever added.

**Fix:**
```python
datasource_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
```

---

_Reviewed: 2026-05-03T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_

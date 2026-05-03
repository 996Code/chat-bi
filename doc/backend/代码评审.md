# Phase 1 代码审查报告

> 审查日期：2026-05-02
> 审查范围：后端全部代码 + 前端核心文件 + 安全 + 架构 + 测试

---

## 一、代码验收（维度七）

| 检查项 | 结果 | 备注 |
|--------|------|------|
| Phase 01-01 验收（8/8） | ✅ | main.py, config.py, models.py, session.py, alembic |
| Phase 01-02 验收（13/13） | ✅ | security.py, login_lock, email_service, auth schemas |
| Phase 01-03 验收（7/7） | ✅ | datasource_service, connection_pool, schema_scanner, encryption |
| Phase 01-04 验收（13/13） | ✅ | graph.py, nodes, sqlglot, timeout, MAX_ROWS |
| Phase 01-05 验收（16/16） | ✅ | Vue3, Element Plus, Pinia, Router, Axios, TypeScript 编译通过 |
| **代码验收总计** | **57/57 ✅** | |

---

## 二、架构审查（维度二）

### ARCH-01 LangGraph 工作流 ✅
- `classify_intent` → `route_by_intent` → `generate_sql` → `execute_sql` → END
- `misleading` → END（非数据查询分支）
- 入口点正确，所有节点已注册

### ARCH-02 QueryState 状态定义 ✅
- 包含 question, datasource_id, schema_context, intent, sql, columns, rows, success, error

### ARCH-03 异步架构 ✅
- LLM 调用：`await get_llm().ainvoke(...)`
- SQL 执行：`async with pool.connect()`
- 查询端点：`async def handle_query(...)`

### ARCH-04 分层架构 ✅
```
backend/app/api/        → auth.py, datasource.py, query.py
backend/app/core/       → config.py, security.py, encryption.py, logging.py
backend/app/db/         → base.py, session.py, models.py, types.py
backend/app/schemas/    → auth.py, datasource.py, query.py
backend/app/services/   → datasource_service.py, connection_pool.py, email_service.py, login_lock_service.py, mysql_schema_scanner.py
backend/app/ai/         → graph.py, client.py, nodes/, prompts/
```

### ARCH-05 依赖注入 ✅
- `get_db`: FastAPI Depends → AsyncSession
- `get_current_user`: FastAPI Depends → dict with tenant_id

### ARCH-06/07 数据模型与租户隔离 ✅
- 所有业务表含 `tenant_id` + index
- User, DataSource, MetadataConfig, AuditLog, SavedQuery 均有 tenant_id

---

## 三、安全审查（维度四）

### SEC-AUTH 认证安全

| 项目 | 状态 | 说明 |
|------|------|------|
| 密码 bcrypt cost 12 | ✅ | security.py:18 `bcrypt.gensalt(rounds=settings.bcrypt_rounds)` |
| JWT HS256 + 15min/7d | ✅ | security.py:29-37 |
| JWT 包含 type/jti | ✅ | type: access/refresh, refresh 含 jti |
| 密码重置不暴露邮箱 | ✅ | auth.py:147 统一返回 200 |
| 密码重置 token 30min | ✅ | security.py:71 `max_age=1800` |
| 邮箱验证 token 24h | ✅ | security.py:84 `max_age=86400` |
| 登录锁定 5次→15min | ✅ | login_lock_service.py |
| **漏洞：JWT key 复用** | ⚠️ | `secret_key` 同时用于 JWT 和 itsdangerous 签名 |

### SEC-DATA 数据安全

| 项目 | 状态 | 说明 |
|------|------|------|
| 凭证 Fernet 加密 | ✅ | encryption.py 使用 Fernet |
| API 响应不暴露密码 | ✅ | DataSourceResponse 无 username/password 字段 |
| SQL 注入 AST 防护 | ✅ | execution.py: SQLGlot 只允许 SELECT |
| 13/13 SQL 注入测试通过 | ✅ | DROP/DELETE/INSERT/UPDATE/ALTER/TRUNCATE/多语句 全拦截 |
| 执行时 READ ONLY | ✅ | execution.py:59 `SET SESSION TRANSACTION READ ONLY` |
| 查询 30s 超时 | ✅ | execution.py:55 `asyncio.timeout(30)` |
| 1000 行截断 | ✅ | execution.py:68 `rows[:MAX_ROWS]` |
| Prompt 角色定义 | ✅ | query_prompt.py SYSTEM_PROMPT 明确"仅生成 SELECT" |

### SEC-TENANT 多租户安全

| 项目 | 状态 | 说明 |
|------|------|------|
| 数据源按 tenant_id 过滤 | ✅ | datasource_service.py list_all() 含 WHERE tenant_id |
| 注册自动创建独立 tenant | ✅ | auth.py 修复后每个用户独立 tenant |
| 跨租户访问 404 | ✅ | 集成测试验证通过 |
| JWT 携带 tenant_id | ✅ | security.py token payload 包含 tenant_id |

### SEC-CORS CORS 安全

| 项目 | 状态 | 说明 |
|------|------|------|
| 开发环境 localhost:5173 | ✅ | config.py cors_origins |
| allow_credentials: true | ✅ | main.py |
| 无通配符 * | ✅ | 具体域名列表 |

---

## 四、自动化测试

| 文件 | 用例 | 通过 | 失败 |
|------|------|------|------|
| test_auth.py | 认证与多租户 | 11 | 11 | 0 |
| test_datasource_query.py | 数据源+查询+SQL注入 | 9 | 9 | 0 |
| test_unit.py | SQL验证+登录锁定单元 | 21 | 21 | 0 |
| test_e2e.py | E2E全流程(注册→登录→数据源→查询) | 8 | 8 | 0 |
| **总计** | **49** | **49** | **0** |

---

## 五、发现的问题

### P0（已修复）

| # | 问题 | 严重度 | 状态 |
|---|------|--------|------|
| 1 | 租户隔离：所有用户共享同一个 tenant | 安全漏洞 | ✅ 已修复 |
| 2 | 登录锁定：check_lock 误删未锁定条目导致计数归零 | 安全漏洞 | ✅ 已修复 |
| 3 | models.py created_at/updated_at 类型标注为 bool | 类型错误 | ✅ 已修复 |

### P1（部分已修复）

| # | 问题 | 说明 | 状态 |
|---|------|------|------|
| 4 | JWT key 复用 | `secret_key` 同时用于 JWT 和 itsdangerous，建议新增 `token_signing_key` | ⏭️ 生产环境 |
| 5 | refresh token 未失效化 | 刷新后旧 token 仍可用 | ⏭️ 需要 Redis |
| 6 | 密码重置后旧 token 未失效 | 重置密码后旧 refresh token 仍可刷新 | ⏭️ 同上 |
| 7 | login_lock 使用内存 dict | 服务重启后锁定丢失 | ⏭️ 生产环境用 Redis |
| 8 | execution.py 访问私有属性 | `pool_manager._pools` | ✅ 已修复：添加 get_pool_by_id() |

### P2（部分已修复）

| # | 问题 | 说明 | 状态 |
|---|------|------|------|
| 9 | config.py 使用 deprecated class Config | 改用 `model_config = ConfigDict(...)` | ✅ 已修复 |
| 10 | 前端 chunk > 500KB | Element Plus + ECharts 全量引入 | ⏭️ 性能优化阶段 |

---

## 六、总结

**代码验收**：57/57 全部通过 ✅
**架构审查**：全部符合要求 ✅
**安全审查**：核心安全机制到位，6 项建议改进（2 项已修复）
**自动化测试**：49/49 全部通过 ✅
**已修复 Bug**：5 个（3 P0 + 2 P1/P2）

Phase 1 核心链路（登录 → 数据源 → 提问 → SQL → 数据表格）代码质量良好，安全机制（bcrypt、JWT、SQLGlot、租户隔离、登录锁定、凭证加密）全部实现并通过测试。

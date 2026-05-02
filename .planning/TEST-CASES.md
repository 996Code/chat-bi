# Phase 1 测试用例（完整版）

> 测试 agent 按此文档逐项执行，标记 PASS/FAIL。
> 覆盖 7 个维度：需求、设计/架构、交互/UX、安全、性能、E2E、代码验收。

## 前置条件

```bash
# 1. 启动后端
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
sleep 3
curl -s http://127.0.0.1:8000/health  # 应返回 {"status":"ok"}

# 2. 测试变量
BASE="http://127.0.0.1:8000/api/v1"
TEST_EMAIL="test_$(date +%s)@chatbi.com"
TEST_PASSWORD="TestPass123"
DB_PATH="backend/chatbi.db"
```

---

# 维度一：需求覆盖测试（按 REQUIREMENTS.md Phase 1 需求）

## 1.1 认证与多租户（AUTH-01 ~ AUTH-08, TENANT-02）

### AUTH-01：邮箱/密码注册
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | `POST /api/v1/auth/register` `{email: "valid@email.com", password: "Valid1234"}` | 201, `{"message": "注册成功，请查收验证邮件"}` |
| 2 | 查数据库 users 表 | 用户记录存在, is_verified=false |
| 3 | 查数据库 tenants 表 | 自动创建了 tenant |

### AUTH-01b：重复邮箱注册
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 注册用户 A | 201 |
| 2 | 用相同邮箱再次注册 | 400 或 422, 提示邮箱已存在 |

### AUTH-01c：密码强度验证
| 测试项 | 输入密码 | 期望 |
|--------|---------|------|
| 太短（7位） | `Abc1234` | 422, "密码至少8个字符" |
| 无字母 | `12345678` | 422, "必须包含字母" |
| 无数字 | `abcdefgh` | 422, "必须包含数字" |
| 空密码 | `""` | 422 |
| 仅空格 | `"        "` | 422 |
| 合法密码 | `ValidPass1` | 201 |

### AUTH-01d：邮箱格式验证
| 输入 | 期望 |
|------|------|
| `"not-an-email"` | 422 |
| `"missing-at.com"` | 422 |
| `"@domain.com"` | 422 |
| `"valid@test.com"` | 通过验证 |

### AUTH-02：JWT 登录
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 注册并登录 | 200 |
| 2 | 检查响应体 | 包含 `access_token` 和 `refresh_token` |
| 3 | 解码 access_token (base64) | payload 包含 `sub`, `email`, `tenant_id`, `role`, `type=access`, `exp` |
| 4 | 检查 token_type | `"bearer"` |

### AUTH-03：bcrypt 密码哈希（cost factor 12）
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 注册用户 | 201 |
| 2 | `SELECT password_hash FROM users WHERE email = '...'` | 值以 `$2b$12$` 开头 |
| 3 | 日志中不出现明文密码 | 搜索日志文件无明文密码 |

### AUTH-04：Token 刷新与轮换
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 登录获取 rt1 (refresh_token_1) | 成功 |
| 2 | `POST /api/v1/auth/refresh` `{refresh_token: rt1}` | 200, 返回 at2 + rt2 |
| 3 | rt2 != rt1 | 新 refresh_token 不同 |
| 4 | 用 rt1 再次刷新 | 失败（旧 token 已失效） |
| 5 | 用 rt2 刷新 | 200 |
| 6 | 用 at2 访问受保护端点 | 200 |

### AUTH-05：CORS 跨域配置
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | `curl -H "Origin: http://localhost:5173" -I $BASE/datasources` | 响应头包含 `Access-Control-Allow-Origin: http://localhost:5173` |
| 2 | 检查响应头 | 包含 `Access-Control-Allow-Credentials: true` |
| 3 | 用非白名单 origin 请求 | 不应返回 CORS 头（或拒绝） |

### AUTH-06：密码重置
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | `POST /api/v1/auth/forgot-password` `{email: "存在的邮箱"}` | 200（不暴露邮箱是否存在） |
| 2 | `POST /api/v1/auth/forgot-password` `{email: "不存在的邮箱"}` | 200（相同响应，防止枚举） |
| 3 | 密码重置 token 使用 itsdangerous 签名 | 代码审查确认 |
| 4 | Token 30 分钟过期 | 代码审查确认 `max_age=1800` |

### AUTH-07：登录失败锁定（5次 → 15分钟）
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 注册用户 | 201 |
| 2 | 用错误密码登录第 1-4 次 | 401, `INVALID_CREDENTIALS` |
| 3 | 第 5 次错误登录 | 401 |
| 4 | 第 6 次错误登录 | 429/401, `ACCOUNT_LOCKED`, 提示"15分钟后重试" |
| 5 | 等待锁定期过后用正确密码登录 | 200, 锁定清除 |
| 6 | 用正确密码在锁定期内登录 | 应被拒绝（仍锁定） |

### AUTH-08：邮箱验证
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 注册新用户 | is_verified=false |
| 2 | 邮箱验证 token 使用 itsdangerous 签名 | 代码审查确认 |
| 3 | Token 24 小时过期 | 代码审查确认 `max_age=86400` |
| 4 | 未验证用户可登录 | 200（Phase 1 不限制未验证用户登录） |

### TENANT-02：租户独立数据源
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 用户 A 登录并创建数据源 DS-A | 201 |
| 2 | 用户 B 登录并创建数据源 DS-B | 201 |
| 3 | 用户 A 列出数据源 | 只看到 DS-A |
| 4 | 用户 B 列出数据源 | 只看到 DS-B |
| 5 | 用户 A 尝试访问 DS-B 的 ID | 404 或 403（无权访问） |

---

## 1.2 数据源管理（DS-01, DS-05, DS-06, DS-07, DSO-01~03, DSO-09）

### DS-01：连接 MySQL
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | `POST /api/v1/datasources`（合法 MySQL 配置） | 201 |
| 2 | 响应中不含 username/password 明文 | 检查响应体字段 |
| 3 | 响应中不含 username_encrypted/password_encrypted | 检查响应体字段 |

### DS-05：连接测试
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 创建数据源 | 201 |
| 2 | `POST /api/v1/datasources/{id}/test` | 无 MySQL 时 400 `CONNECTION_FAILED`；有 MySQL 时 200 `{"success": true}` |
| 3 | 连接测试使用临时引擎 | 代码审查：不占用连接池 |
| 4 | 连接测试使用只读模式 | 代码审查：`read_only=on` 或等效设置 |

### DS-06：自动扫描表结构
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 创建数据源 | 201 |
| 2 | `POST /api/v1/datasources/{id}/scan` | 有 MySQL 时 200，返回表结构信息 |
| 3 | 扫描结果包含 tables, columns, relationships | 检查返回的 JSON 结构 |
| 4 | 扫描查询 INFORMATION_SCHEMA.TABLES | 代码审查 |
| 5 | 扫描查询 INFORMATION_SCHEMA.COLUMNS | 代码审查 |
| 6 | 扫描查询 INFORMATION_SCHEMA.KEY_COLUMN_USAGE（外键） | 代码审查 |
| 7 | 扫描前执行连接测试 | 代码审查：先 test 再 scan |

### DS-07：数据源编辑与删除
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 创建数据源，记下 ID | 201 |
| 2 | `PUT /api/v1/datasources/{id}`（修改名称） | 200, 名称已更新 |
| 3 | `PUT /api/v1/datasources/{id}`（部分更新，只传 name） | 200, 其他字段不变 |
| 4 | `DELETE /api/v1/datasources/{id}` | 204 |
| 5 | 查数据库 | 记录已删除 |
| 6 | 再次 GET 该 ID | 404 |
| 7 | 删除不存在的 ID | 404 |

### DSO-01：连接池管理
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 代码审查 connection_pool.py | pool_size=5, max_overflow=10, pool_pre_ping=True |
| 2 | 创建数据源 | 连接池已初始化 |
| 3 | 删除数据源 | 连接池已关闭 |
| 4 | 每个数据源独立连接池 | 代码审查：不同 DS ID 有不同 engine |

### DSO-02：健康检查
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | `GET /api/v1/datasources/{id}/health` | 200, 返回 `{"healthy": bool, "error": str\|null}` |
| 2 | 健康检查后查数据库 | last_health_check 已更新 |
| 3 | 健康检查失败时 | is_active=false, error 有值 |
| 4 | 健康检查成功时 | is_active=true |

### DSO-03：元数据手动同步
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | `POST /api/v1/datasources/{id}/scan` | 与 DS-06 相同，重复执行应覆盖旧数据 |

### DSO-09：连接参数加密存储
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 创建数据源 | 201 |
| 2 | `SELECT username_encrypted FROM data_sources` | 以 `gAAA` 开头（Fernet 密文） |
| 3 | `SELECT password_encrypted FROM data_sources` | 以 `gAAA` 开头 |
| 4 | 代码审查 encryption.py | 使用 Fernet + SHA256 派生密钥 |
| 5 | 加密密钥来自环境变量 | 代码审查：`settings.data_source_encryption_key` |

---

## 1.3 语义层（META-01）

### META-01：元数据 JSON Schema 存储
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 扫描数据源（有 MySQL 时） | 200 |
| 2 | 查 metadata_configs 表 | 有记录，config 字段为 JSON |
| 3 | JSON 包含 models（表列表） | 检查 JSON 结构 |
| 4 | 每个 model 包含 name, table, columns | 检查 JSON 结构 |
| 5 | 每个 column 包含 name, type, nullable | 检查 JSON 结构 |

---

## 1.4 Text-to-SQL（SQL-01, SQL-02, SQL-07, SQL-08）

### SQL-01：自然语言 → SQL 生成
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | `POST /api/v1/query` `{question: "查询所有用户", datasource_id: "..."}` | 200 |
| 2 | intent = "DataQuery" | 检查响应 |
| 3 | LLM 被调用（非问候语时） | 代码审查：generation.py 调用 LLM |
| 4 | SQL 生成后执行 | 代码审查：执行节点在生成之后 |

### SQL-02：意图识别分流
| 输入 | 期望意图 | 期望行为 |
|------|---------|---------|
| "你好" | Other | 不生成 SQL，返回友好提示 |
| "谢谢" | Other | 不生成 SQL |
| "查询所有用户" | DataQuery | 走 SQL 生成流程 |
| "销售额统计" | DataQuery | 走 SQL 生成流程 |
| "" (空) | 422 拦截 | 不到意图分类节点 |

### SQL-07：查询超时保护
| 检查项 | 期望 |
|--------|------|
| 查询管线整体超时 | 35 秒 (`asyncio.wait_for(..., timeout=35.0)`) |
| SQL 执行超时 | 30 秒 (`asyncio.timeout(30)`) |
| 超时后返回 | `success=false, error="查询超时"`, 不卡死 |

### SQL-08：结果行数限制
| 检查项 | 期望 |
|--------|------|
| MAX_ROWS 常量 | 1000 |
| 超过 1000 行时 | 截断到 1000 行, `truncated=true` |
| 代码审查 execution.py | `if len(rows) > MAX_ROWS: rows = rows[:MAX_ROWS]` |

---

## 1.5 图表（CHART-02）

### CHART-02：表格展示
| 检查项 | 期望 |
|--------|------|
| 前端 ChatView 查询结果 | 数据以 el-table 展示 |
| 查询无结果时 | 显示空状态 |

---

## 1.6 安全（SEC-02）

### SEC-02：数据库只读账号
| 检查项 | 期望 |
|--------|------|
| 连接测试时 | 设置 read_only 模式 |
| 执行 SQL 时 | 代码审查有 `SET SESSION TRANSACTION READ ONLY` |

---

## 1.7 用户体验（UX-11）

### UX-11：空状态设计
| 检查项 | 期望 |
|--------|------|
| 前端无数据源时 | ChatView 显示"请先选择数据源"提示 |
| 前端无消息时 | 显示空状态 + 示例问题按钮 |
| 查询返回空结果时 | 友好提示（非空白页面） |

---

## 1.8 API（API-01, API-03）

### API-01：RESTful API
| 端点 | 方法 | 认证 | 状态码 |
|------|------|------|--------|
| /api/v1/auth/register | POST | 无 | 201 |
| /api/v1/auth/login | POST | 无 | 200 |
| /api/v1/auth/refresh | POST | 无 | 200 |
| /api/v1/auth/forgot-password | POST | 无 | 200 |
| /api/v1/auth/reset-password/confirm | POST | 无 | 200 |
| /api/v1/auth/verify-email | POST | 无 | 200 |
| /api/v1/datasources | GET | 需要 | 200 |
| /api/v1/datasources | POST | 需要 | 201 |
| /api/v1/datasources/{id} | PUT | 需要 | 200 |
| /api/v1/datasources/{id} | DELETE | 需要 | 204 |
| /api/v1/datasources/{id}/test | POST | 需要 | 200/400 |
| /api/v1/datasources/{id}/scan | POST | 需要 | 200/400 |
| /api/v1/datasources/{id}/health | GET | 需要 | 200 |
| /api/v1/query | POST | 需要 | 200/404 |
| /health | GET | 无 | 200 |

### API-03：标准错误响应格式
| 检查项 | 期望 |
|--------|------|
| 401 错误 | `{"code": "UNAUTHORIZED"|"INVALID_TOKEN", "message": "...", "details": null}` |
| 404 错误 | `{"code": "NOT_FOUND"|"DATASOURCE_NOT_FOUND", "message": "...", "details": null}` |
| 400 错误 | `{"code": "CONNECTION_FAILED", "message": "...", "details": null}` |
| 422 错误 | FastAPI 标准验证错误格式，含 `loc`, `msg`, `type` |

---

# 维度二：设计/架构测试

## 2.1 LangGraph 工作流验证

### ARCH-01：Graph 节点完整性
| 检查项 | 期望 |
|--------|------|
| graph.py 存在 | `backend/app/ai/graph.py` |
| 节点：classify_intent | 存在 |
| 节点：generate_sql | 存在（DataQuery 分支） |
| 节点：execute_sql | 存在 |
| 节点：misleading | 存在（Other 分支） |
| 条件路由 | route_by_intent 根据 intent 分流 |
| Entry point | classify_intent |
| END 节点 | misleading → END, execute_sql → END |

### ARCH-02：QueryState 状态定义
| 字段 | 期望 |
|------|------|
| question | str, 必填 |
| datasource_id | str, 必填 |
| schema_context | str |
| intent | str |
| sql | str |
| columns | list[str] |
| rows | list[dict] |
| success | bool |
| error | str |

### ARCH-03：异步架构
| 检查项 | 期望 |
|--------|------|
| 所有 LLM 调用 | 使用 `await` 异步调用 |
| SQL 执行 | 使用 `async with` 连接 |
| 查询端点 | `async def` |
| 无阻塞同步调用 | 代码审查：无 `requests.get()` 等同步调用 |

## 2.2 分层架构验证

### ARCH-04：目录结构符合规划
| 目录 | 应包含 |
|------|--------|
| `backend/app/api/` | auth.py, datasource.py, query.py |
| `backend/app/core/` | config.py, security.py, encryption.py, logging.py |
| `backend/app/db/` | base.py, session.py, models.py, types.py |
| `backend/app/schemas/` | auth.py, datasource.py, query.py |
| `backend/app/services/` | datasource_service.py, connection_pool.py, email_service.py, login_lock_service.py, mysql_schema_scanner.py |
| `backend/app/ai/` | graph.py, client.py, nodes/, prompts/ |
| `frontend/src/views/` | LoginView.vue, RegisterView.vue, ResetPasswordView.vue, ChatView.vue, DataSourceListView.vue |
| `frontend/src/stores/` | authStore.ts, chatStore.ts, datasourceStore.ts |
| `frontend/src/api/` | index.ts |
| `frontend/src/router/` | index.ts |

### ARCH-05：依赖注入模式
| 检查项 | 期望 |
|--------|------|
| get_db | FastAPI Depends, 返回 AsyncSession |
| get_current_user | FastAPI Depends, 返回 dict with tenant_id |
| 所有受保护端点 | 使用 `user=Depends(get_current_user)` |
| 所有 DB 操作端点 | 使用 `db: AsyncSession = Depends(get_db)` |

## 2.3 数据模型验证

### ARCH-06：Model 完整性
| 模型 | 必填字段 | 可选字段 |
|------|---------|---------|
| Tenant | id, name | created_at, updated_at |
| User | id, tenant_id, email, password_hash | is_active, is_verified, role, created_at, updated_at |
| DataSource | id, tenant_id, name, db_type, host, port, database_name, username_encrypted, password_encrypted | is_active, last_health_check, health_check_error, created_at, updated_at |
| MetadataConfig | id, tenant_id, datasource_id, config | created_at, updated_at |
| AuditLog | id, tenant_id, user_id, action, resource_type | resource_id, details, created_at |
| SavedQuery | id, tenant_id, user_id, name, query_text, generated_sql, datasource_id | created_at |

### ARCH-07：租户隔离
| 模型 | tenant_id 字段 | index |
|------|---------------|-------|
| User | 是 | 是 |
| DataSource | 是 | 是 |
| MetadataConfig | 是 | 是 |
| AuditLog | 是 | 是 |
| SavedQuery | 是 | 是 |

---

# 维度三：交互/UX 测试

## 3.1 路由与导航

### UX-NAV-01：路由守卫
| 场景 | 操作 | 期望 |
|------|------|------|
| 未认证访问 / | 清除 localStorage, 访问 `/` | 跳转 `/login?redirect=/` |
| 未认证访问 /datasources | 清除 localStorage, 访问 `/datasources` | 跳转 `/login?redirect=/datasources` |
| 已认证访问 /login | 登录后访问 `/login` | 跳转 `/` |
| 已认证访问 /register | 登录后访问 `/register` | 跳转 `/` |

### UX-NAV-02：页面存在性
| 路径 | 期望内容 |
|------|---------|
| `/login` | 标题"ChatBI"、"智能数据查询平台"、邮箱输入、密码输入、登录按钮、注册链接、忘记密码链接 |
| `/register` | 标题"注册账号"、邮箱输入、密码输入、确认密码输入、注册按钮、返回登录链接 |
| `/reset-password` | 标题"重置密码"、邮箱输入、发送按钮、返回登录链接 |
| `/` (ChatView) | 数据源下拉、管理数据源按钮、消息区域、输入框、发送按钮 |
| `/datasources` | 标题"数据源管理"、添加按钮、数据源列表表格 |

## 3.2 表单交互

### UX-FORM-01：登录表单
| 检查项 | 期望 |
|--------|------|
| 邮箱为空时提交 | 前端验证提示"请输入邮箱" |
| 密码为空时提交 | 前端验证提示"请输入密码" |
| 邮箱格式错误时 | 前端验证提示"邮箱格式不正确" |
| 登录中 | 按钮显示 loading 状态 |
| 登录成功 | 跳转首页，显示成功消息 |
| 登录失败 | 显示错误消息（中文） |

### UX-FORM-02：注册表单
| 检查项 | 期望 |
|--------|------|
| 密码与确认密码不一致 | 前端验证提示"两次密码不一致" |
| 密码少于 8 位 | 前端验证提示"至少8个字符" |
| 注册中 | 按钮显示 loading |
| 注册成功 | 跳转登录页 |

### UX-FORM-03：数据源表单
| 检查项 | 期望 |
|--------|------|
| 名称为空 | 提示必填 |
| 主机为空 | 提示必填 |
| 数据库名为空 | 提示必填 |
| 创建中 | 按钮显示 loading |
| 创建成功 | 对话框关闭，列表刷新 |

## 3.3 对话交互

### UX-CHAT-01：对话流程
| 检查项 | 期望 |
|--------|------|
| 未选择数据源时 | 输入框禁用或提示"请先选择数据源" |
| 选择数据源后 | 输入框可用 |
| 发送消息 | 用户消息显示在右侧（蓝色气泡） |
| 等待回复时 | 显示 loading 指示器 |
| 回复成功 | 助手消息显示在左侧 |
| 有 SQL 结果时 | SQL 以代码块展示，带复制按钮 |
| 有数据结果时 | 数据以表格展示，显示行数 |
| 查询失败时 | 错误消息以红色显示 |

### UX-CHAT-02：空状态
| 场景 | 期望 |
|------|------|
| 无消息时 | 显示空状态 + 示例问题按钮 |
| 示例问题点击 | 自动填入并发送 |
| 无数据源时 | 提示"请先添加数据源" + 跳转链接 |

### UX-CHAT-03：消息顺序
| 检查项 | 期望 |
|--------|------|
| 多条消息 | 按时间顺序从上到下排列 |
| 新消息自动滚动 | 最新消息在底部可见 |
| 用户消息 vs 助手消息 | 视觉区分明显（颜色/位置） |

## 3.4 响应式与可用性

### UX-RESP-01：基础响应式
| 检查项 | 期望 |
|--------|------|
| 窗口宽度 768px | 页面正常显示，无水平滚动 |
| 窗口宽度 375px (手机) | 登录卡片正常显示，不被截断 |
| 表格列过多时 | 可横向滚动 |
| 输入框 | 在窄屏下不被挤压 |

---

# 维度四：安全测试

## 4.1 认证安全

### SEC-AUTH-01：密码安全
| 检查项 | 期望 |
|--------|------|
| 存储格式 | bcrypt hash（`$2b$12$...`） |
| 成本因子 | 12 |
| 日志中 | 永不出现明文密码 |
| API 响应中 | 永不返回 password_hash |
| 传输 | HTTPS 下（生产环境），开发环境 HTTP |

### SEC-AUTH-02：JWT 安全
| 检查项 | 期望 |
|--------|------|
| 签名算法 | HS256 |
| 签名密钥 | 来自环境变量 `secret_key` |
| Access Token 过期 | 15 分钟 |
| Refresh Token 过期 | 7 天 |
| Refresh Token 包含 jti | 是（唯一标识） |
| 过期 Token 无法使用 | 401 |
| 篡改 Token 签名 | 401 |
| Token 中携带 tenant_id | 是 |
| Token 中携带 type 字段 | "access" 或 "refresh" |

### SEC-AUTH-03：密码重置安全
| 检查项 | 期望 |
|--------|------|
| 不存在的邮箱 | 返回 200（不暴露邮箱是否存在） |
| 存在的邮箱 | 返回 200（相同响应） |
| Reset token 单次使用 | 使用后失效 |
| Reset token 过期 | 30 分钟 |
| 重置密码后 | 所有旧 refresh token 失效 |

### SEC-AUTH-04：登录保护
| 检查项 | 期望 |
|--------|------|
| 失败阈值 | 5 次 |
| 锁定时长 | 15 分钟 |
| 锁定期内 | 即使密码正确也拒绝登录 |
| 锁定过期后 | 自动解锁 |
| 成功登录后 | 清除失败计数 |

## 4.2 数据安全

### SEC-DATA-01：凭证加密
| 检查项 | 期望 |
|--------|------|
| 加密算法 | Fernet (AES-128-CBC) |
| 密钥派生 | SHA256 从环境变量派生 |
| 存储格式 | base64 url-safe |
| API 响应中 | 不返回加密密码 |
| 日志中 | 密码脱敏为 `***` |

### SEC-DATA-02：敏感字段脱敏
| 检查项 | 期望 |
|--------|------|
| DataSourceResponse schema | 不包含 username, password, username_encrypted, password_encrypted |
| 日志中邮箱 | 掩码显示（如 `t***t@chatbi.com`） |
| 日志中 JWT | 最多显示前 8 字符 |

### SEC-DATA-03：SQL 注入防护
| 测试项 | 操作 | 期望 |
|--------|------|------|
| AST 拒绝 DROP | `validate_sql("DROP TABLE users")` | False |
| AST 拒绝 DELETE | `validate_sql("DELETE FROM users")` | False |
| AST 拒绝 INSERT | `validate_sql("INSERT INTO t VALUES(1)")` | False |
| AST 拒绝 UPDATE | `validate_sql("UPDATE t SET x=1")` | False |
| AST 拒绝 ALTER | `validate_sql("ALTER TABLE t ADD x INT")` | False |
| AST 拒绝 TRUNCATE | `validate_sql("TRUNCATE TABLE t")` | False |
| AST 拒绝多语句 | `validate_sql("SELECT 1; DROP TABLE t")` | False |
| AST 允许 SELECT | `validate_sql("SELECT 1")` | True |
| AST 允许复杂 SELECT | `validate_sql("SELECT COUNT(*) FROM t GROUP BY x HAVING COUNT(*) > 1")` | True |
| AST 允许注释 | `validate_sql("SELECT 1 -- comment")` | True |
| AST 拒绝空 SQL | `validate_sql("")` | False |
| AST 拒绝语法错误 | `validate_sql("SELEC *")` | False |

### SEC-DATA-04：Prompt Injection 防护
| 检查项 | 期望 |
|--------|------|
| System Prompt 角色定义 | 存在，明确说明"仅生成 SELECT" |
| 用户输入不覆盖 system prompt | 代码审查：用户输入只在 human message 中 |
| 数据库只读模式 | 执行时设置 read_only |

## 4.3 多租户安全

### SEC-TENANT-01：租户隔离
| 检查项 | 期望 |
|--------|------|
| 数据源列表 | 按 tenant_id 过滤 |
| 数据源创建 | 自动绑定 user 的 tenant_id |
| 数据源更新 | 只能更新自己租户的数据源 |
| 数据源删除 | 只能删除自己租户的数据源 |
| 查询执行 | 只能使用自己租户的数据源 |
| 跨租户访问 | 返回 404（不暴露资源存在） |

## 4.4 CORS 安全

### SEC-CORS-01：CORS 配置
| 检查项 | 期望 |
|--------|------|
| 开发环境 | `http://localhost:5173` |
| allow_credentials | True |
| allow_methods | `["*"]` |
| allow_headers | `["*"]` |
| 无通配符 `*` origin | origins 为具体域名列表 |
| 生产可配置 | 通过 `cors_origins` 环境变量 |

---

# 维度五：性能测试

## 5.1 响应时间

### PERF-01：健康检查
| 检查项 | 期望 |
|--------|------|
| `GET /health` 响应时间 | < 100ms |
| 响应内容 | `{"status": "ok", "env": "development"}` |

### PERF-02：认证端点
| 端点 | 期望响应时间 |
|------|------------|
| `POST /api/v1/auth/login`（正确密码） | < 1s（bcrypt cost 12） |
| `POST /api/v1/auth/login`（错误密码） | < 1s |
| `POST /api/v1/auth/refresh` | < 500ms |
| `POST /api/v1/auth/register` | < 2s（含 email 发送） |

### PERF-03：数据源端点
| 端点 | 期望响应时间 |
|------|------------|
| `POST /api/v1/datasources`（创建） | < 1s |
| `GET /api/v1/datasources`（列表） | < 500ms |
| `DELETE /api/v1/datasources/{id}` | < 500ms |

### PERF-04：查询端点
| 检查项 | 期望 |
|--------|------|
| 问候语查询（无 LLM） | < 2s |
| LLM 查询 | < 30s（超时保护） |
| 整体超时 | < 35s |

## 5.2 连接池

### PERF-05：连接池配置
| 检查项 | 期望 |
|--------|------|
| pool_size | 5 |
| max_overflow | 10 |
| pool_pre_ping | True |
| 多数据源不共享连接池 | 每个 DS ID 独立 engine |
| 删除数据源时关闭连接池 | pool_manager.close_pool(ds_id) |

---

# 维度六：E2E 全流程测试

## 6.1 完整用户旅程

### E2E-01：注册 → 登录 → 添加数据源 → 提问
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 注册新账号 | 201, 自动创建 tenant |
| 2 | 登录 | 200, 获取 token |
| 3 | 访问首页（/） | 200, 页面加载 |
| 4 | 访问数据源管理页 | 200, 空列表 |
| 5 | 添加 MySQL 数据源 | 201 |
| 6 | 返回首页，选择数据源 | 下拉中可见新数据源 |
| 7 | 发送问候语 "你好" | 返回友好提示 |
| 8 | 发送数据查询 "查询所有表" | 走 LLM 流程（有 MySQL 时执行） |

### E2E-02：错误恢复流程
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 注册并登录 | 200 |
| 2 | 创建无效数据源（错误主机） | 201 |
| 3 | 测试连接 | 400, CONNECTION_FAILED |
| 4 | 尝试查询 | LLM 调用失败或连接失败，返回错误信息 |
| 5 | 删除数据源 | 204 |

### E2E-03：密码重置流程
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 注册用户 | 201 |
| 2 | 请求密码重置 | 200 |
| 3 | 使用旧密码登录 | 200（密码未实际重置，因为没有 token） |
| 4 | 登录失败 5 次 | 账号锁定 |
| 5 | 锁定期内尝试登录 | 429/401, ACCOUNT_LOCKED |

### E2E-04：Token 生命周期
| 步骤 | 操作 | 期望 |
|------|------|------|
| 1 | 登录获取 at1 + rt1 | 200 |
| 2 | 用 at1 访问受保护端点 | 200 |
| 3 | 用 rt1 刷新 | 200, 返回 at2 + rt2 |
| 4 | 用 at1 再次访问 | 取决于是否过期（15min） |
| 5 | 用 rt1 再次刷新 | 失败（已轮换） |
| 6 | 用 at2 访问 | 200 |
| 7 | 用 rt2 刷新 | 200, 返回 at3 + rt3 |

---

# 维度七：代码验收测试

## 7.1 Phase 01-01 验收

| 检查项 | 命令 | 期望值 |
|--------|------|--------|
| main.py 存在 | `grep "def create_app" backend/app/main.py` | 匹配 |
| CORS 配置 | `grep "CORSMiddleware" backend/app/main.py` | 匹配 |
| 健康检查路由 | `grep "@app.get" backend/app/main.py` | 匹配 |
| config.py 绝对路径 | `grep "BASE_DIR" backend/app/core/config.py` | 匹配 |
| 6 个模型 | `grep "class.*Base" backend/app/db/models.py` | Tenant, User, DataSource, MetadataConfig, AuditLog, SavedQuery |
| GUID 类型 | `grep "GUID" backend/app/db/types.py` | 匹配 |
| async session | `grep "async_session_factory" backend/app/db/session.py` | 匹配 |
| Alembic 配置 | `ls backend/alembic/env.py` | 文件存在 |

## 7.2 Phase 01-02 验收

| 检查项 | 命令 | 期望值 |
|--------|------|--------|
| hash_password | `grep -c "def hash_password" backend/app/core/security.py` | >= 1 |
| verify_password | `grep -c "def verify_password" backend/app/core/security.py` | >= 1 |
| create_access_token | `grep -c "def create_access_token" backend/app/core/security.py` | >= 1 |
| create_refresh_token | `grep -c "def create_refresh_token" backend/app/core/security.py` | >= 1 |
| bcrypt rounds | `grep "bcrypt_rounds" backend/app/core/security.py` | 匹配 |
| itsdangerous 使用 | `grep -c "itsdangerous" backend/app/core/security.py` | >= 2 |
| auth 路由注册 | `grep "auth" backend/app/main.py` | 匹配 |
| 登录锁定服务 | `grep -c "def check_lock" backend/app/services/login_lock_service.py` | >= 1 |
| MAX_ATTEMPTS=5 | `grep "MAX_ATTEMPTS" backend/app/services/login_lock_service.py` | 值 5 |
| LOCK_DURATION=900 | `grep "LOCK_DURATION" backend/app/services/login_lock_service.py` | 值 900 |
| 邮箱服务 | `grep -c "send_verification_email" backend/app/services/email_service.py` | >= 1 |
| 密码验证 schema | `grep -c "def validate_password" backend/app/schemas/auth.py` | >= 1 |
| password 验证规则 | `grep "8" backend/app/schemas/auth.py` | >= 1（8 字符最小值） |

## 7.3 Phase 01-03 验收

| 检查项 | 命令 | 期望值 |
|--------|------|--------|
| DataSourceService | `grep "class DataSourceService" backend/app/services/datasource_service.py` | 匹配 |
| ConnectionPoolManager | `grep "class ConnectionPoolManager" backend/app/services/connection_pool.py` | 匹配 |
| MySQL 扫描器 | `grep "async def scan_mysql_schema" backend/app/services/mysql_schema_scanner.py` | 匹配 |
| Fernet 加密 | `grep "Fernet" backend/app/core/encryption.py` | 匹配 |
| encrypt_value | `grep "def encrypt_value" backend/app/core/encryption.py` | 匹配 |
| decrypt_value | `grep "def decrypt_value" backend/app/core/encryption.py` | 匹配 |
| datasource 路由 | `grep "datasource" backend/app/main.py` | 匹配 |

## 7.4 Phase 01-04 验收

| 检查项 | 命令 | 期望值 |
|--------|------|--------|
| LangGraph graph | `grep "StateGraph" backend/app/ai/graph.py` | 匹配 |
| QueryState | `grep "QueryState" backend/app/ai/graph.py` | 匹配 |
| 意图分类节点 | `grep "classify_intent" backend/app/ai/graph.py` | 匹配 |
| SQL 生成节点 | `grep "generate_sql" backend/app/ai/graph.py` | 匹配 |
| 执行节点 | `grep "execute_sql" backend/app/ai/graph.py` | 匹配 |
| LLM 调用 | `grep "ChatOpenAI" backend/app/ai/nodes/generation.py` | 匹配 |
| SQLGlot 验证 | `grep "sqlglot" backend/app/ai/nodes/execution.py` | 匹配 |
| 30s 超时 | `grep "timeout(30)" backend/app/ai/nodes/execution.py` | 匹配 |
| 1000 行限制 | `grep "MAX_ROWS" backend/app/ai/nodes/execution.py` | 值 1000 |
| query 路由注册 | `grep "query" backend/app/main.py` | 匹配 |
| QueryRequest schema | `grep "class QueryRequest" backend/app/schemas/query.py` | 匹配 |
| QueryResponse schema | `grep "class QueryResponse" backend/app/schemas/query.py` | 匹配 |

## 7.5 Phase 01-05 验收

| 检查项 | 命令 | 期望值 |
|--------|------|--------|
| Vue3 项目 | `ls frontend/src/main.ts` | 文件存在 |
| Element Plus | `grep "element-plus" frontend/src/main.ts` | 匹配 |
| Pinia | `grep "createPinia" frontend/src/main.ts` | 匹配 |
| Vue Router | `grep "createRouter" frontend/src/router/index.ts` | 匹配 |
| 路由守卫 | `grep "beforeEach" frontend/src/router/index.ts` | 匹配 |
| authStore | `grep "defineStore" frontend/src/stores/authStore.ts` | 匹配 |
| chatStore | `grep "defineStore" frontend/src/stores/chatStore.ts` | 匹配 |
| datasourceStore | `grep "defineStore" frontend/src/stores/datasourceStore.ts` | 匹配 |
| LoginView | `ls frontend/src/views/LoginView.vue` | 文件存在 |
| RegisterView | `ls frontend/src/views/RegisterView.vue` | 文件存在 |
| ChatView | `ls frontend/src/views/ChatView.vue` | 文件存在 |
| DataSourceListView | `ls frontend/src/views/DataSourceListView.vue` | 文件存在 |
| Axios 实例 | `grep "axios.create" frontend/src/api/index.ts` | 匹配 |
| Token 拦截器 | `grep "Authorization" frontend/src/api/index.ts` | 匹配 |
| TypeScript 编译 | `cd frontend && npx vue-tsc --noEmit` | 无错误 |
| 构建成功 | `cd frontend && npx vite build` | 输出 dist/ |

---

## 日志策略验证

### LOG-01：结构化日志
| 检查项 | 期望 |
|--------|------|
| logging.py 存在 | `backend/app/core/logging.py` |
| JSON 格式 | 日志输出为 JSON 格式 |
| 敏感数据脱敏 | 密码不记录，邮箱掩码 |
| get_logger 函数 | 可被各模块调用 |

### LOG-02：关键事件日志
| 事件 | 日志级别 | 期望 |
|------|---------|------|
| 用户注册 | INFO | 记录邮箱（掩码） |
| 用户登录成功 | INFO | 记录邮箱（掩码） |
| 用户登录失败 | WARNING | 记录邮箱（掩码） |
| 数据源创建 | INFO | 记录租户 ID |
| 查询提交 | INFO | 记录问题（不含 SQL） |
| SQL 生成 | DEBUG | 记录 SQL（前 200 字符） |
| 查询失败 | ERROR | 记录错误信息 |

---

## 测试执行总结

> 更新时间：2026-05-02
> 执行方式：pytest 自动化集成测试 + 单元测试

### 自动化测试结果

| 文件 | 模块 | 用例数 | 通过 | 失败 | 状态 |
|------|------|--------|------|------|------|
| test_auth.py | 认证与多租户 | 11 | 11 | 0 | ✅ |
| test_datasource_query.py | 数据源+查询+SQL注入 | 9 | 9 | 0 | ✅ |
| test_unit.py | SQL验证+登录锁定单元 | 21 | 21 | 0 | ✅ |
| **总计** | | **41** | **41** | **0** | **✅ 全通过** |

### 已修复的 Bug

| Bug | 根因 | 修复 |
|-----|------|------|
| TENANT-02 租户隔离失败 | auth.py 复用第一个已存在的 tenant，所有用户共享 | 改为每个用户注册时创建独立 tenant |
| AUTH-07 登录锁定不生效 | check_lock 对 locked_until=None 的条目误删，导致失败计数每次归零 | check_lock 只在 locked_until 已设置且过期时才删除 |
| 前端 ChatView loading 错误 | Pinia ref 在非 setup 上下文中用了 .value | 直接使用 chatStore.loading |
| SQLAlchemy 2.0 text() 缺失 | connection_pool.py 中 SELECT 1 未用 text() 包裹 | 添加 text() 包裹 |
| datasource.py 重复赋值 | last_health_check 被赋值两次 | 删除重复行 |

### 各维度覆盖状态

| 维度 | 模块 | 用例数 | 自动化 | 需手动 |
|------|------|--------|--------|--------|
| 需求覆盖 | 认证与多租户 | 15 | 6 项通过 | 9 项（密码强度/邮箱格式/token轮换/密码重置安全等需手动或 LLM 环境） |
| 需求覆盖 | 数据源管理 | 14 | 5 项通过 | 9 项（需要真实 MySQL 连接） |
| 需求覆盖 | 语义层 | 5 | 0 | 需要真实 MySQL |
| 需求覆盖 | Text-to-SQL | 7 | 3 项通过 | 4 项（需要 LLM API） |
| 需求覆盖 | 图表 | 1 | 0 | 需浏览器 UX 测试 |
| 需求覆盖 | 安全 | 1 | 0 | 需审查 |
| 需求覆盖 | 用户体验 | 1 | 0 | 需浏览器 UX 测试 |
| 需求覆盖 | API | 2 | 2 项通过 | 0 |
| 设计/架构 | LangGraph 工作流 | 3 | 0 | 需代码审查 |
| 设计/架构 | 分层架构 | 2 | 0 | 需代码审查 |
| 设计/架构 | 数据模型 | 2 | 0 | 需代码审查 |
| 交互/UX | 路由与导航 | 2 | 0 | 需浏览器 |
| 交互/UX | 表单交互 | 3 | 0 | 需浏览器 |
| 交互/UX | 对话交互 | 3 | 0 | 需浏览器 |
| 交互/UX | 响应式 | 1 | 0 | 需浏览器 |
| 安全 | 认证安全 | 4 | SQL验证通过 | JWT安全/密码重置安全需审查 |
| 安全 | 数据安全 | 4 | SQL注入通过 | 凭证加密需审查 |
| 安全 | 多租户安全 | 1 | ✅ 通过 | 0 |
| 安全 | CORS 安全 | 1 | 0 | 需审查 |
| 性能 | 响应时间 | 4 | 0 | 需要运行服务器测量 |
| 性能 | 连接池 | 1 | 0 | 需代码审查 |
| E2E | 完整用户旅程 | 4 | 部分通过 | 需要真实 MySQL + LLM |
| 代码验收 | Phase 01-01~05 | 57 | 0 | 需代码审查 |
| 日志 | 结构化日志 | 2 | 0 | 需代码审查 |
| **总计** | | **126** | **41 自动** | **85 手动/需环境** |

---

## 测试环境要求

- Python 3.12+（使用 uv 管理）
- Node.js 18+（前端构建）
- SQLite（本地开发，chatbi.db）
- 可选：MySQL 8.0+（用于连接测试和数据扫描测试）
- 可选：有效的 LLM API Key（用于查询生成测试）

## 测试执行顺序

```
1. 代码验收（Phase 01-01 ~ 01-05）—— 不依赖运行环境
2. 安全单元测试（SQL 验证、密码哈希、加密）—— 本地 Python
3. 需求覆盖测试（启动后端，API 测试）—— 需要后端运行
4. E2E 全流程（完整用户旅程）—— 需要后端运行
5. 交互/UX 测试（启动前端，浏览器测试）—— 需要前后端运行
6. 性能测试（响应时间测量）—— 需要后端运行
```

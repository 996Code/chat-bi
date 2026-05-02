# Phase 1 测试用例

> 测试 agent 按此文档逐项执行，标记 PASS/FAIL。

## 前置条件

```bash
# 1. 启动后端
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
sleep 3
curl -s http://127.0.0.1:8000/health  # 应返回 {"status":"ok"}

# 2. 测试用邮箱（用于注册测试）
TEST_EMAIL="test_$(date +%s)@chatbi.com"
TEST_PASSWORD="TestPass123"
BASE="http://127.0.0.1:8000/api/v1"
```

---

## 模块一：认证模块（6 个用例）

### AUTH-01：正常注册
- **操作**：`POST /api/v1/auth/register` `{email: TEST_EMAIL, password: TEST_PASSWORD}`
- **期望**：HTTP 201，返回 `{"message": "注册成功，请查收验证邮件"}`
- **验证**：数据库 users 表有该用户记录，is_verified=false，tenant 已自动创建

### AUTH-02：正常登录
- **操作**：`POST /api/v1/auth/login` `{email: TEST_EMAIL, password: TEST_PASSWORD}`
- **期望**：HTTP 200，返回 `access_token` + `refresh_token`
- **验证**：access_token JWT 解码后包含 `sub`(用户ID)、`email`、`tenant_id`、`role`、`type=access`

### AUTH-03：错误密码
- **操作**：`POST /api/v1/auth/login` `{email: TEST_EMAIL, password: "WrongPass123"}`
- **期望**：HTTP 401，返回 `{"code": "INVALID_CREDENTIALS"}`

### AUTH-04：弱密码注册
- **操作**：`POST /api/v1/auth/register` `{email: "weak@chatbi.com", password: "123"}`
- **期望**：HTTP 422，返回验证错误（密码至少 8 字符、需包含字母和数字）

### AUTH-05：Token 刷新
- **操作**：
  1. 登录获取 refresh_token
  2. `POST /api/v1/auth/refresh` `{refresh_token: <refresh_token>}`
- **期望**：HTTP 200，返回新的 access_token + refresh_token（与旧的不同）

### AUTH-06：未授权访问
- **操作**：`GET /api/v1/datasources`（不带 Authorization 头）
- **期望**：HTTP 401，返回 `{"code": "UNAUTHORIZED"}`

---

## 模块二：数据源模块（6 个用例）

### DS-01：创建数据源
- **操作**：`POST /api/v1/datasources`（带 token）
  ```json
  {"name": "test-mysql", "type": "mysql", "host": "127.0.0.1", "port": 3306, "database_name": "testdb", "username": "root", "password": "root"}
  ```
- **期望**：HTTP 201，返回数据源信息（不含 username/password）
- **验证**：数据库 data_sources 表有记录，username_encrypted 和 password_encrypted 已加密

### DS-02：列出数据源
- **操作**：`GET /api/v1/datasources`（带 token）
- **期望**：HTTP 200，返回数组，包含刚创建的数据源
- **验证**：租户隔离——不同 tenant 的用户看不到彼此的数据源

### DS-03：空名称创建
- **操作**：`POST /api/v1/datasources`（带 token）
  ```json
  {"name": "", "type": "mysql", "host": "127.0.0.1", "port": 3306, "database_name": "db", "username": "u", "password": "p"}
  ```
- **期望**：HTTP 422，返回验证错误

### DS-04：非 MySQL 类型
- **操作**：`POST /api/v1/datasources` `{"type": "postgresql", ...}`
- **期望**：HTTP 422，返回 "Phase 1 仅支持 MySQL"

### DS-05：删除数据源
- **操作**：
  1. 创建数据源获取 ID
  2. `DELETE /api/v1/datasources/{id}`（带 token）
- **期望**：HTTP 204
- **验证**：数据库记录已删除

### DS-06：连接测试（无 MySQL 实例时）
- **操作**：`POST /api/v1/datasources/{id}/test`（带 token）
- **期望**：HTTP 400（无 MySQL 实例时连接失败），返回 `{"code": "CONNECTION_FAILED", ...}`

---

## 模块三：查询模块（7 个用例）

### Q-01：问候语意图分类
- **前置**：已创建数据源
- **操作**：`POST /api/v1/query` `{"question": "你好", "datasource_id": "<id>"}`
- **期望**：HTTP 200，`success=false`，`intent="Other"`，`error` 包含友好提示

### Q-02：数据查询意图
- **前置**：已创建数据源
- **操作**：`POST /api/v1/query` `{"question": "查询所有用户", "datasource_id": "<id>"}`
- **期望**：HTTP 200，`intent="DataQuery"`
- **注意**：无真实 MySQL 时 `success=false`，error 为连接相关错误

### Q-03：空问题
- **操作**：`POST /api/v1/query` `{"question": "", "datasource_id": "<id>"}`
- **期望**：HTTP 422

### Q-04：不存在的 datasource_id
- **操作**：`POST /api/v1/query` `{"question": "test", "datasource_id": "00000000-0000-0000-0000-000000000000"}`
- **期望**：HTTP 404，`{"code": "DATASOURCE_NOT_FOUND"}`

### Q-05：SQL 安全验证（本地单元测试）
```python
from app.ai.nodes.execution import validate_sql

# 应通过
assert validate_sql("SELECT 1")[0] == True
assert validate_sql("SELECT * FROM users")[0] == True
assert validate_sql("SELECT COUNT(*) FROM orders GROUP BY status")[0] == True

# 应拒绝
assert validate_sql("DROP TABLE users")[0] == False
assert validate_sql("DELETE FROM users")[0] == False
assert validate_sql("INSERT INTO users VALUES(1)")[0] == False
assert validate_sql("UPDATE users SET name='x'")[0] == False
assert validate_sql("ALTER TABLE users ADD x INT")[0] == False
assert validate_sql("TRUNCATE TABLE users")[0] == False
assert validate_sql("SELECT 1; DROP TABLE users")[0] == False
```

### Q-06：超长问题
- **操作**：`POST /api/v1/query` `{"question": "x" * 1001, "datasource_id": "<id>"}`
- **期望**：HTTP 422

### Q-07：超时保护
- **期望**：查询管线整体超时 35 秒（`asyncio.wait_for(..., timeout=35.0)`），SQL 执行超时 30 秒
- **验证方式**：代码审查确认 timeout 设置存在

---

## 模块四：前端功能（5 个用例）

### FE-01：前端构建
- **操作**：`cd frontend && npm install && npx vue-tsc --noEmit && npx vite build`
- **期望**：无错误，输出 dist/ 目录

### FE-02：登录页
- **操作**：启动前端 `npm run dev`，访问 `http://localhost:5173/login`
- **期望**：
  - 页面显示"ChatBI 智能数据查询平台"标题
  - 有邮箱和密码输入框
  - 有"登录"按钮
  - 有"注册新账号"和"忘记密码"链接
  - 未登录时访问 `/` 自动跳转 `/login`

### FE-03：注册页
- **操作**：访问 `http://localhost:5173/register`
- **期望**：
  - 有邮箱、密码、确认密码输入框
  - 密码不一致时显示错误
  - 密码少于 8 位时显示错误
  - 注册成功后跳转登录页

### FE-04：对话页
- **操作**：登录后访问 `http://localhost:5173/`
- **期望**：
  - 顶部有数据源下拉选择
  - 有"管理数据源"按钮
  - 显示空状态和示例问题
  - 有输入框和发送按钮
  - 选择数据源后可发送消息
  - 用户消息靠右，助手消息靠左
  - SQL 结果以代码块展示，数据以表格展示

### FE-05：路由守卫
- **操作**：
  1. 清除 localStorage
  2. 访问 `http://localhost:5173/`
- **期望**：自动跳转 `/login?redirect=/`
- **操作**：已登录时访问 `/login`
- **期望**：自动跳转 `/`

---

## 模块五：安全（4 个用例）

### SEC-01：密码哈希
- **验证**：数据库 users 表 password_hash 字段为 bcrypt 哈希（以 `$2b$12$` 开头）
- **操作**：`SELECT password_hash FROM users WHERE email = '<test_email>'`

### SEC-02：数据源凭证加密
- **验证**：数据库 data_sources 表 username_encrypted 和 password_encrypted 为 Fernet 密文（以 `gAAA` 开头）
- **操作**：`SELECT username_encrypted FROM data_sources WHERE name = 'test-mysql'`

### SEC-03：敏感字段不返回
- **操作**：`POST /api/v1/datasources` 创建数据源
- **期望**：响应中不包含 `username`、`password`、`username_encrypted`、`password_encrypted` 字段

### SEC-04：CORS 配置
- **操作**：从 `http://localhost:5173` 发起请求到 `http://127.0.0.1:8000`
- **期望**：`Access-Control-Allow-Origin: http://localhost:5173` 响应头存在

---

## 测试执行总结

| 模块 | 用例数 | 通过 | 失败 | 跳过 |
|------|--------|------|------|------|
| 认证 | 6 | - | - | - |
| 数据源 | 6 | - | - | - |
| 查询 | 7 | - | - | - |
| 前端 | 5 | - | - | - |
| 安全 | 4 | - | - | - |
| **总计** | **28** | - | - | - |

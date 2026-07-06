# 安全策略

## 🔒 报告安全漏洞

如果你发现了安全漏洞，**请不要在 GitHub Issues 中公开报告**。

请通过以下方式私下报告：

- 发送邮件至项目维护者
- 使用 GitHub 的 [Security Advisories](https://github.com/996Code/chat-bi/security/advisories) 功能

我们会在 48 小时内确认收到报告，并在 7 天内给出初步评估。

---

## 🛡️ 安全特性概览

ChatBI 在设计上贯彻 **Fail-Closed** 原则——安全相关功能出问题时拒绝而非放行。

### SQL 安全（三层校验）

| 层级 | 检查内容 | 示例 |
|------|----------|------|
| Layer 1: AST | 只允许 SELECT/UNION/INTERSECT/EXCEPT | 拦截 INSERT/UPDATE/DELETE/DROP |
| Layer 2: 危险函数 | 阻止已知危险函数 | LOAD_FILE, SLEEP, BENCHMARK, INTO OUTFILE |
| Layer 3: 列白名单 | 所有列引用必须在语义层定义 | 防止探测未授权列 |

自愈修复后的 SQL 也会经过同样的三层校验。

### 认证与授权

- JWT access token + refresh token 双令牌机制
- 角色：admin / user / read_only
- 登录锁定：5 次失败后锁定 30 分钟
- 多租户数据隔离 (TenantMixin)

### 数据加密

- 数据源密码使用 Fernet 对称加密存储
- JWT 签名密钥 + Fernet 密钥均从环境变量读取

### 输入净化

- LLM 输入前 NFKC 标准化
- 零宽字符 / 方向控制字符移除

### 启动安全

- 所有 `CHANGE_ME_*` 占位符在启动时检测
- 关键占位符（SECRET_KEY, LLM_API_KEY 等）未替换 → **拒绝启动**
- 非关键占位符（CORS_ORIGINS 等）未替换 → 降级模式 + WARNING 日志

### 审计

- 全部查询写入 audit_logs（含 SQL、耗时、慢查询标记）
- SQL 注入拦截单独标记 `action=sql_injection_blocked`

---

## ⚠️ 安全注意事项

### 必须做的事

- ✅ 生产环境替换所有 `CHANGE_ME_*` 占位符
- ✅ 使用强随机密钥（SECRET_KEY, FERNET_KEY）
- ✅ 配置 CORS_ORIGINS 为实际域名
- ✅ 数据源使用只读账号
- ✅ LLM API Key 定期轮换

### 绝对不要做的事

- ❌ 不要将 `backend/.env` 提交到 Git（已在 .gitignore）
- ❌ 不要在前端代码中硬编码任何密钥
- ❌ 不要在 DEBUG 模式下运行生产环境（会持久化 Prompt 文本，可能含敏感数据）
- ❌ 不要将数据库暴露到公网
- ❌ 不要使用 root 账号连接业务数据库

---

## 🔧 安全相关配置

| 配置项 | 说明 |
|--------|------|
| `SECRET_KEY` | JWT 签名密钥 (生产必须替换) |
| `FERNET_KEY` | 数据源密码加密密钥 (生产必须替换) |
| `CORS_ORIGINS` | 允许的前端域名 (生产必须替换) |
| `BCRYPT_ROUNDS` | bcrypt 哈希轮数 (默认 12) |
| `LOGIN_MAX_ATTEMPTS` | 登录最大尝试次数 (默认 5) |
| `LOGIN_LOCKOUT_MINUTES` | 锁定时长 (默认 30) |
| `RATE_LIMIT_QUERIES_PER_MINUTE` | 查询限流 (默认 30) |
| `RATE_LIMIT_LOGIN_PER_MINUTE` | 登录限流 (默认 5) |

---

## 📋 安全更新策略

- 安全修复优先处理，尽快发布补丁
- 重大漏洞会通过 GitHub Security Advisory 公告
- 建议订阅 GitHub Watch 以获取安全更新通知

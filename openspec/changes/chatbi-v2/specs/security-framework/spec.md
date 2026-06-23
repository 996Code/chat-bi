# Security Framework

## Status: ADDED

## 概述

对标 Claude Code 安全体系：双防线（权限+沙箱）、安全默认（Fail-Closed）、Unicode 清洗、PII 屏障、Trust 建立时序、降级告警。v2 从"默认安全"原则出发，安全措施失效必须告警。

## 需求

### SEC-001: 安全默认原则（Fail-Closed）

**对标 Claude Code**：buildTool 默认值 — isConcurrencySafe: false, isReadOnly: false

所有操作默认需确认，只有显式声明安全的才自动放行：
- SQL 执行默认需确认（可在设置中开启"自动执行"）
- 图表修改走 ask
- 语义层/数据源配置修改必须 admin 角色
- DROP/ALTER/DELETE/INSERT/TRUNCATE → 直接拒绝

**验收标准**：
- [ ] `DELETE FROM users WHERE 1=1` → 直接拒绝 + 记录审计
- [ ] `SELECT LOAD_FILE('/etc/passwd')` → 直接拒绝 + 记录审计
- [ ] 普通用户无法修改语义层配置

### SEC-002: SQL 三层校验

**对标 Claude Code**：沙箱 + 权限系统双防线 + 危险模式剥离

**第一层**：AST 解析 — 拒绝非 SELECT（UPDATE/DELETE/DROP/ALTER/INSERT/TRUNCATE）
**第二层**：危险函数 — 拒绝 LOAD_FILE/INTO OUTFILE/INTO DUMPFILE/SLEEP/BENCHMARK
**第三层**：白名单列名 — SQL 中的列名必须在语义层定义内

自愈生成的 SQL 也经过相同三层校验（v1 教训：自愈 prompt 无安全约束）。

**验收标准**：
- [ ] 三层校验全部通过才允许执行
- [ ] 自愈生成的 SQL 也经过相同三层校验
- [ ] 拦截记录完整审计日志（user_id + 原始 SQL + 拦截原因 + timestamp）

### SEC-003: 多租户框架级强制

**对标 Claude Code**：Sandbox settings 强制 denyWrite — 不需要每次手动检查

SQLAlchemy session event 自动注入 `WHERE tenant_id = ?`：
- 新增 Model + API 不需要手动加 tenant_id 过滤
- 框架自动处理 — 新增 API 无法"忘加"租户条件
- 全局表（Tenant/SystemConfig）白名单不受隔离

**验收标准**：
- [ ] 租户 A 无法看到租户 B 的数据源、查询历史、语义层
- [ ] 新 API 即使没写 tenant_id 过滤，框架自动注入
- [ ] 跨租户访问尝试 → 审计记录 status=blocked

### SEC-004: 密钥安全

**对标 Claude Code**：Trust 建立时序 — init() 只应用安全 env var

- `.env.example` 中安全字段使用 `CHANGE_ME_<name>` 占位符
- 启动时检测占位符 → 拒绝启动 + 明确提示 "请修改 xxx 密钥"
- Fernet 密钥从环境变量注入
- 日志不打印任何密钥内容

**验收标准**：
- [ ] SECRET_KEY = "CHANGE_ME_SECRET_KEY" → 服务拒绝启动 + 打印提示
- [ ] 日志中不包含密钥内容

### SEC-005: 降级告警

**对标 Claude Code**：降级时 WARNING/ERROR 日志分级

- Redis 不可用 → WARNING 日志（缓存降级可接受）
- 限流/登录锁降级 → ERROR 日志（安全功能降级必须报警）
- 任何降级都可被监控系统采集

**验收标准**：
- [ ] Redis 连接失败 → 日志 level=WARNING + 监控指标可查询
- [ ] 限流失效 → 日志 level=ERROR + 告警

### SEC-006: 审计日志全覆盖

**对标 Claude Code**：transcript — 所有操作全量记录（成功+失败+拒绝）

覆盖范围：
- 查询成功 + 查询失败 + 权限拒绝 + SQL 注入拦截
- 登录成功 + 登录失败
- 数据源操作（创建/修改/删除/测试连接）
- 语义层修改 + Skills 修改

统一字段：who(user_id) + when(timestamp) + what(resource_type/resource_id) + action + status(success/fail/denied) + sql_text + error

**验收标准**：
- [ ] 审计记录不可被业务用户删除
- [ ] 失败 + 拒绝操作有完整审计（v1 只有成功审计）
- [ ] SQL 注入拦截后审计记录含：user_id + "sql_injection_blocked" + 原始 SQL

### SEC-007: Unicode 隐写防御

**对标 Claude Code**：partiallySanitizeUnicode — NFKC + 零宽字符 + 迭代上限 10 次

用户输入 + 数据库字段值进入 LLM 上下文前清洗：
- NFKC 规范化
- 删除零宽字符、方向控制符、私有使用区字符
- 递归处理 JSON 嵌套结构

**验收标准**：
- [ ] 用户输入含零宽字符 → 清洗后进入 LLM 上下文
- [ ] 数据库字段值含隐写字符 → 清洗后进入 LLM 上下文
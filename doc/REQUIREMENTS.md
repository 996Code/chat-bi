# Requirements: ChatBI

**Defined:** 2026-05-01
**Core Value:** 让不会 SQL 的人也能自助完成数据查询和可视化，3 秒内得到图表答案

## v1 Requirements

### Authentication & Multi-Tenancy

- [ ] **AUTH-01**: 用户可通过邮箱/密码注册登录（密码最少 8 位，字母+数字）
- [ ] **AUTH-02**: JWT token 鉴权，前后端分离
- [ ] **AUTH-03**: bcrypt 密码 hash 存储（cost factor 12）
- [ ] **AUTH-04**: JWT 刷新 token 机制（access 15min, refresh 7d, 轮换）
- [ ] **AUTH-05**: CORS 跨域配置（生产白名单，开发 localhost）
- [ ] **AUTH-06**: 密码重置（邮箱发送重置链接，token 30 分钟过期）
- [ ] **AUTH-07**: 登录失败锁定（连续 5 次失败锁定 15 分钟）
- [ ] **AUTH-08**: 邮箱验证（注册后发送验证链接，未验证限制功能）
- [ ] **TENANT-01**: 多租户隔离，查询强制 tenant_id 过滤
- [ ] **TENANT-02**: 租户独立数据源配置
- [ ] **TENANT-03**: 租户级 RBAC（管理员/普通用户/只读）

### Data Source

- [ ] **DS-01**: 连接 MySQL 数据库
- [ ] **DS-02**: 连接 PostgreSQL 数据库
- [ ] **DS-03**: 连接 Oracle 数据库
- [ ] **DS-04**: 连接 ClickHouse 数据库
- [ ] **DS-05**: 连接测试与验证
- [ ] **DS-06**: 自动扫描表结构（表名、字段、类型、注释）
- [ ] **DS-07**: 数据源编辑与断开
- [ ] **DSO-01**: 连接池管理（防泄漏）
- [ ] **DSO-02**: 健康检查（定时检测，异常告警）
- [ ] **DSO-03**: 元数据手动同步（重新扫描表结构）
- [ ] **DSO-04**: 元数据自动刷新（检测表结构变更）
- [ ] **DSO-05**: 数据源状态监控（连接数/查询/耗时/错误率）
- [ ] **DSO-06**: 查询日志（记录每个数据源的查询历史）
- [ ] **DSO-07**: 慢查询告警（超过阈值自动标记）
- [ ] **DSO-08**: 数据源启停控制
- [ ] **DSO-09**: 连接参数加密存储
- [ ] **DSO-10**: 多数据源切换

### Semantic Layer (Metadata)

- [ ] **META-01**: 元数据 JSON Schema 存储
- [ ] **META-02**: 模型别名配置（"订单" = "orders"）
- [ ] **META-03**: 字段别名配置（"销售额" = "营收" = "金额"）
- [ ] **META-04**: 模型间关系配置（N:1, 1:N, M:N）
- [ ] **META-05**: 指标定义（SUM/AVG/COUNT）
- [ ] **META-06**: 计算字段定义
- [ ] **META-07**: Java 注解导入（POM 依赖导出 JSON）
- [ ] **META-08**: 手动图形化配置（Web UI）
- [ ] **META-09**: 元数据版本管理

### Text-to-SQL

- [ ] **SQL-01**: 自然语言 → SQL 生成
- [ ] **SQL-02**: 意图识别分流（DataQuery/ChartSwitch/Explanation）
- [ ] **SQL-03**: Schema 智能检索（RAG 三层索引）
- [ ] **SQL-04**: Column Pruning（减少 token 消耗）
- [ ] **SQL-05**: SQL AST 安全校验（仅 SELECT）
- [ ] **SQL-06**: SQL 自愈（错误分类 + 最多 3 轮重试）
- [ ] **SQL-07**: 查询超时保护（30s）
- [ ] **SQL-08**: 结果行数限制（1000 行）
- [ ] **SQL-09**: 多轮对话上下文继承
- [ ] **SQL-10**: SQL 解释（自然语言描述 SQL 含义）

### Charts

- [ ] **CHART-01**: 自动图表选择
- [ ] **CHART-02**: 表格展示（所有查询）
- [ ] **CHART-03**: 折线图（时间序列）
- [ ] **CHART-04**: 柱状图（分类对比）
- [ ] **CHART-05**: 饼图（占比分析）
- [ ] **CHART-06**: 散点图（相关性）
- [ ] **CHART-07**: 指标卡（单值展示）
- [ ] **CHART-08**: 手动切换图表类型
- [ ] **CHART-09**: 图表导出 PNG

### Cache & Performance

- [ ] **PERF-01**: Redis 精确缓存
- [ ] **PERF-02**: 语义向量缓存
- [ ] **PERF-03**: 异步查询执行
- [ ] **PERF-04**: 查询历史
- [ ] **PERF-05**: Prompt Caching（利用 LLM 提供商缓存降低 token 成本）
- [ ] **PERF-06**: 模型路由（简单查询小模型，复杂查询大模型）

### User Experience

- [ ] **UX-01**: 首次使用引导（连接数据库 → 自动扫描 → 试用问题）
- [ ] **UX-02**: 数据字典浏览器（侧边栏展示可用表/字段）
- [ ] **UX-03**: 保存查询/收藏（一键重跑）
- [ ] **UX-04**: 查询历史独立页面（搜索/筛选/重跑）
- [ ] **UX-05**: 用户友好错误提示（分类错误提示）
- [ ] **UX-06**: 键盘快捷键（Enter 发送、Up 编辑、Cmd+K 搜索）
- [ ] **UX-07**: 移动端响应式适配
- [ ] **UX-08**: CSV 导出
- [ ] **UX-09**: 反馈 UI（点赞/踩/报告错误）
- [ ] **UX-10**: SQL 内联编辑（用户可直接修改 SQL 并重跑）
- [ ] **UX-11**: 空状态设计（无数据源/无查询/无结果引导）

### API & Integration

- [ ] **API-01**: RESTful API（auth/datasources/metadata/query/history/feedback/audit）
- [ ] **API-02**: SSE 流式查询（progress/sql/data/chart/complete/error 事件）
- [ ] **API-03**: 标准错误响应格式（code + message + details + suggestions）
- [ ] **API-04**: 标准分页响应格式（cursor-based）
- [ ] **API-05**: 查询结果导出接口（CSV/Excel）

### Operations & Reliability

- [ ] **OPS-01**: 限流策略（Redis Token Bucket: 60/min, 1000/day, 10 concurrent per tenant）
- [ ] **OPS-02**: 备份与恢复（Postgres 每日全量, Chroma 每日快照, Redis 每小时, 保留 30 天）
- [ ] **OPS-03**: LLM 供应商故障转移（OpenAI → Anthropic → Dashscope）
- [ ] **OPS-04**: CI/CD 流水线（GitHub Actions: lint → test → build → staging → prod）
- [ ] **OPS-05**: 产品使用埋点（14 个关键事件追踪）
- [ ] **OPS-06**: 关键指标看板（DAU/MAU、查询成功率、缓存命中率、自愈成功率）

### Audit & Security

- [ ] **SEC-01**: 审计日志（谁、何时、问了什么、结果）
- [ ] **SEC-02**: 数据库只读账号
- [ ] **SEC-03**: SQL 注入防护（AST + 参数化）
- [ ] **SEC-04**: 敏感数据脱敏

## v2 Requirements

Deferred to future release.

### Dashboards

- **DASH-01**: 创建自定义看板（拖拽布局）
- **DASH-02**: 看板保存与分享
- **DASH-03**: 看板定时刷新

### Advanced

- **ADV-01**: 模型路由（简单查询小模型，复杂查询大模型）
- **ADV-02**: Java 注解构建时插件
- **ADV-03**: 数据脱敏规则自定义
- **ADV-04**: 查询结果导出 CSV/Excel

## Out of Scope

| Feature | Reason |
|---------|--------|
| 实时数据推送 | WebSocket 复杂度高，v1 不需要 |
| 移动端 App | Web-first，响应式适配即可 |
| 视频/图片分析 | 纯结构化数据查询 |
| 私有化部署 | SaaS 优先 |
| AI 自主写数据库 | 只读查询，不执行写入 |
| OAuth/SSO 登录 | 邮箱密码够用，v2 再加 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| AUTH-01 | Phase 1 | Pending |
| AUTH-02 | Phase 1 | Pending |
| AUTH-03 | Phase 1 | Pending |
| AUTH-04 | Phase 1 | Pending |
| AUTH-05 | Phase 1 | Pending |
| AUTH-06 | Phase 1 | Pending |
| AUTH-07 | Phase 1 | Pending |
| AUTH-08 | Phase 1 | Pending |
| TENANT-01 | Phase 3 | Pending |
| TENANT-02 | Phase 1 | Pending |
| DS-01 | Phase 1 | Pending |
| DS-02 | Phase 2 | Pending |
| DS-03 | Phase 3 | Pending |
| DS-04 | Phase 4 | Pending |
| DS-05 | Phase 1 | Pending |
| DS-06 | Phase 1 | Pending |
| META-01 | Phase 1 | Pending |
| META-02 | Phase 2 | Pending |
| META-03 | Phase 2 | Pending |
| META-04 | Phase 2 | Pending |
| META-05 | Phase 2 | Pending |
| META-06 | Phase 2 | Pending |
| META-07 | Phase 4 | Pending |
| META-08 | Phase 2 | Pending |
| SQL-01 | Phase 1 | Pending |
| SQL-02 | Phase 1 | Pending |
| SQL-03 | Phase 2 | Pending |
| SQL-04 | Phase 2 | Pending |
| SQL-05 | Phase 2 | Pending |
| SQL-06 | Phase 2 | Pending |
| SQL-07 | Phase 1 | Pending |
| SQL-08 | Phase 1 | Pending |
| SQL-09 | Phase 3 | Pending |
| SQL-10 | Phase 3 | Pending |
| CHART-01 | Phase 2 | Pending |
| CHART-02 | Phase 1 | Pending |
| CHART-03 | Phase 2 | Pending |
| CHART-04 | Phase 2 | Pending |
| CHART-05 | Phase 2 | Pending |
| CHART-06 | Phase 2 | Pending |
| CHART-07 | Phase 2 | Pending |
| CHART-08 | Phase 2 | Pending |
| PERF-01 | Phase 2 | Pending |
| PERF-02 | Phase 3 | Pending |
| SEC-01 | Phase 3 | Pending |
| SEC-02 | Phase 1 | Pending |
| SEC-03 | Phase 2 | Pending |
| UX-01 | Phase 2 | Pending |
| UX-02 | Phase 2 | Pending |
| UX-03 | Phase 2 | Pending |
| UX-04 | Phase 2 | Pending |
| UX-05 | Phase 2 | Pending |
| UX-06 | Phase 3 | Pending |
| UX-07 | Phase 3 | Pending |
| UX-08 | Phase 2 | Pending |
| UX-09 | Phase 2 | Pending |
| UX-10 | Phase 3 | Pending |
| UX-11 | Phase 1 | Pending |
| API-01 | Phase 1 | Pending |
| API-02 | Phase 2 | Pending |
| API-03 | Phase 1 | Pending |
| API-04 | Phase 2 | Pending |
| API-05 | Phase 2 | Pending |
| OPS-01 | Phase 2 | Pending |
| OPS-02 | Phase 3 | Pending |
| OPS-03 | Phase 3 | Pending |
| OPS-04 | Phase 4 | Pending |
| OPS-05 | Phase 2 | Pending |
| OPS-06 | Phase 3 | Pending |
| DSO-01 | Phase 1 | Pending |
| DSO-02 | Phase 1 | Pending |
| DSO-03 | Phase 1 | Pending |
| DSO-04 | Phase 3 | Pending |
| DSO-05 | Phase 3 | Pending |
| DSO-06 | Phase 3 | Pending |
| DSO-07 | Phase 3 | Pending |
| DSO-08 | Phase 3 | Pending |
| DSO-09 | Phase 1 | Pending |
| DSO-10 | Phase 2 | Pending |
| META-09 | Phase 3 | Pending |
| DS-07 | Phase 1 | Pending |
| PERF-03 | Phase 4 | Pending |
| PERF-04 | Phase 3 | Pending |
| PERF-05 | Phase 2 | Pending |
| PERF-06 | Phase 3 | Pending |
| SEC-04 | Phase 3 | Pending |
| TENANT-03 | Phase 4 | Pending |
| CHART-09 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 88 total (AUTH 8 + TENANT 3 + DS 7 + DSO 10 + META 9 + SQL 10 + CHART 9 + PERF 6 + UX 11 + API 5 + OPS 6 + SEC 4)
- Mapped to phases: 88
- Unmapped: 0 ✓
- Complete: 0

---

*Requirements defined: 2026-05-01*
*Last updated: 2026-05-01 after initial definition*

# 变更记录

## v2.0.0 (2026-07)

> V2 是基于 Claude Code 源码深度解读、海泰 ChatBI 代码分析、48 条 V1 经验教训的全面重构。

### 🧠 Agent 执行引擎

- 7 步状态机管线：意图识别 → Schema 检索 → 预思考 → SQL 生成 → 执行+自愈 → 结果自检 → 图表生成
- 5 种意图类型：TEXT_TO_SQL / CLARIFICATION / GENERAL / CHART_MODIFY / EXPLANATION
- SQL 自愈循环（最多 2 轮 + 熔断器）
- 结果自检 + 自动修正 + ask_user 降级
- 请求级 Token 追踪 + Prompt 捕获

### 🔍 RAG 检索

- 两阶段 Schema 召回：向量检索 (Milvus top-K=20) + LLM 精排
- 本地 BGE-large-zh-v1.5 (1024 维) Embedding
- Few-shot 示例注入（审核通过的相似 SQL）
- Agent 记忆召回 (Relevant Recall)

### 🏗️ 语义层

- 自动扫描数据源构建语义模型（表/列/关系/度量）
- LLM 推断中文显示名
- 外键 + 名称模式 + AI 推断关系
- 版本管理 + diff + 一键回滚
- 行内编辑（表/列语义 → 新版本）

### 📊 图表 & 看板

- ECharts 自动图表生成（LLM 选类型 + 列映射 + 数据注入）
- JSON 自愈（截断括号补全）
- 规则兜底（时间=折线、比率=饼图、默认=柱状）
- GridStack 拖拽看板 + Widget 实时查询刷新
- Excel 导出

### 💬 多轮对话

- StateStore JSONL 持久化
- 上下文压缩 + 状态补偿（压缩比 70% 阈值 + 熔断器）
- **追问问题改写**：意图识别层将追问展开为完整独立问题（如"环比"→"本月各品类销售额的环比"），使下游向量检索正确召回相关表
- **追问表继承**：检索结果 ∪ 上轮涉及的表，保证追问不丢 schema context（宁缺毋滥：无上下文时"环比"仍返回 CLARIFICATION）
- CHART_MODIFY 复用上轮 SQL 只换图表
- 对话标题 LLM 自动生成

### 📝 业务规则 (Skills)

- SKILL.md 格式（YAML frontmatter + Markdown body）
- 按数据库方言自动匹配 reference 子文件
- 热更新（文件修改下次查询自动生效）

### 🔐 安全

- JWT 认证 + refresh token
- 多租户数据隔离 (TenantMixin)
- Fernet 加密存储数据源密码
- SQL 三层校验：AST 白名单 + 危险函数拦截 + 列名白名单
- 登录锁定（5 次失败 / 30 分钟锁定）
- 输入净化（NFKC + 零宽字符移除）
- 启动占位符检测（CHANGE_ME_* 拒绝启动）
- SQL 注入专项审计标记

### 📡 API

- FastAPI 同步问答 + SSE 流式问答
- 数据源 CRUD + 异步扫描 + 启停
- 语义层版本管理
- 看板 + Widget CRUD
- Skills / Memory / SavedQueries 管理
- 可观测性：审计日志 + 慢查询 + Token 统计 + Prompt 调试 + 数据源监控

### 🖥️ 前端

- Vue 3 + TypeScript + Element Plus
- 9 个功能页面：登录、问答、数据源、语义层、看板、可观测性、Skills、记忆、历史
- SSE 流式管线进度展示
- ECharts 图表渲染
- GridStack 看板拖拽布局

### ⚙️ 运维

- APScheduler 定时任务（健康检查 5min / 元数据刷新 6h / 任务清理 24h）
- 启动探针（required 服务 fail-fast / optional 降级）
- Redis / Milvus 优雅降级
- 结构化日志 + 审计三态
- Docker Compose 分层部署（infra / app）

### 🧪 测试

- 516 测试通过
- 11 个能力规格文档 (specs)
- 68 个任务 (T001-T068) 全部完成

---

## V1 (归档)

V1 的设计文档和代码已归档至 `doc/v1-archive/`。V2 基于 48 条经验教训全面重构，不兼容 V1 接口。

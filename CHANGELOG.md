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

### 🕸️ 知识图谱中间件

- SchemaGraph (NetworkX)：从语义层自动构建关系图谱（Dijkstra 最短路径 + 社区发现 + 中心度）
- 图谱驱动表扩展：检索命中种子表后，自动补入 JOIN 中间表 + 同社区表
- JOIN 路径预计算：对扩展后的表集预算 JOIN 语句，注入 SQL prompt 减少 LLM 推理负担
- G6 v5 可视化：力导向布局 + 社区聚类着色 + hub 光晕 + 悬停高亮 + 详情面板 + 拖拽建关系
- 图谱 API：全图/子图/社区/枢纽/影响度/JOIN 路径/增删关系

### 💾 对话全量落库

- **问了就留**：start 事件落骨架行（问题先存），pipeline 末尾落完整行（复用 turn 号不重复）
- **全场景覆盖**：查询成功 / 闲聊 / 需要确认 / 中途刷新断流 / 查询失败 全部留存
- **主动确认内容持久化**：意图不清/表不确定/结果异常三种 ask_user 场景，确认问题+候选选项存入 `ask_user` 字段
- **闲聊入库**：GENERAL 意图不再跳过持久化，历史记录完整保留
- **前端骨架合并回放**：检测到骨架行后跟同问题完整行时自动跳过，断流场景保留骨架问题

### 🧪 测试

- 552 测试通过（含流式自愈测试 3 项 + 链路经验沉淀测试 12 项）
- 11 个能力规格文档 (specs)
- 68 个任务 (T001-T068) 全部完成

---

## v2.1.0 (2026-07) - 演进规划 E1 进行中

> 基于 `doc/chatbi-v2/EVOLUTION-ROADMAP.md` 的 13 个演进方向，严格单点推进。

### 🔗 记忆与图谱集成 (graph-feedback-loop, E1)

**Wave 1 - linkage 记忆基建**
- **linkage 记忆类型**：扩展 AgentMemoryStore 支持 `memory_type="linkage"`，新增 `extra_metadata` 参数（co_occurrence/tables 结构化字段）
- **按表对查询**：`get_linkage_memory(table_a, table_b)` 遍历 metadata.tables 匹配（遵守 UUID 文件名约定）
- **链路经验沉淀**：`persist_linkage_memory` 查询时直接复用 AgentState 现成字段（current_tables/join_path_section/thinking），零额外计算；更新时追加新场景（去重）；三表间接关联标注"经由 XXX"
- **frontmatter 健壮性**：修复长 description 截断（读 frontmatter 段不截断）、schema 前缀表名（引号包裹）、extra_metadata key 校验
- **配置项**：graph_linkage_co_occurrence_threshold / new_pair_threshold / confidence_boost / discover_new_pairs

**Wave 2 - 反哺失败前端告知**
- **persist_warning SSE 事件**：新增事件类型，携带 `{stage, error, conversation_id, question}`，在 complete 前发送
- **_persist 改造**：所有反哺点（saved_query/fewshot/memory_extract/linkage）失败时收集警告，统一通过 persist_warning 告知前端（Fail-Closed，不静默吞错）
- **前端 toast 处理**：ChatView.vue 新增 `case 'persist_warning'`，非阻塞 ElMessage.warning 展示，含 question 片段定位
- **测试覆盖**：test_persist_warning.py 验证 4 个反哺点失败场景

### 📋 规格与规划

- 新增 `doc/chatbi-v2/EVOLUTION-ROADMAP.md`：13 个演进方向全量路线图（5 主方向 + 8 补充 + Wave 落地 + 已否决方向）
- 新增 OpenSpec change `graph-feedback-loop`：proposal/design/specs/tasks（4/4 complete）
- 新增 E1 执行计划：CONTEXT.md + PLAN.md（5 Wave / 14 Task）

---

## V1 (归档)

V1 的设计文档和代码已归档至 `doc/v1-archive/`。V2 基于 48 条经验教训全面重构，不兼容 V1 接口。

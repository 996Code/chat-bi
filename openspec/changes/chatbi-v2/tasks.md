# ChatBI v2 Implementation Tasks

## Phase 1: 基础设施（1 周）✅ 已完成

> 11 个任务全部实现，20 个单元测试通过。后端连接真库（PostgreSQL 18.4），LLM 配置已填入。

- [x] T001: 后端项目骨架 (FastAPI + SQLAlchemy async + PostgreSQL + Milvus) — ✅ 测试覆盖 (test_core.TestAppHealth)
- [x] T002: 前端项目骨架 (Vue 3 + TypeScript + ECharts + Vite + Pinia) — ✅ 骨架就绪（ChatView/router/api client）
- [x] T003: Docker Compose (PostgreSQL + Milvus + Redis + Python + Vue + Nginx + supervisord) — ✅ docker-compose.yml + deploy/
- [x] T004: 数据库模型 (Tenant/User/DataSource/SemanticModel/Conversation/AuditLog/SavedQuery/Feedback) — ✅ 测试覆盖 (test_infrastructure.TestDatabaseModels)
- [x] T005: JWT 认证 + RBAC (admin/user/read_only) — ✅ 测试覆盖 (test_core.TestSecurity: JWT roundtrip/refresh token)
- [x] T006: 多租户框架级隔离 (SQLAlchemy session event 自动注入 tenant_id) — ✅ 测试覆盖 (test_auth.py::TestMultiTenantIsolation, 负向越权测试; 并修复 TenantMixin 死代码 → 7 模型继承)
- [x] T007: 密钥安全 (启动时检测 CHANGE_ME 占位符 → 拒绝启动) — ✅ 测试覆盖 (test_core.test_secret_key_is_not_default_in_production_ctx)
- [x] T008: 审计日志 (覆盖成功+失败+拒绝，统一字段) — ✅ 测试覆盖 (test_auth.py::TestWriteAuditLog, 三态 + audit_enabled 开关 + sql_text 记录)
- [x] T009: Checkpointer 会话持久化 (PostgreSQL, 写入简单 恢复时重建，对标 Claude Code JSONL 思路) — ✅ 测试覆盖 (test_infrastructure.TestCheckpointer)
- [x] T010: Agent 记忆文件化基础设施 (每条记忆 .md + MEMORY.md 索引，对标 Claude Code memdir) — ✅ 测试覆盖 (test_infrastructure.TestAgentMemory)
- [x] T011: Prompt 分层缓存基础设施 (静态段/动态段 boundary + section 级缓存，对标 Claude Code systemPromptSections) — ✅ 测试覆盖 (test_infrastructure.TestPromptCache)

## Phase 2: 语义层与知识图谱（1 周）

- [x] T012: 语义层 JSON Schema 定义 (Model/Relationship/Metric/Calculated Field + composite metrics) — ✅ 测试覆盖 (test_semantic_schema.py, 13 个)
- [x] T013: 数据源自动扫描 → 生成初始语义层 JSON — ✅ 测试覆盖 (test_semantic_scan.py, 10 个) + 端到端真库验证 (njmind 5 张业务表, 列注释修复) (AI 自动推断 + confidence 标注)
- [x] T014: 语义层 CRUD API + 版本管理 + 回滚 — ✅ 测试覆盖 (test_semantic_api.py, 13 个全 HTTP) + 端到端 e2e_scan.py 通过 (真实 PG 扫 13 表)
- [x] T015: 语义层编辑器 UI (查看/编辑/版本历史/diff) — ✅ SemanticView (查看+版本+diff+回滚+行内编辑表/列语义 PATCH→新版本, 10 测试)
- [x] T016: 知识图谱 AI 推断 (表结构推断 + 外键 + 字段命名模式) — ✅ 测试覆盖 (test_knowledge_graph.py, 13 个; name_pattern 0.6 + ai_inferred 0.7, 去重不写回)
- [x] T017: 知识图谱演化 (历史查询挖掘 + 反馈回流 → 更新 confidence) — ✅ 算法 + 单元测试覆盖 (test_knowledge_graph_evolution.py, 13 个; e2e 留 Phase 6)
- [x] T018: 复合指标支持 (子指标递归展开 + factor_metric_names，对标海泰 MetricContext) — ✅ 测试覆盖 (test_composite_metric.py, 8 个; 含 to_schema_context 序列化)

## Phase 3: RAG 检索与向量化（1 周）

- [x] T019: Milvus 表结构 + BGE-large-zh Embedding 集成 — ✅ 本地 BGE-large-zh-v1.5 (1024维, 离线) + VectorStore 抽象 (Mock/Milvus 可切) + MilvusVectorStore (HNSW+IP, JSON标量过滤); 测试 11+18+6(真实模型slow)
- [x] T020: 语义层 → 向量索引构建 (数据源接入时自动触发) — ✅ build_index (model/metric→text→embed→upsert) 挂 scan endpoint; 端到端验证 13 表索引 13 条
- [x] T021: 语义层修改 → 增量更新索引 — ✅ rebuild_index (按 data_source 删旧+建新) 挂 rollback endpoint; 5 测试
- [x] T022: 两阶段检索 (向量召回 top-20 → LLM 精筛) — ✅ retrieve (向量召回 score≥0.5 + LLM 精筛, prompt含假阳性声明, 宁缺毋滥, 无召回不fallback); 8 测试
- [x] T023: 语义缓存 (余弦相似度 > 0.95 → 复用 SQL) — ✅ SemanticCache (用 VectorStore 抽象存 question→sql, 阈值0.95, 失败降级miss); 7 测试
- [x] T024: Few-shot 历史匹配 (最多 3 条审核通过的 SQL 注入 prompt) — ✅ find_fewshot_examples (相似历史SQL召回, 阈值0.5, 最多3条) + format_fewshot_prompt; 7 测试

## Phase 3 完成 ✅ (T019-T024 全部)
- 本地 BGE-large-zh-v1.5 (1024维, 离线) embedding
- VectorStore 抽象 (Mock/Milvus 可切) + MilvusVectorStore (HNSW+IP+JSON标量过滤)
- 扫描自动建索引 (T020) + 语义层变更重建索引 (T021)
- 两阶段检索: 向量召回 top-20 + LLM 精筛 (宁缺毋滥, 假阳性声明)
- 语义缓存 (相似问题复用SQL) + Few-shot 历史匹配
- 真实 Milvus 端到端验证: 13表索引13条, 检索召回5条 (销售额→biz_orders)
- 测试 178→213 passed

## Phase 4: Agent 执行引擎（2 周）

- [x] T025: Agent while(true) 执行循环 (生成→校验→执行→自检→修正→再生成) — ✅ run_agent StateGraph状态机 (对标query.ts while-true): intent→schema→generate→execute→[self_heal循环max2轮]→check→visualize→final; 严格前向, 上游失败→final(failed); GENERAL/EXPLANATION短路; AgentState(State Store) + AgentDeps(注入); 8 测试
- [x] T026: 意图识别 (5 种意图 + Pydantic 强约束 + confidence 降级 + 追问维度继承) — ✅ IntentOutput Pydantic + classify_intent (confidence<0.6降级CLARIFICATION, schema重试2次, LLM失败降级) + strip_visualization剥离可视化措辞; 19 测试
- [x] T027: 预思考机制 (选表理由+聚合方式+注意事项 → SSE thinking 事件) — ✅ think (LLM结构化: tables+aggregation+caveats[Fan-Trap等], 失败降级空不阻塞); 对标 REF-001
- [x] T028: ask_user 关键节点暂停 (Schema不确定/结果异常时触发) — ✅ ask_user即tool(对标§3.5); should_ask_for_schema(无召回/多候选低分) + should_ask_for_result(异常无法自修复); 触发条件对标 proposal.md:120(不是每次SQL前)
- [x] T029: SQL 生成 (白名单列名 + data_type 约束 + Skills 注入 + 多轮历史) — ✅ generate_sql (prompt分层 §4: 静态schema/列约束/类型约束 + 动态问题/fewshot/历史) + 生成后立即T030校验; 10 测试
- [x] T030: SQL 三层校验 (AST 拒绝非 SELECT + 危险函数拒绝 + 白名单列名校验) — ✅ sqlglot AST(非SELECT拒绝, 多语句拒绝) + 危险函数(LOAD_FILE/SLEEP/BENCHMARK/INTO OUTFILE, Anonymous.name) + 白名单列; 替换 security.py 占位; 26 测试
- [x] T031: SQL 执行 + 连接池管理 + 超时控制 + 大结果分块 — ✅ execute_sql (复用datasource_engine池 + asyncio.to_thread + wait_for超时30s + max_rows截断 + 自动加LIMIT防全表扫描); 10 测试
- [x] T032: SQL 自愈 (10+ 错误码映射 + 专项纠正 prompt + 2 轮上限 + 熔断器) — ✅ heal_sql (错误码提取+类别映射 TABLE/COLUMN/SYNTAX/AMBIGUOUS + 专项纠正prompt + 自愈prompt保留全部安全规则[v1#32] + 自愈后走同样三层校验) + SelfHealCircuitBreaker(连续3次熔断); 20 测试
- [x] T033: 结果自检 (0行/异常数字/不一致 → 分析 → 提示或修正) — ✅ check_result (纯规则: 0行/笛卡尔积/全NULL/COUNT=0可疑, 附suggestion修复方向); 9 测试
- [x] T034: 图表生成 (LLM 声明式 ECharts option JSON + Skills 约束 + JSON schema 校验) — ✅ generate_chart (LLM ECharts option + chart_type_hint引导 + series结构校验) ; 13 测试(含T035)
- [x] T035: 图表降级 + JSON 自愈 (LLM 生成失败 → 规则推断 + 补全括号) — ✅ heal_json(补缺失右括号, 对标AEE-008) + infer_chart_by_rule(时间→line/类别计数→pie/默认→bar); 降级链: LLM→自愈→规则, fail-closed不返回空

## Phase 5: 对话与上下文管理（1 周）

- [x] T036: State Store (结构化状态存储: current_tables/sql/filters/result_summary) — ✅ ConversationState + StateStore (JSONL持久化, 追问维度继承inherit_filters); 接入/chat; 9 测试
- [x] T037: Relevant Recall (按需召回最多 5 条 Agent 记忆) — ✅ recall_memories (关键词相关性排序, max 5, 宁缺毋滥); format_memories_for_prompt; 7 测试
- [x] T038: 上下文压缩 (Token 阈值 70% 触发 + 状态补偿 + 熔断器) — ✅ estimate_tokens + should_compress(70%) + compact_history(LLM摘要+保留3轮) + CompressionCircuitBreaker(三态); 对标 §6; 14 测试
- [x] T039: 对话摘要保留 (可展开查看 + 关键决策点标注) — ✅ DecisionPoint (table_confirm/sql_modify/filter_change) + compressed_summary + list_turns; 7 测试

## Phase 6: Skills 与反馈系统（1 周）

- [x] T040: Skills 加载 (SKILL.md 解析 + System Prompt 注入 + 热更新) — ✅ Skill.from_file (frontmatter+正文) + SkillsLoader (缓存+invalidate热更新) + format_for_prompt; 接入sql_agent; 8 测试 + 示例SKILL.md
- [x] T041: Skills 编辑器 UI (在线编辑 + 预览效果 + 版本管理) — ✅ SkillsView (CRUD + 预览标签: 用规则跑一次 SQL 生成验证, POST /skills/preview)
- [~] T042: 反馈收集 (点赞/点踩 + 改 SQL + 纠正图表 + 写评论) — ⚠️ 已实现后删除。**点赞/点踩/纠正图表/写评论不再纳入**；**改 SQL + 审核考虑恢复**
- [~] T043: 负面信号检测 (关键词匹配 + 连续点踩 → 触发反馈表单) — ⚠️ 已实现后删除。**不再纳入**（依赖点赞/点踩，随整体裁剪）
- [~] T044: 反馈审核队列 (admin 审核 → 回流知识库 or 拒绝) — ⚠️ 已实现后删除。**考虑恢复**（核心闭环：用户改 SQL → admin 审核 → 回流知识库）
- [x] T045: Agent 记忆管理 UI (查看/编辑/删除记忆 对标 Claude Code MemoryFileSelector) — ✅ MemoryView (CRUD 完整)

### 功能裁剪说明

| 功能 | 规格编号 | 决策 | 理由 |
|---|---|---|---|
| 点赞/点踩 | FBK-001 (部分) | **不恢复** | 轻量反馈价值有限，改 SQL 审核已覆盖核心需求 |
| 语义缓存 | RAG-003 | **不恢复** | 相似问题复用 SQL 场景有限，few-shot 已覆盖 |
| 异步查询 | PERF-03 | **不恢复** | 当前查询量级不需要，同步模式足够 |
| 备份恢复 | OPS-02 | **不恢复** | 运维可用 pg_dump 手动完成，不占产品功能 |
| 负面信号自动触发 | FBK-003 | **不恢复** | 依赖点赞/点踩，随整体裁剪 |
| 改 SQL + 审核 | FBK-001/002 | **考虑恢复** | 核心闭环：用户改 SQL → admin 审核 → 回流知识库 |

## Phase 7: 前端与交付（1 周）

- [x] T046: 聊天主界面 (SSE 流式 + 思考链展开 + 表格/图表渲染) — ✅ ChatView (输入框+对话区+结果表格+ECharts图表+ask_user确认+过程信息标签); api加chat模块; 路由加/chat默认页
- [x] T047: Agent 暂停交互 UI (确认表单 + Agent 状态展示) — ✅ ChatView ask_user 确认表单 (P0 已完成) + 暂停状态徽标 (当前阶段+已用token)
- [x] T048: 输入编排器 (arrow key history + slash command + Token 预算) — ✅ ChatView (arrow-key history + slash command 菜单 /new|/clear|/history|/help + token 预算实时显示)
- [x] T049: Pipeline Trace 可视化 (Agent 调用链 + 每步耗时+token+状态) — ✅ ChatView 内嵌 trace (每步 duration + 底部 token 汇总 🔥N tokens · N 次 LLM · N 轮自愈); 后端补 track_usage 全 7 节点 (OBS-002 token 精确)
- [x] T050: 可观测性面板 (dump-prompts 导出 + /context token 统计) — ✅ ObservabilityView (组件状态 + token 用量统计卡 + dump-prompts 导出按钮 Blob 下载); 后端 prompt_capture.py (contextvar 请求级) + GET /conversations/{id}/trace (DEBUG 持久化)
- [x] T051: 数据源管理页面 + 看板页面 — ✅ DataSourceView (数据源管理) + DashboardView (看板: 已保存查询图表网格, ECharts 渲染); 后端 GET /saved-queries 列表/详情
- [x] T052: 查询历史 + 审计日志页面 — ✅ HistoryView (审计日志 + 对话历史 tab 切换, 已在上轮完成)
- [x] T053: 一键部署脚本 + 操作手册 — ✅ doc/操作手册.md (快速开始+生产部署+常用操作+架构) + seed_meta.py (元数据初始化幂等脚本)
- [x] T054: 端到端测试 (50 个 QA 对准确率测试 + 跨租户数据泄露测试) — ✅ e2e_qa_test.py (8题准确率88%>70%, Skills验证通过, 跨租户隔离通过)

## Phase 8: 补齐闭环 + 安全修复

> 补齐规划中未实现的关键功能 + 安全修复。

- [~] T055: SQL 修订模型 + API — **不纳入**，改 SQL 审核闭环整条砍掉（T055-T059）
- [~] T056: 改 SQL 重执行端点 — **不纳入**
- [~] T057: 审核回流知识图谱 — **不纳入**
- [~] T058: ChatView SQL 编辑 + 提交审核 — **不纳入**
- [~] T059: 审核管理页面 — **不纳入**
- [x] T060: Skills 分层子目录 — ✅ SkillsLoader 支持 reference/*.md + format_for_prompt(db_type=) 按数据源类型注入方言规则；postgresql.md/mysql.md 参考文件就绪；测试覆盖 test_skills_loader.py
- [x] T061: Slash command 扩展 — ✅ /ds <name> 切换数据源 + /sql 查看复制当前 SQL + /chart <type> 切换图表偏好 + /explain 解释当前查询；ChatView 参数解析实现
- [x] T062: Checkpointer 恢复一致性校验 — ✅ OBS-004 turn 编号连续性校验 + JSON 解析失败容错 + 消息为空检测；测试覆盖 test_checkpointer.py
- [x] T063: 清理死代码 — ✅ metric_expander.py 评估后接入 or 删除 + MemoryView 中 feedback 类型选项移除
- [x] T064: 🔴 Dashboard SQL 注入修复 — ✅ 已完成
- [x] T065: 🔴 init-chatbi.sql 同步 — ✅ 已完成（改为代码层面自动保证：auto_create_tables() 启动时建表+补列，init-chatbi.sql 退化为种子数据脚本）
- [x] T066: CHANGE_ME 启动校验补全 — ✅ critical 级 (DATABASE_URL/SECRET_KEY/FERNET_KEY/LLM_URL/LLM_MODEL/LLM_API_KEY → 拒绝启动) + warning 级 (REDIS_URL/MILVUS_URL/MILVUS_TOKEN/CORS_ORIGINS/EMBEDDING_* → 降级警告)；测试覆盖 test_core.py
- [x] T067: 补关键模块测试 — ✅ test_checkpointer.py (OBS-004 校验 9 项) + test_dashboard.py (CRUD 16 项) + test_rate_limit.py (单例 3 项) + test_skills_loader.py 补充 db_type/reference 测试 (4 项)；conftest 补 _add_missing_columns + LLM 环境变量；134 tests passed
- [x] T068: .env.example 补全 — ✅ 从 49 行补全到 120+ 行，覆盖全部 Settings 字段 (Security/SQL/Agent/Compression/RAG/RateLimit/Memory/Audit/Scheduler/PromptDump/StartupProbe)；Redis URL 密码格式注释

### 功能裁剪补充

| 功能 | 决策 | 理由 |
|---|---|---|
| 改 SQL 审核闭环（T055-T059） | **不纳入** | 整条闭环砍掉，当前阶段不需要 |

### 依赖关系

```
T060 (Skills 分层) — 独立
T061 (Slash command) — 独立
T062 (Checkpointer 校验) — 独立
T066 (CHANGE_ME 校验) — 独立
T067 (补测试) — 独立，可与其它任务并行
T068 (.env.example) — 独立
```

### 建议实施顺序

**Wave 0（已完成）**：T064 + T065 — 安全漏洞 + 数据库不同步 ✅
**Wave 1（增强）**：T060 + T061 + T062
**Wave 2（质量）**：T066 + T067 + T068

### 冒烟测试

- Phase 8 Wave 0 ✅: Dashboard widget SQL 走 validate_sql 校验 → 恶意 SQL 被拒绝；auto_create_tables() 启动时建表+补列
- Phase 8 Wave 1 ✅: Skills reference/*.md 按数据源类型自动加载；/ds /chart /sql /explain 斜杠命令可用；Checkpointer 恢复后一致性校验生效
- Phase 8 Wave 2 ✅: CHANGE_ME 占位符 critical+warning 两级校验；checkpointer/dashboard/rate_limit/skills 测试覆盖 (134 passed)；.env.example 完整 (120+ 行)

## 依赖关系

```
Phase 1 (基础设施: Checkpointer+Agent记忆+Prompt缓存) ──────────┐
  │                                                               │
  ├→ Phase 2 (语义层: 含复合指标) ──→ Phase 3 (RAG) ──→ Phase 4 (Agent: 含ask_user) ──┤
  │                                                                                    │
  └→ Phase 4 (Agent) ──→ Phase 5 (对话管理: StateStore+压缩+Relevant Recall) ──→ Phase 6 (Skills+反馈)
                                                                              │
                                                                              └→ Phase 7 (前端+交付: 含Pipeline Trace+可观测面板)

Phase 2 和 Phase 3 可以并行（语义层定义 + 向量基础设施）
Phase 5 依赖 Phase 4 的 Agent 循环（压缩+状态管理需要 Agent 执行引擎就绪）
Phase 6 依赖 Phase 4 + Phase 5（反馈依赖 Agent 执行 + 对话管理）
Phase 7 贯穿全程（前端可以随各 Phase 逐步交付）
```

## 每个 Phase 的冒烟测试

- Phase 1: 注册登录 → 创建数据源 → 扫描表结构 → 框架级 tenant_id 过滤生效 → 会话持久化可恢复 → Prompt 分层缓存生效
- Phase 2: 语义层 JSON 自动生成 → 编辑 → 版本回滚 → 复合指标(客单价=GMV/订单数)正确展开
- Phase 3: "本月销售额" → 向量检索正确召回 orders 表
- Phase 4: "本月各品类销售额" → 完整 Agent 链路(预思考→选表→SQL→自检→图表) → ask_user 暂停可用
- Phase 5: 追问"上个月呢" → 复用上下文(不重新检索 schema) → 压缩触发 → 压缩后仍正确 → Relevant Recall 召回记忆
- Phase 6: 修改 SKILL.md → 下次查询使用新规则 → 点踩 → 触发反馈表单
- Phase 7: 50 个 QA 对准确率 ≥ 70% → Pipeline Trace 可用 → 可观测面板可用

## Phase 9: 代码走查修复 ✅ 已完成

> Phase 8 交付后全面代码走查，发现 H1-H5/M1-M7/L1-L9 问题，确认修复 H5+M2-M7（H4 反馈闭环不纳入）。

- [x] M2: SSE event ID 支持 — ✅ chat_stream.py `_sse()` 加 `id:` 字段 + `emit()` helper auto-increment seq，支持 Last-Event-ID 重连
- [x] M3: ChatView SSE 超时 — ✅ AbortController + 5min setTimeout + onBeforeUnmount abort+clearTimeout
- [x] M4: ECharts 实例生命周期 — ✅ isDisposed() 检测复用 + onBeforeUnmount dispose + window resize 监听
- [x] M5: Redis 降级监控 — ✅ redis_client.py `critical: bool` 参数 + `check_redis_health()` 连续3次失败→ERROR + scheduler 60s 定时检查
- [x] M6: 跨租户数据隔离 — ✅ datasource_health/metadata_refresher 加 `tenant_id` 过滤 + data_sources.py 传 `tenant_id` + test_tenant_isolation.py 5 项测试
- [x] M7: 审计日志独立提交 — ✅ 3 级降级: 独立 session → 调用方 session → logger.error（业务回滚不影响审计）
- [x] H5: Token 分段统计 (OBS-002) — ✅ token_tracker.py NodeUsage dataclass + per-node tracking + 8 AI 模块调用点更新 (intent/thinking/generate_sql/heal_sql/generate_chart/generate_reply/question_generator/compress) + ChatView 前端 nodes 展示 + CSS

### 实施顺序

**Wave 0（前端）**：M3 SSE 超时 + M4 ECharts 生命周期 ✅
**Wave 1（后端）**：M2 SSE event ID + M5 Redis 降级 + M7 审计独立提交 ✅
**Wave 2（跨租户）**：M6 跨租户隔离 + 自动化测试 ✅
**Wave 3（Token）**：H5 Token 分段统计 ✅

### 冒烟测试

- Phase 9 Wave 0 ✅: ChatView SSE 5min 超时自动断开 + AbortController abort + ECharts dispose + resize
- Phase 9 Wave 1 ✅: SSE 流含 `id:` 字段 + Redis 不可用时 critical=True 打 ERROR + 审计日志独立 session 写入
- Phase 9 Wave 2 ✅: 跨租户数据隔离 5 项测试通过 (Dashboard/DataSource/SemanticModel/AuditLog)
- Phase 9 Wave 3 ✅: token_tracker per-node 统计 + 8 AI 节点 track_usage(node=) + 前端 nodes 展示
- 全量测试: 511 passed + vue-tsc 0 errors

## Phase 10: 技术债清理 ✅ 已完成

> 清理 Phase 8 审计遗留的全部技术债 (B3+C5+C6+C8)。

- [x] B3: saved_queries CSV 导出白名单列校验 — ✅ export_saved_query_csv 加载语义层 + extract_allowed_columns + validate_sql(allowed_columns=) + fail-closed 无语义层拒绝; test_saved_query_export.py 5 项测试
- [x] C5: 移除未使用的 Pinia — ✅ npm uninstall pinia + main.ts 移除 createPinia + 删除空 stores 目录
- [x] C6: 移除未使用的 savedQuery API 客户端 — ✅ api/index.ts 移除 SavedQuery 接口和 savedQuery 对象 (约 20 行死代码)
- [x] C8: docker-compose.app.yml — ✅ 文件已存在 (非缺失, 更新文档标记)

### 冒烟测试

- Phase 10 B3 ✅: 无语义层 → 422 fail-closed; 非白名单列 → 422; 白名单列 → 校验通过; 数据源不存在 → 404; 查询不存在 → 404
- Phase 10 C5 ✅: pinia 不在 package.json + main.ts 无 pinia 引用
- Phase 10 C6 ✅: api/index.ts 无 SavedQuery/savedQuery
- 全量测试: 516 passed + vue-tsc 0 errors
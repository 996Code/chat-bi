# 项目状态

## 当前位置
- **阶段**: 演进规划 E1 / graph-feedback-loop（✅ 全部完成，5/5 Wave）
- **状态**: v2 核心交付完成（68 任务）+ E1 演进完成（587 tests passed）
- **前序**: chatbi-v2 Phase 1-10 全部完成（B3+C5+C6+C8 技术债清理收尾）

## OpenSpec 关联
- **活动 change**: `openspec/changes/graph-feedback-loop/`
- **规格状态**: 4/4 artifacts complete（proposal/design/specs/tasks）
- **计划状态**: E1-CONTEXT.md + E1-PLAN.md 已生成（5 Wave / 14 Task）
- **执行进度**:
  - ✅ Wave 1: Task 1.1（linkage 记忆基建 + helper）+ Task 1.2（config 配置项）
  - ✅ Task 2.1: persist_linkage_memory 函数 + 测试
  - ✅ Wave 2: Task 2.2-2.4（persist_warning SSE + 前端 toast + C1/W1/W2 修复）
  - ✅ Wave 3: 图谱 confidence 更新（乐观锁 + 新表对发现 + sync_linkage_to_graph + 整理后同步）
  - ✅ Wave 4: 前端冲突弹框（三选项 + retry 端点）
  - ✅ Wave 5: 双路召回确认 + 端到端验收测试 + 文档
- **下一步**: 选择下一个演进方向（路线图 13 个方向，E1 已完成）
- **演进路线图**: `doc/chatbi-v2/EVOLUTION-ROADMAP.md`（13 个方向，E1 已完成）


## Phase 进度

| Phase | 名称 | 状态 |
|-------|------|------|
| 1 | 基础设施 | ✅ 已完成（代码就绪，含质量改进 58 测试 / 覆盖率 87%） |
| 2 | 语义层与知识图谱 | ✅ 已完成（T012-T018 全部，17/54） |
| 3 | RAG 检索与向量化 | ✅ 已完成（T019-T024 全部，23/54，213 测试 / 覆盖率 83%） |
| 4 | Agent 执行引擎 | ✅ 已完成（T025-T035 全部，34/54，379 测试） |
| 5 | 对话与上下文管理 | ✅ 已完成（T036-T039 全部，38/54，414 测试） |
| 6 | Skills 与反馈系统 | ✅ 已完成（T040-T045 全部） |
| 7 | 前端与交付 | ✅ 已完成（T046-T054 全部，54/54，441 测试） |
| 8 | 补齐闭环 | ✅ 已完成（T060-T068 全部，T055-T059 裁剪，134 tests passed） |
| 9 | 代码走查修复 | ✅ 已完成（H5+M2-M7，511 tests passed） |
| 10 | 技术债清理 | ✅ 已完成（B3+C5+C6+C8，516 tests passed） |

## 已完成内容（Phase 1）

**后端** (`backend/app/`)
- `main.py` — FastAPI 入口 + lifespan
- `core/config.py` — 统一配置（41 个可配置项 + 启动时密钥检测）
- `core/security.py` — bcrypt + JWT(access/refresh) + SQL SELECT-only 校验
- `core/auth.py` — JWT 认证 + RBAC + 多租户 contextvars + 审计写入
- `core/checkpointer.py` — append-only JSONL 会话持久化
- `core/agent_memory.py` — 文件化记忆 + MEMORY.md 索引（200 行截断保护）
- `core/prompt_cache.py` — 静态/动态分层缓存 + boundary
- `core/logging.py` / `redis_client.py` / `milvus_client.py`
- `db/models.py` — 8 个核心业务模型
- `db/session.py` — 懒加载引擎 + 数据库类型适配
- `api/` — 目前只有 `/ping` + `/health`

**前端** (`frontend/src/`) — 骨架就绪
- `main.ts` / `App.vue` / `router/` / `ChatView.vue`（占位）/ `api/client.ts`（JWT 拦截器）

**测试** — 134 passed，覆盖率 83% (`pytest --cov=backend/app`)
- `test_core.py` — 配置/密钥安全/JWT/SQL 校验/Fernet/健康检查（13 个）
- `test_infrastructure.py` — 模型/Checkpointer/记忆/缓存（11 个）
- `test_semantic_schema.py` — 语义层 JSON Schema（13 个，T012）
- `test_semantic_scan.py` — 数据源扫描/外键关系/注释/source 标注（10 个，T013）
- `test_composite_metric.py` — 复合指标展开/schema_context 序列化（8 个，T018）
- `test_datasource_engine.py` — 动态业务库引擎/URL拼接/连接池（8 个，Wave0-preB）
- `test_llm_client.py` — LLM client 单例/中文推断/降级（7 个，Wave0-preC）
- `test_auth.py` — JWT 依赖/RBAC/多租户隔离/审计写入（16 个，T006/T008 补齐）
- `test_integration.py` — HTTP 层 + auth 端到端 + DB 集成（9 个）
- `test_semantic_api.py` — 语义层 CRUD/版本/回滚/diff/多租户 全 HTTP（13 个，T014）

**质量保证**
- 覆盖率门禁 75% (`pytest --cov --cov-fail-under=75`)
- auth.py 覆盖率从 0% → 90%（安全盲区消除）

**基础设施**
- `docker-compose.yml` + `deploy/`（Nginx + .env.example）+ `deploy.sh`
- 数据库已初始化（192.168.99.22，8 张表 + default_tenant + admin 用户）

## 功能裁剪记录

以下功能经确认不再纳入 v2 范围：

| 功能 | 规格编号 | 原状态 | 决策 | 理由 |
|---|---|---|---|---|
| 点赞/点踩 | FBK-001 (部分) | 已实现后删除 | **不恢复** | 轻量反馈价值有限，改 SQL 审核已覆盖核心需求 |
| 语义缓存 | RAG-003 | 已实现后删除 | **不恢复** | 相似问题复用 SQL 场景有限，few-shot 已覆盖 |
| 异步查询 | PERF-03 | 已实现后删除 | **不恢复** | 当前查询量级不需要，同步模式足够 |
| 备份恢复 | OPS-02 | 已实现后删除 | **不恢复** | 运维可用 pg_dump 手动完成，不占产品功能 |
| 负面信号自动触发 | FBK-003 | 已实现后删除 | **不恢复** | 依赖点赞/点踩，随整体裁剪 |
| 改 SQL + 审核 | FBK-001/002 | 已实现后删除 | **考虑恢复** | 核心闭环：用户改 SQL → admin 审核 → 回流知识库 |

## 全面审计记录（2026-07-01）

### 🔴 严重（必须修复）

| # | 问题 | 位置 | 状态 |
|---|---|---|---|
| A1 | **Dashboard SQL 注入漏洞** | `dashboard.py` add_widget/refresh_widget | ✅ T064 已修复 |
| A2 | **init-chatbi.sql 与 models.py 不同步** | `init-chatbi.sql` vs `models.py` | ✅ T065 已修复（auto_create_tables 代码层保证） |

### 🟡 中等（应该修复）

| # | 问题 | 位置 | 状态 |
|---|---|---|---|
| B1 | **CHANGE_ME 启动校验不完整** | `config.py` validate_settings_on_startup | ✅ T066 已修复（critical 6 项拒绝启动 + warning 7 项降级日志） |
| B2 | **22 个模块无测试覆盖** | backend/tests/ | ✅ T067 已补（checkpointer 9 + dashboard 16 + rate_limit 3 + skills 4） |
| B3 | **saved_queries CSV 导出跳过白名单列校验** | `saved_queries.py` export_saved_query_csv | ✅ Phase 10 已修复（加载语义层+白名单校验+fail-closed） |
| B4 | **.env.example 缺 30+ 配置项** | `backend/.env.example` | ✅ T068 已修复（120+ 行，覆盖全部 Settings 字段 + Redis 密码格式注释） |
| B5 | **Dashboard 仅 require_user** | `dashboard.py` | ✅ 不做 (当前场景足够) |

### 🟢 低（可以后续处理）

| # | 问题 | 位置 | 状态 |
|---|---|---|---|
| C1 | **metric_expander.py 整模块死代码** | services/metric_expander.py | ✅ T063 已清理 |
| C2 | **knowledge_graph 两个孤立函数** | services/knowledge_graph.py | ⏳ 遗留 |
| C3 | **ChatView SSE 无超时** | frontend ChatView.vue | ✅ Phase 9 已修复 |
| C4 | **ECharts 实例未 dispose** | frontend ChatView.vue | ✅ Phase 9 已修复 |
| C5 | **Pinia 已安装但未使用** | frontend | ✅ Phase 10 已移除 |
| C6 | **savedQuery API 无 UI** | frontend api/index.ts | ✅ Phase 10 已移除死代码 |
| C7 | **Docker Minio 镜像过老** | docker-compose.infra.yml | ✅ 不影响 (Milvus 内部存储, 兼容即可, 生产部署时更新) |
| C8 | **缺 docker-compose.app.yml** | docker/ | ✅ 已存在 (非缺失) |
| C9 | **Redis 密码格式未文档** | .env.example | ✅ T068 已修复 |

## 遗留技术债
- **TenantMixin 死代码已修复**：Phase 1 的 TenantMixin 定义在 auth.py 但 7 个模型都没继承 → 已迁移到 models.py 并让所有 tenant-scoped 模型继承（对标 v1 #48）
- **多租户 session event 自动注入未实现**：当前是 contextvar + TenantMixin.tenant_filter() 显式过滤（调用方需手动 `.where(...)`），非全局自动注入。auth.py 注释已更正。设计决策: 不引入全局 session event 自动注入。理由: (1) 影响 all queries, bug 会导致全系统隔离失效, 风险 > 收益; (2) 业务库 SQL 执行(T031)不走 ORM, session event 对它无效; (3) 多租户隔离靠 data_source 归属
- **saved_queries CSV 导出跳过白名单列校验** (B3): ✅ Phase 10 已修复 — 加载语义层提取 allowed_columns + fail-closed 无语义层拒绝
- **Dashboard 仅 require_user** (B5): ✅ 不做 (当前场景足够)
- **knowledge_graph.py 的 apply_feedback_signals()** 为死代码（反馈数据流已断）
- **knowledge_graph 两个孤立函数** (C2): mine_implicit_relationships/apply_feedback_signals 仅测试调用，无生产调用路径
- **ChatView SSE 无超时** (C3): ✅ Phase 9 已修复 — AbortController + 5min timeout + onBeforeUnmount 清理
- **ECharts 实例未 dispose** (C4): ✅ Phase 9 已修复 — isDisposed 检测复用 + onBeforeUnmount dispose + resize 监听
- **Pinia 已安装但未使用** (C5): ✅ Phase 10 已移除
- **savedQuery API 无 UI** (C6): ✅ Phase 10 已移除死代码
- **Docker Minio 镜像过老** (C7): ✅ 不影响 (Milvus 内部存储, 兼容即可)
- **缺 docker-compose.app.yml** (C8): ✅ 文件已存在 (非缺失)
- **追问主动优化建议**（REF-002）未实现

## OpenSpec 关联
- **Change**: chatbi-v2
- **路径**: openspec/changes/chatbi-v2/
- **规格**: 10 个 capability specs ✓
- **设计**: design.md ✓
- **任务**: 54 个 tasks ✓

## 活动日志
- 2026-06-05: /ai:spec 完成 — 基于 Claude Code 源码深度解读的完整规格
- 2026-06-08: Phase 1 实现（11 任务 + 20 测试 + 数据库初始化）
- 2026-06-23: 核实状态、勾选 Phase 1、提交 git 基线
- 2026-06-23: 文档结构整改（CLAUDE.md 重写 v2 版 + v1 归档 + tasks 单源）
- 2026-06-23: Phase 2 T012 语义层 JSON Schema（Pydantic v2，13 测试）
- 2026-06-23: 质量改进 — pytest-cov 门禁 75% + 补 auth 测试（T006/T008）+ 修复 TenantMixin 死代码（7 模型未继承，对标 v1 #48）+ 集成测试骨架。测试 33→58，覆盖率 87%
- 2026-06-23: Phase 2 T013 数据源扫描（semantic_scanner.py，10 测试）+ 端到端连真库验证。在 njmind 建 5 张示例业务表（biz_前缀，电商场景）+ 样本数据。端到端发现并修复列注释读取缺陷（PG 无 get_column_comment，改从 col dict.comment 读）。测试 58→68，覆盖率 88%
- 2026-06-23: Phase 2 Wave 0 三前置（Fernet/动态引擎/LLM client，19 测试）+ T018 复合指标（8 测试）。测试 68→95
- 2026-06-23: Phase 2 T014 语义层 CRUD API（13 个全 HTTP 测试）+ 端到端 e2e_scan.py 通过（真实 PG 扫 13 表生成语义层）。修复 conftest 引擎隔离缺陷（StaticPool 共享连接）。测试 95→108
- 2026-06-23: 清除 v1 遗留硬编码（config.py 的 qwen3-30b/localhost:8000/EMPTY/postgres:postgres→CHANGE_ME 占位符 + deploy/.env.example 的 LLM_MODEL + e2e 脚本走 config）。对标 v1 #44，fail-closed 设计
- 2026-06-23: 最小可视化前端（LoginView + DataSourceView + SemanticView）+ dev-token 端点。修复扫描超时（asyncio.gather 并行 + 跳系统表，40s→7s）+ FK 冲突（dev_user→admin_user）+ 前端隐藏系统表退化提示
- 2026-06-23: uv 依赖管理（uv.lock，93 包）+ pyproject 提到项目根 + start-backend.sh 清 shell 环境变量
- 2026-06-23: Phase 2 收尾 — T016 知识图谱 AI 推断（name_pattern 0.6 + ai_inferred 0.7，去重不写回）+ T017 知识图谱演化（mine_implicit_relationships 频繁 JOIN 提升 + apply_feedback_signals 纠正/点赞三态，算法先行 e2e 留 Phase 6）。测试 108→134，覆盖率 83%
- 2026-06-23: 清理环境配置错乱 — 删除根目录 v1 死 env 文件（.env/.env.home/.env.office 含真实密钥被提交 + 误污染 shell 环境）+ 修 gitignore + backend/.env.example 占位符化。config.py 审计：所有外部配置走 settings，无写死
- 2026-06-23: **Phase 3 RAG 检索全链路打通（T019-T024）** — 本地 BGE-large-zh-v1.5（1024维离线 embedding，讯飞 xopkimik26 非标准协议调不通→改本地）+ VectorStore 抽象（Mock/Milvus 可切）+ MilvusVectorStore（HNSW+IP+JSON标量过滤+load）+ 扫描自动建索引(T020) + 语义层变更重建索引(T021) + 两阶段检索(T022: 向量召回top20+score≥0.35+LLM精筛, 宁缺毋滥, 假阳性声明, 无召回不fallback) + 语义缓存(T023: 相似问题复用SQL) + Few-shot(T024: 审核SQL注入prompt最多3条)。本地 docker infra (PG pgvector + Milvus v2.4.0 + etcd + minio)。真实 Milvus 端到端验证：13表索引13条，检索召回5条（销售额→biz_orders）。修复 Milvus collection load bug + score阈值适配 BGE(0.5→0.35)。测试 134→213 passed
- 2026-06-23: **Phase 4 Agent 执行引擎全部完成（T025-T035）** — 5 Wave 严格按依赖链。Claude Code 对标矩阵钉在 03-PLAN.md（query.ts while-true/§3.3 Fail-Closed/§3.5 ask_user即tool/§4 prompt分层/§6.4 熔断器）。T030 SQL三层校验(sqlglot AST+危险函数Anonymous.name+白名单列) + T026 意图识别(5意图Pydantic+confidence降级+strip_visualization) + T031 SQL执行(连接池+asyncio.to_thread+wait_for超时+自动LIMIT) + T029 SQL生成(prompt分层§4+白名单data_type约束+生成即校验) + T032 SQL自愈(错误码映射+保留全部安全规则v1#32+熔断器§6.4) + T033 结果自检(0行/笛卡尔积/全NULL/COUNT=0纯规则) + T034/T035 图表(LLM ECharts+JSON自愈补括号+规则推断降级) + T025 主循环(StateGraph状态机: intent→schema→generate→execute→[self_heal循环]→check→visualize→final, 严格前向+上游失败final(failed)) + T027 预思考 + T028 ask_user(即tool)。测试 213→336 passed (+123)

## 下一步
Phase 10 已完成。技术债已全部清理或标记不影响。

## 活动日志 (续)
- 2026-06-25: **V2 新增 4 功能 + 调度基础设施（DSO-02/04/05 + PERF-03 + OPS-02）** — Wave0: APScheduler 调度器单例(scheduler.py, lifespan 接入, 3 个定时任务: 健康检查/元数据刷新/任务清理, fail-closed 降级) + config 4 个可配置项。Wave1 后端: DSO-02 数据源健康检查(datasource_health.py ping+定时检查+恢复+2端点) + DSO-05 状态监控(AuditLog加data_source_id + GROUP BY聚合端点 query_count/avg_duration/error_rate/slow_count) + DSO-04 元数据自动刷新(semantic_diff.py 抽函数含列级diff + metadata_refresher.py 全量扫→diff→有变更写新版本+合并旧注释) + PERF-03 异步查询(QueryTask模型+3端点提交/轮询/取消+后台create_task跑Agent+进度映射+防重复+定时清理) + OPS-02 备份恢复(pg_dump/psql subprocess+从DATABASE_URL解析参数+confirm二次确认+fail-closed)。Wave2 前端: DataSourceView健康检查/元数据刷新按钮 + ObservabilityView数据源监控表+备份恢复卡片 + ChatView异步执行开关+任务进度卡片+2s轮询。Wave3: 14 新测试(diff5+metrics3+async4+backup2) + 验证(pytest 480 passed / vue-tsc 0 / build 通过 / scheduler日志确认3任务注册 / 5新端点401鉴权可达)
- 2026-06-25: **Phase 7 前端收尾全部完成（T015/T041/T045/T047-T052，54/54）** — 三波交付。Wave1 后端: token_tracker 补全 7 AI 节点 track_usage (OBS-002 token 精确) + prompt_capture.py (contextvar 请求级 prompt 捕获) + GET /conversations/{id}/trace (dump-prompts, DEBUG 持久化) + saved_queries.py (GET 列表/详情)。Wave2 前端: ChatView 内嵌 trace (每步 duration + 🔥token 汇总 + 自愈轮数) + T047 暂停状态徽标 + T048 slash command 菜单 + token 预算; ObservabilityView 重做 (token 统计卡 + dump-prompts Blob 导出); DashboardView 看板 (已保存查询 ECharts 网格)。Wave3 补齐: T015 语义层行内编辑 (PATCH 表/列语义→append-only 新版本+重建索引) + T041 Skills 预览 (POST /skills/preview 用规则跑 SQL)。测试 425→441 (+16: 11 observability + 5 semantic PATCH)。vue-tsc 零错误 + vite build 通过
- 2026-07-02: **Phase 9 代码走查修复全部完成（H5+M2-M7）** — 4 Wave 交付。Wave0 前端: M3 ChatView SSE AbortController+5min timeout+onBeforeUnmount清理 + M4 ECharts isDisposed复用+dispose+resize监听。Wave1 后端: M2 SSE event ID(id:字段+auto-increment seq, 支持Last-Event-ID重连) + M5 Redis降级监控(redis_client.py critical参数+check_redis_health连续3次失败→ERROR+scheduler 60s定时检查) + M7 审计独立提交(3级降级: 独立session→调用方session→logger.error)。Wave2 跨租户: M6 datasource_health/metadata_refresher加tenant_id过滤+data_sources.py传tenant_id+test_tenant_isolation.py 5项测试。Wave3 H5 Token分段: token_tracker.py NodeUsage dataclass+per-node tracking+8 AI模块调用点更新(intent/thinking/generate_sql/heal_sql/generate_chart/generate_reply/question_generator/compress) + ChatView前端nodes展示+CSS。511 tests passed + vue-tsc 0 errors
- 2026-07-02: **Phase 10 技术债清理全部完成（B3+C5+C6+C8）** — B3: saved_queries.py CSV导出补白名单列校验(加载语义层+extract_allowed_columns+fail-closed无语义层拒绝) + test_saved_query_export.py 5项测试。C5: 移除Pinia依赖(npm uninstall+main.ts清理+删除空stores目录)。C6: 移除api/index.ts中未使用的SavedQuery接口和savedQuery客户端(约20行死代码)。C8: docker-compose.app.yml已存在(非缺失,更新文档标记)。516 tests passed + vue-tsc 0 errors — Wave0 安全修复: T064 Dashboard SQL注入(validate_sql校验) + T065 init-chatbi.sql同步(auto_create_tables代码层保证, _add_missing_columns补列)。Wave1 增强: T060 Skills分层子目录(reference/*.md + format_for_prompt(db_type=) + postgresql.md/mysql.md) + T061 Slash command扩展(/ds /sql /chart /explain + 参数解析) + T062 Checkpointer OBS-004一致性校验(turn编号连续性+JSON解析容错+消息为空检测)。Wave2 质量: T066 CHANGE_ME启动校验补全(critical 6项拒绝启动 + warning 7项降级日志) + T067 补关键模块测试(test_checkpointer 9项 + test_dashboard 16项 + test_rate_limit 3项 + test_skills_loader db_type/reference 4项; conftest补_add_missing_columns+LLM环境变量) + T068 .env.example补全(49→120+行, 覆盖全部Settings字段 + Redis密码格式注释)。功能裁剪: T055-T059改SQL审核闭环整条砍掉。134 tests passed

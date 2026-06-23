# 项目状态

## 当前位置
- **阶段**: chatbi-v2 / Phase 2 完成（T012-T018 全部，17/54），下一步 Phase 3
- **状态**: Phase 2 收尾完成，准备规划 Phase 3（RAG 检索）

## Phase 进度

| Phase | 名称 | 状态 |
|-------|------|------|
| 1 | 基础设施 | ✅ 已完成（代码就绪，含质量改进 58 测试 / 覆盖率 87%） |
| 2 | 语义层与知识图谱 | ✅ 已完成（T012-T018 全部，17/54） |
| 3 | RAG 检索与向量化 | 待规划 |
| 4 | Agent 执行引擎 | 待规划 |
| 5 | 对话与上下文管理 | 待规划 |
| 6 | Skills 与反馈系统 | 待规划 |
| 7 | 前端与交付 | 待规划 |

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

## 遗留技术债
- **TenantMixin 死代码已修复**：Phase 1 的 TenantMixin 定义在 auth.py 但 7 个模型都没继承 → 已迁移到 models.py 并让所有 tenant-scoped 模型继承（对标 v1 #48）
- **多租户 session event 自动注入未实现**：当前是 contextvar + TenantMixin.tenant_filter() 显式过滤（调用方需手动 `.where(...)`），非全局自动注入。auth.py 注释已更正。待 T014 有真实查询场景时补 session event
- 前端仅占位，认证/数据源 UI 待 Phase 7
- SQL 危险函数校验（INTO OUTFILE/LOAD_FILE）未实现，留 Phase 4 T030（v1 #46 的另一半）
- 前端 chunk 过大（index.js 1MB），生产部署前需代码分割
- **端到端业务链路测试**待 Phase 2 API 实现后补（注册→登录→数据源→扫描→语义层，已在 test_integration.py 预留）

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

## 下一步
Phase 2 全部完成（T012-T018）。下一步规划 **Phase 3（RAG 检索与向量化，T019-T024）**。
计划文档：`.planning/phases/chatbi-v2/02-PLAN.md`（已完成的 4 Wave）。

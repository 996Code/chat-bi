# 项目状态

## 当前位置
- **阶段**: chatbi-v2 / Phase 2 执行中（T012 完成，12/54）
- **状态**: 下一步 T013（数据源扫描）或 T018（复合指标，可并行）

## Phase 进度

| Phase | 名称 | 状态 |
|-------|------|------|
| 1 | 基础设施 | ✅ 已完成（代码就绪，含质量改进 58 测试 / 覆盖率 87%） |
| 2 | 语义层与知识图谱 | ⏳ 执行中（T012 完成，12/54） |
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

**测试** — 58 passed，覆盖率 87% (`pytest backend/tests/ --cov=app`)
- `test_core.py` — 配置/密钥安全/JWT/SQL 校验/健康检查（9 个）
- `test_infrastructure.py` — 模型/Checkpointer/记忆/缓存（11 个）
- `test_semantic_schema.py` — 语义层 JSON Schema（13 个，T012）
- `test_auth.py` — JWT 依赖/RBAC/多租户隔离/审计写入（16 个，T006/T008 补齐）
- `test_integration.py` — HTTP 层 + auth 端到端 + DB 集成（9 个）

**质量保证**
- 覆盖率门禁 75% (`pytest --cov --cov-fail-under=75`)
- auth.py 覆盖率从 0% → 90%（安全盲区消除）

**基础设施**
- `docker-compose.yml` + `deploy/`（Nginx + .env.example）+ `deploy.sh`
- 数据库已初始化（192.168.99.22，8 张表 + default_tenant + admin 用户）

## 遗留技术债
- **TenantMixin 死代码已修复**：Phase 1 的 TenantMixin 定义在 auth.py 但 7 个模型都没继承 → 已迁移到 models.py 并让所有 tenant-scoped 模型继承（对标 v1 #48）
- 前端仅占位，认证/数据源 UI 待 Phase 7
- SQL 危险函数校验（INTO OUTFILE/LOAD_FILE）未实现，留 Phase 4 T030（v1 #46 的另一半）
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

## 下一步
执行 Phase 2 / T012（语义层 JSON Schema 定义）—— `/ai:do` 推进。
计划文档：`.planning/phases/chatbi-v2/02-PLAN.md`（4 Wave：T012 地基 → T013/T014 扫描+CRUD → T018 复合指标 → T015/T016/T017 前端+知识图谱）。
顺带在 T014 清 Phase 1 遗留债（T006 多租户 + T008 审计测试）。

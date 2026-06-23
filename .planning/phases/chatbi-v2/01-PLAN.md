# Phase 1 计划：基础设施（回溯性）

> ⚠️ **本计划为回溯性记录**：Phase 1 代码已实现后补写，用于让 `/ai:resume`、`/ai:do`、`/ai:check` 工作流正确识别 Phase 1 状态。不是事前规划。
>
> Phase 2+ 起将走正式 `/ai:plan` 流程（事前 PLAN.md → `/ai:do` 执行）。

## Wave 1：项目骨架与数据层（无依赖，可并行）

### T001 后端项目骨架 — `type: setup`
- **read_first**: `doc/chatbi-v2/design.md`（技术栈）、`backend/app/main.py`
- **实现**: FastAPI 入口 + lifespan + `/health` + `/ping`
- **acceptance_criteria**: `GET /health` 返回 `{"status":"ok"}`；`GET /chat-bi/api/v1/ping` 返回 pong
- **状态**: ✅ 已完成（测试 `test_core.TestAppHealth`）

### T003 Docker Compose — `type: setup`
- **read_first**: `docker-compose.yml`、`deploy/`、`design.md` 技术栈
- **实现**: PostgreSQL + Milvus + Redis + Python + Vue + Nginx + supervisord 编排
- **acceptance_criteria**: `docker-compose config` 校验通过；`.env.example` 含占位符
- **状态**: ✅ 已完成（docker-compose.yml + deploy/nginx.conf + deploy.sh）

### T004 数据库模型 — `type: tdd`（Red→Green→Refactor）
- **read_first**: `openspec/changes/chatbi-v2/specs/security-framework/spec.md`、`backend/app/db/models.py`
- **实现**: 8 个核心模型（Tenant/User/DataSource/SemanticModel/Conversation/AuditLog/SavedQuery/Feedback），全部带 tenant_id
- **acceptance_criteria**: 模型 CRUD 正常；AuditLog 支持 success/fail/denied 三态；Feedback 支持 5 种类型
- **状态**: ✅ 已完成（测试 `test_infrastructure.TestDatabaseModels`）

### T002 前端项目骨架 — `type: setup`
- **read_first**: `frontend/src/main.ts`、`vite.config.ts`
- **实现**: Vue3 + TS + Vite + Pinia + router + ChatView（占位）+ api client（JWT 拦截器）
- **acceptance_criteria**: `npx vite` 启动正常；代理到后端 8999
- **状态**: ✅ 已完成（骨架就绪）

## Wave 2：安全与认证（依赖 Wave 1 的 T004 模型）

### T005 JWT 认证 + RBAC — `type: tdd`
- **read_first**: `doc/经验教训.md`（#3 JWT 四字段、#20 refresh 全字段）、`backend/app/core/security.py`、`auth.py`
- **实现**: `get_current_user` / `require_role` / `require_admin` FastAPI 依赖；access/refresh 双 token
- **acceptance_criteria**: JWT 含 user_id/email/tenant_id/role 四字段；refresh token 含全部鉴权字段；RBAC 三角色（admin/user/read_only）
- **状态**: ✅ 已完成（测试 `test_core.TestSecurity`：JWT roundtrip + refresh + SQL 校验）

### T006 多租户框架级隔离 — `type: implement`
- **read_first**: `doc/经验教训.md`（#48 多租户默认隔离 — P0 泄露）、`auth.py`（contextvars + TenantMixin）
- **实现**: contextvars 存 tenant_id + TenantMixin.tenant_filter
- **acceptance_criteria**: 框架层自动注入 tenant_id 过滤，不靠手动 WHERE
- **状态**: ⚠️ 已实现但**缺单元测试**（只测了模型字段，未验证框架级隔离行为）→ **遗留债**

### T007 密钥安全 — `type: tdd`
- **read_first**: `doc/经验教训.md`（#44 密钥硬编码）、`config.py:validate_settings_on_startup`
- **实现**: 启动时检测 SECRET_KEY/FERNET_KEY 的 `CHANGE_ME` 前缀，命中则 raise RuntimeError 拒绝启动
- **acceptance_criteria**: 占位符密钥启动被拒；真实密钥启动通过
- **状态**: ✅ 已完成（测试 `test_core.test_secret_key_is_not_default_in_production_ctx`）

### T008 审计日志 — `type: implement`
- **read_first**: `doc/经验教训.md`（#41 审计三态）、`auth.py:write_audit_log`
- **实现**: `write_audit_log` 写入 AuditLog，受 `audit_enabled` 开关控制
- **acceptance_criteria**: success/fail/denied 三态全覆盖；字段统一
- **状态**: ⚠️ 已实现但**缺单元测试**（只测了模型字段，未验证写入行为）→ **遗留债**

## Wave 3：Agent 基础设施（依赖 Wave 1，独立于 Wave 2）

### T009 Checkpointer 会话持久化 — `type: tdd`
- **read_first**: `doc/Claude-Code-源码深度解读.md`（§8 append-only JSONL）、`backend/app/core/checkpointer.py`
- **实现**: append-only JSONL，双层命名空间 (tenant_id, conversation_id)，写入简单恢复时重建
- **acceptance_criteria**: save_turn + load_conversation 往返一致；get_last_state 正确
- **状态**: ✅ 已完成（测试 `test_infrastructure.TestCheckpointer`）

### T010 Agent 记忆文件化 — `type: tdd`
- **read_first**: `doc/Claude-Code-源码深度解读.md`（§5 Memory 四层 + 200 行截断）、`backend/app/core/agent_memory.py`
- **实现**: 每条记忆独立 .md + MEMORY.md 索引；200 行/25KB 截断保护
- **acceptance_criteria**: save/read/delete 正常；索引超 200 行触发截断
- **状态**: ✅ 已完成（测试 `test_infrastructure.TestAgentMemory`）

### T011 Prompt 分层缓存 — `type: tdd`
- **read_first**: `doc/Claude-Code-源码深度解读.md`（§4.3 Prompt 缓存 + boundary）、`backend/app/core/prompt_cache.py`
- **实现**: 静态段（TTL 缓存）+ 动态段（每次算）+ `PROMPT_DYNAMIC_BOUNDARY` 标记 + invalidate_static
- **acceptance_criteria**: 静态段二次 assemble 不重算；invalidate 后重算
- **状态**: ✅ 已完成（测试 `test_infrastructure.TestPromptCache`）

## 进度汇总

| Task | 类型 | 状态 | 测试 |
|------|------|------|------|
| T001 后端骨架 | setup | ✅ | health/ping |
| T002 前端骨架 | setup | ✅ | (手动) |
| T003 Docker | setup | ✅ | (配置校验) |
| T004 数据库模型 | tdd | ✅ | 4 个 |
| T005 JWT+RBAC | tdd | ✅ | 4 个 |
| T006 多租户 | implement | ⚠️ | **缺** |
| T007 密钥安全 | tdd | ✅ | 1 个 |
| T008 审计日志 | implement | ⚠️ | **缺** |
| T009 Checkpointer | tdd | ✅ | 2 个 |
| T010 Agent 记忆 | tdd | ✅ | 3 个 |
| T011 Prompt 缓存 | tdd | ✅ | 2 个 |

**总计**: 11/11 完成，20 测试通过，2 个缺测试（T006/T008）

## 遗留技术债 → 后续处理建议

- **T006 + T008 测试缺口**：建议在 Phase 2 引入语义层 CRUD API（T014）后补——届时会有 HTTP 层 + 认证中间件，可一起测试框架级多租户隔离和审计写入，比现在单独测更自然
- **SQL 危险函数校验**：Phase 1 的 `security.py:validate_sql_select_only` 只做了 AST SELECT-only，**未覆盖危险函数黑名单**（INTO OUTFILE/LOAD_FILE 等）。这是 v1 #46 的另一半，留给 Phase 4 T030（SQL 三层校验）补全

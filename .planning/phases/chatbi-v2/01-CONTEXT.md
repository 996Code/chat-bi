# Phase 1 上下文：基础设施

## 需求来源

OpenSpec change：`chatbi-v2`
相关规格：`openspec/changes/chatbi-v2/specs/security-framework/spec.md`（认证/多租户/审计）、`openspec/changes/chatbi-v2/design.md`

> ⚠️ 本文件为**回溯性上下文**（Phase 1 代码已实现后补写），用于让 `/ai:resume`、`/ai:do`、`/ai:check` 工作流能正确识别 Phase 1 状态。不是事前规划。

## 决策

### 技术选型（来自 design.md Key Design Decisions）

| 维度 | 选择 | 依据 |
|------|------|------|
| 后端框架 | FastAPI + SQLAlchemy async | async 原生支持，对接 asyncpg/aiomysql |
| 元数据库 | PostgreSQL 一站式 | Milvus + Checkpointer 一站搞定（对标 v1 经验教训） |
| Python 版本 | 3.12+ | 用 `X \| None` 语法，不用 `Optional[X]` |
| 配置 | pydantic-settings + lru_cache | 所有魔法数字集中 config.py，环境变量控制 |
| 认证 | JWT (HS256) + bcrypt | access/refresh 双 token（对标 v1 经验教训 #3/#20） |
| 会话持久化 | append-only JSONL | 写入简单，恢复时重建（对标 Claude Code checkpointer） |
| Agent 记忆 | 文件化 .md + MEMORY.md 索引 | 对标 Claude Code memdir，200 行截断保护 |
| Prompt 缓存 | 静态/动态分层 + boundary | 对标 Claude Code systemPromptSections |

### 实现方式

- **配置层**：`config.py` 统一 41 个可配置项 + `validate_settings_on_startup()` 启动时检测 `CHANGE_ME` 占位符拒绝启动（T007）
- **安全层**：`security.py` 提供 bcrypt 哈希 + JWT(access/refresh) + `validate_sql_select_only()` AST 级 SELECT-only 校验
- **认证层**：`auth.py` 提供 `get_current_user` / `require_role` / `require_admin` FastAPI 依赖 + contextvars 多租户 + `write_audit_log` 审计写入
- **持久化层**：
  - `db/models.py` — 8 个核心模型（Tenant/User/DataSource/SemanticModel/Conversation/AuditLog/SavedQuery/Feedback），全部带 tenant_id
  - `db/session.py` — 懒加载引擎 + 数据库类型适配（mysql/postgresql）
  - `core/checkpointer.py` — JSONL 会话持久化（双层命名空间 tenant_id/conversation_id）
  - `core/agent_memory.py` — 文件化记忆 + 200 行/25KB 截断保护
- **缓存层**：`core/prompt_cache.py` — 静态段（可缓存，TTL）+ 动态段（每次算）+ `PROMPT_DYNAMIC_BOUNDARY` 标记
- **基础设施客户端**：`redis_client.py`（含降级处理）、`milvus_client.py`、`logging.py`（TimedRotatingFileHandler）

### 不做的事（来自 proposal.md Non-Goals）

- OAuth/SSO 登录（邮箱密码够用）
- Oracle/ClickHouse 支持（后续方言扩展）
- 移动端 App（Web-first 响应式即可）

## 规格参考

- `openspec/changes/chatbi-v2/specs/security-framework/spec.md` — 认证/授权/多租户/审计 SHALL 语句
- `openspec/changes/chatbi-v2/specs/agent-execution-engine/spec.md` — Checkpointer/记忆/Prompt 缓存（基础设施部分）
- `openspec/changes/chatbi-v2/design.md` — 完整架构决策

## 测试现状

20 passed（`pytest backend/tests/`）：
- `test_core.py`（9 个）：配置加载、密钥检测、密码哈希、JWT access/refresh、SQL SELECT-only、健康检查、/ping
- `test_infrastructure.py`（11 个）：8 模型 CRUD、Checkpointer 存取、记忆存取/删除/截断、Prompt 缓存命中/失效

## 遗留技术债

- **T006 多租户隔离缺单元测试**：contextvars + TenantMixin 已实现，但未验证"框架级自动注入 tenant_id 过滤"行为
- **T008 审计日志缺单元测试**：`write_audit_log` 已实现，但未验证 success/fail/denied 三态写入
- 建议在 Phase 2 引入 CRUD API 后补这两个测试（需要 HTTP 层 + 认证中间件，借 Phase 2 的 API 一起测更自然）

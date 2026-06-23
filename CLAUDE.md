# ChatBI v2 — 项目说明（Agent 必读）

> 自然语言转 SQL 的 BI 平台 — 让不会 SQL 的人也能自助完成数据查询和可视化
>
> **v2 从零重构**，基于 Claude Code 源码深度解读 + 海泰 ChatBI 代码分析 + v1 的 48 条经验教训。

---

## ⚡ 快速上手（Agent 第一件事）

```bash
# 了解当前状态（唯一真相源）
cat .planning/STATE.md

# 跑测试确认环境 OK
.venv/bin/python -m pytest backend/tests/ -q

# 看任务进度
openspec list                    # 显示 N/54 tasks
cat openspec/changes/chatbi-v2/tasks.md
```

## 当前状态

- **Phase 1（基础设施）已完成**：11/11 任务，20 测试通过
- **下一步**：Phase 2（语义层），从 T012 开始
- 详见 `.planning/STATE.md`（唯一状态真相源）

---

## 开发工作流：`/ai:*` 命令

本项目用一套自定义斜杠命令驱动 spec-driven 开发：

```
/ai:spec  →  /ai:plan  →  /ai:do  →  /ai:check  →  /ai:resume / /ai:debug
 需求定义     规划        执行       全面审查       恢复/调试
```

- 命令定义在 `.claude/commands/ai/`
- 规格产出在 `openspec/changes/chatbi-v2/`（OpenSpec 方法论）
- 执行计划在 `.planning/phases/chatbi-v2/`（GSD 结构）
- **任务清单的真相源**：`openspec/changes/chatbi-v2/tasks.md`（不是 doc/ 版）

## 必读文档（按优先级）

| 优先级 | 文档 | 为什么读 |
|--------|------|---------|
| P0 | `.planning/STATE.md` | 当前状态、已完成、遗留债、下一步（**唯一状态真相源**） |
| P0 | `doc/chatbi-v2/proposal.md` | 5 条设计法则 + P0/P1/P2 目标 + 关键决策 |
| P0 | `doc/chatbi-v2/design.md` | Leader-Worker 架构 + Agent 7 步执行流程 + State Store + Prompt 分层 |
| P1 | `doc/Claude-Code-源码深度解读.md` | 设计法则来源（while-true/Fail-Closed/ask_user/压缩/Prompt分层） |
| P1 | `doc/海泰ChatBI完整代码分析.md` | BI 领域打法（Stage状态机/复合指标/两阶段RAG/Skills分层） |
| P1 | `doc/经验教训.md` | v1 的 48 条坑，**别重蹈覆辙**（尤其"安全降级无声"根本模式） |
| P2 | `doc/chatbi-v2/specs/*/spec.md` | 11 个领域规格（按 Phase 需要时读） |

> ⚠️ v1 文档已归档到 `doc/v1-archive/`，**不是当前开发依据**。需要对照 v1 历史时再翻。

---

## 5 条设计法则（不可妥协）

1. **执行引擎是 while(true) 不是一次调用** — 生成 SQL → 执行 → 自愈 → 自检 → 修正 → 再生成
2. **安全默认是"否"（Fail-Closed）** — 出问题时显式失败，绝不静默放行（v1 教训根本模式）
3. **"问用户"就是普通 Tool** — ask_user 不是特殊机制，Agent 不确定时自己决定暂停
4. **压缩 = 压缩 + 状态补偿** — 压缩后补回语义层 + 当前 SQL + 筛选条件 + Skills
5. **Prompt 分层可缓存** — 语义层/Skills 放 boundary 前（可缓存），用户问题/历史放后面

## BI 领域关键原则（来自海泰 + v1 教训）

- **宁缺毋滥**：检索/匹配失败返回空 + 明确提示，**禁止 prompt 出现"不要返回空"**（v1 #29 幻觉根源）
- **单次执行硬约束**：execute_sql 最多 1 次 + 自愈最多 2 轮
- **SQL 三层校验**：AST 拒绝非 SELECT + 危险函数拒绝 + 白名单列名（v1 #46 只做了一半）
- **追问维度继承**：normalized_question = 锚点未重写维度 + 本轮新增维度

---

## 项目结构

```
chat-bi/
├── backend/app/
│   ├── main.py              # FastAPI 入口 + lifespan
│   ├── api/                 # API 路由（目前只有 /ping、/health）
│   ├── core/                # config / security / auth / checkpointer /
│   │                        # agent_memory / prompt_cache / logging / redis / milvus
│   ├── db/                  # models（8 张表）/ session（懒加载引擎）
│   ├── ai/ models/ schemas/ services/   # Phase 2+ 占位
│   └── tests/               # 20 passed
├── frontend/src/            # Vue3 + TS 骨架（ChatView 占位 + api client JWT 拦截器）
├── openspec/changes/chatbi-v2/   # ★ OpenSpec 规格（任务真相源）
├── .planning/                    # ★ GSD 状态（STATE/ROADMAP/PROJECT + phases/）
├── doc/
│   ├── chatbi-v2/               # v2 方案文档（proposal/design/specs）
│   ├── Claude-Code-源码深度解读.md   # 设计法则来源
│   ├── 海泰ChatBI完整代码分析.md     # BI 领域打法来源
│   ├── 经验教训.md                  # v1 48 条坑
│   └── v1-archive/               # v1 历史文档（非当前依据）
└── .claude/commands/ai/         # /ai:* 工作流命令
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12, FastAPI, SQLAlchemy async (asyncpg) |
| AI | LangGraph + 自建 Agent 封装（取 deepagents 思路） |
| 元数据库 | **PostgreSQL**（业务 + Checkpointer + Store） |
| 向量库 | Milvus + BGE-large-zh-v1.5 (1024 维) |
| 缓存 | Redis 7 |
| SQL 校验 | SQLGlot（AST） |
| 前端 | Vue 3, TypeScript, ECharts, Pinia |

## 开发规范（硬约束）

- **测试**：`.venv/bin/python -m pytest backend/tests/ -q`，20 passed 为底线，**变更后必跑**
- **Python 3.12+**：用 `X | None`，不用 `Optional[X]`
- **配置集中**：所有魔法数字/超时/阈值在 `app/core/config.py`，环境变量控制（v1 #1）
- **API 前缀**：`settings.api_prefix`（默认 `/chat-bi/api/v1`）
- **测试隔离**：conftest.py 用 SQLite in-memory，不碰真实数据库（v1 #5）
- **JWT 四字段**：user_id / email / tenant_id / role（v1 #3/#20）
- **密钥安全**：启动时检测 `CHANGE_ME` 占位符拒绝启动（v1 #44）
- **多租户**：框架级隔离（contextvars + TenantMixin），不靠手动 WHERE（v1 #48）
- **审计三态**：success / fail / denied 全覆盖（v1 #41）

## 启动命令

```bash
# 后端
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8999 &

# 前端
cd frontend && npx vite --host 0.0.0.0 --port 5173 &

# 测试
.venv/bin/python -m pytest backend/tests/ -v
```

## 遗留技术债（进 Phase 2 前建议补）

- **T006 多租户隔离** + **T008 审计日志**：代码已实现但缺单元测试（只测了模型字段，没验证框架级行为）
- 建议在 Phase 2 引入 CRUD API 后一起补（需要 HTTP 层 + 认证中间件）

---

*最后更新: 2026-06-23*

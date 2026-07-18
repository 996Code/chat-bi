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

- **全部 10 Phase 已完成**：68 任务 (T001-T068)，552 测试通过
- **Phase 10（技术债清理）已收尾**，详见 `.planning/STATE.md`（唯一状态真相源）
- **新增能力**（2026-07）：🕸️知识图谱中间件（SchemaGraph 可视化 + JOIN 路径驱动）+ 💾对话全量落库（问了就留 + 主动确认内容留存）+ 🔗记忆与图谱集成（E1 进行中，linkage 记忆沉淀）
- **演进规划**：`doc/chatbi-v2/EVOLUTION-ROADMAP.md`（13 个方向），当前推进 E1 graph-feedback-loop
- 开源文档已就绪：README.md / LICENSE (MIT) / CONTRIBUTING.md / SECURITY.md / CHANGELOG.md

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
6. **对话问了就留** — start 事件落骨架行（问题先存），pipeline 末尾落完整行（复用 turn 号）；闲聊/确认/中断全量留存，刷新不丢提问记录

## BI 领域关键原则（来自海泰 + v1 教训）

- **宁缺毋滥**：检索/匹配失败返回空 + 明确提示，**禁止 prompt 出现"不要返回空"**（v1 #29 幻觉根源）
- **单次执行硬约束**：execute_sql 最多 1 次 + 自愈最多 2 轮
- **SQL 三层校验**：AST 拒绝非 SELECT + 危险函数拒绝 + 白名单列名（v1 #46 只做了一半）
- **追问维度继承**：normalized_question = 锚点未重写维度 + 本轮新增维度
- **追问语义断裂修复**：检索阶段无对话历史 → intent 层问题改写（展开为完整问题）+ agent 层表继承（检索结果 ∪ 上轮表）

---

## 项目结构

```
chat-bi/
├── backend/app/
│   ├── main.py              # FastAPI 入口 + lifespan
│   ├── api/                 # API 路由 (chat/stream/data-sources/observability/...)
│   ├── core/                # config / security / auth / sql_validator /
│   │                        # llm_client / token_tracker / prompt_capture / ...
│   ├── db/                  # models（8 张表）/ session（懒加载引擎）
│   ├── ai/                  # Agent 核心管线 (agent/intent/thinking/sql_agent/state_store/...)
│   ├── schemas/             # Pydantic 模型 (semantic_layer/...)
│   ├── services/            # 业务服务 (retriever/embedder/graph_service/indexer/...)
│   └── tests/               # 552 passed
├── frontend/src/            # Vue3 + TS (9 个功能页面 + api 封装层)
├── openspec/changes/chatbi-v2/   # ★ OpenSpec 规格（任务真相源）
├── .planning/                    # ★ GSD 状态（STATE/ROADMAP/PROJECT + phases/）
├── doc/
│   ├── chatbi-v2/               # v2 方案文档（proposal/design/specs）
│   ├── Claude-Code-源码深度解读.md   # 设计法则来源
│   ├── 海泰ChatBI完整代码分析.md     # BI 领域打法来源
│   ├── 经验教训.md                  # v1 48 条坑
│   └── v1-archive/               # v1 历史文档（非当前依据）
├── skills/                       # SKILL.md 业务规则
├── docker/                       # Docker Compose (infra + app)
├── deploy/                       # 生产部署 (nginx + .env)
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

- **测试**：`.venv/bin/python -m pytest backend/tests/ -q`，552 passed 为底线，**变更后必跑**
- **Python 3.12+**：用 `X | None`，不用 `Optional[X]`
- **配置集中**：所有魔法数字/超时/阈值在 `app/core/config.py`，环境变量控制（v1 #1）
- **API 前缀**：`settings.api_prefix`（默认 `/chat-bi/api/v1`）
- **测试隔离**：conftest.py 用 SQLite in-memory，不碰真实数据库（v1 #5）
- **JWT 四字段**：user_id / email / tenant_id / role（v1 #3/#20）
- **密钥安全**：启动时检测 `CHANGE_ME` 占位符拒绝启动（v1 #44）
- **多租户**：框架级隔离（contextvars + TenantMixin），不靠手动 WHERE（v1 #48）
- **审计三态**：success / fail / denied 全覆盖（v1 #41）

## 环境搭建（首次 clone 后）

```bash
# 1. 安装 uv (若未装): https://docs.astral.sh/uv/
#    macOS: brew install uv

# 2. 创建虚拟环境 + 按 uv.lock 锁定的精确版本装依赖 (可复现)
uv sync                    # 在项目根执行, 自动用 .python-version 选 Python

# 3. 配置 .env (从 .env.example 复制后填真实值)
cp backend/.env.example backend/.env
# 必须填: SECRET_KEY / FERNET_KEY (生成: python -c "import secrets;print(secrets.token_urlsafe(32))")
#         DATABASE_URL / LLM_URL / LLM_MODEL / LLM_API_KEY 等 (config.py 里 CHANGE_ME_ 的项)

# 4. 前端依赖
cd frontend && npm install
```

`pyproject.toml` + `uv.lock` 在项目根。`.python-version` 固定 3.12.13，`uv.lock` 锁定 93 个依赖包的精确版本——保证任何人 `uv sync` 后环境一致。`.venv/` 也在项目根（被 .gitignore 忽略）。

## 启动命令

```bash
# 后端 (推荐用启动脚本, 自动隔离 shell 环境变量对 .env 的干扰)
./start-backend.sh            # 或 ./start-backend.sh --reload (热重载)
# 等效手动: uv run uvicorn app.main:app --host 0.0.0.0 --port 8999 --app-dir backend

# 前端
cd frontend && npx vite --host 0.0.0.0 --port 5173 &

# 测试
uv run pytest backend/tests/ -v
```

## 遗留技术债

- knowledge_graph 孤儿函数未清理
- REF-002 跟进建议未实现
- 详见 `.planning/STATE.md`

---

*最后更新: 2026-07-18（E1 Wave 1 进行中）*

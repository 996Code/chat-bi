<p align="center">
  <h1 align="center">💬 ChatBI</h1>
  <p align="center">
    <strong>自然语言驱动的智能 BI 平台</strong><br>
    用对话代替 SQL，让每个人都能查询数据
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/Vue-3.5-4FC08D?style=flat-square&logo=vue.js" alt="Vue" />
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License" />
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat-square&logo=postgresql" alt="PostgreSQL" />
  <img src="https://img.shields.io/badge/Milvus-2.4-00A1EA?style=flat-square" alt="Milvus" />
</p>

---

## ✨ 功能亮点

🧠 **智能问答** — 用自然语言提问，自动识别意图、检索 Schema、生成 SQL、执行查询、渲染图表

🔄 **自愈循环** — SQL 执行失败时自动修复（最多 2 轮），三层安全校验确保只读安全

📊 **交互式图表** — 支持 ECharts 多种图表类型，自然语言切换图表，看板拖拽布局

🏗️ **语义层** — 自动扫描数据源构建语义模型，支持版本管理和回滚

🔍 **RAG 检索** — 向量检索 + LLM 精排两阶段 Schema 召回，Few-shot 示例注入

📝 **业务规则** — SKILL.md 格式定义业务规则，热更新，按数据库方言自动匹配

💬 **多轮对话** — 上下文压缩 + 状态补偿，追问时自动继承上次查询维度

🔐 **企业级安全** — JWT 认证、Fernet 加密、SQL 注入三层拦截、多租户隔离、审计日志

---

## 🖼️ 系统截图

<table>
  <tr>
    <td><b>💬 智能问答</b></td>
    <td><b>📊 图表可视化</b></td>
  </tr>
  <tr>
    <td>
      输入自然语言问题，自动走完意图识别 → Schema 检索 → SQL 生成 → 执行 → 图表渲染全流程，支持 SSE 流式展示管线进度。
    </td>
    <td>
      自动生成 ECharts 图表，支持柱状图、折线图、饼图等类型，可对话切换图表类型。看板页支持 GridStack 拖拽布局。
    </td>
  </tr>
  <tr>
    <td><b>🏗️ 语义层编辑</b></td>
    <td><b>🔐 可观测性</b></td>
  </tr>
  <tr>
    <td>
      自动扫描数据源生成语义模型，支持表/列语义编辑、关系图谱、版本对比与一键回滚。
    </td>
    <td>
      审计日志、Token 用量统计、Prompt 调试、慢查询监控、数据源健康检查，运维全景可观测。
    </td>
  </tr>
</table>

---

## 🏗️ 核心架构

```
                        ┌─────────────────────────────────────────────────┐
                        │              用户 (自然语言提问)                  │
                        └────────────────────┬────────────────────────────┘
                                             │
                        ┌────────────────────▼────────────────────────────┐
                        │           FastAPI (JWT + 限流 + 审计)            │
                        └────────────────────┬────────────────────────────┘
                                             │
               ┌─────────────────────────────▼─────────────────────────────┐
               │                   Agent 状态机 (7 步管线)                  │
               │                                                             │
               │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
               │  │ 意图识别  │→│ Schema   │→│ 预思考    │→│ SQL 生成  │  │
               │  │ Intent   │  │ 检索 RAG │  │ Thinking │  │ + 三层   │  │
               │  └──────────┘  └──────────┘  └──────────┘  │ 校验     │  │
               │                                              └────┬─────┘  │
               │  ┌──────────┐  ┌──────────┐  ┌──────────┐       │        │
               │  │ 图表生成  │←│ 结果自检  │←│ 执行+自愈 │←──────┘        │
               │  │ Chart    │  │ Check    │  │ Execute  │                 │
               │  └──────────┘  └──────────┘  └──────────┘                 │
               │                                                             │
               │  辅助模块:  Skills 业务规则 │ Few-shot 示例 │ Memory 记忆   │
               │             上下文压缩     │ 多轮对话管理                  │
               └─────────────────────────────────────────────────────────────┘
                                             │
               ┌─────────────────────────────▼─────────────────────────────┐
               │                      基础设施层                             │
               │  PostgreSQL (元数据+pgvector)  │  Milvus (向量检索)        │
               │  Redis (缓存+限流)            │  BGE-large-zh (Embedding) │
               │  LLM (OpenAI 兼容接口)         │  业务数据库 (MySQL/PG)     │
               └────────────────────────────────────────────────────────────┘
```

---

## 🛠️ 技术栈

### 后端

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.12+ |
| 框架 | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| AI 编排 | LangGraph + LangChain |
| LLM | OpenAI 兼容接口 (讯飞/DeepSeek/OpenAI...) |
| 向量库 | Milvus 2.4 |
| Embedding | BGE-large-zh-v1.5 (本地 1024 维) |
| SQL 解析 | SQLGlot |
| 认证 | JWT (python-jose) + bcrypt |
| 加密 | Fernet (数据源密码) |
| 缓存 | Redis 7 |
| 调度 | APScheduler |

### 前端

| 类别 | 技术 |
|------|------|
| 框架 | Vue 3.5 + TypeScript 5.6 |
| 构建 | Vite 6 |
| UI | Element Plus 2.8 |
| 图表 | ECharts 5.5 |
| 看板 | GridStack 11 |
| 导出 | ExcelJS |

### 基础设施

| 服务 | 用途 |
|------|------|
| PostgreSQL 16 (pgvector) | 元数据 + 向量索引 |
| Redis 7 | 缓存 + 限流 + 分布式锁 |
| Milvus 2.4 | 高性能向量检索 |
| etcd + MinIO | Milvus 依赖 |

---

## 🚀 快速开始

### 1. 前置条件

- Docker Desktop（运行 PG + Redis + Milvus）
- Python 3.12+（推荐用 [uv](https://github.com/astral-sh/uv) 管理）
- Node.js 18+
- LLM API Key（OpenAI 兼容接口，如讯飞 MAAS / DeepSeek / OpenAI）

### 2. 启动基础设施

```bash
# 克隆项目
git clone https://github.com/996Code/chat-bi.git
cd chat-bi

# 启动 PG + Redis + Milvus (首次拉镜像约 3 分钟, Milvus 启动约 90 秒)
docker compose -f docker/docker-compose.infra.yml up -d

# 确认全部 healthy
docker compose -f docker/docker-compose.infra.yml ps
```

### 3. 配置环境变量

```bash
cd backend
cp .env.example .env
```

编辑 `backend/.env`，填入你的 LLM 配置（其他已有本地默认值）：

```bash
LLM_URL=https://你的LLM接口/v1       # OpenAI 兼容接口
LLM_API_KEY=你的API密钥               # 必填, CHANGE_ME 占位符会拒绝启动
LLM_MODEL=你的模型名称                # 如 deepseek-chat
```

### 4. 安装依赖 + 下载模型

```bash
# 安装 Python 依赖 (uv 自动创建 .venv)
uv sync

# 下载 Embedding 模型 (~1.3GB, 首次需要)
uv run python backend/scripts/download_embedding_model.py
```

### 5. 初始化数据库

```bash
# 建元数据表 (FastAPI 启动时自动建表, 也可手动)
./start-backend.sh &
sleep 5 && curl http://localhost:8999/health
# 看到 {"status":"ok"} 后 Ctrl+C 停掉

# (可选) 建示例业务库 + 测试数据
docker exec -i chatbi-infra-postgres psql -U root -d postgres -c "CREATE DATABASE chatbi_sample;"
docker exec -i chatbi-infra-postgres psql -U root -d chatbi_sample < backend/scripts/seed_business_schema.sql
SEED_DB_HOST=localhost SEED_DB_NAME=chatbi_sample uv run python backend/scripts/seed_business_data.py
```

### 6. 启动服务

```bash
# 后端 (推荐启动脚本, 自动隔离 shell 环境变量)
./start-backend.sh

# 前端 (新终端)
cd frontend && npm install && npx vite --host 0.0.0.0 --port 5173
```

### 7. 访问

| 地址 | 说明 |
|------|------|
| http://localhost:5173 | 前端界面 |
| http://localhost:8999/docs | API 文档 (Swagger) |
| http://localhost:8999/health | 健康检查 |

---

## 📁 项目结构

```
chat-bi/
├── backend/
│   ├── app/
│   │   ├── ai/                  # 🧠 Agent 核心管线
│   │   │   ├── agent.py         # 状态机主循环 (7 步)
│   │   │   ├── intent.py        # 意图识别
│   │   │   ├── thinking.py      # 预思考 (选表理由+陷阱)
│   │   │   ├── sql_agent.py     # SQL 生成 (Prompt 分层)
│   │   │   ├── sql_healer.py    # SQL 自愈
│   │   │   ├── chart_agent.py   # 图表生成
│   │   │   ├── compressor.py    # 上下文压缩
│   │   │   ├── state_store.py   # 对话状态持久化
│   │   │   ├── recall.py        # Agent 记忆召回
│   │   │   └── ...
│   │   ├── api/                 # 🌐 API 端点
│   │   │   ├── chat.py          # 同步问答
│   │   │   ├── chat_stream.py   # SSE 流式问答
│   │   │   ├── data_sources.py  # 数据源管理
│   │   │   ├── observability.py # 可观测性
│   │   │   └── ...
│   │   ├── core/                # ⚙️ 核心基础设施
│   │   │   ├── config.py        # 集中配置 (41+ 环境变量)
│   │   │   ├── auth.py          # JWT + 角色权限
│   │   │   ├── security.py      # Fernet 加密
│   │   │   ├── sql_validator.py # SQL 三层校验
│   │   │   ├── llm_client.py    # LLM 客户端
│   │   │   └── ...
│   │   ├── db/                  # 💾 数据模型
│   │   ├── schemas/             # 📋 Pydantic 模型
│   │   └── services/            # 🔧 业务服务
│   │       ├── retriever.py     # RAG 两阶段检索
│   │       ├── embedder.py      # Embedding 服务
│   │       ├── semantic_scanner.py  # 语义层自动扫描
│   │       ├── skills_loader.py # Skills 规则加载
│   │       └── ...
│   ├── tests/                   # 🧪 测试
│   └── .env.example             # 环境变量模板
├── frontend/
│   └── src/
│       ├── views/               # 📱 页面组件
│       ├── api/                 # 🔌 API 封装
│       └── router/              # 🚦 路由
├── docker/                      # 🐳 Docker Compose
├── deploy/                      # 🚀 生产部署
├── skills/                      # 📝 业务规则 (SKILL.md)
├── doc/                         # 📚 设计文档
│   ├── chatbi-v2/               # v2 设计文档 (proposal, design, specs)
│   └── v1-archive/              # v1 归档 (只读)
└── openspec/                    # 📋 任务追踪
```

---

## ⚙️ 关键配置

所有配置通过环境变量管理，`backend/.env.example` 包含完整模板和说明。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://root:root@localhost:5432/chatbi` | PostgreSQL 连接 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接 |
| `MILVUS_URL` | `http://localhost:19530` | Milvus 连接 |
| `LLM_URL` | — | LLM 接口地址 (必填) |
| `LLM_API_KEY` | — | LLM API 密钥 (必填) |
| `LLM_MODEL` | — | LLM 模型名称 (必填) |
| `EMBEDDING_BACKEND` | `local` | Embedding 后端 (`local` / `api`) |
| `SQL_EXECUTION_TIMEOUT` | `30` | SQL 执行超时 (秒) |
| `SQL_MAX_ROWS` | `10000` | 结果最大行数 |
| `RATE_LIMIT_QUERIES_PER_MINUTE` | `30` | 查询限流 (次/分钟) |
| `DEBUG` | `false` | 调试模式 (开启后持久化 Prompt) |
| `SECRET_KEY` | `CHANGE_ME_*` | JWT 签名密钥 (生产必须修改) |

> ⚠️ 带 `CHANGE_ME_` 前缀的占位符在生产环境必须替换，否则后端拒绝启动。

---

## 🧪 测试

```bash
# 运行全部测试
uv run pytest backend/tests/ -q

# 带覆盖率
uv run pytest --cov=backend/app --cov-fail-under=75

# 只跑核心测试
uv run pytest backend/tests/test_auth.py backend/tests/test_infrastructure.py backend/tests/test_v2_new_features.py -q
```

---

## 🚀 生产部署

```bash
# 全栈 Docker 部署 (PG + Milvus + Backend + Frontend + Nginx)
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env 填入生产配置
./deploy.sh
```

部署架构：

```
用户 → Nginx (:28080)
         ├── / → Vue 前端 (静态文件)
         └── /chat-bi/api/ → FastAPI 后端 (:8999)
                              ├── PostgreSQL (元数据)
                              ├── Milvus (向量检索)
                              ├── Redis (缓存)
                              └── 业务数据库 (MySQL/PG)
```

---

## 📖 API 文档

启动后端后访问 http://localhost:8999/docs 查看完整的 Swagger API 文档。

主要端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | 同步问答 |
| POST | `/chat/stream` | SSE 流式问答 |
| GET/POST | `/data-sources` | 数据源管理 |
| GET | `/semantic-models` | 语义层查看 |
| GET | `/dashboards` | 看板管理 |
| GET | `/conversations` | 对话列表 |
| GET | `/health/detail` | 系统状态 |

---

## 🤝 贡献

欢迎贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解：

- 开发环境搭建
- 代码规范
- 提交 PR 流程

---

## 🔒 安全

发现安全漏洞？请阅读 [SECURITY.md](SECURITY.md) 了解报告方式。

核心安全特性：
- **SQL 三层校验**：AST 白名单 + 危险函数拦截 + 列名白名单
- **JWT + 多租户隔离**：租户间数据完全隔离
- **Fernet 加密**：数据源密码加密存储
- **启动占位符检测**：`CHANGE_ME_*` 密钥拒绝启动

---

## 📜 变更记录

详见 [CHANGELOG.md](CHANGELOG.md)。

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

---

## 🙏 致谢

本项目的设计灵感来自：

- **Claude Code** — Agent 执行引擎、Prompt 分层、Fail-Closed 设计理念
- **海泰 ChatBI** — BI 领域 Agent 管线、语义层、Schema 两阶段检索
- **48 条 V1 经验教训** — 从 V1 踩坑中总结的安全、架构、可用性法则

感谢所有开源社区的贡献者。

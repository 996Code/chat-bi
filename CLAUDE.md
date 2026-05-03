# ChatBI — Claude Code 项目说明

> 自然语言转 SQL 的 BI 平台 — 让不会 SQL 的人也能自助完成数据查询和可视化
>
> 新成员接手项目前请**先按顺序读完以下文档**。

---

## 必读文档（按优先级）

| 优先级 | 文档 | 读它的原因 |
|--------|------|-----------|
| P0 | `doc/项目概览.md` | 产品核心价值、技术栈、架构决策 |
| P0 | `doc/需求文档.md` | 所有开发的依据，88 个 v1 需求清单 |
| P0 | `doc/路线图.md` | 4 个 Phase 规划、当前进度、依赖关系 |
| P1 | `doc/规划文档.md` | 详细架构设计、数据库表设计、API 规范 |
| P1 | `doc/经验教训.md` | 开发过程中的坑和解决方案（执行阶段 9 条实战经验） |
| P1 | `doc/方法论.md` | 从 0 做项目的完整方法论（规划 → 执行 → 交付） |
| P2 | `doc/检查清单.md` | 各 Phase 验收检查项 |
| P2 | `doc/测试用例.md` | 测试策略和场景清单 |
| P2 | `doc/项目状态.md` | 当前项目状态 |
| P2 | `doc/操作手册.md` | 部署、配置、API 文档、故障排查、Docker 部署指南 |
| P2 | `README.md` | 项目文档索引、需求追溯矩阵 |

## 评审与审计文档（按需查阅）

| 文档 | 说明 |
|------|------|
| `doc/backend/代码评审-最终.md` | 代码质量审计 |
| `doc/安全审计-最新.md` | 安全合规审计 |
| `doc/架构评审-最新.md` | 架构质量评审 |
| `doc/前端评审-最新.md` | 前端代码评审 |

## 每日开发记录

`doc/开发记录-*.md` — 每日工作会话记录。

---

## 项目结构速览

```
chat-bi/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── api/                 # API 路由（auth, datasource, query 等 9 个模块）
│   │   ├── ai/                  # LangGraph AI 管道（intent → schema → sql → execute → self-heal）
│   │   ├── core/                # 配置、安全、日志、限流
│   │   ├── db/                  # SQLAlchemy 模型、会话
│   │   └── services/            # 业务服务层
│   ├── tests/                   # 190 个测试用例
│   ├── seed.py                  # 业务数据初始化（可重复运行）
│   └── .env.example             # 环境变量模板
├── frontend/
│   └── src/
│       ├── views/               # 页面（ChatView, DataModelView, DataSource 等）
│       ├── components/          # 组件（ChartRenderer, DataDictionary）
│       ├── stores/              # Pinia 状态管理
│       └── api/                 # Axios 封装
├── doc/                       # 统一文档目录（规划、评审、审计、部署）
├── .planning/                   # 仅保留 PROJECT.md
├── deploy/                      # Docker 部署配置
├── deploy.sh                    # 一键部署脚本
├── docker-compose.yml           # 容器编排
├── Dockerfile                   # 单容器镜像构建
└── README.md                   # 项目入口文档
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12, FastAPI, SQLAlchemy async (aiomysql) |
| AI | LangGraph, LangChain, OpenAI-compatible API |
| 数据库 | MySQL 9.4 |
| 缓存 | Redis 7（查询缓存、限流、登录锁） |
| 前端 | Vue 3, TypeScript, Element Plus, ECharts, Pinia, Vue Router |
| 安全 | JWT(HS256), bcrypt, Fernet 加密, SQLGlot AST 校验 |
| 部署 | Docker + docker-compose + nginx + supervisord |

## 开发规范

- **测试**：每次变更后运行 `.venv/bin/python -m pytest tests/ -x -q`，190 passed 为底线
- **配置**：所有魔法数字集中在 `app/core/config.py`，通过环境变量控制
- **API 前缀**：统一使用 `settings.api_prefix`（默认 `/chat-bi/api/v1`）
- **端口**：后端默认 8999，前端 dev 5173
- **测试隔离**：conftest.py 强制使用 SQLite in-memory，不碰真实数据库
- **seed 脚本**：必须支持重复运行（truncate + insert），UUID 固定

## Docker 部署

```bash
cp deploy/.env.example deploy/.env   # 首次：编辑配置
./deploy.sh                          # 一键部署
```

详细部署指南见 `doc/操作手册.md` 第 11 章。

## 核心 AI 查询流程

```
用户提问 → 意图识别 → RAG 表检索 → SQL 生成 → AST 安全校验 → 执行查询 → SQL 自愈(最多2轮) → 图表推断 → 返回结果
```

## 注意事项

1. **不要跳过测试** — 190 个测试覆盖认证、数据源、查询、审计等核心功能
2. **不要硬编码配置** — 所有超时、阈值、密钥走 `config.py`
3. **不要直接操作数据库** — 通过 SQLAlchemy ORM，测试用 SQLite
4. **API 路径统一** — 走 `/chat-bi/api/v1` 前缀
5. **数据源必须真实可连** — seed 数据必须指向真实数据库，不能用假库名
6. **JWT 必须包含所有鉴权字段** — user_id、email、tenant_id、role

---

*最后更新: 2026-05-03*

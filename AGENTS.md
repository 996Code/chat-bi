# AGENTS.md

> 跨 AI agent 工具的通用项目约定。工具专属的详细说明在 `CLAUDE.md`（Claude Code）。
> 本文件保持极简，只放任何 agent 都需要知道的硬约束。

## 先读这个

1. `.planning/STATE.md` — 当前状态（**唯一状态真相源**）
2. `CLAUDE.md` — 完整项目说明、必读文档、设计法则
3. `openspec/changes/chatbi-v2/tasks.md` — 任务清单（**唯一任务真相源**）

## 不可妥协的约束

- **跑测试再交付**：`.venv/bin/python -m pytest backend/tests/ -q`，必须 709 passed
- **Python 3.12+ 语法**：`X | None`，不要 `Optional[X]`
- **配置不硬编码**：所有魔法数字/超时/阈值进 `app/core/config.py`，走环境变量
- **安全 Fail-Closed**：任何降级都要 WARNING + 告警，安全相关功能出问题拒绝而非放行（v1 根本模式）
- **宁缺毋滥**：检索/匹配失败返回空 + 明确提示，禁止"不要返回空"类指令（诱导幻觉）
- **不碰真实数据库**：测试用 SQLite in-memory（conftest.py 隔离）
- **密钥不进 git**：`.env` 已被 `.gitignore` 忽略，只提交 `.env.example`（占位符 `CHANGE_ME_*`）

## 常用命令

```bash
# 测试
uv run pytest backend/tests/ -q

# 启动后端 (推荐启动脚本, 自动隔离 shell 环境变量干扰)
./start-backend.sh

# 任务进度
openspec list

# 前端
cd frontend && npx vite --host 0.0.0.0 --port 5173
```

## 别碰

- `doc/Claude-Code-源码深度解读.md`、`doc/海泰ChatBI完整代码分析.md`、`doc/经验教训.md` — 是 v2 的设计依据，只读不改
- `doc/v1-archive/` — v1 历史文档，不是当前开发依据
- `backend/.env` — 本地真实密钥，不提交（已在 .gitignore）

## 新增文档（必读）

- `doc/architecture-v2.md` — 完整架构图（11 章，含代码引用索引）
- `doc/code-reference-index.md` — 460+ 关键定义 file:line 索引
- `doc/learning-roadmap.md` — 7 阶段学习路线图

## 开发工作流

本项目用 `/ai:*` 斜杠命令（定义在 `.claude/commands/ai/`）：`/ai:spec` → `/ai:plan` → `/ai:do` → `/ai:check`。详见各命令文件。

#!/usr/bin/env bash
# ChatBI v2 后端启动脚本
#
# 作用: 隔离 shell 全局环境变量对 .env 的覆盖 (pydantic-settings 优先级 环境变量 > .env)
# 不修改你的全局环境, 只在这个子进程里清掉干扰变量, 让 ChatBI 用 backend/.env
#
# 用法:
#   ./start-backend.sh            # 默认 0.0.0.0:8999
#   ./start-backend.sh --reload   # 开发热重载

set -e

# 脚本所在目录 = 项目根 (脚本放在项目根下)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 隔离 shell 全局环境变量 (防止覆盖 .env) ──────────────────
# 这些变量可能被其他项目/工具设在 shell 里 (如 .zshrc / launchctl)
# pydantic-settings 优先级: 环境变量 > .env 文件, 不清会用错配置
unset DATABASE_URL \
      REDIS_URL \
      MILVUS_URL MILVUS_TOKEN \
      LLM_URL LLM_BASE_URL LLM_MODEL LLM_API_KEY \
      EMBEDDING_URL EMBEDDING_MODEL EMBEDDING_API_KEY \
      SECRET_KEY FERNET_KEY \
      2>/dev/null || true

echo "✓ 已隔离 shell 环境变量, ChatBI 将使用 backend/.env"

# ── 启动 ─────────────────────────────────────────────────────
VENV=".venv/bin/python"
if [ ! -f "$VENV" ]; then
    echo "✗ 找不到 .venv, 请先创建虚拟环境"
    exit 1
fi

if [ "$1" = "--reload" ]; then
    echo "→ 启动后端 (热重载): http://localhost:8999  docs: http://localhost:8999/docs"
    exec $VENV -m uvicorn app.main:app \
        --host 0.0.0.0 --port 8999 \
        --app-dir backend \
        --reload
else
    echo "→ 启动后端: http://localhost:8999  docs: http://localhost:8999/docs"
    exec $VENV -m uvicorn app.main:app \
        --host 0.0.0.0 --port 8999 \
        --app-dir backend
fi

#!/bin/sh
set -e

# Generate SECRET_KEY if not provided
if [ -z "$SECRET_KEY" ]; then
  echo "[entrypoint] Generating SECRET_KEY..."
  export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

# Generate DATA_SOURCE_ENCRYPTION_KEY if not provided (must be 32 bytes for Fernet)
if [ -z "$DATA_SOURCE_ENCRYPTION_KEY" ]; then
  echo "[entrypoint] Generating DATA_SOURCE_ENCRYPTION_KEY..."
  export DATA_SOURCE_ENCRYPTION_KEY=$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
fi

# Apply defaults for optional vars
export APP_ENV="${APP_ENV:-production}"
export APP_PORT="${APP_PORT:-8000}"

# Database: default to host machine MySQL via host.docker.internal
# - If using docker-compose with-mysql profile, set DATABASE_URL=mysql+aiomysql://root:yjt_mysql@mysql:3306/chatbi
# - If using external MySQL, set DATABASE_URL=mysql+aiomysql://user:pass@IP:3306/chatbi
export DATABASE_URL="${DATABASE_URL:-mysql+aiomysql://root:yjt_mysql@host.docker.internal:3306/chatbi}"

# Redis: default to host machine Redis via host.docker.internal
# - If using docker-compose with-redis profile, set REDIS_URL=redis://redis:6379/0
# - If using external Redis, set REDIS_URL=redis://IP:6379/0
export REDIS_URL="${REDIS_URL:-redis://host.docker.internal:6379/0}"

export LLM_BASE_URL="${LLM_BASE_URL:-https://coding.dashscope.aliyuncs.com/v1}"
export LLM_MODEL="${LLM_MODEL:-qwen3.6-plus}"
export CORS_ORIGINS="${CORS_ORIGINS:-[\"*\"]}"
export FRONTEND_URL="${FRONTEND_URL:-http://localhost:8080}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "[entrypoint] App environment: $APP_ENV"
echo "[entrypoint] Database: $(echo $DATABASE_URL | sed 's/\/\/.*@/\/\/***@/')"
echo "[entrypoint] Redis: $(echo $REDIS_URL | sed 's/\/\/.*@/\/\/***@/')"

exec "$@"

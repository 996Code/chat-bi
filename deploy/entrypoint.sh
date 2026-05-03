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
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./chatbi.db}"
export LLM_BASE_URL="${LLM_BASE_URL:-https://api.openai.com/v1}"
export LLM_MODEL="${LLM_MODEL:-gpt-4o}"
export CORS_ORIGINS="${CORS_ORIGINS:-[\"*\"]}"
export FRONTEND_URL="${FRONTEND_URL:-http://localhost:8080}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "[entrypoint] App environment: $APP_ENV"
echo "[entrypoint] Database: $(echo $DATABASE_URL | sed 's/\/\/.*@/\/\/***@/')"

exec "$@"

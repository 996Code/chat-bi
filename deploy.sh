#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE="$PROJECT_DIR/.env.example"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo "========================================"
echo "  ChatBI 一键部署脚本"
echo "========================================"
echo ""

# ---- Step 0: 检查环境 ----
log_info "检查环境..."

if ! command -v docker &> /dev/null; then
  log_error "Docker 未安装，请先安装 Docker"
  exit 1
fi

if ! docker compose version &> /dev/null; then
  log_error "Docker Compose 未安装，请使用 Docker 20.10+ 或安装 docker-compose-plugin"
  exit 1
fi

if ! command -v git &> /dev/null; then
  log_warn "git 未安装，跳过代码更新"
  SKIP_PULL=true
else
  SKIP_PULL=false
fi

# ---- Step 1: 初始化 .env ----
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$PROJECT_DIR/.env.home" ]; then
    log_info ".env 不存在，使用 .env.home 模板创建"
    cp "$PROJECT_DIR/.env.home" "$ENV_FILE"
  elif [ -f "$PROJECT_DIR/.env.example" ]; then
    log_info ".env 不存在，使用 .env.example 模板创建"
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
  else
    log_error "未找到环境模板文件"
    exit 1
  fi
fi

# ---- Step 2: 更新代码 ----
if [ "$SKIP_PULL" = false ]; then
  log_info "更新代码..."
  cd "$PROJECT_DIR"

  # Check if this is a git repo
  if git rev-parse --git-dir &> /dev/null; then
    # Stash local changes if any
    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
      log_warn "检测到本地修改，正在 stash"
      git stash
    fi

    git pull origin $(git rev-parse --abbrev-ref HEAD) || {
      log_error "git pull 失败"
      exit 1
    }

    # Pop stash if we stashed
    if git stash list &> /dev/null; then
      log_info "恢复本地修改"
      git stash pop || log_warn "stash 恢复冲突，请手动解决"
    fi

    # If .env was lost during git pull, recreate from template
    if [ ! -f "$ENV_FILE" ]; then
      if [ -f "$PROJECT_DIR/.env.home" ]; then
        cp "$PROJECT_DIR/.env.home" "$ENV_FILE"
      elif [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
      else
        log_error "未找到 .env 模板文件"
        exit 1
      fi
      log_info ".env 已重新创建"
    fi
  else
    log_warn "不是 git 仓库，跳过代码更新"
  fi
else
  log_warn "跳过代码更新（git 未安装）"
fi

# ---- Step 3: 停止旧容器 ----
log_info "停止旧容器..."
cd "$PROJECT_DIR"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down --remove-orphans 2>/dev/null || true

# Final check: ensure .env exists (Dockerfile COPY needs it)
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$PROJECT_DIR/.env.home" ]; then
    cp "$PROJECT_DIR/.env.home" "$ENV_FILE"
  elif [ -f "$PROJECT_DIR/.env.example" ]; then
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
  else
    log_error "未找到 .env 模板文件"
    exit 1
  fi
  log_info ".env 已创建"
fi

# ---- Step 4: 构建并启动 ----
# 检查是否需要启动内置 MySQL/Redis
# 如果 DATABASE_URL 包含 "mysql:3306" 或 "redis:6379"（Docker 内部主机名），自动启用对应 profile
PROFILES=""
source "$ENV_FILE" 2>/dev/null || true

# 最终检查：确保 .env 存在（Dockerfile COPY 需要）
if [ ! -f "$ENV_FILE" ]; then
  log_warn ".env 不存在，自动从模板创建"
  if [ -f "$PROJECT_DIR/.env.home" ]; then
    cp "$PROJECT_DIR/.env.home" "$ENV_FILE"
  elif [ -f "$PROJECT_DIR/.env.example" ]; then
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
  else
    log_error "未找到 .env 模板文件"
    exit 1
  fi
fi

if echo "${DATABASE_URL:-}" | grep -q "@mysql:"; then
  PROFILES="$PROFILES --profile with-mysql"
  log_info "检测到使用内置 MySQL，启用 with-mysql profile"
fi

if echo "${REDIS_URL:-}" | grep -q "//redis:"; then
  PROFILES="$PROFILES --profile with-redis"
  log_info "检测到使用内置 Redis，启用 with-redis profile"
fi

log_info "构建并启动新容器..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" $PROFILES build --no-cache
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" $PROFILES up -d --build

# ---- Step 5: 检查状态 ----
echo ""
log_info "容器状态:"
docker compose -f "$COMPOSE_FILE" ps

echo ""
log_info "等待后端启动..."
sleep 5

# Health check
MAX_RETRIES=30
RETRY=0
while [ $RETRY -lt $MAX_RETRIES ]; do
  if curl -sf http://127.0.0.1:${CHATBI_PORT:-28080}/chat-bi/health > /dev/null 2>&1; then
    log_info "ChatBI 启动成功！"
    echo ""
    echo "  访问地址: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${CHATBI_PORT:-28080}/chat-bi/"
    echo "  健康检查: http://127.0.0.1:${CHATBI_PORT:-28080}/chat-bi/health"
    echo ""
    log_info "查看日志: docker compose logs -f chatbi"
    log_info "停止服务: docker compose down"
    log_info "启动内置 MySQL/Redis: docker compose --profile with-mysql --profile with-redis up -d"
    exit 0
  fi
  RETRY=$((RETRY + 1))
  echo -n "."
  sleep 2
done

echo ""
log_error "后端未在预期时间内启动，请检查日志:"
echo "  docker compose logs chatbi"
exit 1
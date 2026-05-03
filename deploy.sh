#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
ENV_FILE="$PROJECT_DIR/deploy/.env"
ENV_EXAMPLE="$PROJECT_DIR/deploy/.env.example"

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
  log_warn "deploy/.env 不存在，从 .env.example 复制"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  log_warn "请编辑 $ENV_FILE 填入实际配置"
  log_warn "按回车继续，或 Ctrl+C 退出修改配置"
  read -r
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

# ---- Step 4: 构建并启动 ----
log_info "构建并启动新容器..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build

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
  if curl -sf http://127.0.0.1:${CHATBI_PORT:-8080}/health > /dev/null 2>&1; then
    log_info "ChatBI 启动成功！"
    echo ""
    echo "  访问地址: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${CHATBI_PORT:-8080}"
    echo "  健康检查: http://127.0.0.1:${CHATBI_PORT:-8080}/health"
    echo ""
    log_info "查看日志: docker compose logs -f chatbi"
    log_info "停止服务: docker compose down"
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

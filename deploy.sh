#!/bin/bash
# ============================================================
# ChatBI v2 — 一键部署脚本
#
# 用法:
#   ./deploy.sh              # 首次部署（构建 + 启动）
#   ./deploy.sh --rebuild    # 重新构建并启动
#   ./deploy.sh --stop       # 停止所有服务
#   ./deploy.sh --restart    # 重启所有服务
#   ./deploy.sh --logs       # 查看日志
#   ./deploy.sh --status     # 查看服务状态
#   ./deploy.sh --clean      # 停止并清理所有数据（危险！）
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1"; }

# ── 检查依赖 ──────────────────────────────────────────────

check_prerequisites() {
    log_step "检查依赖..."

    local missing=()

    if ! command -v docker &>/dev/null; then
        missing+=("docker")
    fi

    if ! command -v docker compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
        missing+=("docker-compose")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        log_error "缺少依赖: ${missing[*]}"
        echo ""
        echo "安装指南:"
        echo "  Docker: https://docs.docker.com/get-docker/"
        echo "  Docker Compose 已包含在 Docker Desktop 中"
        exit 1
    fi

    log_info "依赖检查通过 (Docker + Docker Compose)"
}

# ── 环境文件 ──────────────────────────────────────────────

setup_env() {
    log_step "检查环境配置..."

    if [ ! -f "deploy/.env" ]; then
        if [ -f "deploy/.env.example" ]; then
            log_warn "deploy/.env 不存在，从 .env.example 复制"
            cp deploy/.env.example deploy/.env
            log_warn "请编辑 deploy/.env 填入真实配置后重新运行"
            exit 1
        else
            log_error "deploy/.env 和 deploy/.env.example 都不存在"
            exit 1
        fi
    fi

    # 检查是否有 CHANGE_ME 占位符
    if grep -q "CHANGE_ME" deploy/.env 2>/dev/null; then
        log_error "deploy/.env 中存在 CHANGE_ME 占位符，请先替换为真实密钥"
        echo ""
        echo "生成密钥:"
        echo "  python3 -c \"import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(32))\""
        echo "  python3 -c \"import secrets; print('FERNET_KEY=' + secrets.token_urlsafe(32))\""
        exit 1
    fi

    log_info "环境配置就绪"
}

# ── 命令实现 ──────────────────────────────────────────────

cmd_deploy() {
    check_prerequisites
    setup_env

    log_step "构建并启动所有服务..."
    docker compose --env-file deploy/.env up -d --build

    log_info "等待服务就绪..."
    sleep 5

    # 健康检查
    echo ""
    log_step "健康检查..."

    # PostgreSQL
    if docker compose exec -T postgres pg_isready -U postgres &>/dev/null 2>&1; then
        log_info "PostgreSQL  ✓"
    else
        log_warn "PostgreSQL  ✗ (可能还在启动)"
    fi

    # Redis
    if docker compose exec -T redis redis-cli ping &>/dev/null 2>&1; then
        log_info "Redis       ✓"
    else
        log_warn "Redis       ✗ (可能还在启动)"
    fi

    # Backend
    if curl -sf http://localhost:8999/health &>/dev/null 2>&1; then
        log_info "Backend     ✓ (http://localhost:8999)"
    else
        log_warn "Backend     ✗ (可能还在启动)"
    fi

    # Frontend
    if curl -sf http://localhost:28080 &>/dev/null 2>&1; then
        log_info "Frontend    ✓ (http://localhost:28080)"
    else
        log_warn "Frontend    ✗ (可能还在启动)"
    fi

    echo ""
    log_info "部署完成！"
    echo ""
    echo "  前端:  http://localhost:28080"
    echo "  后端:  http://localhost:8999"
    echo "  API:   http://localhost:8999/chat-bi/api/v1"
    echo "  Health: http://localhost:8999/health"
    echo ""
    echo "  查看日志: ./deploy.sh --logs"
    echo "  停止服务: ./deploy.sh --stop"
}

cmd_stop() {
    log_step "停止所有服务..."
    docker compose --env-file deploy/.env down
    log_info "服务已停止"
}

cmd_restart() {
    log_step "重启所有服务..."
    docker compose --env-file deploy/.env restart
    log_info "服务已重启"
}

cmd_logs() {
    docker compose --env-file deploy/.env logs -f --tail=100
}

cmd_status() {
    echo ""
    echo "ChatBI v2 服务状态"
    echo "=================="
    docker compose --env-file deploy/.env ps
    echo ""

    # 端口占用
    echo "端口监听:"
    for port in 5432 6379 19530 8999 28080; do
        if lsof -i :$port &>/dev/null 2>&1; then
            echo "  :$port  ✓"
        else
            echo "  :$port  ✗"
        fi
    done
}

cmd_clean() {
    log_warn "这将删除所有容器、数据卷和镜像！"
    echo ""
    read -p "确认删除？输入 'yes' 继续: " confirm
    if [ "$confirm" != "yes" ]; then
        log_info "已取消"
        exit 0
    fi

    log_step "停止并删除容器..."
    docker compose --env-file deploy/.env down -v

    log_step "清理构建缓存..."
    docker builder prune -f 2>/dev/null || true

    log_info "清理完成"
}

# ── 入口 ──────────────────────────────────────────────────

case "${1:-}" in
    --rebuild)
        log_info "重新构建模式"
        check_prerequisites
        setup_env
        docker compose --env-file deploy/.env build --no-cache
        docker compose --env-file deploy/.env up -d
        log_info "重新构建完成"
        ;;
    --stop)
        cmd_stop
        ;;
    --restart)
        cmd_restart
        ;;
    --logs)
        cmd_logs
        ;;
    --status)
        cmd_status
        ;;
    --clean)
        cmd_clean
        ;;
    --help|-h)
        echo "ChatBI v2 部署脚本"
        echo ""
        echo "用法: ./deploy.sh [选项]"
        echo ""
        echo "选项:"
        echo "  (无参数)      首次部署（构建 + 启动）"
        echo "  --rebuild     重新构建并启动"
        echo "  --stop        停止所有服务"
        echo "  --restart     重启所有服务"
        echo "  --logs        查看实时日志"
        echo "  --status      查看服务状态"
        echo "  --clean       停止并清理所有数据（危险！）"
        echo "  --help        显示此帮助"
        ;;
    *)
        cmd_deploy
        ;;
esac

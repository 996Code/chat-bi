#!/bin/bash
# ============================================================
# ChatBI v2 — 一键部署脚本
#
# 用法:
#   ./deploy.sh              # 首次部署（构建 + 启动 + 初始化数据）
#   ./deploy.sh --init       # 仅初始化数据（元数据 + 业务演示数据）
#   ./deploy.sh --rebuild    # 重新构建并启动
#   ./deploy.sh --stop       # 停止所有服务
#   ./deploy.sh --restart    # 重启所有服务
#   ./deploy.sh --logs       # 查看日志
#   ./deploy.sh --status     # 查看服务状态
#   ./deploy.sh --clean      # 停止并清理所有数据（危险！）
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# deploy.sh 可能在项目根目录或 deploy/ 子目录, 统一找到项目根
if [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    PROJECT_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../docker-compose.yml" ]; then
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    echo "[ERROR] 找不到 docker-compose.yml, 请在项目根目录或 deploy/ 目录运行" >&2
    exit 1
fi
cd "$PROJECT_DIR"

# Compose 文件
COMPOSE_FILES="-f docker-compose.yml"

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

    if ! docker compose version &>/dev/null 2>&1; then
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
            echo ""
            echo "必填项:"
            echo "  POSTGRES_PASSWORD  — 元数据库密码"
            echo "  SAMPLE_DB_PASSWORD — 业务演示库密码"
            echo "  ADMIN_PASSWORD     — 管理员密码"
            echo "  LLM_URL / LLM_MODEL / LLM_API_KEY — LLM 服务"
            echo "  SECRET_KEY / FERNET_KEY — 安全密钥"
            echo ""
            echo "生成密钥:"
            echo "  python3 -c \"import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(32))\""
            echo "  python3 -c \"import secrets; print('FERNET_KEY=' + secrets.token_urlsafe(32))\""
            exit 1
        else
            log_error "deploy/.env 和 deploy/.env.example 都不存在"
            exit 1
        fi
    fi

    # 检查是否有 CHANGE_ME 占位符
    if grep -q "CHANGE_ME" deploy/.env 2>/dev/null; then
        log_error "deploy/.env 中存在 CHANGE_ME 占位符，请先替换为真实值"
        echo ""
        echo "生成密钥:"
        echo "  python3 -c \"import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(32))\""
        echo "  python3 -c \"import secrets; print('FERNET_KEY=' + secrets.token_urlsafe(32))\""
        exit 1
    fi

    log_info "环境配置就绪"
}

# ── docker compose 封装 ──────────────────────────────────

dc() {
    docker compose --env-file deploy/.env $COMPOSE_FILES "$@"
}

# ── 健康检查 ──────────────────────────────────────────────

health_check() {
    echo ""
    log_step "健康检查..."

    # PostgreSQL
    if dc exec -T postgres pg_isready -U "${POSTGRES_USER:-root}" -d "${POSTGRES_DB:-chatbi}" &>/dev/null 2>&1; then
        log_info "PostgreSQL    ✓ (:5432)"
    else
        log_warn "PostgreSQL    ✗ (可能还在启动)"
    fi

    # 业务演示库
    if dc exec -T sample-db pg_isready -U "${SAMPLE_DB_USER:-postgres}" -d "${SAMPLE_DB_NAME:-chatbi_ecom}" &>/dev/null 2>&1; then
        log_info "Sample DB     ✓ (:5433)"
    else
        log_warn "Sample DB     ✗ (可能还在启动)"
    fi

    # Redis
    if dc exec -T redis redis-cli --no-auth-warning -a "${REDIS_PASSWORD:-redis_pass}" ping &>/dev/null 2>&1; then
        log_info "Redis         ✓ (:6379)"
    else
        log_warn "Redis         ✗ (可能还在启动)"
    fi

    # Backend
    if curl -sf http://localhost:8999/health &>/dev/null 2>&1; then
        log_info "Backend       ✓ (http://localhost:8999)"
    else
        log_warn "Backend       ✗ (可能还在启动，或 LLM/Embedding 未配)"
    fi

    # Frontend
    if curl -sf http://localhost:28080 &>/dev/null 2>&1; then
        log_info "Frontend      ✓ (http://localhost:28080)"
    else
        log_warn "Frontend      ✗ (可能还在启动)"
    fi
}

# ── 判断是否已初始化 ──────────────────────────────────────

is_initialized() {
    # 检查元数据库是否有 admin 用户
    dc exec -T postgres psql -U "${POSTGRES_USER:-root}" -d "${POSTGRES_DB:-chatbi}" \
        -t -A -c "SELECT COUNT(*) FROM users WHERE id='admin_user'" 2>/dev/null | grep -q "1"
}

# ── 命令实现 ──────────────────────────────────────────────

cmd_deploy() {
    check_prerequisites
    setup_env

    log_step "构建并启动所有服务..."
    dc up -d --build

    log_info "等待服务就绪..."
    sleep 10

    health_check

    # 检查是否需要初始化
    if is_initialized; then
        log_info "元数据已初始化，跳过"
    else
        echo ""
        log_warn "首次部署！需要初始化数据，正在执行..."
        cmd_init
    fi

    echo ""
    log_info "部署完成！"
    print_access_info
}

cmd_init() {
    check_prerequisites
    setup_env

    log_step "初始化数据..."

    # 确保 backend 镜像已构建
    log_info "构建 init 容器..."
    dc build backend

    # 运行 init service (一次性)
    log_info "执行 seed_meta.py + seed_ecom_data.py..."
    dc --profile init up init

    log_info "数据初始化完成"
    echo ""
    echo "  元数据库: 租户 default_tenant + admin 用户 + 示例电商库数据源"
    echo "  业务演示库: 121 张表 + 电商示例数据"
    echo ""
    echo "  下一步: 前端登录 → 数据源页扫描 → 聊天页提问"
}

cmd_stop() {
    log_step "停止所有服务..."
    dc down
    log_info "服务已停止"
}

cmd_restart() {
    log_step "重启所有服务..."
    dc restart
    log_info "服务已重启"
}

cmd_logs() {
    dc logs -f --tail=100
}

cmd_status() {
    echo ""
    echo "ChatBI v2 服务状态"
    echo "=================="
    dc ps
    echo ""

    # 端口占用
    echo "端口监听:"
    for port in 5432 5433 6379 19530 8999 28080; do
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
    dc down -v

    log_step "清理构建缓存..."
    docker builder prune -f 2>/dev/null || true

    log_info "清理完成"
}

print_access_info() {
    echo ""
    echo "  ┌─────────────────────────────────────────────┐"
    echo "  │  ChatBI v2 已就绪                            │"
    echo "  │                                              │"
    echo "  │  前端:   http://localhost:28080               │"
    echo "  │  后端:   http://localhost:8999                │"
    echo "  │  API:    http://localhost:8999/chat-bi/api/v1 │"
    echo "  │  健康检查: http://localhost:8999/health        │"
    echo "  │                                              │"
    echo "  │  查看日志: ./deploy.sh --logs                 │"
    echo "  │  停止服务: ./deploy.sh --stop                 │"
    echo "  └─────────────────────────────────────────────┘"
}

# ── 入口 ──────────────────────────────────────────────────

case "${1:-}" in
    --init)
        cmd_init
        ;;
    --rebuild)
        log_info "重新构建模式"
        check_prerequisites
        setup_env
        dc build --no-cache
        dc up -d
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
        echo "  (无参数)      首次部署（构建 + 启动 + 自动初始化）"
        echo "  --init        仅初始化数据（元数据 + 业务演示数据）"
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

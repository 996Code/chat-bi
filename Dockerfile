# Stage 1: Frontend build
FROM node:20-alpine AS frontend-builder

# Use domestic mirror for npm
RUN npm config set registry https://registry.npmmirror.com

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Stage 2: Backend dependencies
FROM python:3.12-slim AS backend-builder

WORKDIR /app/backend
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir . -i https://mirrors.aliyun.com/pypi/simple/

# Stage 3: Combined runtime (Debian-based, same libc as builder)
FROM python:3.12-slim

# System deps (nginx + supervisor)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true && \
    apt-get update && \
    apt-get install -y --no-install-recommends nginx supervisor wget && \
    rm -rf /var/lib/apt/lists/*

# Backend packages from builder
COPY --from=backend-builder /usr/local /usr/local

# Backend source
COPY backend/ /app/backend/
WORKDIR /app/backend

# Frontend static files
COPY --from=frontend-builder /app/frontend/dist /usr/share/nginx/html

# Nginx + supervisor config
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf
COPY deploy/nginx.conf /etc/nginx/sites-enabled/default
COPY deploy/supervisord.conf /etc/supervisor/conf.d/chatbi.conf

# Entrypoint: generate secrets on first run, then start supervisor
RUN mkdir -p /var/log/supervisor /var/log/nginx /var/run /etc/nginx/sites-enabled
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

EXPOSE 28080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:28080/chat-bi/health || exit 1

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/chatbi.conf"]

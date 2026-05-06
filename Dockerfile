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
RUN pip install --no-cache-dir --prefix=/install . -i https://mirrors.aliyun.com/pypi/simple/

# Stage 3: Combined runtime
FROM nginx:1.25-alpine

# System deps
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories && \
    apk add --no-cache supervisor python3 && \
    ln -sf python3 /usr/bin/python

# Backend
COPY --from=backend-builder /install /usr/local
COPY backend/ /app/backend/
WORKDIR /app/backend

# Frontend static files
COPY --from=frontend-builder /app/frontend/dist /usr/share/nginx/html

# Nginx + supervisor config
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/supervisord.conf /etc/supervisor/conf.d/chatbi.conf

# Entrypoint: generate secrets on first run, then start supervisor
RUN mkdir -p /var/log/supervisor /var/log/nginx /var/run
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/health || exit 1

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/chatbi.conf"]

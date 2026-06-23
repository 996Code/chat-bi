FROM node:20-alpine AS build

WORKDIR /app
COPY package.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html

# 默认 nginx 配置（docker-compose 会用 deploy/nginx.conf 覆盖）
# 如果单独构建前端镜像，使用自带的简洁配置
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
# 构建阶段：编译 Vue 3 + TypeScript
FROM node:20-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# 构建时注入后端地址（由 docker-compose 传入）
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# 构建时注入高德 Web 端 JS API 配置
ARG VITE_AMAP_WEB_JS_KEY
ENV VITE_AMAP_WEB_JS_KEY=$VITE_AMAP_WEB_JS_KEY
ARG VITE_AMAP_SECURITY_CODE
ENV VITE_AMAP_SECURITY_CODE=$VITE_AMAP_SECURITY_CODE

RUN npm run build

# 运行阶段：Nginx 托管静态文件 + 反向代理
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

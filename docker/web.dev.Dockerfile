# syntax=docker/dockerfile:1
# AETHER MIGRATE — Web SPA Dockerfile (DEV)
# Uses node:20-alpine + nginx:alpine for local development.

FROM node:20-alpine AS builder
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY apps/web/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=builder /app/dist /usr/share/nginx/html
COPY apps/web/nginx.conf /etc/nginx/nginx.conf
RUN adduser -D -u 1001 appuser && \
    chown -R appuser:appuser /usr/share/nginx/html /var/cache/nginx /var/log/nginx /etc/nginx
USER 1001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q -O- http://127.0.0.1:8080/ > /dev/null || exit 1
CMD ["nginx", "-g", "daemon off;"]

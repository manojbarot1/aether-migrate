# syntax=docker/dockerfile:1
# AETHER MIGRATE — Web SPA Dockerfile
# Stage 1: Node.js build using Red Hat UBI 9 Node.js minimal image
# Stage 2: Nginx runtime using Red Hat UBI 9 Nginx image

# ---------------------------------------------------------------------------
# Stage 1: builder — npm install + vite build
# NOTE: Use Red Hat UBI9 nodejs-20-minimal for compliance
# ---------------------------------------------------------------------------
FROM registry.redhat.io/ubi9/nodejs-20-minimal:latest AS builder

WORKDIR /app

# Copy package manifests
COPY apps/web/package.json apps/web/package-lock.json* ./

# Install dependencies
RUN npm ci --prefer-offline

# Copy source
COPY apps/web/ ./

# Build production assets
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: runtime — nginx serves the static assets
# NOTE: registry.redhat.io/ubi9/nginx-122:latest is the Red Hat nginx image
# ---------------------------------------------------------------------------
FROM registry.redhat.io/ubi9/nginx-122:latest AS runtime

# Copy built assets to nginx html directory
COPY --from=builder /app/dist /usr/share/nginx/html

# Custom nginx config to enable SPA routing (handle 404 → index.html)
COPY apps/web/nginx.conf /etc/nginx/nginx.conf

# nginx-122 image already runs as non-root (UID 1001)
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]

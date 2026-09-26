# syntax=docker/dockerfile:1.7
# Caddy edge proxy, built from source so Go and module security fixes land without
# waiting for an upstream release. Runs unprivileged on high ports (8080/8443).
ARG CADDY_VERSION=v2.11.4
FROM golang:1.26.8-alpine3.24 AS build
ARG CADDY_VERSION
RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch "${CADDY_VERSION}" https://github.com/caddyserver/caddy.git .
# Security floor for transitive dependencies (see Trivy in CI). Bump as advisories land.
RUN go get golang.org/x/crypto@v0.57.0 golang.org/x/net@v0.59.0 golang.org/x/text@v0.42.0 \
        google.golang.org/grpc@v1.84.0 \
    && go mod tidy
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/caddy ./cmd/caddy && /out/caddy version

FROM alpine:3.24
RUN apk upgrade --no-cache && apk add --no-cache ca-certificates \
    && addgroup -S -g 10002 caddy && adduser -S -D -H -u 10002 -G caddy caddy \
    && mkdir -p /data/caddy /config/caddy /etc/caddy && chown -R caddy:caddy /data /config
COPY --from=build /out/caddy /usr/bin/caddy
COPY deploy/config/caddy/Caddyfile deploy/config/caddy/Caddyfile.http deploy/config/caddy/routes.caddy /etc/caddy/
ENV XDG_CONFIG_HOME=/config XDG_DATA_HOME=/data
USER caddy
EXPOSE 8080 8443
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -q --spider http://127.0.0.1:2019/metrics || exit 1
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

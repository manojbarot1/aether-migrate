.PHONY: bootstrap bootstrap-dev up down dev logs test lint typecheck build clean help

COMPOSE      := docker compose
BASE         := -f deploy/compose/compose.yaml
PROD         := $(BASE) -f deploy/compose/compose.prod.yaml
DEV          := $(BASE) -f deploy/compose/compose.dev.yaml
OBS          := $(BASE) -f deploy/compose/compose.observability.yaml
LOCAL_LLM    := $(BASE) -f deploy/compose/compose.local-llm.yaml

##@ Setup

bootstrap: ## Production first-time setup: generate secrets, init OpenBao, run migrations
	@bash deploy/scripts/bootstrap.sh

bootstrap-dev: ## Dev first-time setup: copy .env, start infra, run migrations (no OpenBao unseal needed)
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; fi
	@echo "Starting infra services (postgres, openbao in dev mode)..."
	$(COMPOSE) $(DEV) up -d postgres openbao redis
	@echo "Waiting for postgres..."
	@sleep 5
	@echo "Running migrations..."
	$(COMPOSE) $(DEV) run --rm migrate
	@echo ""
	@echo "✓ Dev bootstrap complete. Run 'make dev' to start all services."
	@echo "  UI:           http://localhost"
	@echo "  Keycloak:     http://localhost:8081  (admin / admin)"
	@echo "  Temporal UI:  http://localhost:8088"
	@echo "  OpenBao:      http://localhost:8200  (token: devroot)"
	@echo "  Postgres:     localhost:5432  (aether / devpassword)"

##@ Runtime

up: ## Start all services in production mode (detached)
	$(COMPOSE) $(PROD) up -d

dev: ## Start all services in development mode (foreground, hot reload)
	$(COMPOSE) $(DEV) up

dev-d: ## Start all services in development mode (detached/background)
	$(COMPOSE) $(DEV) up -d

obs: ## Start with observability stack (Prometheus, Grafana, Loki, OTel)
	$(COMPOSE) $(OBS) up -d

local-llm: ## Start with local Ollama LLM
	$(COMPOSE) $(LOCAL_LLM) up -d

down: ## Stop and remove containers (keeps volumes)
	$(COMPOSE) $(DEV) down

clean: ## Stop, remove containers AND volumes
	$(COMPOSE) $(DEV) down -v --remove-orphans

##@ Operations

logs: ## Tail logs for all services
	$(COMPOSE) $(DEV) logs -f

logs-%: ## Tail logs for a specific service (e.g. make logs-api)
	$(COMPOSE) $(DEV) logs -f $*

migrate: ## Run Alembic database migrations (dev)
	$(COMPOSE) $(DEV) run --rm migrate

backup: ## Create a timestamped backup of PostgreSQL and OpenBao
	@bash deploy/scripts/backup.sh

rotate: ## Rotate PostgreSQL password
	@bash deploy/scripts/rotate.sh

upgrade: ## Pull images, rebuild app containers, run migrations, rolling restart
	@bash deploy/scripts/upgrade.sh

##@ Testing & Quality

test: ## Run Python test suite
	uv run pytest tests/ -v --cov=packages --cov-report=term-missing

test-fast: ## Run tests without coverage (faster)
	uv run pytest tests/ -v -x

lint: ## Run ruff linter and ESLint
	uv run ruff check .
	cd apps/web && npm run lint

lint-fix: ## Auto-fix ruff and ESLint issues
	uv run ruff check --fix .
	cd apps/web && npm run lint -- --fix

typecheck: ## Run mypy type checking
	uv run mypy packages/core packages/audit packages/db apps/api --strict

format: ## Format Python code with ruff
	uv run ruff format .

##@ Build

build: ## Build all Docker images
	$(COMPOSE) $(BASE) build

build-%: ## Build a specific Docker image (e.g. make build-api)
	$(COMPOSE) $(BASE) build $*

build-multi: ## Build multi-platform images (amd64 + arm64) via buildx
	docker buildx build --platform linux/amd64,linux/arm64 \
	  -f docker/api.Dockerfile -t aether-migrate/api:latest .
	docker buildx build --platform linux/amd64,linux/arm64 \
	  -f docker/ai.Dockerfile -t aether-migrate/ai:latest .
	docker buildx build --platform linux/amd64,linux/arm64 \
	  -f docker/mcp.Dockerfile -t aether-migrate/mcp:latest .
	docker buildx build --platform linux/amd64,linux/arm64 \
	  -f docker/worker.Dockerfile -t aether-migrate/worker:latest .
	docker buildx build --platform linux/amd64,linux/arm64 \
	  -f docker/web.Dockerfile -t aether-migrate/web:latest .

##@ Help

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} \
	  /^[a-zA-Z_0-9%-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } \
	  /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

.DEFAULT_GOAL := help

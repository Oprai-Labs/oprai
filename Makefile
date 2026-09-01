ifneq (,$(wildcard .env))
    include .env
    export
endif

.PHONY: help install build test dev dev-all up down logs ps migrate clean backup restore backup-list reset health
.PHONY: proto build-go build-rust build-python build-angular build-all
.PHONY: dev-infra dev-stop dev-go dev-rust dev-python dev-angular dev-solana-ts build-solana-ts
.PHONY: migrate-py
.PHONY: docker-build docker-up docker-down docker-logs
.PHONY: tokens-verify tokens-sync

BACKUP_DIR := backups
TIMESTAMP  := $(shell date +%Y%m%d_%H%M%S)
DB_CONTAINER := oprai-postgres
DB_USER := $(or $(DB_SUPERUSER),postgres)
DB_NAME := $(or $(DB_SUPERDB),oprai)

# Default target
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ──────────────── Protobuf ────────────────

proto: ## Generate gRPC code from proto definitions (Go, Python, Rust)
	@./scripts/build-protos.sh

# ──────────────── Token registry ────────────────

tokens-verify: ## Cross-check shared/tokens.json against the Jupiter token API
	@node scripts/verify-tokens.mjs

tokens-sync: ## Regenerate language-specific token modules from shared/tokens.json
	@node scripts/sync-tokens.mjs

# ──────────────── Build (Polyglot) ────────────────

build-go: ## Build all Go services (auth, admin, gateway)
	cd services/auth-service-go && go build -o bin/auth-service ./cmd/auth-service
	cd services/admin-service-go && go build -o bin/admin-service ./cmd/admin-service
	cd services/gateway-go && go build -o bin/gateway ./cmd/gateway

build-rust: ## Build Rust solana-service
	cd services/solana-service-rs && cargo build --release

build-python: ## Install Python dependencies (chat, memory, knowledge-ingestion) in venvs
	cd services/chat-service-py && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd services/memory-service-py && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd services/knowledge-ingestion-service && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
	cd services/oprai-tg-bot && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

build-angular: ## Build Angular frontend
	cd apps/oprai && npm install && npx ng build --configuration production

build-all: proto build-go build-rust build-python build-angular ## Build everything (proto + all services + frontend)

# ──────────────── Setup ────────────────

build-solana-ts: ## Install and build TypeScript solana-service
	cd services/solana-service-ts && npm install

dev-solana-ts: ## Run TypeScript solana-service in dev mode
	cd services/solana-service-ts && PORT=3030 npx ts-node-dev --respawn --transpile-only src/index.ts

install: ## Install all dependencies (Node.js + Go + Rust + Python)
	pnpm install
	cd services/auth-service-go && go mod download
	cd services/admin-service-go && go mod download
	cd services/gateway-go && go mod download
	cd scripts && go mod download
	cd apps/oprai && npm install
	$(MAKE) build-python
	$(MAKE) build-solana-ts

test: ## Run all tests
	pnpm test

llm-eval: ## Run the full LLM regression eval (slow, costs API credits) — 200+ cases across all protocols
	cd services/chat-service-py && .venv/bin/pytest tests/test_llm_*.py --maxfail=20 -q --tb=line

llm-eval-fast: ## Run only the core-action LLM eval subset (~80 cases) — fastest sanity check
	cd services/chat-service-py && .venv/bin/pytest tests/test_llm_core_actions.py -q --tb=line

clean: ## Remove all build artifacts
	pnpm clean
	rm -rf services/auth-service-go/bin
	rm -rf services/admin-service-go/bin
	rm -rf services/gateway-go/bin
	rm -rf services/solana-service-rs/target
	rm -rf apps/oprai/dist

# ──────────────── Development ────────────────

dev: dev-all ## Alias for dev-all

dev-infra: ## Start infrastructure only (Postgres, Redis, Qdrant)
	docker compose -f docker-compose.infra.yml up -d

dev-stop: ## Stop infrastructure
	docker compose -f docker-compose.infra.yml down

dev-all: dev-infra ## Start infra + all polyglot services in one terminal (requires honcho)
	@echo "Waiting for infrastructure..."
	@until docker exec $(DB_CONTAINER) pg_isready -U $(DB_USER) > /dev/null 2>&1; do sleep 1; done
	@echo "Infrastructure ready. Starting all services..."
	honcho start -f Procfile.dev -e .env

dev-go: ## Run Go services in dev mode (requires air)
	@echo "Starting Go services..."
	cd services/gateway-go && go run ./cmd/gateway &
	cd services/auth-service-go && go run ./cmd/auth-service &
	cd services/admin-service-go && go run ./cmd/admin-service &
	@wait

dev-rust: ## Run Rust solana-service in dev mode
	cd services/solana-service-rs && cargo run

dev-python: ## Run Python services in dev mode
	cd services/chat-service-py && uvicorn app.main:app --reload --port 3020 &
	cd services/memory-service-py && uvicorn app.main:app --reload --port 3040 &
	@wait

dev-angular: ## Run Angular frontend in dev mode
	cd apps/oprai && npx ng serve --port 3000

# ──────────────── Docker (Polyglot Full Stack) ────────────────

docker-build: ## Build all Docker images (polyglot)
	docker compose -f infra/docker-compose.yml build

docker-up: ## Start polyglot stack in Docker (infra + services + monitoring)
	docker compose -f infra/docker-compose.yml up -d

docker-down: ## Stop polyglot Docker stack
	docker compose -f infra/docker-compose.yml down

docker-logs: ## Tail logs from polyglot Docker stack
	docker compose -f infra/docker-compose.yml logs -f --tail=50

# ──────────────── Docker (root compose) ────────────────

up: ## Start full stack in Docker (infra + all polyglot services)
	docker compose up -d --build

down: ## Stop all Docker containers
	docker compose down

logs: ## Tail Docker logs
	docker compose logs -f --tail=50

ps: ## Show running containers
	docker compose ps

# ──────────────── Database ────────────────

migrate: ## Run ALL database migrations (schema DDL + Alembic)
	@[ -n "$(DATABASE_URL)" ] || (echo "ERROR: DATABASE_URL is required. Set it in .env or pass as argument." && exit 1)
	@echo "==> Running schema DDL migrations..."
	psql "$(DATABASE_URL)" -f services/auth-service-go/sql/schema.sql
	psql "$(DATABASE_URL)" -f services/admin-service-go/sql/schema.sql
	psql "$(DATABASE_URL)" -f services/chat-service-py/sql/schema.sql
	psql "$(DATABASE_URL)" -f services/memory-service-py/sql/schema.sql
	psql "$(DATABASE_URL)" -f services/solana-service-rs/migrations/001_create_schema.sql
	psql "$(DATABASE_URL)" -f services/oprai-tg-bot/sql/schema.sql
	psql "$(DATABASE_URL)" -f agent-platform/migrations/001_initial_schema.sql
	psql "$(DATABASE_URL)" -f agent-platform/migrations/002_subscriptions.sql
	@echo "==> Running Alembic migrations (chat-service-py + memory-service-py)..."
	$(MAKE) migrate-py
	@echo "==> All migrations complete."

migrate-py: ## Run Python service Alembic migrations (chat-service-py, memory-service-py)
	cd services/chat-service-py && DATABASE_URL="postgresql+asyncpg://$(DB_USER):$(DB_SUPERPASS)@localhost:5433/$(DB_NAME)" .venv/bin/alembic upgrade head
	cd services/memory-service-py && DATABASE_URL="postgresql+asyncpg://$(DB_USER):$(DB_SUPERPASS)@localhost:5433/$(DB_NAME)" .venv/bin/alembic upgrade head

init-roles: ## Create per-service DB roles (requires AUTH/CHAT/SOLANA/MEMORY/ADMIN _DB_PASS env vars)
	@[ -n "$(AUTH_DB_PASS)" ]   || (echo "ERROR: AUTH_DB_PASS is required"   && exit 1)
	@[ -n "$(CHAT_DB_PASS)" ]   || (echo "ERROR: CHAT_DB_PASS is required"   && exit 1)
	@[ -n "$(SOLANA_DB_PASS)" ] || (echo "ERROR: SOLANA_DB_PASS is required" && exit 1)
	@[ -n "$(MEMORY_DB_PASS)" ] || (echo "ERROR: MEMORY_DB_PASS is required" && exit 1)
	@[ -n "$(ADMIN_DB_PASS)" ]  || (echo "ERROR: ADMIN_DB_PASS is required"  && exit 1)
	@echo "==> Creating per-service database roles..."
	psql "$(DATABASE_URL)" \
		--variable="auth_pass=$(AUTH_DB_PASS)" \
		--variable="chat_pass=$(CHAT_DB_PASS)" \
		--variable="solana_pass=$(SOLANA_DB_PASS)" \
		--variable="memory_pass=$(MEMORY_DB_PASS)" \
		--variable="admin_pass=$(ADMIN_DB_PASS)" \
		-f scripts/db/init_roles.sql
	@echo "==> Roles created successfully."

seed-admin: ## Seed initial admin user (set ADMIN_INITIAL_PASSWORD env var first)
	@[ -n "$(ADMIN_INITIAL_PASSWORD)" ] || (echo "ERROR: ADMIN_INITIAL_PASSWORD is required" && exit 1)
	DATABASE_URL="$(DATABASE_URL)" ADMIN_INITIAL_PASSWORD="$(ADMIN_INITIAL_PASSWORD)" \
		bash scripts/db/seed_admin.sh

# ──────────────── Backup & Restore ────────────────

backup: ## Create a full database backup (pg_dump)
	@mkdir -p $(BACKUP_DIR)
	@echo "Creating backup..."
	@docker exec $(DB_CONTAINER) pg_dump -U $(DB_USER) -d $(DB_NAME) --format=custom \
		> $(BACKUP_DIR)/oprai_$(TIMESTAMP).dump
	@echo "Backup saved: $(BACKUP_DIR)/oprai_$(TIMESTAMP).dump"
	@echo "Size: $$(du -sh $(BACKUP_DIR)/oprai_$(TIMESTAMP).dump | cut -f1)"

backup-list: ## List all available backups
	@if [ -d $(BACKUP_DIR) ] && [ "$$(ls -A $(BACKUP_DIR)/*.dump 2>/dev/null)" ]; then \
		echo "Available backups:"; \
		ls -lh $(BACKUP_DIR)/*.dump | awk '{print "  " $$NF " (" $$5 ", " $$6 " " $$7 " " $$8 ")"}'; \
	else \
		echo "No backups found in $(BACKUP_DIR)/"; \
	fi

restore: ## Restore database from latest backup (or BACKUP=path)
	@if [ -z "$(BACKUP)" ]; then \
		LATEST=$$(ls -t $(BACKUP_DIR)/*.dump 2>/dev/null | head -1); \
		if [ -z "$$LATEST" ]; then \
			echo "ERROR: No backups found. Specify with: make restore BACKUP=backups/file.dump"; \
			exit 1; \
		fi; \
		echo "Restoring from latest: $$LATEST"; \
		echo "WARNING: This will overwrite the current database."; \
		read -p "Continue? [y/N] " confirm; \
		if [ "$$confirm" != "y" ] && [ "$$confirm" != "Y" ]; then \
			echo "Cancelled."; \
			exit 0; \
		fi; \
		docker exec -i $(DB_CONTAINER) pg_restore -U $(DB_USER) -d $(DB_NAME) --clean --if-exists < $$LATEST; \
	else \
		echo "Restoring from: $(BACKUP)"; \
		echo "WARNING: This will overwrite the current database."; \
		read -p "Continue? [y/N] " confirm; \
		if [ "$$confirm" != "y" ] && [ "$$confirm" != "Y" ]; then \
			echo "Cancelled."; \
			exit 0; \
		fi; \
		docker exec -i $(DB_CONTAINER) pg_restore -U $(DB_USER) -d $(DB_NAME) --clean --if-exists < $(BACKUP); \
	fi
	@echo "Restore complete."

# ──────────────── Reset (with safety) ────────────────

reset: ## Reset database (creates backup first, requires confirmation)
	@echo "╔══════════════════════════════════════════════╗"
	@echo "║  WARNING: This will DESTROY all database     ║"
	@echo "║  data and recreate from scratch.             ║"
	@echo "╚══════════════════════════════════════════════╝"
	@read -p "Type 'RESET' to confirm: " confirm; \
	if [ "$$confirm" != "RESET" ]; then \
		echo "Cancelled."; \
		exit 0; \
	fi
	@echo "Creating backup before reset..."
	@mkdir -p $(BACKUP_DIR)
	@docker exec $(DB_CONTAINER) pg_dump -U $(DB_USER) -d $(DB_NAME) --format=custom \
		> $(BACKUP_DIR)/oprai_pre_reset_$(TIMESTAMP).dump 2>/dev/null || true
	@echo "Backup saved: $(BACKUP_DIR)/oprai_pre_reset_$(TIMESTAMP).dump"
	@echo "Stopping infrastructure..."
	docker compose -f docker-compose.infra.yml down -v
	docker compose -f docker-compose.infra.yml up -d
	@echo "Waiting for Postgres..."
	@until docker exec $(DB_CONTAINER) pg_isready -U $(DB_USER) > /dev/null 2>&1; do sleep 1; done
	@echo "Running migrations..."
	$(MAKE) migrate
	@echo "Reset complete. Previous data backed up."

# ──────────────── Utilities ────────────────

health: ## Check gateway health (aggregated)
	@curl -s http://localhost:3001/health | python3 -m json.tool 2>/dev/null || echo "Gateway not running"

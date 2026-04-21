# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OPRAI is a DeFi-native conversational AI assistant for Solana. It transforms natural language into on-chain actions (swaps, token launches, staking, transfers) with wallet-based authentication (SIWS).

## Common Commands

### Setup & Run (Polyglot — primary)
```bash
cp .env.example .env              # Pre-filled with generated secrets; add your own OpenAI key
make install                      # Install all deps (Node.js + Go + Rust + Python)
make proto                        # Generate gRPC stubs from proto/ (Go, Python, Rust)
make build-python                 # Create Python venvs and install deps
make dev-infra                    # Start Postgres (5433), Redis (6379), Qdrant (6333) in Docker
make migrate                      # Run database migrations
make dev-all                      # Start infra + all 7 polyglot services via honcho
make health                       # Check gateway aggregated health
```

### Individual Service Groups
```bash
make dev-go                       # Gateway + Auth + Admin (Go)
make dev-rust                     # Solana service (Rust)
make dev-python                   # Chat + Memory (Python, uvicorn --reload)
make dev-angular                  # Angular frontend on :3000
```

### Build
```bash
make build-go                     # Compile all Go services
make build-rust                   # cargo build --release for Rust
make build-angular                # ng build --configuration production
make build-all                    # proto + all services + frontend
```

### Legacy Node.js Stack (still compiles, gradually being replaced)
```bash
pnpm install && pnpm build        # Build all legacy packages/services via Turborepo
pnpm dev                          # Start legacy services (excludes oprai-web)
pnpm test                         # Run all Vitest tests (legacy)
pnpm --filter @oprai/auth-service dev        # Single legacy service
pnpm --filter @oprai/auth-middleware test     # Single legacy package test
pnpm --filter @oprai/chat-service db:migrate # Single service migrations
pnpm dev:admin                    # Legacy admin-service + admin-panel only
```

### Database
```bash
make migrate                      # All service migrations
make backup                       # pg_dump to backups/
make restore                      # Restore from latest (or BACKUP=path)
make reset                        # Drop + recreate (creates backup first, requires confirmation)
```

### Docker (Full Stack)
```bash
make docker-up                    # Build + start full polyglot stack with monitoring
make docker-down                  # Stop
make docker-logs                  # Tail logs
docker compose up -d              # Legacy Node.js stack only
```

## Architecture

**Dual-stack monorepo**: polyglot services (primary) coexist with legacy Node.js services during migration. Inter-service communication is **gRPC + Protobuf** (14 proto files under `proto/`). Monitoring via **Prometheus** (:9090) + **Grafana** (:3333).

```
Frontend (Angular :3000) → Bearer JWT → Gateway (Go :3001)
                                          ├── auth-service (Go :3010/50051)    → auth_schema
                                          ├── chat-service (Python :3020/50052) → chat_schema
                                          ├── solana-service (Rust :3030/50053) → solana_schema
                                          └── memory-service (Python :3040/50054) → memory_schema

Admin Panel (Angular :3000) → Bearer JWT → admin-service (Go :3050/50055) → cross-schema SQL
```

### Polyglot Services (new, primary)
| Service | Path | Language | Framework | Port (HTTP/gRPC) |
|---------|------|----------|-----------|------------------|
| Gateway | `services/gateway-go/` | Go | Chi, gobreaker | 3001 |
| Auth | `services/auth-service-go/` | Go | Chi, pgx, golang-jwt, go-redis | 3010 / 50051 |
| Chat | `services/chat-service-py/` | Python | FastAPI, LangChain, SQLAlchemy | 3020 / 50052 |
| Solana | `services/solana-service-rs/` | Rust | Actix-Web, Tonic, solana-sdk, Diesel | 3030 / 50053 |
| Memory | `services/memory-service-py/` | Python | FastAPI, qdrant-client, OpenAI | 3040 / 50054 |
| Admin | `services/admin-service-go/` | Go | Chi, sqlc, bcrypt | 3050 / 50055 |
| Frontend | `apps/oprai/` | TypeScript | Angular 19, standalone components | 3000 |
| OpraiOS | `opraios/` | Python | Pydantic, OpenAI, solana-py | standalone |

### Legacy Services (Node.js, being replaced)
`services/gateway/`, `services/auth-service/`, `services/chat-service/`, `services/solana-service/`, `services/memory-service/` — Express + Sequelize + Turborepo. `apps/chat-web/` and `apps/admin-panel/` — Next.js 14.

### Shared Packages (legacy, used by legacy services)
- **`@oprai/types`** — TypeScript interfaces (auth, chat, solana, memory)
- **`@oprai/auth-middleware`** — Express JWT middleware
- **`@oprai/db-config`** — Sequelize connection factory + umzug migration runner
- **`@oprai/solana-common`** — Token registry (COMMON_TOKENS), Solana RPC helpers

### Gateway
Single entry point. JWT validation, `X-User-Wallet` + `X-Internal-Api-Key` header injection, rate limiting (100/min global, 20/min auth), health aggregation, circuit breaker (gobreaker).

### Auth Flow
```
1. POST /auth/nonce → { nonce, nonceId } (stored in Redis, 10-min TTL)
2. Client signs nonce with wallet (tweetnacl ed25519)
3. POST /auth/verify → { token, expiresAt } (JWT in body, HS256, 3-day expiry)
4. Client stores JWT in localStorage (key: oprai-auth-token)
5. Every request: Authorization: Bearer <jwt>
6. Gateway validates JWT → injects X-User-Wallet + X-Internal-Api-Key → proxies to service
```

### Solana Action Flow
1. User sends natural language → chat-service → LLM with SOLANA_ACTION_PROMPT
2. LLM returns action blocks: `[ACTION:transfer] to=HwM... amount=1 token=SOL`
3. Frontend parses action blocks (intent-parser)
4. Frontend calls `/actions/quote` → `/actions/build` → user signs with wallet → submit TX

### Database
- Single PostgreSQL instance (:5433), per-service schema isolation
- Schemas: `auth_schema`, `chat_schema`, `solana_schema`, `memory_schema`, `admin_schema`
- No cross-service foreign keys; services reference each other by string IDs
- Legacy: Sequelize ORM + umzug migrations
- Polyglot: pgx (Go), SQLAlchemy (Python), Diesel (Rust)

### Admin Service
Separate auth (username/password + bcrypt, `admin_schema.admin_users`). Cross-schema raw SQL. Does NOT go through gateway. Default admin: `admin`/`admin123`.

## Port Reference

| Service | HTTP | gRPC | | Infrastructure | Port |
|---------|------|------|-|----------------|------|
| Frontend | 3000 | — | | PostgreSQL | 5433 |
| Gateway | 3001 | — | | Redis | 6379 |
| Auth | 3010 | 50051 | | Qdrant HTTP | 6333 |
| Chat | 3020 | 50052 | | Qdrant gRPC | 6334 |
| Solana | 3030 | 50053 | | Prometheus | 9090 |
| Memory | 3040 | 50054 | | Grafana | 3333 |
| Admin | 3050 | 50055 | | | |
| Marketing | 3100 | — | | | |
| OpraiOS MCP | 8000 | — | (standalone, optional) | | |

## Frontend Routes (Angular)

Main pages under `apps/oprai/src/app/features/`:
- **Chat** (`/`) — Home page, AI chat interface
- **Portfolio** (`/portfolio`) — Wallet holdings, token balances
- **Agents** (`/agents`) — AI agent management
- **Voice** (`/voice`) — Voice-based interactions
- **Admin** (`/admin`) — Admin panel (separate layout, bypasses gateway)

Legacy redirects: `/market`, `/explore`, `/trade`, `/settings`, `/tokens`, `/nft`, `/defi` → all redirect to `/`

## Protobuf & gRPC

14 proto files under `proto/` organized by domain: `common/`, `auth/`, `chat/`, `solana/`, `memory/`, `admin/`. Run `make proto` (or `./scripts/build-protos.sh [go|python|rust]`) to generate stubs:
- Go: `protoc-gen-go` + `protoc-gen-go-grpc` → `services/<svc>/proto/gen/go/`
- Python: `grpcio-tools` → `services/<svc>/proto_gen/`
- Rust: `tonic-build` via `build.rs` (generated at `cargo build` time)

## Environment Variables

Single `.env.example` at repo root (pre-filled with generated secrets). Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPRAI_JWT_SECRET` | Yes | JWT signing secret |
| `OPRAI_INTERNAL_API_KEY` | Yes | Gateway-to-service shared key |
| `OPRAI_OPENAI_API_KEY` | Yes | OpenAI API key for LLM and embeddings |
| `DATABASE_URL` | Auto | Composed from `DB_SUPERUSER`/`DB_SUPERPASS`/`DB_SUPERDB` |
| `REDIS_URL` | No | Default: `redis://localhost:6379` |
| `QDRANT_URL` | No | Default: `http://localhost:6333` |
| `SOLANA_RPC` | No | Default: mainnet public |
| `OPRAI_ADMIN_JWT_SECRET` | Yes | Admin panel JWT secret |
| `BIRDEYE_API_KEY` | No | Market data API (proxied via gateway) |
| `JUPITER_API_KEY` | No | Jupiter API key |

## CI/CD

5 GitHub Actions workflows with path-based triggers:

| Workflow | Trigger paths | Steps |
|----------|---------------|-------|
| `go-services.yml` | `services/*-go/**` | vet, test, build, Docker push |
| `python-services.yml` | `services/*-py/**` | ruff, pytest, Docker push |
| `rust-service.yml` | `services/*-rs/**` | fmt, clippy, test, build, Docker push |
| `angular-frontend.yml` | `apps/oprai/**` | lint, test, build, Docker push |
| `proto-check.yml` | `proto/**` | buf lint, breaking change detection |

## Build Notes

### Legacy TypeScript
- `NodeNext` module resolution — all imports need `.js` extension (e.g., `import { env } from "./config/env.js"`)
- **TS2742 "inferred type cannot be named"**: Annotate `const router: Router = Router()` explicitly
- `@solana/spl-token` is ESM-only: use dynamic `import()` in CJS/NodeNext modules
- `fetch().json()` returns `unknown` in strict TS: cast with `as any`
- Test files excluded from `tsc` build via `tsconfig.json` (`"exclude": ["src/__tests__"]`); Vitest handles them separately
- Changing `@oprai/types` or `@oprai/auth-middleware` triggers rebuild of all dependent services

### Polyglot
- **Go**: Entry points at `cmd/<service>/main.go`. Run `go mod tidy` if build fails.
- **Rust**: First build downloads crates (3-5 min). Proto stubs generated at build time via `build.rs` + tonic-build.
- **Python**: Services use venvs (`.venv/`). Install with `make build-python`. Run with `.venv/bin/uvicorn`.
- **Angular**: Angular 19 with standalone components, lazy-loaded modules. Build with `npx ng build`.
- **Proto generation**: Requires `protoc` (`brew install protobuf`), `protoc-gen-go`/`protoc-gen-go-grpc` (Go), `grpcio-tools` (Python).

### Process Manager
`Procfile.dev` defines all 7 services for `honcho`. `make dev-all` starts infrastructure + all services in one terminal with color-coded logs.

### Frontend Design System
- **CSS Variables**: All styling uses `--op-*` prefixed tokens (e.g., `--op-bg-surface-1`, `--op-text-primary`, `--op-brand`)
- **Brand Colors**: Indigo (`#5b5fc7`) → Cyan (`#06B6D4`) gradient
- **Location**: `apps/oprai/src/styles.scss`
- **Note**: Admin pages use a separate token system (`--bg-primary`, `--text-primary`, etc.) — not migrated

## OpraiOS (Agent Platform)

Python package at `opraios/` for building, training, and deploying AI agents for Solana DeFi. Separate from polyglot services — has its own venv and dependencies.

### Setup & Run
```bash
cd opraios
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                    # Run tests
.venv/bin/python -m opraios.mcp.server  # Start MCP server
```

### Architecture
```
opraios/
├── core/               # Core framework (agent_builder, character, plugin_system)
├── plugins/            # DeFi protocol plugins (jupiter, orca, kamino, drift, etc.)
├── templates/          # 16 agent templates (trading, yield, security)
├── mcp/                # Claude Code MCP integration
├── runner/             # Strategy runner daemon + scheduler
└── tests/              # pytest test suite (25+ test files)
```

### Key Components
- **AgentBuilder** — Fluent API for agent creation (`core/agent_builder.py`)
- **Plugin System** — Actions, Providers, Evaluators for DeFi protocols
- **Character System** — JSON-based agent personalities
- **Strategy Runner** — Daemon-based job scheduler with cron support
- **Safety System** — Fund movement protection with wallet limits
- **Cost Tracker** — LLM API cost monitoring

### Testing
```bash
.venv/bin/pytest tests/                    # All tests
.venv/bin/pytest tests/test_simulation.py  # Single file
.venv/bin/pytest -k "gas"                  # Filter by name
```

### Plugin Development
Plugins live in `opraios/plugins/`. Each plugin has:
- `plugin.json` or `manifest.json` — metadata
- `plugin.py` or `main.py` — entry point
- Actions, Providers, Evaluators as needed

Install plugins via `PluginManager.install(source)` — supports local paths, GitHub repos, and zip URLs.

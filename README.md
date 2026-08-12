# OPRAI

DeFi-native conversational AI assistant for Solana. Transforms natural language into on-chain actions — swaps, token launches, staking, transfers — with wallet-based authentication (SIWS).

## Architecture

```
                           ┌─────────────────────────────────────────────┐
                           │              Gateway (Go :3001)             │
                           │   JWT validation · Rate limiting · CORS    │
Frontend (Angular :3000) ──┤   Circuit breaker · gRPC fan-out          │
                           └──────┬──────┬──────┬──────┬───────────────┘
                                  │      │      │      │
                    ┌─────────────┘      │      │      └─────────────┐
                    ▼                    ▼      ▼                    ▼
             Auth Service          Chat Service  Solana Service   Memory Service
             Go :3010/50051        Py :3020/50052  Rust :3030/50053 Py :3040/50054
             JWT · SIWS · Redis    LLM · SSE      TX build · DeFi  Qdrant · Embeddings
                    │                    │      │                    │
                    └────────────────────┴──────┴────────────────────┘
                                         │
                              PostgreSQL :5433  ·  Redis :6379  ·  Qdrant :6333
```

| Service | Language | Framework | Port (HTTP/gRPC) |
|---------|----------|-----------|------------------|
| Gateway | Go | Chi, gobreaker | 3001 |
| Auth | Go | Chi, pgx, golang-jwt | 3010 / 50051 |
| Chat | Python | FastAPI, LangChain | 3020 / 50052 |
| Solana | Rust | Actix-Web, Tonic, solana-sdk | 3030 / 50053 |
| Memory | Python | FastAPI, qdrant-client, OpenAI | 3040 / 50054 |
| Admin | Go | Chi, cross-schema SQL | 3050 / 50055 |
| Frontend | TypeScript | Angular 19 | 3000 |

Inter-service communication: **gRPC + Protobuf** (15 proto files under `proto/`).
Monitoring: **Prometheus** (:9090) + **Grafana** (:3333).

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker & Compose | latest | [docker.com](https://www.docker.com/) |
| Go | 1.22+ | `brew install go` |
| Rust | 1.77+ | [rustup.rs](https://rustup.rs/) |
| Python | 3.12+ | `brew install python@3.12` |
| Node.js | 20+ | `brew install node` |
| pnpm | 9+ | `npm install -g pnpm` |
| protoc | 3+ | `brew install protobuf` |
| honcho | 2+ | `brew install honcho` |

## Quick Start

### 1. Clone and configure

```bash
git clone <repository-url>
cd oprai
cp .env.example .env
```

Edit `.env` and set your **OpenAI API key**:

```env
OPRAI_OPENAI_API_KEY=sk-proj-your-key-here
```

### 2. Install dependencies

```bash
make install
```

This installs Node.js (pnpm), Go (mod download), and Angular (npm) dependencies.

For Python services:

```bash
make build-python
```

For Rust (first build downloads crates — takes a few minutes):

```bash
cd services/solana-service-rs && cargo build
```

### 3. Generate gRPC stubs from Protobuf

```bash
make proto
```

### 4. Start infrastructure

```bash
make dev-infra
```

Starts PostgreSQL (:5433), Redis (:6379), and Qdrant (:6333) in Docker.

### 5. Run database migrations

```bash
make migrate
```

### 6. Start all services

```bash
make dev-all
```

This uses `honcho` to start all 7 services (3 Go + 2 Python + 1 Rust + 1 Angular) in a single terminal with color-coded logs.

### 7. Verify

```bash
make health
```

Open:
- **Frontend**: http://localhost:3000
- **Gateway health**: http://localhost:3001/health
- **Grafana**: http://localhost:3333 (admin / admin)

## Alternative Run Methods

### Full Docker (zero local toolchain)

```bash
cp .env.example .env
make docker-up        # builds + starts everything with monitoring
make docker-logs      # tail logs
make docker-down      # stop
```

### Individual services

```bash
make dev-go           # gateway + auth + admin (Go)
make dev-rust         # solana-service (Rust)
make dev-python       # chat + memory (Python)
make dev-angular      # frontend (Angular)
```

## Makefile Commands

```
  proto               Generate gRPC code from proto definitions
  build-go            Build all Go services (auth, admin, gateway)
  build-rust          Build Rust solana-service
  build-python        Install Python dependencies (chat, memory)
  build-angular       Build Angular frontend
  build-all           Build everything (proto + all services + frontend)
  install             Install all dependencies (Node.js + Go + Rust + Python)
  test                Run all tests
  clean               Remove all build artifacts

  dev-all             Start infra + all polyglot services in one terminal
  dev                 Alias for dev-all
  dev-infra           Start infrastructure only (Postgres, Redis, Qdrant)
  dev-stop            Stop infrastructure
  dev-go              Run Go services in dev mode
  dev-rust            Run Rust solana-service in dev mode
  dev-python          Run Python services in dev mode
  dev-angular         Run Angular frontend in dev mode

  up                  Start full stack in Docker (root compose)
  down                Stop all Docker containers
  logs                Tail Docker logs
  docker-up           Start polyglot stack with monitoring (infra/)
  docker-down         Stop polyglot Docker stack
  docker-logs         Tail logs from polyglot Docker stack

  migrate             Run database migrations for all services
  migrate-py          Run Python Alembic migrations (chat, memory)
  seed-admin          Seed initial admin user (set ADMIN_INITIAL_PASSWORD)
  backup              Create a full database backup
  backup-list         List all available backups
  restore             Restore database from latest backup (or BACKUP=path)
  reset               Reset database (creates backup first, requires confirmation)
  health              Check gateway health (aggregated)
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPRAI_JWT_SECRET` | Yes | JWT signing secret |
| `OPRAI_INTERNAL_API_KEY` | Yes | Gateway-to-service shared key |
| `OPRAI_OPENAI_API_KEY` | Yes | OpenAI API key for LLM and embeddings |
| `OPRAI_OPENAI_MODEL` | No | Chat model (default: `gpt-4o-mini`) |
| `DB_SUPERUSER` | No | PostgreSQL user (default: `postgres`) |
| `DB_SUPERPASS` | Yes | PostgreSQL password |
| `DB_SUPERDB` | No | PostgreSQL database (default: `oprai`) |
| `DATABASE_URL` | Auto | Composed from DB_SUPERUSER/PASS/DB |
| `REDIS_URL` | No | Redis URL (default: `redis://localhost:6379`) |
| `QDRANT_URL` | No | Qdrant URL (default: `http://localhost:6333`) |
| `SOLANA_RPC` | No | Solana RPC endpoint (default: mainnet public) |
| `SOLANA_NETWORK` | No | `mainnet-beta` or `devnet` |
| `CORS_ORIGIN` | No | Allowed CORS origin (default: `http://localhost:3000`) |
| `OPRAI_ADMIN_JWT_SECRET` | Yes | Admin panel JWT secret |
| `ADMIN_CORS_ORIGIN` | No | Admin CORS origin (default: `http://localhost:3200`) |
| `BIRDEYE_API_KEY` | No | Market data API (proxied via gateway) |
| `JUPITER_API_KEY` | No | Jupiter API key |
| `HELIUS_API_KEY` | No | Helius enhanced RPC |
| `PINATA_JWT` | No | IPFS upload via Pinata |

## Project Structure

```
oprai/
├── proto/                          gRPC contract definitions (15 files)
│   ├── common/                     Shared types, health check
│   ├── auth/                       Auth + User RPCs
│   ├── chat/                       Session + Message RPCs
│   ├── solana/                     Action + Quote + Protocol RPCs
│   ├── memory/                     Vector + Consent RPCs
│   ├── admin/                      Admin auth + Analytics + Audit RPCs
│   └── stream/                     Real-time streaming RPCs
├── services/
│   ├── gateway-go/                 Go — API gateway, circuit breaker, rate limiting
│   ├── auth-service-go/            Go — SIWS auth, JWT, Redis nonce, user CRUD
│   ├── chat-service-py/            Python — FastAPI, LangChain, SSE streaming
│   ├── solana-service-rs/          Rust — TX building, Jupiter, Marinade, Jito
│   ├── memory-service-py/          Python — Qdrant vectors, OpenAI embeddings
│   └── admin-service-go/           Go — Admin panel backend, cross-schema SQL
├── apps/
│   ├── oprai/                      Angular 19 — chat + admin UI
│   ├── admin-panel/                Admin panel (Angular)
│   └── marketing-site/             Marketing landing page (Next.js)
├── opraios/                        OpraiOS — AI agent framework (Python)
│   ├── core/                       Agent builder, character system, plugins
│   ├── plugins/                    DeFi protocol plugins (Jupiter, Orca, Kamino, etc.)
│   ├── templates/                  16 agent templates (trading, yield, security)
│   ├── mcp/                        Claude Code MCP integration
│   ├── sdk/                        TypeScript SDK
│   ├── runner/                     Strategy runner daemon + scheduler
│   └── visual-builder-web/         Visual workflow editor (React + ReactFlow)
├── agent-platform/                 Agent marketplace platform (Go + Angular)
│   ├── services/                   Agent, marketplace, connector services
│   ├── frontend/                   Angular marketplace UI
│   ├── programs/                   Solana Anchor program (agent identity NFT)
│   └── proto/                      Agent platform protobuf definitions
├── packages/
│   └── media/                      Brand and UI imagery
├── infra/
│   ├── docker-compose.yml          Full polyglot stack with monitoring
│   ├── docker-compose.infra.yml    Infrastructure only (dev)
│   ├── prometheus/                 Prometheus scrape configs
│   └── grafana/                    Dashboards + datasource provisioning
├── docs/                           Technical documentation
│   ├── services/                   Per-service documentation
│   ├── frontend/                   Frontend documentation
│   ├── agents/                     Agent framework documentation
│   └── proto/                      Protobuf documentation
├── scripts/
│   └── build-protos.sh             Proto code generation (Go/Python/Rust)
├── .github/workflows/              CI/CD (7 workflows)
├── Procfile.dev                    Honcho process definitions
├── Makefile                        Developer shortcuts
└── .env.example                    Environment config template
```

## Database

Single PostgreSQL instance with per-service schema isolation:

| Service | Schema | Tables |
|---------|--------|--------|
| Auth (Go) | `auth_schema` | `users`, `login_logs` |
| Chat (Python) | `chat_schema` | `chat_sessions`, `chat_messages` |
| Solana (Rust) | `solana_schema` | `transactions` |
| Memory (Python) | `memory_schema` | `user_consents` |
| Admin (Go) | `admin_schema` | `admin_users`, `admin_audit_log` |

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

## Auth Flow

```
1. POST /auth/nonce → { nonce, nonceId }     (stored in Redis, 10-min TTL)
2. Client signs nonce with wallet            (tweetnacl ed25519)
3. POST /auth/verify → { token, expiresAt }  (JWT, HS256, 3-day expiry)
4. Client stores JWT in localStorage         (key: oprai-auth-token)
5. Every request: Authorization: Bearer <jwt>
6. Gateway validates → injects X-User-Wallet + X-Internal-Api-Key → proxies to service
```

## Solana Action Flow

```
1. User sends natural language → chat-service → LLM with SOLANA_ACTION_PROMPT
2. LLM returns action blocks: [ACTION:swap] {"inputMint": "SOL", "outputMint": "USDC", ...}
3. Frontend parses action blocks (IntentParserService)
4. Frontend calls /actions/quote → /actions/build
5. User signs transaction with wallet
6. Submit TX to Solana RPC
```

## OpraiOS — AI Agent Framework

Python package at `opraios/` for building, training, and deploying AI agents for Solana DeFi.

### Setup

```bash
cd opraios
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                    # Run tests
.venv/bin/python -m opraios.mcp.server  # Start MCP server
```

### Features

- **Agent Builder** — Fluent API for agent creation
- **16 Templates** — Trading, yield, security, utility agents
- **10 DeFi Plugins** — Jupiter, Orca, Kamino, Drift, Jito, Meteora, Raydium, Tensor, BlazeStake
- **Visual Builder** — ReactFlow-based drag-and-drop workflow editor
- **Strategy Runner** — Daemon-based job scheduler with cron support
- **Simulation Mode** — Transaction dry-run with risk assessment
- **MCP Server** — Claude Code integration
- **TypeScript SDK** — `@opraios/sdk` package
- **Safety System** — Fund movement protection with wallet limits
- **Cost Tracker** — LLM API cost monitoring

See [opraios/README.md](opraios/README.md) for full documentation.

## Agent Platform

Separate application at `agent-platform/` for creating, deploying, and monetizing AI agents on Solana.

- Agent creation with form-based builder + visual drag-drop editor
- On-chain identity as NFTs (Anchor program)
- Marketplace with search, ratings, reviews
- Multi-platform connectors (Discord, Telegram, Twitter)
- Monetization: free, pay-per-use, subscription

See [agent-platform/README.md](agent-platform/README.md) for full documentation.

## CI/CD

7 GitHub Actions workflows with path-based triggers:

| Workflow | Trigger paths | Steps |
|----------|---------------|-------|
| `go-services.yml` | `services/*-go/**` | vet, test, build, Docker push |
| `python-services.yml` | `services/*-py/**` | ruff, pytest, Docker push |
| `rust-service.yml` | `services/*-rs/**` | fmt, clippy, test, build, Docker push |
| `angular-frontend.yml` | `apps/oprai/**` | lint, test, build, Docker push |
| `proto-check.yml` | `proto/**` | buf lint, breaking change detection |
| `opraios-ci.yml` | `opraios/**` | pytest, lint |
| `opraios-publish.yml` | `opraios/**` (tags) | Build and publish package |

## Troubleshooting

**Port conflict**: `lsof -i :PORT` to find the process, or change ports in `.env`.

**Database connection**: `docker exec oprai-postgres pg_isready -U postgres`

**Go build fails**: Run `go mod tidy` inside each Go service directory.

**Rust first build slow**: Normal — downloading and compiling crates takes 3-5 minutes.

**Python import errors**: Make sure you ran `make build-python` and `make proto`.

**Proto generation fails**: Ensure `protoc` is installed: `brew install protobuf`.

**Reset everything**: `make reset` (creates backup, drops all data, re-runs migrations).

**Clear all Docker state**: `make docker-down && docker volume prune`

## License

MIT

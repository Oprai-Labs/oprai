# OPRAI Documentation Index

Technical documentation covering the entire OPRAI codebase.

## Quick Start

```bash
make dev-all        # Start all services
make health         # Check service health

# Individual services
make dev-go         # Gateway + Auth + Admin
make dev-rust       # Solana service
make dev-python     # Chat + Memory
make dev-angular    # Frontend
```

## Documentation Structure

```
docs/
├── README.md                     # This file — index
├── quick-reference.md            # Quick reference for developers
│
├── services/                     # Polyglot service documentation
│   ├── gateway-go.md             # API Gateway (Go)
│   ├── auth-service-go.md        # SIWS Auth (Go)
│   ├── admin-service-go.md       # Admin Panel Backend (Go)
│   ├── chat-service-py.md        # LLM Chat (Python)
│   ├── chat-service-internals.md # Chat service internals
│   ├── memory-service-py.md      # Vector Memory (Python)
│   └── solana-service-rs.md      # Solana TX Builder (Rust)
│
├── frontend/                     # Frontend documentation
│   └── angular-app.md            # Angular 19 App
│
├── agents/                       # Agent framework documentation
│   ├── opraios.md                # OpraiOS AI Agent Platform
│   └── plugin-reference.md       # Protocol plugins reference
│
└── proto/                        # Protobuf definitions
    ├── README.md                 # Proto index
    ├── common-types.md
    ├── common-health.md
    ├── auth-service.md
    ├── chat-session.md
    ├── chat-message.md
    ├── solana-action.md
    ├── solana-quote.md
    ├── solana-protocol.md
    ├── memory-service.md
    ├── admin-service.md
    └── stream-service.md
```

## Service Summary

| Service | Language | Port (HTTP/gRPC) | Description |
|---------|----------|------------------|-------------|
| Gateway | Go | 3001 | JWT auth, rate limiting, reverse proxy |
| Auth | Go | 3010 / 50051 | SIWS authentication, JWT issuance |
| Chat | Python | 3020 / 50052 | LLM chat, streaming, action generation |
| Solana | Rust | 3030 / 50053 | TX builder, protocol integrations |
| Memory | Python | 3040 / 50054 | Vector search, conversation summarization |
| Admin | Go | 3050 / 50055 | Admin panel, cross-schema queries |
| Frontend | TypeScript | 3000 | Angular 19, wallet adapter |
| OpraiOS | Python | standalone | AI agent framework |

## Architecture Diagram

```
                                    ┌─────────────────────────────────────┐
                                    │           FRONTEND (:3000)           │
                                    │         Angular 19 + Signals         │
                                    └──────────────────┬──────────────────┘
                                                       │
                                                       ▼
                                    ┌─────────────────────────────────────┐
                                    │          GATEWAY (:3001)             │
                                    │   JWT Auth + Rate Limit + Proxy      │
                                    └──────────────────┬──────────────────┘
                                                       │
         ┌─────────────────┬─────────────────┬─────────┴─────────┬─────────────────┐
         │                 │                 │                   │                 │
         ▼                 ▼                 ▼                   ▼                 ▼
    ┌─────────┐      ┌─────────┐      ┌─────────────┐     ┌─────────┐      ┌─────────┐
    │  AUTH   │      │  CHAT   │      │   SOLANA    │     │ MEMORY  │      │  ADMIN  │
    │ :3010   │      │ :3020   │      │    :3030    │     │ :3040   │      │ :3050   │
    │ Go      │      │ Python  │      │    Rust     │     │ Python  │      │ Go      │
    └────┬────┘      └────┬────┘      └──────┬──────┘     └────┬────┘      └────┬────┘
         │                │                  │                 │                │
         ▼                ▼                  ▼                 ▼                ▼
    ┌─────────────────────────────────────────────────────────────────────────────────┐
    │                           POSTGRESQL (:5433)                                      │
    │   auth_schema | chat_schema | solana_schema | memory_schema | admin_schema       │
    └─────────────────────────────────────────────────────────────────────────────────┘
                                                       │
         ┌─────────────────┬─────────────────┬─────────┴─────────┐
         │                 │                 │                   │
         ▼                 ▼                 ▼                   ▼
    ┌─────────┐      ┌─────────┐      ┌─────────────┐     ┌─────────────┐
    │  Redis  │      │ Qdrant  │      │   OpenAI    │     │ Solana RPC  │
    │  :6379  │      │  :6333  │      │    API      │     │  (Multi)    │
    └─────────┘      └─────────┘      └─────────────┘     └─────────────┘
```

## Auth Flow (SIWS)

```
1. POST /auth/nonce → { nonce, nonceId }
2. Client signs: "OPRAI login: {nonce}"
3. POST /auth/verify → { token, expiresAt }
4. Store JWT in localStorage (key: oprai-auth-token)
5. Every request: Authorization: Bearer <jwt>
6. Gateway validates → injects X-User-Wallet + X-Internal-Api-Key
```

## Solana Action Flow

```
1. User sends natural language → Chat Service → LLM
2. LLM returns: [ACTION:swap] {"inputMint": "SOL", ...}
3. Frontend parses action blocks (IntentParserService)
4. Frontend calls /actions/quote → /actions/build
5. User signs with wallet
6. Submit TX to RPC
```

## Protobuf Schemas

15 proto files in `proto/`:

| Domain | Proto File | Services |
|--------|------------|----------|
| Common | `common/types.proto`, `common/health.proto` | HealthService |
| Auth | `auth/auth.proto`, `auth/user.proto` | AuthService, UserService |
| Chat | `chat/session.proto`, `chat/message.proto` | ChatSessionService, ChatMessageService |
| Solana | `solana/action.proto`, `solana/quote.proto`, `solana/protocol.proto` | SolanaActionService, SolanaQuoteService, SolanaProtocolService |
| Memory | `memory/consent.proto`, `memory/vector.proto` | MemoryConsentService, MemoryVectorService |
| Admin | `admin/admin_auth.proto`, `admin/analytics.proto`, `admin/audit.proto` | AdminAuthService, AdminAnalyticsService, AdminAuditService |
| Stream | `stream/stream.proto` | StreamService |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPRAI_JWT_SECRET` | Yes | JWT signing secret |
| `OPRAI_INTERNAL_API_KEY` | Yes | Gateway-to-service auth |
| `OPRAI_OPENAI_API_KEY` | Yes | OpenAI API key |
| `DATABASE_URL` | Auto | PostgreSQL connection |
| `REDIS_URL` | No | Redis connection |
| `QDRANT_URL` | No | Qdrant URL |
| `SOLANA_RPC` | No | Solana RPC endpoint |
| `BIRDEYE_API_KEY` | No | Market data |
| `JUPITER_API_KEY` | No | Jupiter API |

## Build Commands

```bash
make install        # Install all deps
make proto          # Generate gRPC stubs
make build-all      # Build all services
make dev-all        # Start all services
make migrate        # Run migrations
make health         # Check service health
```

## Test Coverage

| Module | Tests |
|--------|-------|
| OpraiOS (Python) | 313+ tests |
| Angular Frontend | 19+ tests (intent-parser) |
| Go Services | Service-specific tests |
| Python Services | pytest |
| Rust Service | cargo test |

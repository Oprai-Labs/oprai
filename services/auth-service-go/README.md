# Auth Service (Go)

SIWS (Sign In With Solana) authentication and user management.

## Responsibilities

- Nonce generation and Redis-based storage (10-min TTL)
- SIWS signature verification (ed25519)
- JWT issuance (HS256, 3-day expiry)
- User CRUD (wallet-based identity)
- Login logging

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/nonce` | Get a nonce for wallet signing |
| POST | `/auth/verify` | Verify signature, issue JWT |
| GET | `/auth/me` | Get current user info |
| PUT | `/auth/profile` | Update user profile |
| GET | `/auth/users/:wallet` | Get user by wallet address |

## Auth Flow

```
1. POST /auth/nonce → { nonce, nonceId }
2. Client signs: "OPRAI login: {nonce}" with wallet (ed25519)
3. POST /auth/verify { wallet, signature, nonceId } → { token, expiresAt }
4. Client stores JWT → Authorization: Bearer <token> on every request
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | HTTP port (default: 3010) |
| `GRPC_PORT` | No | gRPC port (default: 50051) |
| `OPRAI_JWT_SECRET` | Yes | JWT signing secret |
| `OPRAI_INTERNAL_API_KEY` | Yes | Service-to-service auth key |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis for nonce storage |

## Database Schema

- **Schema**: `auth_schema`
- **Tables**: `users`, `login_logs`

## Run

```bash
# Dev (native)
cd services/auth-service-go && go run ./cmd/auth-service

# Docker
docker compose -f infra/docker-compose.yml up auth-service-go

# Build binary
cd services/auth-service-go && go build -o bin/auth-service ./cmd/auth-service
```

## Project Structure

```
auth-service-go/
├── cmd/
│   └── auth-service/     Entry point
├── internal/
│   ├── config/           Configuration loading
│   ├── handlers/         HTTP handlers
│   ├── models/           Data models
│   ├── repository/       Database queries
│   ├── service/          Business logic
│   ├── grpc/             gRPC server
│   └── middleware/       Internal auth middleware
├── sql/                  Migration files
├── Dockerfile
├── go.mod
└── go.sum
```

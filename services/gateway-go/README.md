# Gateway Service (Go)

API Gateway for the OPRAI platform. Single entry point for all frontend requests.

## Responsibilities

- JWT validation and authentication
- `X-User-Wallet` + `X-Internal-Api-Key` header injection
- Rate limiting (100/min global, 20/min auth)
- Health aggregation from all downstream services
- Circuit breaker (gobreaker) for gRPC calls
- CORS handling
- Reverse proxy to backend services

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Aggregated health check (all services) |
| ANY | `/auth/*` | Proxy to auth-service (:3010) |
| ANY | `/chat/*` | Proxy to chat-service (:3020) |
| ANY | `/solana/*` | Proxy to solana-service (:3030) |
| ANY | `/memory/*` | Proxy to memory-service (:3040) |
| ANY | `/admin/*` | Proxy to admin-service (:3050) |
| ANY | `/market/*` | Proxy to Birdeye/Jupiter market data |

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | HTTP port (default: 3001) |
| `GRPC_PORT` | No | gRPC port (default: 50050) |
| `OPRAI_JWT_SECRET` | Yes | JWT signing secret |
| `OPRAI_INTERNAL_API_KEY` | Yes | Service-to-service auth key |
| `CORS_ORIGIN` | No | Allowed origin (default: `http://localhost:3000`) |
| `AUTH_SERVICE_URL` | Yes | Auth service HTTP URL |
| `AUTH_SERVICE_GRPC` | Yes | Auth service gRPC address |
| `CHAT_SERVICE_URL` | Yes | Chat service HTTP URL |
| `CHAT_SERVICE_GRPC` | Yes | Chat service gRPC address |
| `SOLANA_SERVICE_URL` | Yes | Solana service HTTP URL |
| `SOLANA_SERVICE_GRPC` | Yes | Solana service gRPC address |
| `MEMORY_SERVICE_URL` | Yes | Memory service HTTP URL |
| `MEMORY_SERVICE_GRPC` | Yes | Memory service gRPC address |
| `REDIS_URL` | Yes | Redis for rate limiting |

## Run

```bash
# Dev (native)
cd services/gateway-go && go run ./cmd/gateway

# Docker
docker compose -f infra/docker-compose.yml up gateway-go

# Build binary
make build-go
```

## Project Structure

```
gateway-go/
├── cmd/
│   └── gateway/          Entry point
├── internal/
│   ├── config/           Configuration loading
│   ├── middleware/        JWT auth, rate limiting, CORS
│   ├── proxy/            Reverse proxy handlers
│   ├── grpc/             gRPC client connections
│   └── health/           Health aggregation
├── gateway/              Gateway binary output
├── Dockerfile
├── go.mod
└── go.sum
```

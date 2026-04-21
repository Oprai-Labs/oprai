# Auth Service (Go)

SIWS (Sign-In with Solana) authentication and user management service.

## Quick Start

```bash
cd services/auth-service-go
go run ./cmd/auth-service
# → HTTP: http://localhost:3010
# → gRPC: localhost:50051
```

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │         AUTH SERVICE                 │
                                    │                                     │
                                    │  ┌─────────────────────────────┐    │
                                    │  │      HTTP Server (:3010)    │    │
                                    │  │   Chi Router + Handlers     │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    │  ┌──────────────┴──────────────┐    │
                                    │  │     gRPC Server (:50051)    │    │
                                    │  │   AuthService + UserService │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    └─────────────────┼───────────────────┘
                                                      │
         ┌────────────────────────────────────────────┼────────────────────────────┐
         │                                            │                            │
         ▼                                            ▼                            ▼
    ┌─────────────┐                           ┌─────────────┐              ┌─────────────┐
    │ PostgreSQL  │                           │   Redis     │              │  Gateway    │
    │ auth_schema │                           │   :6379     │              │   :3001     │
    └─────────────┘                           └─────────────┘              └─────────────┘
```

---

## File Structure

```
services/auth-service-go/
├── cmd/
│   └── auth-service/
│       └── main.go              # Entry point
├── internal/
│   ├── config/
│   │   └── env.go               # Environment config
│   ├── server/
│   │   ├── http.go              # Chi HTTP server
│   │   └── grpc.go              # gRPC server
│   ├── db/
│   │   ├── connection.go        # pgx pool
│   │   ├── models.go            # User, LoginLog structs
│   │   └── queries.go           # SQL queries
│   ├── handlers/
│   │   ├── auth_handler.go      # Auth endpoints
│   │   └── user_handler.go      # User endpoints
│   ├── services/
│   │   ├── jwt.go               # JWT issuance/validation
│   │   ├── nonce.go             # Nonce generation/consumption
│   │   └── signature.go         # Ed25519 verification
│   └── middleware/
│       ├── auth.go              # X-Internal-Api-Key validation
│       └── metrics.go           # Prometheus metrics
├── go.mod
└── go.sum
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3010` | HTTP port |
| `GRPC_PORT` | `50051` | gRPC port |
| `OPRAI_JWT_SECRET` | *(required in prod)* | JWT signing key |
| `OPRAI_INTERNAL_API_KEY` | *(required in prod)* | For requests from Gateway |
| `DATABASE_URL` | *(required in prod)* | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection |
| `DB_SCHEMA` | `auth_schema` | PostgreSQL schema |
| `NODE_ENV` / `GO_ENV` | `development` | Environment |

---

## Auth Flow (SIWS)

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │     │   Gateway    │     │ Auth Service │     │    Redis     │
│   (Angular)  │     │    (Go)      │     │    (Go)      │     │              │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │                    │
       │ 1. POST /auth/nonce                     │                    │
       │   { walletAddress }                     │                    │
       │──────────────────►│                    │                    │
       │                    │ Proxy              │                    │
       │                    │───────────────────►│                    │
       │                    │                    │ Generate nonce     │
       │                    │                    │───────────────────►│
       │                    │                    │  SET nonce:uuid    │
       │                    │                    │◄───────────────────│
       │                    │                    │                    │
       │                    │  { nonce, nonceId }│                    │
       │◄──────────────────│◄───────────────────│                    │
       │                    │                    │                    │
       │ 2. Sign nonce with wallet               │                    │
       │    message = "OPRAI login: <nonce>"     │                    │
       │                    │                    │                    │
       │ 3. POST /auth/verify                    │                    │
       │   { walletAddress, signature, nonceId } │                    │
       │──────────────────►│                    │                    │
       │                    │ Proxy              │                    │
       │                    │───────────────────►│                    │
       │                    │                    │ Consume nonce      │
       │                    │                    │───────────────────►│
       │                    │                    │  GET + DEL         │
       │                    │                    │◄───────────────────│
       │                    │                    │                    │
       │                    │                    │ Verify ed25519 sig │
       │                    │                    │                    │
       │                    │                    │ GetOrCreateUser    │
       │                    │                    │──────────► PostgreSQL
       │                    │                    │                    │
       │                    │                    │ Issue JWT          │
       │                    │                    │                    │
       │                    │  { token, expiresAt }                   │
       │◄──────────────────│◄───────────────────│                    │
       │                    │                    │                    │
       │ 4. Store JWT in localStorage            │                    │
       │    key: "oprai-auth-token"              │                    │
```

---

## Services

### JWT Service

```go
// services/jwt.go
type JWTClaims struct {
    Wallet string `json:"w"`  // Wallet address (short)
    jwt.RegisteredClaims
}

type JWTService struct {
    secret []byte
    ttl    time.Duration  // 3 days default
}

// Issue - Create new JWT
func (s *JWTService) Issue(walletAddress string) (*JWTResult, error)

// Validate - Validate JWT
func (s *JWTService) Validate(tokenString string) (wallet string, expiresAt time.Time, err error)
```

**JWT Format:**
```json
{
  "w": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d",
  "iat": 1704067200,
  "exp": 1704326400
}
```

**Signing:** HS256 (HMAC-SHA256)

---

### Nonce Service

```go
// services/nonce.go
type NonceService struct {
    redis    *redis.Client
    ttl      time.Duration  // 10 minutes default
    fallback map[string]fallbackEntry  // In-memory fallback
}

// Generate - Create new nonce
func (ns *NonceService) Generate(ctx context.Context) (*NonceResult, error)
// Returns: { nonce: "32-char-hex", nonceId: "uuid" }

// Consume - Use nonce (single use)
func (ns *NonceService) Consume(ctx context.Context, nonceID string) (string, error)
// Atomic GET + DEL operation
```

**Redis Key:** `oprai:nonce:{nonceId}`
**TTL:** 10 minutes

**Fallback:** If Redis is unavailable, an in-memory store is used.

---

### Signature Service

```go
// services/signature.go
func VerifySignature(walletBase58 string, message []byte, signatureBase58 string) bool
```

**Algorithm:** Ed25519 (used by Solana)

**Message Format:** `"OPRAI login: {nonce}"`

**Verification:**
1. Decode wallet address from base58 → 32 bytes
2. Decode signature from base58 → 64 bytes
3. `ed25519.Verify(publicKey, message, signature)`

---

## HTTP Endpoints

### Auth Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/nonce` | Create nonce |
| `GET` | `/auth/nonce` | Create nonce (alternative) |
| `POST` | `/auth/verify` | Verify signature, get JWT |
| `GET` | `/auth/session` | Check session status |
| `POST` | `/auth/logout` | Logout (stateless) |
| `GET` | `/auth/me` | Current user info |

### User Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/users/me` | Current user |
| `GET` | `/users/{wallet}` | User info |
| `PATCH` | `/users/{wallet}` | Update user |

---

## Handler Details

### HandleNonce

```go
// POST /auth/nonce
func (h *AuthHandler) HandleNonce(w http.ResponseWriter, r *http.Request) {
    result, err := h.nonceService.Generate(r.Context())
    // ...
    writeJSON(w, http.StatusOK, nonceResponse{
        Nonce:   result.Nonce,    // 32-char hex
        NonceID: result.NonceID,  // UUID
    })
}
```

**Request Body:** (optional)
```json
{
  "walletAddress": "Hx7b8k..."  // Optional, for logging
}
```

**Response:**
```json
{
  "nonce": "a1b2c3d4e5f6...",
  "nonceId": "uuid-1234-..."
}
```

---

### HandleVerify

```go
// POST /auth/verify
func (h *AuthHandler) HandleVerify(w http.ResponseWriter, r *http.Request) {
    var req verifyRequest
    json.NewDecoder(r.Body).Decode(&req)

    // 1. Resolve nonce
    nonce := h.nonceService.Consume(ctx, req.NonceID)

    // 2. Construct message
    message := fmt.Sprintf("OPRAI login: %s", nonce)

    // 3. Verify signature
    if !services.VerifySignature(req.WalletAddress, []byte(message), req.Signature) {
        writeError(w, 401, "Invalid signature")
        return
    }

    // 4. Get or create user
    user, _ := h.queries.GetOrCreateUser(ctx, req.WalletAddress)

    // 5. Log login (async)
    go h.logSuccessLogin(r, req.WalletAddress, user.ID)

    // 6. Issue JWT
    result, _ := h.jwtService.Issue(req.WalletAddress)

    writeJSON(w, 200, verifyResponse{
        OK:        true,
        Token:     result.Token,
        ExpiresAt: result.ExpiresAt,
    })
}
```

**Request Body:**
```json
{
  "walletAddress": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d",
  "signature": "4ZkJ9...base58...",
  "nonceId": "uuid-1234-..."
}
```

**Response:**
```json
{
  "ok": true,
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "expiresAt": "2024-01-18T10:00:00.000Z"
}
```

---

### HandleSession

```go
// GET /auth/session
func (h *AuthHandler) HandleSession(w http.ResponseWriter, r *http.Request) {
    authHeader := r.Header.Get("Authorization")
    if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
        writeJSON(w, 200, sessionResponse{Authenticated: false})
        return
    }

    tokenStr := authHeader[7:]
    wallet, expiresAt, err := h.jwtService.Validate(tokenStr)
    if err != nil {
        writeJSON(w, 200, sessionResponse{Authenticated: false})
        return
    }

    writeJSON(w, 200, sessionResponse{
        Authenticated: true,
        Wallet:        wallet,
        ExpiresAt:     expiresAt.Format(...),
    })
}
```

**Response (Authenticated):**
```json
{
  "authenticated": true,
  "wallet": "Hx7b8k...",
  "expiresAt": "2024-01-18T10:00:00.000Z"
}
```

**Response (Not Authenticated):**
```json
{
  "authenticated": false
}
```

---

## Database Models

### User

```go
// db/models.go
type User struct {
    ID                     string
    WalletAddress          string
    Chain                  string
    DisplayName            pgtype.Text
    RiskTolerance          pgtype.Text
    PreferredProtocols     []string
    AutoSuggestionsAllowed bool
    Role                   string
    Status                 string
    CreatedAt              time.Time
    UpdatedAt              time.Time
}
```

### LoginLog

```go
type LoginLog struct {
    ID            string
    UserID        string
    WalletAddress string
    IPAddress     string
    UserAgent     string
    Country       pgtype.Text
    Success       bool
    FailureReason pgtype.Text
    CreatedAt     time.Time
}
```

---

## Database Schema

```sql
-- auth_schema.users
CREATE TABLE auth_schema.users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address          VARCHAR(66) UNIQUE NOT NULL,
    chain                   VARCHAR(20) DEFAULT 'solana',
    display_name            VARCHAR(100),
    risk_tolerance          VARCHAR(10),
    preferred_protocols     TEXT[],
    auto_suggestions_allowed BOOLEAN DEFAULT true,
    role                    VARCHAR(20) DEFAULT 'user',
    status                  VARCHAR(20) DEFAULT 'active',
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- auth_schema.login_logs
CREATE TABLE auth_schema.login_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES auth_schema.users(id),
    wallet_address  VARCHAR(66) NOT NULL,
    ip_address      VARCHAR(45),
    user_agent      TEXT,
    country         VARCHAR(2),
    success         BOOLEAN NOT NULL,
    failure_reason  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_login_logs_wallet ON auth_schema.login_logs(wallet_address);
CREATE INDEX idx_login_logs_created ON auth_schema.login_logs(created_at);
```

---

## gRPC Service

```protobuf
// proto/auth/auth.proto
service AuthService {
  rpc GetNonce (GetNonceRequest) returns (GetNonceResponse);
  rpc VerifySignature (VerifySignatureRequest) returns (VerifySignatureResponse);
  rpc CheckSession (CheckSessionRequest) returns (CheckSessionResponse);
  rpc Logout (LogoutRequest) returns (LogoutResponse);
}

service UserService {
  rpc GetUser (GetUserRequest) returns (UserProfile);
  rpc UpdateUser (UpdateUserRequest) returns (UserProfile);
  rpc ListUsers (ListUsersRequest) returns (ListUsersResponse);
}
```

---

## Internal API Key Middleware

```go
// middleware/auth.go
func InternalAuth(internalAPIKey string) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            // Skip for health check
            if r.URL.Path == "/health" {
                next.ServeHTTP(w, r)
                return
            }

            key := r.Header.Get("X-Internal-Api-Key")
            if key == "" || key != internalAPIKey {
                writeError(w, http.StatusUnauthorized, "Unauthorized")
                return
            }
            next.ServeHTTP(w, r)
        })
    }
}
```

**Note:** Requests from the Gateway include the `X-Internal-Api-Key` header.

---

## Prometheus Metrics

```go
// middleware/metrics.go
var (
    httpRequestsTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{
            Name: "auth_http_requests_total",
            Help: "Total HTTP requests",
        },
        []string{"method", "path", "status"},
    )

    loginsTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{
            Name: "auth_logins_total",
            Help: "Total login attempts",
        },
        []string{"status"},  // success, failed
    )
)
```

---

## Error Codes

| HTTP | Error | Description |
|------|-------|-------------|
| 400 | `walletAddress is required` | Missing wallet |
| 400 | `signature is required` | Missing signature |
| 400 | `Nonce missing or expired` | Nonce not found or expired |
| 401 | `Invalid signature` | Ed25519 verification failed |
| 401 | `Unauthorized` | Invalid API key |
| 500 | `Failed to generate nonce` | Redis error |
| 500 | `Failed to issue token` | JWT error |

---

## Build & Run

```bash
# Development
cd services/auth-service-go
go run ./cmd/auth-service

# Build
go build -o bin/auth-service ./cmd/auth-service

# Production
./bin/auth-service
```

---

## Testing

```bash
# Get nonce
curl -X POST http://localhost:3010/auth/nonce \
  -H "Content-Type: application/json" \
  -H "X-Internal-Api-Key: dev-internal-key-change" \
  -d '{"walletAddress": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d"}'

# Verify (signature is created on the frontend)
curl -X POST http://localhost:3010/auth/verify \
  -H "Content-Type: application/json" \
  -H "X-Internal-Api-Key: dev-internal-key-change" \
  -d '{
    "walletAddress": "Hx7b8k...",
    "signature": "base58-signature",
    "nonceId": "uuid-from-nonce"
  }'

# Check session
curl http://localhost:3010/auth/session \
  -H "Authorization: Bearer <jwt>"
```

---

## Dependencies

```go
// go.mod
require (
    github.com/go-chi/chi/v5 v5.0.12
    github.com/golang-jwt/jwt/v5 v5.2.0
    github.com/google/uuid v1.6.0
    github.com/jackc/pgx/v5 v5.5.5
    github.com/mr-tron/base58 v1.2.0
    github.com/prometheus/client_golang v1.19.0
    github.com/redis/go-redis/v9 v9.5.1
    google.golang.org/grpc v1.62.0
)
```

# Gateway Service (Go)

API Gateway - Single entry point for all requests. JWT validation, rate limiting, CORS, circuit breaker, and reverse proxy.

## Quick Start

```bash
cd services/gateway-go
go run ./cmd/gateway
# → http://localhost:3001
```

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │          GATEWAY (:3001)             │
                                    │                                     │
                                    │  ┌─────────────────────────────┐    │
                                    │  │        Middleware Stack      │    │
                                    │  │  1. RequestID               │    │
                                    │  │  2. Recoverer               │    │
                                    │  │  3. CORS                    │    │
                                    │  │  4. Metrics                 │    │
                                    │  │  5. Logger                  │    │
                                    │  │  6. Rate Limit (100/min)    │    │
                                    │  │  7. JWT Auth                │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    │  ┌──────────────┴──────────────┐    │
                                    │  │         Router (Chi)         │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    └─────────────────┼───────────────────┘
                                                      │
         ┌────────────────┬────────────────┬─────────┴────────┬────────────────┐
         │                │                │                  │                │
         ▼                ▼                ▼                  ▼                ▼
    ┌─────────┐     ┌─────────┐     ┌─────────┐        ┌─────────┐     ┌─────────┐
    │  Auth   │     │  Chat   │     │ Solana  │        │ Memory  │     │ Market  │
    │ :3010   │     │ :3020   │     │ :3030   │        │ :3040   │     │ External│
    └─────────┘     └─────────┘     └─────────┘        └─────────┘     └─────────┘
```

---

## File Structure

```
services/gateway-go/
├── cmd/
│   └── gateway/
│       └── main.go              # Entry point
├── internal/
│   ├── config/
│   │   └── env.go               # Environment config
│   ├── server/
│   │   └── router.go            # Chi router + routes
│   ├── middleware/
│   │   ├── auth.go              # JWT validation
│   │   ├── cors.go              # CORS handling
│   │   ├── logger.go            # Request logging
│   │   ├── metrics.go           # Prometheus metrics
│   │   └── rate_limit.go        # IP-based rate limiting
│   ├── handlers/
│   │   ├── health.go            # Aggregated health
│   │   ├── auth_proxy.go        # Auth service proxy
│   │   ├── chat_proxy.go        # Chat service proxy
│   │   ├── solana_proxy.go      # Solana service proxy
│   │   ├── memory_proxy.go      # Memory service proxy
│   │   ├── market_proxy.go      # External API proxy
│   │   └── upload.go            # IPFS upload proxy
│   └── proxy/
│       ├── grpc_clients.go      # gRPC connection pool
│       └── circuit_breaker.go   # Circuit breaker
├── go.mod
└── go.sum
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3001` | Gateway HTTP port |
| `OPRAI_JWT_SECRET` | *(required)* | JWT signing key |
| `OPRAI_INTERNAL_API_KEY` | *(required)* | Inter-service API key |
| `CORS_ORIGIN` | `http://localhost:3000` | Allowed CORS origin |
| `NODE_ENV` | `development` | Environment |
| `TRUST_PROXY_HEADERS` | `false` | Trust X-Forwarded-For headers? |
| `AUTH_SERVICE_GRPC` | `localhost:50051` | Auth gRPC address |
| `CHAT_SERVICE_GRPC` | `localhost:50052` | Chat gRPC address |
| `SOLANA_SERVICE_GRPC` | `localhost:50053` | Solana gRPC address |
| `MEMORY_SERVICE_GRPC` | `localhost:50054` | Memory gRPC address |
| `BIRDEYE_API_KEY` | - | Market data API |
| `JUPITER_API_KEY` | - | Jupiter API |
| `HELIUS_API_KEY` | - | Helius RPC |
| `PINATA_JWT` | - | IPFS upload |

---

## Middleware Stack

### 1. RequestID
Adds a unique ID to each request. Used for tracing through logs.

### 2. Recoverer
Recovers from panics and returns 500.

### 3. CORS
```go
// cors.go
func CORSMiddleware(origin string) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            w.Header().Set("Access-Control-Allow-Origin", origin)
            w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
            w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-User-Wallet")
            w.Header().Set("Access-Control-Allow-Credentials", "true")

            if r.Method == "OPTIONS" {
                w.WriteHeader(http.StatusOK)
                return
            }
            next.ServeHTTP(w, r)
        })
    }
}
```

### 4. Metrics (Prometheus)
```go
// metrics.go
var (
    httpRequestsTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{
            Name: "gateway_http_requests_total",
            Help: "Total HTTP requests",
        },
        []string{"method", "path", "status"},
    )

    httpRequestDuration = promauto.NewHistogramVec(
        prometheus.HistogramOpts{
            Name:    "gateway_http_request_duration_seconds",
            Help:    "HTTP request duration",
            Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10},
        },
        []string{"method", "path"},
    )
)
```

**Metrics Endpoint:** `GET /metrics`

### 5. Logger
Structured logging (slog/JSON).

### 6. Rate Limiting

| Type | Limit | Burst | Usage |
|------|-------|-------|-------|
| Global | 100/min | 10 | All endpoints |
| Auth | 20/min | 5 | `/auth/*` endpoints |

```go
// rate_limit.go
type rateLimiterStore struct {
    mu       sync.RWMutex
    limiters map[string]*ipLimiter  // IP → limiter
    rate     rate.Limit
    burst    int
}

// Cleanup: Every 5 minutes, delete IPs not seen for 10 minutes
```

**IP Extraction:**
- `TRUST_PROXY_HEADERS=false` → use `r.RemoteAddr` (safe)
- `TRUST_PROXY_HEADERS=true` → use `X-Forwarded-For` or `X-Real-IP`

### 7. JWT Auth

```go
// auth.go
func JWTAuth(jwtSecret string) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            authHeader := r.Header.Get("Authorization")
            if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
                next.ServeHTTP(w, r)  // Non-blocking: pass but no wallet
                return
            }

            tokenString := strings.TrimPrefix(authHeader, "Bearer ")
            token, err := jwt.Parse(tokenString, /* ... */)

            if err != nil || !token.Valid {
                r.Header.Del("Authorization")  // Strip invalid token
                next.ServeHTTP(w, r)
                return
            }

            claims := token.Claims.(jwt.MapClaims)
            wallet := claims["w"].(string)  // "w" = wallet claim

            // Inject wallet into context + header
            ctx := context.WithValue(r.Context(), WalletKey, wallet)
            r = r.WithContext(ctx)
            r.Header.Set("X-User-Wallet", wallet)

            next.ServeHTTP(w, r)
        })
    }
}
```

**JWT Claims:**
```json
{
  "w": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d",
  "iat": 1704067200,
  "exp": 1704326400
}
```

---

## Routes

### Health
| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| `GET` | `/health` | `AggregatedHealth` | Status of all services |
| `GET` | `/metrics` | `promhttp.Handler()` | Prometheus metrics |

### Auth
| Method | Path | Rate Limit | Description |
|--------|------|------------|-------------|
| `POST` | `/auth/nonce` | 20/min | Get nonce |
| `POST` | `/auth/verify` | 20/min | Verify signature |
| `GET` | `/auth/me` | 20/min | Current user |
| `GET` | `/users/me` | 100/min | Current user (alias) |
| `GET` | `/users/{wallet}` | 100/min | User info |

### Chat
| Method | Path | Timeout | Description |
|--------|------|---------|-------------|
| `POST` | `/chat/` | 30s | Send chat message |
| `GET` | `/chat/stream` | 5min (SSE) | Streaming chat |
| `POST` | `/chat/messages/stream` | 5min (SSE) | Streaming message |
| `GET` | `/chat/sessions` | 30s | Session list |
| `POST` | `/chat/sessions` | 30s | Create session |
| `GET` | `/chat/sessions/{id}` | 30s | Session details |
| `DELETE` | `/chat/sessions/{id}` | 30s | Delete session |
| `PATCH` | `/chat/sessions/{id}` | 30s | Update session |
| `GET` | `/chat/sessions/{id}/messages` | 30s | Message list |
| `POST` | `/chat/sessions/{id}/messages` | 30s | Send message |
| `GET` | `/chat/sessions/{id}/messages/stream` | 5min (SSE) | Streaming messages |

### Solana / Actions
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/actions/quote` | Get swap quote |
| `POST` | `/actions/build` | Build transaction |
| `POST` | `/actions/submit` | Submit transaction |
| `GET` | `/actions/limit-orders` | Limit order list |
| `GET` | `/actions/dca-orders` | DCA order list |

### Solana / Protocols & Tokens
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/protocols/` | Supported protocols |
| `GET` | `/protocols/{protocol}` | Protocol details |
| `GET` | `/tokens/` | Token list |
| `GET` | `/tokens/{symbol}` | Token info |

### Solana / Transactions
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/transactions/` | Transaction list |
| `GET` | `/transactions/{id}` | Transaction details |
| `GET` | `/balance/` | Query balance |

### Memory
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/memory/` | List memories |
| `POST` | `/memory/` | Save memory |
| `DELETE` | `/memory/` | Delete all memories |
| `GET` | `/memory/search` | Search memory |
| `DELETE` | `/memory/{id}` | Delete single memory |
| `GET` | `/consent/` | Get consent status |
| `PUT` | `/consent/` | Update consent |
| `POST` | `/summarize` | Summarize text |

### Market (External APIs)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/market/prices` | Get prices |
| `GET` | `/market/tokens/search` | Search token |
| `GET` | `/market/tokens/strict` | Token list |
| `GET` | `/market/tokens/{mint}` | Token details |
| `GET` | `/market/ohlcv` | OHLCV data |
| `GET` | `/market/trending` | Trending tokens |
| `GET` | `/market/analytics/{mint}` | Token analytics |
| `GET` | `/market/pairs/{mint}` | Trading pairs |
| `GET` | `/market/trades/{mint}` | Trade history |
| `GET` | `/market/holders/{mint}` | Holder list |
| `GET` | `/market/jito/tip-floor` | Jito tip floor |
| `GET` | `/market/security/{mint}` | Security analysis |
| `GET` | `/market/latest-pairs` | New pairs |
| `POST` | `/market/helius/transactions` | Query Helius TX |
| `POST` | `/rpc` | Solana JSON-RPC proxy |

### Upload
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/upload/image` | Upload image to IPFS |

---

## Circuit Breaker

Separate circuit breaker for each backend service.

```go
// circuit_breaker.go
type CircuitBreaker struct {
    cb   *gobreaker.CircuitBreaker
    name string
}

// Settings:
// - MaxRequests: 3 (max 3 requests in half-open state)
// - Interval: 30s (counts reset)
// - Timeout: 15s (open → half-open transition)
// - ReadyToTrip: error rate > 50% with min 5 requests
```

**States:**
| State | Value | Description |
|-------|-------|-------------|
| Closed | 0 | Normal |
| HalfOpen | 1 | Testing |
| Open | 2 | Rejecting |

**Metrics:**
```
circuit_breaker_state{service="auth"} 0
circuit_breaker_state{service="chat"} 0
circuit_breaker_state{service="solana"} 0
circuit_breaker_state{service="memory"} 0
```

---

## Proxy Pattern

All backend services are routed via reverse proxy.

```go
// auth_proxy.go
type AuthProxy struct {
    proxy          *httputil.ReverseProxy
    internalAPIKey string
}

func NewAuthProxy(authServiceURL string, internalAPIKey string) *AuthProxy {
    target, _ := url.Parse(authServiceURL)
    rp := httputil.NewSingleHostReverseProxy(target)

    // Custom director: inject internal headers
    originalDirector := rp.Director
    rp.Director = func(req *http.Request) {
        originalDirector(req)
        req.Header.Set("X-Internal-Api-Key", internalAPIKey)
        // X-User-Wallet already injected by JWT middleware
    }

    // Strip CORS from upstream
    rp.ModifyResponse = func(resp *http.Response) error {
        resp.Header.Del("Access-Control-Allow-Origin")
        // ...
        return nil
    }

    return &AuthProxy{proxy: rp, internalAPIKey: internalAPIKey}
}
```

**Injected Headers:**
| Header | Source | Description |
|--------|--------|-------------|
| `X-Internal-Api-Key` | Config | Inter-service auth |
| `X-User-Wallet` | JWT | Authenticated wallet |

---

## Health Check

```go
// health.go
func (h *HealthHandler) AggregatedHealth(w http.ResponseWriter, r *http.Request) {
    serviceHealths := h.clients.CheckHealth(r.Context())

    // Determine overall status
    allOk := true
    allDown := true
    for _, sh := range serviceHealths {
        if sh.Status != "ok" { allOk = false }
        if sh.Status != "down" { allDown = false }
    }

    status := "degraded"
    if allOk { status = "ok" }
    else if allDown { status = "down" }

    httpStatus := http.StatusOK
    if status == "down" { httpStatus = http.StatusServiceUnavailable }

    writeJSON(w, httpStatus, map[string]interface{}{
        "status":   status,
        "services": serviceHealths,
    })
}
```

**Response Example:**
```json
{
  "status": "ok",
  "services": [
    {"name": "auth-service", "status": "ok", "latency_ms": 5.2},
    {"name": "chat-service", "status": "ok", "latency_ms": 8.1},
    {"name": "solana-service", "status": "ok", "latency_ms": 12.3},
    {"name": "memory-service", "status": "ok", "latency_ms": 3.4}
  ]
}
```

---

## SSE Streaming

Special timeout handling for Server-Sent Events.

```go
// router.go
// Normal routes: 30s timeout
defaultTimeout := chimiddleware.Timeout(30 * time.Second)

// SSE routes: Context-only timeout (no ResponseWriter wrap)
streamingTimeout := func(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        ctx, cancel := context.WithTimeout(r.Context(), 5*time.Minute)
        defer cancel()
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}

// SSE routes
r.With(streamingTimeout).Get("/chat/stream", chatProxy.StreamChat)
r.With(streamingTimeout).Get("/chat/sessions/{id}/messages/stream", chatProxy.StreamMessages)
```

**Why chimiddleware.Timeout is not used:**
- It wraps the ResponseWriter → writes get buffered
- SSE flushes are blocked → streaming breaks

---

## Error Handling

```go
// helpers.go
func writeError(w http.ResponseWriter, status int, message string) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    json.NewEncoder(w).Encode(map[string]string{
        "error": message,
    })
}

// Proxy error handler
rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
    slog.Error("proxy error", "error", err, "path", r.URL.Path)
    writeError(w, http.StatusBadGateway, "Service unavailable")
}
```

---

## Graceful Shutdown

```go
// main.go
quit := make(chan os.Signal, 1)
signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

select {
case sig := <-quit:
    slog.Info("received shutdown signal", "signal", sig)
case err := <-errCh:
    slog.Error("server error", "error", err)
}

ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
defer cancel()

httpServer.Shutdown(ctx)  // Wait for pending requests
```

---

## Build & Run

```bash
# Development
cd services/gateway-go
go run ./cmd/gateway

# Build
go build -o bin/gateway ./cmd/gateway

# Production
./bin/gateway
```

---

## Testing

```bash
# Health check
curl http://localhost:3001/health

# Auth flow
curl -X POST http://localhost:3001/auth/nonce \
  -H "Content-Type: application/json" \
  -d '{"wallet_address": "Hx7b8k..."}'

# With JWT
curl http://localhost:3001/chat/sessions \
  -H "Authorization: Bearer <jwt>"
```

---

## Prometheus Metrics

```promql
# Request rate
rate(gateway_http_requests_total[5m])

# Latency P99
histogram_quantile(0.99, rate(gateway_http_request_duration_seconds_bucket[5m]))

# Circuit breaker state
circuit_breaker_state{service="auth"}

# Error rate
rate(gateway_http_requests_total{status=~"5.."}[5m]) / rate(gateway_http_requests_total[5m])
```

---

## Dependencies

```go
// go.mod
require (
    github.com/go-chi/chi/v5 v5.0.12
    github.com/golang-jwt/jwt/v5 v5.2.0
    github.com/google/uuid v1.6.0
    github.com/joho/godotenv v1.5.1
    github.com/prometheus/client_golang v1.19.0
    github.com/sony/gobreaker v0.5.0
    golang.org/x/time v0.5.0
    google.golang.org/grpc v1.62.0
)
```

# Admin Service (Go)

Analytics, audit logging, and management services for the admin panel. **Operates independently from the Gateway.**

## Quick Start

```bash
cd services/admin-service-go
go run ./cmd/admin-service
# → HTTP: http://localhost:3050
# → gRPC: localhost:50055
```

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │        ADMIN SERVICE                 │
                                    │                                     │
                                    │  ┌─────────────────────────────┐    │
                                    │  │      HTTP Server (:3050)    │    │
                                    │  │   Chi + Admin JWT Auth      │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    │  ┌──────────────┴──────────────┐    │
                                    │  │     gRPC Server (:50055)    │    │
                                    │  │   AdminAuth + Analytics     │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    └─────────────────┼───────────────────┘
                                                      │
         ┌────────────────────────────────────────────┼────────────────────────┐
         │                                            │                        │
         ▼                                            ▼                        │
    ┌─────────────┐                           ┌─────────────┐                 │
    │ PostgreSQL  │                           │ Admin Panel │                 │
    │ admin_schema│                           │  (Angular)  │                 │
    │ cross-schema│                           │   :3200     │                 │
    └─────────────┘                           └─────────────┘                 │
         │                                                                     │
         │  SQL Queries Span:                                                  │
         │  ├── auth_schema.users                                              │
         │  ├── chat_schema.chat_sessions                                      │
         │  ├── chat_schema.chat_messages                                      │
         │  ├── solana_schema.transactions                                     │
         │  └── admin_schema.*                                                 │
         └─────────────────────────────────────────────────────────────────────┘
```

**Note:** The admin service does not go through the Gateway. The frontend connects directly.

---

## File Structure

```
services/admin-service-go/
├── cmd/
│   └── admin-service/
│       └── main.go              # Entry point
├── internal/
│   ├── config/
│   │   └── env.go               # Environment config
│   ├── server/
│   │   ├── http.go              # Chi HTTP server
│   │   ├── grpc.go              # gRPC server
│   │   └── json.go              # JSON helpers
│   ├── db/
│   │   ├── connection.go        # pgx pool
│   │   ├── models.go            # Data models
│   │   └── queries.go           # Cross-schema SQL queries
│   ├── handlers/
│   │   ├── auth_handler.go      # Admin auth
│   │   ├── dashboard_handler.go # Dashboard stats
│   │   ├── audit_handler.go     # Audit logs
│   │   ├── transaction_handler.go # Transaction listing
│   │   ├── session_handler.go   # Session listing
│   │   ├── iplog_handler.go     # IP logs
│   │   ├── export_handler.go    # CSV/JSON export
│   │   └── login_guard.go       # Brute-force protection
│   ├── middleware/
│   │   ├── admin_auth.go        # Admin JWT validation
│   │   ├── audit.go             # Audit middleware
│   │   └── metrics.go           # Prometheus
│   └── services/
│       └── auth.go              # Admin JWT service
├── go.mod
└── go.sum
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3050` | HTTP port |
| `GRPC_PORT` | `50055` | gRPC port |
| `OPRAI_ADMIN_JWT_SECRET` | *(required)* | Admin JWT secret |
| `DATABASE_URL` | *(required)* | PostgreSQL connection |
| `DB_SCHEMA` | `admin_schema` | Admin schema |
| `NODE_ENV` | `development` | Environment |

---

## Admin Auth

### Login Flow

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│ Admin Panel  │     │  Admin Service   │     │  PostgreSQL  │
│   (Angular)  │     │      (Go)        │     │ admin_schema │
└──────┬───────┘     └────────┬─────────┘     └──────┬───────┘
       │                      │                      │
       │ POST /admin/login    │                      │
       │ { username, password }                     │
       │─────────────────────►│                      │
       │                      │                      │
       │                      │ Brute-force check    │
       │                      │ (in-memory)          │
       │                      │                      │
       │                      │ GetAdminByUsername   │
       │                      │─────────────────────►│
       │                      │                      │
       │                      │ bcrypt.Compare       │
       │                      │                      │
       │                      │ Create JWT           │
       │                      │                      │
       │                      │ CreateAuditLog       │
       │                      │─────────────────────►│
       │                      │                      │
       │  { token, expiresAt }│                      │
       │◄─────────────────────│                      │
```

### Brute-Force Protection

```go
// handlers/login_guard.go
var (
    loginAttempts = make(map[string]*ipAttempt)  // IP → attempts
    usernameLocks = make(map[string]time.Time)   // username → locked until
)

const (
    maxAttempts    = 5               // Max failed attempts
    windowDuration = 15 * time.Minute // Time window
    lockDuration   = 30 * time.Minute // Lock duration
)

func checkLogin(ip, username string) (blocked bool, reason string, retryAfter int)
func recordFailure(ip, username string) string
func recordSuccess(ip, username string)
```

**Rules:**
- 5 failed attempts → 30 minute lock
- Per-IP and per-username tracking
- Counters reset on successful login

---

## HTTP Endpoints

### Auth Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/admin/login` | - | Admin login |
| `GET` | `/admin/verify` | Admin | Validate token |
| `GET` | `/admin/users` | Superadmin | Admin list |
| `POST` | `/admin/users` | Superadmin | Create admin |
| `DELETE` | `/admin/users/{id}` | Superadmin | Delete admin |
| `POST` | `/admin/users/{id}/reset-password` | Superadmin | Reset password |

### Dashboard Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/admin/stats` | Admin | Dashboard statistics |
| `GET` | `/admin/users/growth` | Admin | User growth |
| `GET` | `/admin/tx/volume` | Admin | Transaction volume |

### Data Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/admin/users` | Admin | User list |
| `GET` | `/admin/transactions` | Admin | Transaction list |
| `GET` | `/admin/sessions` | Admin | Chat session list |
| `GET` | `/admin/audit` | Admin | Audit log list |
| `GET` | `/admin/ip-logs` | Admin | IP log list |

### Export Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/admin/export/audit` | Admin | Audit log export (CSV/JSON) |
| `GET` | `/admin/export/transactions` | Admin | TX export |
| `GET` | `/admin/export/users` | Admin | User export |

---

## Handler Details

### Login

```go
// POST /admin/login
func (h *AuthHandler) Login(w http.ResponseWriter, r *http.Request) {
    var req loginRequest
    json.NewDecoder(r.Body).Decode(&req)

    ip := extractIP(r)

    // 1. Brute-force check
    if blocked, reason, retryAfter := checkLogin(ip, req.Username); blocked {
        w.Header().Set("Retry-After", fmt.Sprintf("%d", retryAfter))
        writeJSON(w, 429, map[string]string{"error": reason})
        return
    }

    // 2. Get admin
    admin, err := h.queries.GetAdminByUsername(ctx, req.Username)
    if err != nil {
        recordFailure(ip, req.Username)
        writeJSON(w, 401, map[string]string{"error": "Invalid credentials"})
        return
    }

    // 3. Verify password
    if err := bcrypt.CompareHashAndPassword([]byte(admin.PasswordHash), []byte(req.Password)); err != nil {
        recordFailure(ip, req.Username)
        writeJSON(w, 401, map[string]string{"error": "Invalid credentials"})
        return
    }

    // 4. Clear failure counters
    recordSuccess(ip, req.Username)

    // 5. Create JWT
    token, expiresAt, _ := h.authService.CreateToken(admin.Username, admin.Role)

    // 6. Audit log (async)
    go h.queries.CreateAuditLog(ctx, db.AuditLogEntry{
        AdminID:       admin.ID,
        AdminUsername: admin.Username,
        Action:        "admin.login",
        IPAddress:     ip,
    })

    writeJSON(w, 200, map[string]interface{}{
        "ok":        true,
        "token":     token,
        "expiresAt": expiresAt,
        "username":  admin.Username,
    })
}
```

### CreateAdmin

```go
// POST /admin/users
// Only superadmin can create new admins
func (h *AuthHandler) CreateAdmin(w http.ResponseWriter, r *http.Request) {
    var req createAdminRequest
    json.NewDecoder(r.Body).Decode(&req)

    // Generate random password
    password := generatePassword()  // 20-char base64

    // Hash with bcrypt
    hash, _ := bcrypt.GenerateFromPassword([]byte(password), 12)

    admin, _ := h.queries.CreateAdminUser(ctx, req.Username, string(hash))

    writeJSON(w, 201, map[string]interface{}{
        "ok": true,
        "admin": {
            "id":       admin.ID,
            "username": admin.Username,
            "role":     admin.Role,
        },
        "generatedPassword": password,  // Show once!
    })
}
```

**Note:** The password is only shown at creation time. It cannot be viewed afterwards.

---

## Dashboard Stats

```go
// GET /admin/stats?days=30
func (h *DashboardHandler) GetStats(w http.ResponseWriter, r *http.Request) {
    stats, err := h.queries.GetDashboardStats(ctx)
    writeJSON(w, 200, stats)
}
```

**Response:**
```json
{
  "totalUsers": 1523,
  "totalSessions": 4521,
  "totalTransactions": 8765,
  "totalMessages": 23456,
  "usersToday": 45,
  "txToday": 123,
  "sessionsActive": 78,
  "messagesToday": 456,
  "trends": {
    "users7dAgo": 1400,
    "tx7dAgo": 8000,
    "sessions7dAgo": 4200,
    "messages7dAgo": 22000
  },
  "txByStatus": [
    {"status": "confirmed", "count": 8500},
    {"status": "pending", "count": 15},
    {"status": "failed", "count": 250}
  ],
  "txByAction": [
    {"action": "swap", "count": 5000},
    {"action": "transfer", "count": 2500},
    {"action": "stake", "count": 1000},
    {"action": "launch_token", "count": 265}
  ],
  "recentUsers": [...],
  "recentTransactions": [...]
}
```

---

## Cross-Schema Queries

```go
// db/queries.go
func (q *Queries) GetDashboardStats(ctx context.Context) (*DashboardStats, error) {
    // Single query with cross-schema joins
    query := `
        SELECT
            (SELECT COUNT(*) FROM auth_schema.users) as total_users,
            (SELECT COUNT(*) FROM chat_schema.chat_sessions WHERE deleted_at IS NULL) as total_sessions,
            (SELECT COUNT(*) FROM solana_schema.transactions) as total_transactions,
            (SELECT COUNT(*) FROM chat_schema.chat_messages) as total_messages,
            (SELECT COUNT(*) FROM auth_schema.users WHERE created_at > NOW() - INTERVAL '1 day') as users_today,
            (SELECT COUNT(*) FROM solana_schema.transactions WHERE created_at > NOW() - INTERVAL '1 day') as tx_today,
            (SELECT COUNT(*) FROM chat_schema.chat_sessions WHERE updated_at > NOW() - INTERVAL '1 hour') as sessions_active,
            (SELECT COUNT(*) FROM chat_schema.chat_messages WHERE created_at > NOW() - INTERVAL '1 day') as messages_today
    `
    // ...
}
```

**Accessed Schemas:**
- `auth_schema.users` - User data
- `chat_schema.chat_sessions` - Chat sessions
- `chat_schema.chat_messages` - Messages
- `solana_schema.transactions` - Transactions
- `admin_schema.*` - Admin data

---

## Audit Logs

### Audit Middleware

```go
// middleware/audit.go
func AuditMiddleware(action string) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            next.ServeHTTP(w, r)

            // Log after request completes
            go func() {
                entry := db.AuditLogEntry{
                    AdminID:       GetAdminID(r.Context()),
                    AdminUsername: GetAdminUsername(r.Context()),
                    Action:        action,
                    IPAddress:     extractIP(r),
                    TargetType:    extractTargetType(r),
                    TargetID:      extractTargetID(r),
                }
                q.CreateAuditLog(context.Background(), entry)
            }()
        })
    }
}
```

### Audit Actions

| Action | Description |
|--------|-------------|
| `admin.login` | Admin login |
| `admin.logout` | Admin logout |
| `admin.create` | Admin created |
| `admin.delete` | Admin deleted |
| `password.reset` | Password reset |
| `user.suspend` | User suspended |
| `user.ban` | User banned |
| `user.activate` | User activated |
| `export.audit` | Audit log export |
| `export.users` | User export |

---

## IP Logs (Multi-Account Detection)

```go
// GET /admin/ip-logs?multiAccountOnly=true
func (h *IPLogHandler) List(w http.ResponseWriter, r *http.Request) {
    params := parseIPLogParams(r)

    logs, total, _ := h.queries.ListIPLogs(ctx, params)

    writeJSON(w, 200, PaginatedResult{
        Data: logs,
        Total: total,
        // ...
    })
}
```

**Response:**
```json
{
  "data": [
    {
      "id": "uuid",
      "ipAddress": "192.168.1.100",
      "walletsFromIp": 3,  // 3 different wallets from this IP
      "country": "TR",
      "lastSeen": "2024-01-15T10:00:00Z",
      "wallets": ["Hx7b...", "9WzD...", "Gst4..."]
    }
  ],
  "total": 150
}
```

**Usage:** For multi-account / Sybil detection.

---

## Export

### CSV Export

```go
// GET /admin/export/audit?from=2024-01-01&to=2024-01-31&format=csv
func (h *ExportHandler) ExportAudit(w http.ResponseWriter, r *http.Request) {
    format := r.URL.Query().Get("format")  // csv or json

    w.Header().Set("Content-Disposition", "attachment; filename=audit_logs.csv")
    w.Header().Set("Content-Type", "text/csv")

    writer := csv.NewWriter(w)
    defer writer.Flush()

    // Write header
    writer.Write([]string{"ID", "Admin", "Action", "Target", "IP", "Timestamp"})

    // Stream rows
    rows, _ := h.queries.StreamAuditLogs(ctx, params)
    for row := range rows {
        writer.Write([]string{
            row.ID,
            row.AdminUsername,
            row.Action,
            row.TargetID,
            row.IPAddress,
            row.CreatedAt.Format(time.RFC3339),
        })
    }
}
```

---

## Admin Roles

| Role | Permissions |
|------|-------------|
| `superadmin` | Everything + create/delete admins |
| `admin` | View all data, user management |
| `viewer` | Read-only access |

**Role Middleware:**
```go
// middleware/admin_auth.go
func RequireRole(role string) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            currentRole := GetAdminRole(r.Context())
            if currentRole != "superadmin" && currentRole != role {
                writeJSON(w, 403, map[string]string{"error": "Forbidden"})
                return
            }
            next.ServeHTTP(w, r)
        })
    }
}
```

---

## Database Schema

```sql
-- admin_schema.admin_users
CREATE TABLE admin_schema.admin_users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20) DEFAULT 'admin',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- admin_schema.admin_audit_log
CREATE TABLE admin_schema.admin_audit_log (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id      UUID REFERENCES admin_schema.admin_users(id),
    admin_username VARCHAR(50),
    action        VARCHAR(100) NOT NULL,
    target_type   VARCHAR(50),
    target_id     VARCHAR(100),
    details       JSONB,
    ip_address    VARCHAR(45),
    user_agent    TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_admin ON admin_schema.admin_audit_log(admin_id);
CREATE INDEX idx_audit_action ON admin_schema.admin_audit_log(action);
CREATE INDEX idx_audit_created ON admin_schema.admin_audit_log(created_at);
```

---

## Default Admin

```sql
-- First admin user — inserted by scripts/db/seed_admin.sh, never by a migration.
-- The bcrypt hash (cost 12) is generated from $ADMIN_INITIAL_PASSWORD at seed time.
INSERT INTO admin_schema.admin_users (username, password_hash, role)
VALUES (:admin_user, :admin_hash, 'superadmin')
ON CONFLICT (username) DO NOTHING;
```

**Note:** This password must be changed in production!

---

## gRPC Service

```protobuf
// proto/admin/admin_auth.proto
service AdminAuthService {
  rpc Login (AdminLoginRequest) returns (AdminLoginResponse);
  rpc VerifyToken (AdminVerifyTokenRequest) returns (AdminVerifyTokenResponse);
  rpc CreateAdmin (CreateAdminRequest) returns (AdminUser);
  rpc ResetPassword (ResetPasswordRequest) returns (ResetPasswordResponse);
}

// proto/admin/analytics.proto
service AdminAnalyticsService {
  rpc GetDashboardStats (GetDashboardStatsRequest) returns (DashboardStats);
  rpc GetUserGrowth (GetUserGrowthRequest) returns (TimeSeriesResponse);
  rpc GetTransactionVolume (GetTransactionVolumeRequest) returns (TimeSeriesResponse);
}

// proto/admin/audit.proto
service AdminAuditService {
  rpc ListAuditLogs (ListAuditLogsRequest) returns (ListAuditLogsResponse);
  rpc ExportAuditLogs (ExportAuditLogsRequest) returns (stream ExportAuditLogsChunk);
  rpc ListIpLogs (ListIpLogsRequest) returns (ListIpLogsResponse);
  rpc ListTransactions (ListTransactionsRequest) returns (ListTransactionsResponse);
  rpc ListSessions (ListSessionsRequest) returns (ListSessionsResponse);
}
```

---

## Prometheus Metrics

```go
var (
    adminLoginsTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{
            Name: "admin_logins_total",
            Help: "Total admin login attempts",
        },
        []string{"status"},  // success, failed
    )

    adminActionsTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{
            Name: "admin_actions_total",
            Help: "Total admin actions",
        },
        []string{"action"},
    )
)
```

---

## Build & Run

```bash
# Development
cd services/admin-service-go
go run ./cmd/admin-service

# Build
go build -o bin/admin-service ./cmd/admin-service

# Production
./bin/admin-service
```

---

## Testing

```bash
# Login
curl -X POST http://localhost:3050/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "$ADMIN_INITIAL_PASSWORD"}'

# Dashboard stats
curl http://localhost:3050/admin/stats \
  -H "Authorization: Bearer <jwt>"

# Create admin (superadmin only)
curl -X POST http://localhost:3050/admin/users \
  -H "Authorization: Bearer <superadmin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"username": "newadmin"}'

# Export audit logs
curl http://localhost:3050/admin/export/audit?format=csv \
  -H "Authorization: Bearer <jwt>" \
  -o audit_logs.csv
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
    golang.org/x/crypto v0.21.0
    github.com/prometheus/client_golang v1.19.0
    google.golang.org/grpc v1.62.0
)
```

---

## Security Notes

1. **Brute-force Protection:** 5 failed attempts → 30 min lock
2. **Password Generation:** 20-char random base64
3. **bcrypt Cost:** 12 (strong hashing)
4. **JWT Expiry:** 24 hours
5. **Self-deletion Prevention:** Cannot delete own account
6. **Audit Logging:** All admin actions logged
7. **No Gateway:** Direct access, separate auth

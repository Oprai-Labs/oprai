# proto/admin/ - Admin Panel Services

Authentication, analytics, and audit services for the admin panel.

## Services

| Service | File | Description |
|---------|------|-------------|
| AdminAuthService | admin_auth.proto | Admin authentication |
| AdminAnalyticsService | analytics.proto | Dashboard statistics |
| AdminAuditService | audit.proto | Audit logs and reports |

**Port:** **50055 (gRPC)** / **3050 (HTTP)**
**Note:** Operates **independently** from the Gateway. Direct access.

---

# AdminAuthService

Username/password-based authentication for admin users.

## RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `Login` | AdminLoginRequest | AdminLoginResponse | Admin login |
| `VerifyToken` | AdminVerifyTokenRequest | AdminVerifyTokenResponse | Verify token |
| `CreateAdmin` | CreateAdminRequest | AdminUser | Create admin (superadmin) |
| `ResetPassword` | ResetPasswordRequest | ResetPasswordResponse | Reset password |

---

## AdminUser

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID |
| `username` | `string` | Username |
| `role` | `string` | "superadmin" \| "admin" \| "viewer" |
| `created_at` | `Timestamp` | Creation |
| `last_login_at` | `Timestamp` | Last login |

### Roles

| Role | Permissions |
|------|-------------|
| `superadmin` | Everything + admin creation |
| `admin` | User management, data viewing |
| `viewer` | Read-only |

---

## Login

### AdminLoginRequest

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | Username |
| `password` | `string` | Password |

### AdminLoginResponse

| Field | Type | Description |
|-------|------|-------------|
| `token` | `string` | Admin JWT (HS256, 24 hours) |
| `expires_at` | `Timestamp` | Token expiration |
| `admin` | `AdminUser` | Admin info |

**Default Admin:**
- Username: `admin`
- Password: `admin123`

---

## CreateAdmin

### CreateAdminRequest

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | Username (3-50 characters) |

**Note:** Password is auto-generated and shown once.

---

## ResetPassword

### ResetPasswordRequest

| Field | Type | Description |
|-------|------|-------------|
| `admin_id` | `string` | Admin ID |
| `password` | `string` | New password (min 8 characters) |

---

# AdminAnalyticsService

Dashboard and time series data.

## RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `GetDashboardStats` | GetDashboardStatsRequest | DashboardStats | General statistics |
| `GetUserGrowth` | GetUserGrowthRequest | TimeSeriesResponse | User growth |
| `GetTransactionVolume` | GetTransactionVolumeRequest | TimeSeriesResponse | Transaction volume |

---

## DashboardStats

| Field | Type | Description |
|-------|------|-------------|
| `total_users` | `int64` | Total users |
| `total_sessions` | `int64` | Total sessions |
| `total_messages` | `int64` | Total messages |
| `total_transactions` | `int64` | Total transactions |
| `new_users_period` | `int64` | New users in period |
| `active_users_period` | `int64` | Active users |
| `new_sessions_period` | `int64` | New sessions |
| `new_messages_period` | `int64` | New messages |
| `new_transactions_period` | `int64` | New transactions |
| `tx_pending` | `int64` | Pending TX |
| `tx_confirmed` | `int64` | Confirmed TX |
| `tx_failed` | `int64` | Failed TX |
| `action_breakdown` | `repeated ActionCount` | Action type distribution |
| `protocol_breakdown` | `repeated ProtocolCount` | Protocol distribution |
| `generated_at` | `Timestamp` | Generation time |

### ActionCount / ProtocolCount

```protobuf
message ActionCount {
  string action = 1;  // "transfer", "swap", etc.
  int64  count  = 2;
}

message ProtocolCount {
  string protocol = 1;  // "jupiter", "marinade", etc.
  int64  count    = 2;
}
```

---

## TimeSeriesResponse

### TimeSeriesPoint

| Field | Type | Description |
|-------|------|-------------|
| `date` | `Timestamp` | Date |
| `count` | `int64` | Count |
| `value` | `string` | Optional value (e.g., SOL volume) |

---

# AdminAuditService

Audit logs and reporting.

## RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `ListAuditLogs` | ListAuditLogsRequest | ListAuditLogsResponse | Audit logs |
| `ExportAuditLogs` | ExportAuditLogsRequest | `stream` ExportAuditLogsChunk | Log export |
| `ListIpLogs` | ListIpLogsRequest | ListIpLogsResponse | IP logs |
| `ListTransactions` | ListTransactionsRequest | ListTransactionsResponse | Transaction list |
| `ListSessions` | ListSessionsRequest | ListSessionsResponse | Session list |

---

## AuditLogEntry

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID |
| `admin_id` | `string` | Admin ID |
| `admin_name` | `string` | Admin name |
| `action` | `string` | Action (e.g., "user.suspend") |
| `target_type` | `string` | Target type ("user", "admin", etc.) |
| `target_id` | `string` | Target ID |
| `metadata` | `google.protobuf.Struct` | JSON details |
| `ip_address` | `string` | IP address |
| `user_agent` | `string` | User agent |
| `created_at` | `Timestamp` | Time |

### Action Types

| Action | Description |
|--------|-------------|
| `user.suspend` | User suspended |
| `user.ban` | User banned |
| `user.activate` | User activated |
| `admin.create` | Admin created |
| `admin.delete` | Admin deleted |
| `password.reset` | Password reset |
| `login.success` | Successful login |
| `login.failed` | Failed login |

---

## ExportAuditLogs

### ExportAuditLogsRequest

| Field | Type | Description |
|-------|------|-------------|
| `date_range` | `DateRange` | Date range |
| `format` | `ExportFormat` | CSV \| JSON |

### ExportFormat (Enum)

| Value | Number |
|-------|--------|
| `EXPORT_FORMAT_UNSPECIFIED` | 0 |
| `EXPORT_FORMAT_CSV` | 1 |
| `EXPORT_FORMAT_JSON` | 2 |

---

## IpLogEntry

IP-based logs for multi-account detection.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID |
| `wallet_address` | `string` | Wallet |
| `ip_address` | `string` | IP |
| `user_agent` | `string` | Browser |
| `country` | `string` | Country |
| `logged_at` | `Timestamp` | Time |
| `wallets_from_ip` | `int32` | How many wallets from this IP? |

**Multi-Account Detection:**
`wallets_from_ip > 1` -> Suspicious activity

---

## AdminTransactionEntry

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID |
| `wallet_address` | `string` | Wallet |
| `session_id` | `string` | Chat session |
| `action` | `string` | Action type |
| `status` | `string` | Status |
| `protocol` | `string` | Protocol |
| `signature` | `string` | TX signature |
| `error_message` | `string` | Error |
| `params` | `google.protobuf.Struct` | Transaction parameters |
| `created_at` | `Timestamp` | Creation |
| `confirmed_at` | `Timestamp` | Confirmation |

---

## Database Schema

### admin_schema.admin_users

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `username` | VARCHAR(50) | Unique |
| `password_hash` | VARCHAR(255) | bcrypt hash |
| `role` | VARCHAR(20) | superadmin/admin/viewer |
| `created_at` | TIMESTAMP | |
| `last_login_at` | TIMESTAMP | |

### admin_schema.admin_audit_log

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | |
| `admin_id` | UUID | FK |
| `admin_name` | VARCHAR(50) | |
| `action` | VARCHAR(50) | |
| `target_type` | VARCHAR(30) | |
| `target_id` | VARCHAR(100) | |
| `metadata` | JSONB | |
| `ip_address` | VARCHAR(45) | |
| `user_agent` | TEXT | |
| `created_at` | TIMESTAMP | |

---

## Backend Implementation (Go)

```go
// services/admin-service-go/internal/handlers/auth.go
func (h *Handler) Login(c *gin.Context) {
    var req AdminLoginRequest
    if err := c.ShouldBindJSON(&req); err != nil {
        c.JSON(400, gin.H{"error": "Invalid request"})
        return
    }

    // Get admin from DB
    admin, err := h.db.GetAdminByUsername(c.Request.Context(), req.Username)
    if err != nil {
        c.JSON(401, gin.H{"error": "Invalid credentials"})
        return
    }

    // Verify password
    if !bcrypt.CheckPasswordHash(req.Password, admin.PasswordHash) {
        // Log failed attempt
        h.audit.Log(c, "login.failed", "admin", admin.ID, nil)
        c.JSON(401, gin.H{"error": "Invalid credentials"})
        return
    }

    // Generate JWT
    token, expiresAt := h.jwt.Generate(admin)

    // Log successful login
    h.audit.Log(c, "login.success", "admin", admin.ID, nil)

    // Update last login
    h.db.UpdateLastLogin(c.Request.Context(), admin.ID)

    c.JSON(200, gin.H{
        "token":      token,
        "expires_at": expiresAt,
        "admin":      admin,
    })
}
```

---

## API Endpoints

**Note:** Admin service operates **independently** from the gateway.

| HTTP | Endpoint | gRPC Call |
|------|----------|-----------|
| `POST` | `/admin/login` | `Login` |
| `POST` | `/admin/verify` | `VerifyToken` |
| `POST` | `/admin/create` | `CreateAdmin` |
| `POST` | `/admin/reset-password` | `ResetPassword` |
| `GET` | `/admin/stats` | `GetDashboardStats` |
| `GET` | `/admin/users/growth` | `GetUserGrowth` |
| `GET` | `/admin/transactions/volume` | `GetTransactionVolume` |
| `GET` | `/admin/audit` | `ListAuditLogs` |
| `GET` | `/admin/audit/export` | `ExportAuditLogs` |
| `GET` | `/admin/ip-logs` | `ListIpLogs` |
| `GET` | `/admin/transactions` | `ListTransactions` |
| `GET` | `/admin/sessions` | `ListSessions` |

---

## Frontend Implementation (Angular)

```typescript
// apps/oprai/src/app/features/admin/services/admin.service.ts
@Injectable({ providedIn: 'root' })
export class AdminService {
    private http = inject(HttpClient);
    private apiUrl = '/admin';  // Direct to admin-service

    async login(username: string, password: string): Promise<AdminLoginResponse> {
        const response = await this.http.post<AdminLoginResponse>(
            `${this.apiUrl}/login`,
            { username, password }
        ).toPromise();

        localStorage.setItem('oprai-admin-token', response.token);
        return response;
    }

    async getDashboardStats(days: number = 30): Promise<DashboardStats> {
        return this.http.get<DashboardStats>(
            `${this.apiUrl}/stats?days=${days}`
        ).toPromise();
    }

    async exportAuditLogs(format: 'csv' | 'json', dateRange: DateRange): Promise<Blob> {
        return this.http.get(`${this.apiUrl}/audit/export`, {
            params: {
                format,
                from: dateRange.from.toISOString(),
                to: dateRange.to.toISOString()
            },
            responseType: 'blob'
        }).toPromise();
    }
}
```

---

## JWT Claims

Admin JWT payload:

```json
{
  "sub": "admin_abc123",
  "username": "admin",
  "role": "superadmin",
  "iat": 1704067200,
  "exp": 1704153600
}
```

**Environment Variables:**

| Variable | Description |
|----------|-------------|
| `OPRAI_ADMIN_JWT_SECRET` | Admin JWT signing key |
| `ADMIN_JWT_EXPIRY_HOURS` | Token validity duration (default: 24) |

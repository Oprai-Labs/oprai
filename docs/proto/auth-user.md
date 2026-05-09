# proto/auth/user.proto

User profile management service. CRUD operations for user information.

## File Information
- **Package**: `oprai.auth`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/authpb`
- **Dependencies**: `google/protobuf/timestamp.proto`, `proto/common/types.proto`

---

## Service: UserService

User management service. Port: **50051 (gRPC)** / **3010 (HTTP)**

### RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `GetUser` | GetUserRequest | UserProfile | Get user info |
| `UpdateUser` | UpdateUserRequest | UserProfile | Update user |
| `ListUsers` | ListUsersRequest | ListUsersResponse | User list (admin) |

---

## Messages

### UserProfile

Main user entity.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID (auto-generated) |
| `wallet_address` | `string` | Solana wallet address (base58) |
| `chain` | `string` | Chain name (e.g., "solana") |
| `display_name` | `string` | Display name (optional) |
| `risk_tolerance` | `string` | Risk tolerance: "low" \| "medium" \| "high" |
| `preferred_protocols` | `repeated string` | Preferred protocols |
| `auto_suggestions_allowed` | `bool` | Auto-suggestion permission |
| `role` | `string` | Role: "user" \| "admin" |
| `created_at` | `google.protobuf.Timestamp` | Creation time |
| `updated_at` | `google.protobuf.Timestamp` | Update time |

**Example:**
```json
{
  "id": "usr_a1b2c3d4",
  "wallet_address": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d",
  "chain": "solana",
  "display_name": "CryptoTrader",
  "risk_tolerance": "medium",
  "preferred_protocols": ["jupiter", "orca", "raydium"],
  "auto_suggestions_allowed": true,
  "role": "user",
  "created_at": "2024-01-15T10:00:00Z",
  "updated_at": "2024-01-20T14:30:00Z"
}
```

---

### GetUserRequest

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | User ID (oneof) |
| `wallet_address` | `string` | Wallet address (oneof) |

**Note:** Only one of `id` or `wallet_address` should be sent.

**Example:**
```json
// Query by wallet
{ "wallet_address": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d" }

// Query by ID
{ "id": "usr_a1b2c3d4" }
```

---

### UpdateUserRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet_address` | `string` | User to update (required) |
| `display_name` | `optional string` | New display name |
| `risk_tolerance` | `optional string` | New risk tolerance |
| `preferred_protocols` | `repeated string` | New protocol list |
| `auto_suggestions_allowed` | `optional bool` | Suggestion setting |

**Behavior:**
- Only non-empty fields are updated
- `wallet_address` cannot be changed
- `role` cannot be changed from this endpoint (admin-only)

**Example:**
```json
{
  "wallet_address": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d",
  "display_name": "NewName",
  "risk_tolerance": "high",
  "preferred_protocols": ["jupiter", "drift"]
}
```

---

### ListUsersRequest (Admin)

| Field | Type | Description |
|-------|------|-------------|
| `pagination` | `oprai.common.Pagination` | Pagination |
| `date_range` | `oprai.common.DateRange` | Date filter |
| `search` | `string` | Free-text search (wallet, display_name) |
| `sort` | `string` | Sort field |
| `order` | `oprai.common.SortOrder` | Sort direction |
| `status` | `string` | Status filter: "active" \| "suspended" \| "banned" |
| `role` | `string` | Role filter |

**Sort Fields:**
- `created_at`
- `wallet_address`
- `display_name`
- `role`
- `status`

**Example:**
```json
{
  "pagination": { "page": 1, "limit": 20 },
  "date_range": {
    "from": "2024-01-01T00:00:00Z",
    "to": "2024-01-31T23:59:59Z"
  },
  "search": "trader",
  "sort": "created_at",
  "order": "SORT_ORDER_DESC",
  "status": "active"
}
```

---

### ListUsersResponse

| Field | Type | Description |
|-------|------|-------------|
| `users` | `repeated UserProfile` | User list |
| `pagination` | `oprai.common.PaginatedResponse` | Pagination info |

---

## Database Schema

### auth_schema.users

```sql
CREATE TABLE auth_schema.users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address          VARCHAR(44) UNIQUE NOT NULL,
    chain                   VARCHAR(20) DEFAULT 'solana',
    display_name            VARCHAR(100),
    risk_tolerance          VARCHAR(10) CHECK (risk_tolerance IN ('low', 'medium', 'high')),
    preferred_protocols     TEXT[],
    auto_suggestions_allowed BOOLEAN DEFAULT true,
    role                    VARCHAR(20) DEFAULT 'user',
    status                  VARCHAR(20) DEFAULT 'active',
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_users_wallet ON auth_schema.users(wallet_address);
CREATE INDEX idx_users_created_at ON auth_schema.users(created_at);
```

---

## Call Sites

### Gateway -> Auth Service
```go
// services/gateway-go/internal/handlers/user.go
func (h *Handler) GetUser(c *gin.Context) {
    wallet := c.GetString("X-User-Wallet") // Comes from JWT

    client := authpb.NewUserServiceClient(h.authConn)
    resp, err := client.GetUser(ctx, &authpb.GetUserRequest{
        Identifier: &authpb.GetUserRequest_WalletAddress{
            WalletAddress: wallet,
        },
    })
    // ...
}
```

### Admin Panel
```typescript
// apps/oprai/src/app/features/admin/services/user.service.ts
async listUsers(page: number, filters: UserFilters): Promise<PaginatedUsers> {
    return this.http.post('/admin/users/list', {
        pagination: { page, limit: 20 },
        ...filters
    }).toPromise();
}
```

### Chat Service (User Context)
```python
# services/chat-service-py/app/services/context.py
async def get_user_preferences(wallet: str) -> dict:
    # gRPC call to auth-service
    response = await user_stub.GetUser(GetUserRequest(wallet_address=wallet))
    return {
        "risk_tolerance": response.risk_tolerance,
        "preferred_protocols": response.preferred_protocols,
    }
```

---

## Risk Tolerance Usage

The chat service makes recommendations based on the user's risk tolerance:

```python
# chat-service LLM prompt
RISK_PROMPTS = {
    "low": "Conservative strategies only. Prioritize capital preservation.",
    "medium": "Balanced approach. Mix of stable and growth strategies.",
    "high": "Aggressive strategies acceptable. Higher risk tolerance."
}

def build_llm_context(user: UserProfile) -> str:
    risk = user.risk_tolerance or "medium"
    return f"User risk profile: {risk}. {RISK_PROMPTS[risk]}"
```

---

## API Endpoints (Gateway Proxy)

| HTTP | gRPC | Description |
|------|------|-------------|
| `GET /user` | `GetUser` | Current user info |
| `PATCH /user` | `UpdateUser` | Update profile |
| `POST /admin/users/list` | `ListUsers` | Admin user list |

---

## Error Cases

| gRPC Code | Description |
|-----------|-------------|
| `NOT_FOUND` | User not found |
| `INVALID_ARGUMENT` | Invalid wallet format or parameter |
| `PERMISSION_DENIED` | Unauthorized access for admin operation |
| `INTERNAL` | Database error |

# proto/auth/auth.proto

Authentication service. SIWS (Sign-In with Solana) based wallet authentication.

## File Information
- **Package**: `oprai.auth`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/authpb`
- **Dependencies**: `google/protobuf/timestamp.proto`

---

## Service: AuthService

Main authentication service. Port: **50051 (gRPC)** / **3010 (HTTP)**

### RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `GetNonce` | GetNonceRequest | GetNonceResponse | Generate nonce for wallet |
| `VerifySignature` | VerifySignatureRequest | VerifySignatureResponse | Verify signature, get JWT |
| `CheckSession` | CheckSessionRequest | CheckSessionResponse | Check JWT validity |
| `Logout` | LogoutRequest | LogoutResponse | End session |

---

## Auth Flow (SIWS)

```
+-------------+                    +-------------+                    +-------------+
|   Frontend  |                    |   Gateway   |                    | Auth Service|
+------+------|                    +------+------|                    +------+------+
       |                                  |                                  |
       | 1. POST /auth/nonce              |                                  |
       |   { wallet_address }             |                                  |
       | ------------------------------>  |                                  |
       |                                  | gRPC: GetNonce                   |
       |                                  | ------------------------------>  |
       |                                  |                                  |
       |                                  |   { nonce, nonce_id }            |
       |                                  | <------------------------------  |
       |   { nonce, nonce_id }            |                                  |
       | <------------------------------  |                                  |
       |                                  |                                  |
       | 2. Sign nonce with wallet        |                                  |
       |   (tweetnacl ed25519)            |                                  |
       |                                  |                                  |
       | 3. POST /auth/verify             |                                  |
       |   { wallet, signature, nonce_id }|                                  |
       | ------------------------------>  |                                  |
       |                                  | gRPC: VerifySignature            |
       |                                  | ------------------------------>  |
       |                                  |                                  |
       |                                  |   { token, expires_at }          |
       |                                  | <------------------------------  |
       |   { token, expires_at }          |                                  |
       | <------------------------------  |                                  |
       |                                  |                                  |
       | 4. Store JWT in localStorage     |                                  |
       |   key: "oprai-auth-token"        |                                  |
       |                                  |                                  |
```

---

## Messages

### GetNonceRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet_address` | `string` | Solana wallet address (base58) |

**Example:**
```json
{
  "wallet_address": "Hx7b...9k2m"
}
```

---

### GetNonceResponse

| Field | Type | Description |
|-------|------|-------------|
| `nonce` | `string` | Random string to be signed |
| `nonce_id` | `string` | Server-side nonce ID (Redis key) |

**Example:**
```json
{
  "nonce": "oprai-nonce-a7b3c9d2e5f1",
  "nonce_id": "nonce:uuid-1234"
}
```

**Backend Behavior:**
- Nonce is stored in Redis
- TTL: 10 minutes
- Key format: `nonce:{nonce_id}`

---

### VerifySignatureRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet_address` | `string` | Wallet address of the signer |
| `signature` | `string` | Base58-encoded ed25519 signature |
| `nonce_id` | `string` | ID from GetNonce |

**Signature Verification:**
```typescript
// Frontend (tweetnacl)
import nacl from 'tweetnacl';

const message = new TextEncoder().encode(nonce);
const signature = nacl.sign.detached(message, keypair.secretKey);
const signatureBase58 = bs58.encode(signature);
```

```go
// Backend (Go)
import "github.com/gagliardetto/solana-go"

func verifySignature(wallet, nonce, signature string) bool {
    pubkey := solana.MustPublicKeyFromBase58(wallet)
    sig := solana.SignatureFromBase58(signature)
    message := []byte(nonce)
    return pubkey.Verify(message, sig)
}
```

---

### VerifySignatureResponse

| Field | Type | Description |
|-------|------|-------------|
| `token` | `string` | JWT (HS256, 3-day expiry) |
| `expires_at` | `google.protobuf.Timestamp` | Token expiration time |

**JWT Payload:**
```json
{
  "w": "Hx7b...9k2m",      // wallet address
  "iat": 1704067200,        // issued at
  "exp": 1704326400         // expiry (iat + 3 days)
}
```

---

### CheckSessionRequest

| Field | Type | Description |
|-------|------|-------------|
| `token` | `string` | Bearer JWT |

**Usage:**
- Gateway calls on every request
- Frontend checks on page load

---

### CheckSessionResponse

| Field | Type | Description |
|-------|------|-------------|
| `authenticated` | `bool` | Is the token valid? |
| `wallet` | `string` | Wallet address |
| `expires_at` | `google.protobuf.Timestamp` | Token expiration time |

---

### LogoutRequest / LogoutResponse

| Request | Type | Description |
|---------|------|-------------|
| `token` | `string` | JWT to invalidate |

| Response | Type | Description |
|----------|------|-------------|
| `success` | `bool` | Operation successful |

**Backend Behavior:**
- Token is added to blacklist (Redis)
- TTL: Remaining duration of the token

---

### SessionPayload

Used for inter-service JWT claims.

| Field | Type | Description |
|-------|------|-------------|
| `w` | `string` | Wallet address (short) |
| `iat` | `int64` | Issued at (unix timestamp) |
| `exp` | `int64` | Expiry (unix timestamp) |

---

## Call Sites

### Gateway (Go)
```go
// services/gateway-go/internal/middleware/auth.go
func (m *AuthMiddleware) ValidateToken(token string) (*authpb.CheckSessionResponse, error) {
    conn := m.authConn // gRPC connection to auth-service
    client := authpb.NewAuthServiceClient(conn)
    return client.CheckSession(ctx, &authpb.CheckSessionRequest{Token: token})
}
```

### Frontend (Angular)
```typescript
// apps/oprai/src/app/services/auth.service.ts
async login(walletAddress: string): Promise<string> {
    // 1. Get nonce
    const { nonce, nonce_id } = await this.http.post('/auth/nonce', { wallet_address: walletAddress }).toPromise();

    // 2. Sign with wallet
    const signature = await this.walletService.signMessage(nonce);

    // 3. Verify and get JWT
    const { token } = await this.http.post('/auth/verify', {
        wallet_address: walletAddress,
        signature,
        nonce_id
    }).toPromise();

    localStorage.setItem('oprai-auth-token', token);
    return token;
}
```

---

## Error Cases

| gRPC Code | Description |
|-----------|-------------|
| `NOT_FOUND` | Nonce not found or expired |
| `INVALID_ARGUMENT` | Invalid wallet address format |
| `UNAUTHENTICATED` | Signature verification failed |
| `INTERNAL` | Redis/DB error |

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPRAI_JWT_SECRET` | JWT signing secret | (required) |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379` |
| `NONCE_TTL_SECONDS` | Nonce validity duration | `600` (10 min) |
| `JWT_EXPIRY_HOURS` | JWT validity duration | `72` (3 days) |

# Quick Reference for LLMs

This file summarizes critical information for quickly understanding the codebase.

---

## 1. What is the Project?

**OPRAI** — DeFi AI assistant for Solana. Natural language → on-chain transactions.

**User:** "Swap 1 SOL to USDC" → **AI:** `[ACTION:swap]` → **Frontend:** TX build → **User signs** → **Submit**

---

## 2. Service Architecture

```
Frontend (Angular :3000)
    ↓ Bearer JWT
Gateway (Go :3001)
    ↓ X-Internal-Api-Key + X-User-Wallet
├── Auth (Go :3010)     → auth_schema
├── Chat (Python :3020) → chat_schema
├── Solana (Rust :3030) → solana_schema
└── Memory (Python :3040) → memory_schema
```

---

## 3. Auth Flow (5 seconds)

```
1. POST /auth/nonce → {nonce, nonceId}
2. Client signs "OPRAI login: {nonce}"
3. POST /auth/verify → {token, expiresAt}
4. localStorage.setItem('oprai-auth-token', token)
5. Every request: Authorization: Bearer <token>
```

**JWT Claims:** `{w: "wallet_address", iat: ..., exp: ...}`

---

## 4. Action Flow (10 seconds)

```
User message → Chat Service → LLM
LLM outputs: [ACTION:swap] {"inputMint": "SOL", ...}
Frontend parses: IntentParserService.parse()
Frontend calls: /actions/quote → /actions/build
User signs TX → Submit to RPC
```

---

## 5. Action/Query Format

**Preferred (JSON):**
```
[ACTION:swap] {"inputMint": "SOL", "outputMint": "USDC", "amount": "1000000000"}
[QUERY:balance] {"wallet": "self", "token": "all"}
[CLARIFY:staking] {"question": "Which protocol?", "options": [...]}
```

**Legacy (key=value):**
```
[ACTION:transfer] to=HwM... amount=1 token=SOL
```

---

## 6. Key Files to Read

| Purpose | File |
|---------|------|
| Angular routing | `apps/oprai/src/app/app.routes.ts` |
| Wallet connect | `apps/oprai/src/app/core/services/wallet.service.ts` |
| Intent parsing | `apps/oprai/src/app/features/chat/services/intent-parser.service.ts` |
| Chat streaming | `services/chat-service-py/app/services/message.py` |
| LLM wrapper | `services/chat-service-py/app/services/llm.py` |
| Solana TX builder | `services/solana-service-rs/src/services/swap.rs` |
| Plugin base | `opraios/core/plugin_system.py` |

---

## 7. Database Schemas

| Schema | Tables | Service |
|--------|--------|---------|
| `auth_schema` | users, login_logs | auth-service-go |
| `chat_schema` | chat_sessions, chat_messages | chat-service-py |
| `solana_schema` | transactions | solana-service-rs |
| `memory_schema` | user_consents | memory-service-py |
| `admin_schema` | admin_users | admin-service-go |

---

## 8. Environment Variables (Required)

```bash
OPRAI_JWT_SECRET=xxx           # JWT signing
OPRAI_INTERNAL_API_KEY=xxx     # Gateway-to-service auth
OPRAI_OPENAI_API_KEY=xxx       # LLM + embeddings
DATABASE_URL=postgres://...    # PostgreSQL
```

---

## 9. Common Patterns

### Angular Service (Signals)
```typescript
@Injectable({ providedIn: 'root' })
export class MyService {
  private readonly _data = signal<Data | null>(null);
  readonly data = this._data.asReadonly();

  async load(): Promise<void> {
    const result = await this.api.get<Data>('/endpoint');
    this._data.set(result);
  }
}
```

### Rust Service (Actix)
```rust
#[post("/actions/build")]
async fn build_action(
    body: web::Json<BuildRequest>,
    solana: web::Data<SolanaRpc>,
) -> Result<HttpResponse, AppError> {
    let tx = build_transaction(&body, &solana).await?;
    Ok(HttpResponse::Ok().json(BuildResponse { transaction: tx }))
}
```

### Python Service (FastAPI)
```python
@router.post("/memory")
async def store_memory(
    request: StoreRequest,
    db: AsyncSession = Depends(get_db),
    wallet: str = Depends(require_wallet),
):
    point = await vector_store.store(request.payload)
    return {"point": point}
```

### Go Service (Chi)
```go
func (h *Handler) HandleNonce(w http.ResponseWriter, r *http.Request) {
    result, err := h.nonceService.Generate(r.Context())
    if err != nil {
        writeError(w, 500, "Failed to generate nonce")
        return
    }
    writeJSON(w, 200, nonceResponse{
        Nonce:   result.Nonce,
        NonceID: result.NonceID,
    })
}
```

---

## 10. API Endpoints Quick List

### Auth
- `POST /auth/nonce` — Get nonce
- `POST /auth/verify` — Verify signature → JWT
- `GET /auth/session` — Check auth status

### Chat
- `POST /chat/sessions/{id}/messages/stream` — SSE streaming
- `GET /chat/sessions` — List sessions
- `POST /chat/sessions` — Create session

### Actions
- `POST /actions/quote` — Get swap quote
- `POST /actions/build` — Build transaction
- `POST /actions/submit` — Submit transaction

### Portfolio
- `GET /balance/` — Token balances
- `GET /transactions/` — Transaction history

### Memory
- `GET /memory` — List memories
- `POST /memory` — Store memory
- `GET /memory/search` — Semantic search
- `PUT /consent` — Update consent flags

---

## 11. Error Handling

### Frontend
```typescript
this.api.post('/endpoint', body).subscribe({
  next: (data) => { /* success */ },
  error: (err) => {
    if (err.status === 401) { /* re-auth */ }
    else { /* show error */ }
  },
});
```

### Backend (Go)
```go
type AppError struct {
    Code    int    `json:"code"`
    Message string `json:"message"`
}

func (e AppError) Error() string { return e.Message }
```

### Backend (Python)
```python
class AppError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
```

---

## 12. Testing Commands

```bash
# All tests
make test

# Angular
npx ng test

# Go
go test ./...

# Python
.venv/bin/pytest tests/

# Rust
cargo test
```

---

## 13. Debug Tips

**Frontend not connecting:**
1. Check `environment.ts` → `apiBase`
2. Check localStorage for `oprai-auth-token`
3. Check browser console for CORS errors

**Auth failing:**
1. Check `OPRAI_JWT_SECRET` matches across services
2. Check `OPRAI_INTERNAL_API_KEY` in gateway + services
3. Check Redis is running for nonces

**Transactions failing:**
1. Check RPC endpoint (Helius/Quicknode)
2. Check user has enough SOL for fees
3. Check slippage settings

---

## 14. File Locations Cheat Sheet

```
Config:     .env, apps/oprai/src/environments/
Routes:     apps/oprai/src/app/app.routes.ts
Services:   apps/oprai/src/app/core/services/
Components: apps/oprai/src/app/features/*/components/

Backend:    services/*/
Protobuf:   proto/
Migrations: services/*/migrations/
Tests:      */tests/, */__tests__/
```

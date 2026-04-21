# proto/chat/session.proto

Chat session management. Creating and managing conversation sessions per user.

## File Information
- **Package**: `oprai.chat`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/chatpb`
- **Dependencies**: `google/protobuf/timestamp.proto`, `proto/common/types.proto`

---

## Service: ChatSessionService

Chat session management. Port: **50052 (gRPC)** / **3020 (HTTP)**

### RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `CreateSession` | CreateSessionRequest | SessionMeta | Create new session |
| `GetSession` | GetSessionRequest | SessionMeta | Get session info |
| `ListSessions` | ListSessionsRequest | ListSessionsResponse | Session list |
| `UpdateSessionTitle` | UpdateSessionTitleRequest | SessionMeta | Update session title |
| `DeleteSession` | DeleteSessionRequest | DeleteSessionResponse | Delete session |

---

## Messages

### SessionMeta

Chat session metadata.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID (auto) |
| `wallet` | `string` | Owner wallet address |
| `title` | `string` | Session title |
| `created_at` | `google.protobuf.Timestamp` | Creation time |
| `updated_at` | `google.protobuf.Timestamp` | Update time |

**Example:**
```json
{
  "id": "sess_abc123",
  "wallet": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d",
  "title": "What is the SOL price?",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:35:00Z"
}
```

---

### CreateSessionRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet` | `string` | Owner wallet address |
| `title` | `string` | Initial title (optional) |

---

### GetSessionRequest

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `string` | Session UUID |
| `wallet` | `string` | Authorization check |

---

### ListSessionsRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet` | `string` | Owner wallet |
| `pagination` | `oprai.common.Pagination` | Pagination |
| `date_range` | `oprai.common.DateRange` | Date filter |
| `search` | `string` | Title search (optional) |

---

### ListSessionsResponse

| Field | Type | Description |
|-------|------|-------------|
| `sessions` | `repeated SessionMeta` | Session list |
| `pagination` | `oprai.common.PaginatedResponse` | Pagination info |

---

### UpdateSessionTitleRequest

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `string` | Session UUID |
| `wallet` | `string` | Owner wallet |
| `title` | `string` | New title |

---

### DeleteSessionRequest / DeleteSessionResponse

| Request Field | Type | Description |
|---------------|------|-------------|
| `session_id` | `string` | Session UUID |
| `wallet` | `string` | Owner wallet |

| Response Field | Type | Description |
|----------------|------|-------------|
| `success` | `bool` | Deletion successful |

**Note:** Soft delete - messages remain in DB but are marked as deleted.

---

## API Endpoints (via Gateway)

| HTTP | Endpoint | gRPC Call |
|------|----------|-----------|
| `POST` | `/chat/sessions` | `CreateSession` |
| `GET` | `/chat/sessions/:id` | `GetSession` |
| `GET` | `/chat/sessions` | `ListSessions` |
| `PATCH` | `/chat/sessions/:id` | `UpdateSessionTitle` |
| `DELETE` | `/chat/sessions/:id` | `DeleteSession` |

---

## Database Schema

**Table:** `chat_schema.chat_sessions`

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `wallet` | VARCHAR(66) | Owner wallet |
| `title` | VARCHAR(255) | Session title |
| `deleted_at` | TIMESTAMP | Soft delete |
| `created_at` | TIMESTAMP | Creation |
| `updated_at` | TIMESTAMP | Update |

---

## Call Sites

### Gateway (Go)
```go
// services/gateway-go/internal/handlers/chat.go
func (h *Handler) CreateSession(w http.ResponseWriter, r *http.Request) {
    wallet := middleware.GetWallet(r)
    resp, err := h.chatClient.CreateSession(ctx, &chatpb.CreateSessionRequest{
        Wallet: wallet,
        Title: r.URL.Query().Get("title"),
    })
    // ...
}
 return f.  NewConversations will automatically create a session
 }             |
            |
            |  GET /chat/sessions -> ListSessions                      |
            |
            |  POST /chat/sessions/:id/messages -> SendMessage (streaming) |
            |
            |  GET /chat/sessions/:id/messages -> GetMessages               |
            +---------------------------------------------------------------+
```

---

### Frontend (Angular)
```typescript
// apps/oprai/src/app/services/chat.service.ts
@Injectable({ providedIn: 'root' })
export class ChatService {
    private http = inject(HttpClient);

    async createSession(title?: string): Promise<Session> {
        return this.http.post<Session>('/chat/sessions', { title }).toPromise();
    }

    async sendMessage(sessionId: string, content: string): Observable<ChatMessage> {
        // Server-Sent Events (SSE) streaming
        return this.http.post(`/chat/sessions/${sessionId}/messages`, {
            session_id: sessionId,
            content
        });
    }
}
```

---

## Session Lifecycle

```
+---------------------------------------------------------------------+
|                        Session Lifecycle                            |
+---------------------------------------------------------------------+

   POST /chat/sessions
          |
          v
   +----------------+
   | SessionMeta    |
   | id: "sess_..."   |
   +--------+-------+
          |
          |  User sends first message
          v
   +----------------+-----------------------------------------+
   | ChatMessage     |     SendMessage (streaming)           |
   | id: "msg_..."    |--------------------------------------->|
   | role: USER      |                                   |
   | content: "..."   |                                   |
   +----------------+                                   |
          |                                             |
          |  AI generates response (streamed)                 |
          v                                             |
   +----------------+                                   +----------------+
   | StreamToken    |       ...       |  ChatMessage     |
   | delta: "Sol"   |--------------------->|  (completed)    |
   | index: 0        |               |  role: ASSISTANT|
   +----------------+               |  content: "..." |
                                                       +----------------+
```

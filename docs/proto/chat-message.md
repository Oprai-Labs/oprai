# proto/chat/message.proto

Chat messaging service. Streaming AI responses and message history.

## File Information
- **Package**: `oprai.chat`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/chatpb`
- **Dependencies**: `google/protobuf/timestamp.proto`, `proto/common/types.proto`

---

## Service: ChatMessageService

Messaging service. Port: **50052 (gRPC)** / **3020 (HTTP)**

### RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `SendMessage` | SendMessageRequest | `stream` SendMessageResponse | Send message, receive streaming response |
| `GetMessages` | GetMessagesRequest | GetMessagesResponse | Message history |

---

## Enums

### MessageRole

| Value | Number | Description |
|-------|--------|-------------|
| `MESSAGE_ROLE_UNSPECIFIED` | 0 | Unspecified |
| `MESSAGE_ROLE_USER` | 1 | User message |
| `MESSAGE_ROLE_ASSISTANT` | 2 | AI assistant response |
| `MESSAGE_ROLE_SYSTEM` | 3 | System message |

### AlertLevel

| Value | Number | Description |
|-------|--------|-------------|
| `ALERT_LEVEL_UNSPECIFIED` | 0 | Unspecified |
| `ALERT_LEVEL_INFO` | 1 | Info |
| `ALERT_LEVEL_WARNING` | 2 | Warning |
| `ALERT_LEVEL_CRITICAL` | 3 | Critical |

### MemoryScope

| Value | Number | Description |
|-------|--------|-------------|
| `MEMORY_SCOPE_UNSPECIFIED` | 0 | Unspecified |
| `MEMORY_SCOPE_LOCAL` | 1 | This session only |
| `MEMORY_SCOPE_GLOBAL` | 2 | All sessions |

---

## Messages

### ChatMessage

Main message entity.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | UUID |
| `speaker` | `MessageRole` | Speaker (USER/ASSISTANT/SYSTEM) |
| `content` | `string` | Message content |
| `streaming` | `bool` | Is it currently being streamed? |
| `timestamp` | `google.protobuf.Timestamp` | Message time |
| `annotations` | `repeated Annotation` | Additional annotations |

**Example:**
```json
{
  "id": "msg_xyz789",
  "speaker": "MESSAGE_ROLE_ASSISTANT",
  "content": "SOL price is currently $150.42.",
  "streaming": false,
  "timestamp": "2024-01-15T10:31:00Z",
  "annotations": [
    {
      "alert": {
        "level": "ALERT_LEVEL_INFO",
        "message": "Price data is real-time"
      }
    }
  ]
}
```

---

### Annotation

Discriminated union - additional metadata for a message.

| oneof | Type | Description |
|-------|------|-------------|
| `alert` | `AlertAnnotation` | Alert/info message |
| `memory` | `MemoryAnnotation` | Memory reference |

---

### AlertAnnotation

| Field | Type | Description |
|-------|------|-------------|
| `level` | `AlertLevel` | Alert level |
| `message` | `string` | Alert message |

**Use cases:**
- Price change alert
- Risk warning (high slippage)
- Transaction approval required

---

### MemoryAnnotation

| Field | Type | Description |
|-------|------|-------------|
| `scope` | `MemoryScope` | Memory scope |
| `reference_id` | `string` | Memory ID |
| `label` | `string` | Memory label |

**Usage:**
- When AI references previously saved information
- When user preferences are recalled

---

### SendMessageRequest

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `string` | Session UUID |
| `wallet` | `string` | User wallet |
| `content` | `string` | User message (natural language) |

---

### SendMessageResponse (Streaming)

| oneof | Type | Description |
|-------|------|-------------|
| `token` | `StreamToken` | Streaming token chunk |
| `completed_message` | `ChatMessage` | Completed message |

**Flow:**
```
Frame 1: { token: { delta: "Sol", index: 0 } }
Frame 2: { token: { delta: " price", index: 1 } }
Frame 3: { token: { delta: " $150.", index: 2 } }
Frame 4: { token: { delta: "42", index: 3 } }
Frame 5: { completed_message: { id: "...", content: "SOL price $150.42", ... } }
```

---

### StreamToken

| Field | Type | Description |
|-------|------|-------------|
| `delta` | `string` | Incremental text fragment |
| `index` | `int32` | Token sequence number |

---

### GetMessagesRequest

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `string` | Session UUID |
| `wallet` | `string` | Authorization |
| `pagination` | `oprai.common.Pagination` | Pagination |

---

### GetMessagesResponse

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `repeated ChatMessage` | Message list |
| `pagination` | `oprai.common.PaginatedResponse` | Pagination info |

---

## Streaming Flow

```
+----------+         +----------+         +--------------+         +----------+
| Frontend |         | Gateway  |         | Chat Service |         |   LLM    |
| (Angular)|         |  (Go)    |         |  (Python)    |         | (OpenAI) |
+----+-----+         +----+-----+         +------+-------+         +----+-----+
     |                    |                      |                      |
     | POST /chat/message |                      |                      |
     | { content: "..." } |                      |                      |
     |------------------->|                      |                      |
     |                    | gRPC: SendMessage    |                      |
     |                    |--------------------->|                      |
     |                    |                      |  OpenAI API (stream) |
     |                    |                      |--------------------->|
     |                    |                      |                      |
     |                    |                      |   token: "Sol"       |
     |                    |                      |<---------------------|
     |                    |  stream: StreamToken |                      |
     |                    |<---------------------|                      |
     |  SSE: data: "Sol"  |                      |                      |
     |<-------------------|                      |                      |
     |                    |                      |   token: " price"   |
     |                    |                      |<---------------------|
     |                    |  stream: StreamToken |                      |
     |                    |<---------------------|                      |
     |  SSE: data: " price"                      |                      |
     |<-------------------|                      |                      |
     |       ...          |        ...           |         ...          |
     |                    |                      |                      |
     |                    |                      |   [DONE]             |
     |                    |  completed_message   |                      |
     |                    |<---------------------|                      |
     |  SSE: data: [DONE] |                      |                      |
     |<-------------------|                      |                      |
```

---

## Backend Implementation (Python)

```python
# services/chat-service-py/app/grpc/message.py
class ChatMessageServicer(chat_pb2_grpc.ChatMessageServiceServicer):
    async def SendMessage(self, request, context):
        # 1. Save user message to DB
        user_msg = await self.save_message(
            session_id=request.session_id,
            wallet=request.wallet,
            role="user",
            content=request.content
        )

        # 2. Get conversation history for context
        history = await self.get_history(request.session_id)

        # 3. Build LLM prompt with context
        messages = self.build_llm_messages(history, request.content)

        # 4. Stream LLM response
        full_content = ""
        async for token in self.llm_client.stream_chat(messages):
            full_content += token
            yield chat_pb2.SendMessageResponse(
                token=chat_pb2.StreamToken(
                    delta=token,
                    index=len(full_content.split()) - 1
                )
            )

        # 5. Save assistant message
        assistant_msg = await self.save_message(
            session_id=request.session_id,
            wallet=request.wallet,
            role="assistant",
            content=full_content
        )

        # 6. Send completed message
        yield chat_pb2.SendMessageResponse(
            completed_message=assistant_msg
        )
```

---

## Frontend Implementation (Angular)

```typescript
// apps/oprai/src/app/services/chat.service.ts
@Injectable({ providedIn: 'root' })
export class ChatService {
    private http = inject(HttpClient);

    sendMessage(sessionId: string, content: string): Observable<StreamEvent> {
        // Server-Sent Events (SSE) via fetch API
        return new Observable(observer => {
            const eventSource = new EventSource(
                `/chat/sessions/${sessionId}/messages/stream?content=${encodeURIComponent(content)}`
            );

            eventSource.onmessage = (event) => {
                const data = JSON.parse(event.data);

                if (data.completed_message) {
                    observer.next({ type: 'complete', message: data.completed_message });
                    observer.complete();
                    eventSource.close();
                } else if (data.token) {
                    observer.next({ type: 'token', delta: data.token.delta });
                }
            };

            eventSource.onerror = (error) => {
                observer.error(error);
                eventSource.close();
            };

            return () => eventSource.close();
        });
    }
}

// Usage in component
async sendMessage() {
    this.isStreaming = true;
    this.currentResponse = '';

    this.chatService.sendMessage(this.sessionId, this.userInput).subscribe({
        next: (event) => {
            if (event.type === 'token') {
                this.currentResponse += event.delta;
            } else if (event.type === 'complete') {
                this.messages.push(event.message);
                this.isStreaming = false;
            }
        },
        error: (err) => {
            console.error('Stream error:', err);
            this.isStreaming = false;
        }
    });
}
```

---

## Database Schema

**Table:** `chat_schema.chat_messages`

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `session_id` | UUID | FK -> chat_sessions |
| `wallet` | VARCHAR(66) | Wallet |
| `role` | VARCHAR(20) | user/assistant/system |
| `content` | TEXT | Message content |
| `annotations` | JSONB | Annotations |
| `created_at` | TIMESTAMP | Creation |

---

## Annotation Examples

### Alert Annotation
```json
{
  "alert": {
    "level": "ALERT_LEVEL_WARNING",
    "message": "This transaction may have high slippage (5%+)"
  }
}
```

### Memory Annotation
```json
{
  "memory": {
    "scope": "MEMORY_SCOPE_GLOBAL",
    "reference_id": "mem_abc123",
    "label": "User risk tolerance: medium"
  }
}
```

---

## Action Parsing (AI -> Frontend)

AI responses can contain action blocks:

```
[ACTION:swap]
input_mint=So11111111111111111111111111111111111111112
output_mint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
amount=1
slippage_bps=50
```

The frontend parses this and displays it in the UI:
- "Swap 1 SOL -> USDC" button
- If user confirms -> `/actions/build` -> sign -> `/actions/submit`

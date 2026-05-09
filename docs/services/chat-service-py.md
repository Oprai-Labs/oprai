# Chat Service (Python)

AI-powered chat service with streaming LLM responses, session management, character system, and DeFi analytics.

## Quick Start

```bash
cd services/chat-service-py
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload --port 3020
# → HTTP: http://localhost:3020
# → gRPC: localhost:50052
```

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │         CHAT SERVICE                 │
                                    │                                     │
                                    │  ┌─────────────────────────────┐    │
                                    │  │     FastAPI Server (:3020)  │    │
                                    │  │   Routes + Middleware       │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    │  ┌──────────────┴──────────────┐    │
                                    │  │     gRPC Server (:50052)    │    │
                                    │  │  ChatSession + ChatMessage  │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    └─────────────────┼───────────────────┘
                                                      │
         ┌────────────────────────────────────────────┼────────────────────────────┐
         │                                            │                            │
         ▼                                            ▼                            ▼
    ┌─────────────┐                           ┌─────────────┐              ┌─────────────┐
    │ PostgreSQL  │                           │   Redis     │              │   Memory    │
    │ chat_schema │                           │   Cache     │              │  Service    │
    └─────────────┘                           └─────────────┘              └─────────────┘
```

---

## File Structure

```
services/chat-service-py/
├── app/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Pydantic settings
│   ├── grpc_server.py             # gRPC servicers
│   │
│   ├── db/
│   │   ├── connection.py          # SQLAlchemy async engine
│   │   └── character_repository.py # Character DB operations
│   │
│   ├── models/
│   │   ├── session.py             # ChatSession model
│   │   ├── message.py             # ChatMessage model
│   │   ├── summary.py             # ChatSummary model
│   │   └── character.py           # Character models
│   │
│   ├── middleware/
│   │   └── auth.py                # X-User-Wallet + X-Internal-Api-Key
│   │
│   ├── routes/
│   │   └── characters.py          # Character CRUD endpoints
│   │
│   ├── services/
│   │   ├── session.py             # Session CRUD
│   │   ├── message.py             # Message CRUD + streaming
│   │   ├── llm.py                 # LLM orchestration (Chat + Responses API)
│   │   ├── summary.py             # Block-based summarization
│   │   ├── streaming.py           # gRPC streaming services
│   │   ├── memory_client.py       # Memory service HTTP client
│   │   ├── cache.py               # Redis cache service
│   │   ├── yield_aggregator.py    # DeFi yield comparison
│   │   ├── portfolio_optimizer.py # Portfolio analysis
│   │   ├── risk_assessment.py     # Position risk analysis
│   │   ├── token_security.py      # Token rug pull detection
│   │   ├── trending_tokens.py     # DexScreener integration
│   │   └── title_generator.py     # Auto-generate session titles
│   │
│   ├── plugins/
│   │   ├── base.py                # Plugin base classes
│   │   ├── manager.py             # Plugin lifecycle
│   │   ├── defi_plugins.py        # DeFi protocol plugins
│   │   └── jupiter_plugin.py      # Jupiter swap plugin
│   │
│   ├── prompts/
│   │   └── loader.py              # Prompt file loader
│   │
│   ├── character/
│   │   ├── loader.py              # Character file loader
│   │   └── prompt_builder.py      # Character prompt builder
│   │
│   └── agents/
│       └── autonomous.py          # Autonomous agent loop
│
├── proto_gen/                     # Generated gRPC stubs
│   └── proto/chat/
│       ├── session_pb2.py
│       ├── session_pb2_grpc.py
│       ├── message_pb2.py
│       └── message_pb2_grpc.py
│
├── alembic/                       # Database migrations
│   └── versions/
│
└── pyproject.toml
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3020` | HTTP port |
| `GRPC_PORT` | `50052` | gRPC port |
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection |
| `DB_SCHEMA` | `chat_schema` | PostgreSQL schema |
| `OPRAI_OPENAI_API_KEY` | *(required)* | OpenAI API key |
| `OPRAI_OPENAI_MODEL` | `gpt-4o-mini` | Primary LLM model |
| `OPRAI_OPENAI_FALLBACK_MODEL` | `gpt-4o-mini` | Fallback model |
| `OPRAI_GPT_MAX_TOKENS` | `4096` | Max tokens per response |
| `OPRAI_GPT_REASONING_EFFORT` | `medium` | Reasoning effort (o-series) |
| `MEMORY_SERVICE_URL` | `http://localhost:3040` | Memory service URL |
| `GATEWAY_URL` | `http://localhost:3001` | Gateway URL |
| `OPRAI_INTERNAL_API_KEY` | *(required)* | Inter-service auth |
| `OPRAI_JWT_SECRET` | *(required)* | JWT validation |
| `SOLANA_RPC_URL` | `https://api.mainnet-beta.solana.com` | Solana RPC |
| `CORS_ALLOWED_ORIGINS` | `""` | Comma-separated CORS origins |

---

## LLM Service

### Supported Models

**Chat Completions API (LangChain):**
- `gpt-4o-mini`, `gpt-4o`, `gpt-4-turbo`, etc.

**Responses API (Reasoning Models):**
- `gpt-5-mini`, `gpt-5`
- `o1`, `o1-mini`, `o1-preview`
- `o3`, `o3-mini`, `o4-mini`

### LLMService Class

```python
# services/llm.py
class LLMService:
    def __init__(self):
        self._model = settings.OPRAI_OPENAI_MODEL
        self._use_responses_api = self._model in _RESPONSES_API_MODELS
        self._client = AsyncOpenAI(...)

    async def astream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        """Stream token deltas."""
        if self._use_responses_api:
            async for chunk in self._astream_responses(messages):
                yield chunk
        else:
            async for chunk in self._astream_chat(messages):
                yield chunk

    async def acomplete(self, messages: list[dict]) -> str:
        """Non-streaming completion."""
        ...
```

### Responses API Flow

```
Client Request → LLMService._astream_responses()
                    │
                    ├─► response.reasoning_summary_text.delta
                    │   └─► yield "<thinking>" + reasoning text
                    │
                    └─► response.output_text.delta
                        └─► yield "</thinking>" + output text
```

### Fallback Mechanism

```python
# Primary model with fallback
primary = ChatOpenAI(model="gpt-4o", ...)
fallback = ChatOpenAI(model="gpt-4o-mini", ...)
self._llm = primary.with_fallbacks([fallback])
```

---

## Streaming Flow (SSE)

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │     │   Gateway    │     │ Chat Service │     │   OpenAI     │
│   (Angular)  │     │    (Go)      │     │   (Python)   │     │              │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │                    │
       │ POST /chat/sessions/{id}/messages/stream                    │
       │ { content, sessionId }                                       │
       │──────────────────►│                    │                    │
       │                    │ Proxy              │                    │
       │                    │───────────────────►│                    │
       │                    │                    │ 1. Save user msg   │
       │                    │                    │ 2. Build context   │
       │                    │                    │ 3. Stream LLM      │
       │                    │                    │───────────────────►│
       │                    │                    │                    │
       │                    │                    │◄───────────────────│
       │                    │                    │  token deltas      │
       │                    │                    │                    │
       │  SSE: data: {"delta": "..."}          │                    │
       │◄──────────────────│◄───────────────────│                    │
       │  SSE: data: {"delta": "..."}          │                    │
       │◄──────────────────│◄───────────────────│                    │
       │  ...               │                    │                    │
       │  SSE: data: {"messageId": "..."}      │                    │
       │◄──────────────────│◄───────────────────│                    │
       │  SSE: data: [DONE] │                    │                    │
       │◄──────────────────│◄───────────────────│                    │
```

### SSE Event Types

| Event | Payload | Description |
|-------|---------|-------------|
| `delta` | `{"delta": "token"}` | Streaming text chunk |
| `thinking` | `{"thinking": "reasoning"}` | Reasoning model thought |
| `messageId` | `{"messageId": "uuid"}` | Saved message ID |
| `title` | `{"title": "New Title"}` | Auto-generated title |
| `error` | `{"error": "msg", "errorType": "type"}` | Error message |
| `[DONE]` | — | Stream complete |

---

## Context Building

### `build_llm_context()` - Building LLM Messages

```python
# services/summary.py
async def build_llm_context(
    db: AsyncSession,
    session_id: str,
    wallet: str,
    current_attachments: list[dict] | None = None,
) -> list[dict[str, str]]:
    """Build messages array for LLM call."""
```

**Order:**
1. **System Prompt** — `prompts/system_prompt.txt`
2. **Wallet Context** — Connected wallet + SOL balance
3. **Error Recovery Protocol** — Transaction failure handling
4. **Attachment Context** — Uploaded images/files
5. **Summaries** — Block summaries (messages 1-10, 11-20, etc.)
6. **Memory Context** — Long-term memory search results
7. **Raw Messages** — Remaining unsummarised messages
8. **Query Snapshots** — Previous [QUERY:] results

### Block-Based Summarization

```
Messages 1-10:   Sent raw to LLM
Message 11:      summary(1-10) + msg 11
Messages 12-20:  summary(1-10) + raw msgs 11-N
Message 21:      summary(1-10) + summary(11-20) + msg 21
...

BLOCK_SIZE = 10
```

**Trigger:**
```python
await maybe_create_summary(db, session_id, wallet, message_count)
# Called after each message_count increment
```

---

## Database Models

### ChatSession

```python
# models/session.py
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = {"schema": "chat_schema"}

    id: UUID                    # Primary key
    user_id: str                # User identifier
    wallet_address: str         # Owner wallet
    title: str                  # Session title
    message_count: int          # Total messages
    created_at: datetime
    updated_at: datetime
    is_deleted: bool            # Soft delete
    deleted_at: datetime | None
    pinned: bool                # Pinned status
    pinned_at: datetime | None
```

### ChatMessage

```python
# models/message.py
class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = {"schema": "chat_schema"}

    id: UUID                    # Primary key
    session_id: UUID            # FK → chat_sessions
    wallet_address: str         # Owner wallet
    role: str                   # user | assistant | system
    content: str                # Message text
    metadata_: dict | None      # JSONB (attachments, annotations, etc.)
    created_at: datetime
```

### ChatSummary

```python
# models/summary.py
class ChatSummary(Base):
    __tablename__ = "chat_summaries"
    __table_args__ = {"schema": "chat_schema"}

    id: UUID                    # Primary key
    session_id: UUID            # FK → chat_sessions
    block_index: int            # 0, 1, 2, ...
    summary_text: str           # LLM-generated summary
    message_start: int          # First message in block (1-indexed)
    message_end: int            # Last message in block
    created_at: datetime
```

---

## HTTP Endpoints

### Health & Metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/cache/health` | Redis health |
| `GET` | `/cache/stats` | Cache statistics |
| `POST` | `/cache/invalidate` | Clear cache |

### Sessions

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/chat/sessions` | Required | List sessions |
| `POST` | `/chat/sessions` | Required | Create session |
| `GET` | `/chat/sessions/{id}` | Required | Get session |
| `PATCH` | `/chat/sessions/{id}` | Required | Update title |
| `DELETE` | `/chat/sessions/{id}` | Required | Soft delete |
| `POST` | `/chat/sessions/{id}/pin` | Required | Pin/unpin |

### Messages

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/chat/sessions/{id}/messages` | List messages |
| `POST` | `/chat/sessions/{id}/messages` | Send message (non-streaming) |
| `POST` | `/chat/sessions/{id}/messages/stream` | SSE streaming |
| `PATCH` | `/chat/sessions/{id}/messages/{msgId}` | Update metadata |

### Characters

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/characters` | List characters |
| `POST` | `/characters` | Create character |
| `GET` | `/characters/templates` | Public templates |
| `GET` | `/characters/{id}` | Get character |
| `PATCH` | `/characters/{id}` | Update character |
| `DELETE` | `/characters/{id}` | Delete character |
| `POST` | `/characters/{id}/duplicate` | Duplicate |
| `POST` | `/characters/{id}/export` | Export JSON |
| `POST` | `/characters/import` | Import JSON |
| `GET` | `/characters/{id}/runtime` | Runtime status |
| `POST` | `/characters/{id}/start` | Start runtime |
| `POST` | `/characters/{id}/stop` | Stop runtime |
| `POST` | `/characters/{id}/prompt` | Generate prompt |

### DeFi Analytics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/yields` | Get yields by category |
| `GET` | `/yields/all` | All yield categories |
| `POST` | `/portfolio/analyze` | Portfolio analysis |
| `POST` | `/portfolio/optimize` | Optimization suggestions |
| `GET` | `/protocols/compare` | Protocol comparison |
| `POST` | `/risk/analyze` | Portfolio risk |
| `POST` | `/risk/position` | Single position risk |
| `POST` | `/token/security` | Token rug pull check |
| `GET` | `/tokens/trending` | Trending tokens |

---

## gRPC Service

### ChatSessionService

```protobuf
service ChatSessionService {
  rpc CreateSession(CreateSessionRequest) returns (SessionMeta);
  rpc GetSession(GetSessionRequest) returns (SessionMeta);
  rpc ListSessions(ListSessionsRequest) returns (ListSessionsResponse);
  rpc UpdateSessionTitle(UpdateSessionTitleRequest) returns (SessionMeta);
  rpc DeleteSession(DeleteSessionRequest) returns (DeleteSessionResponse);
}
```

### ChatMessageService

```protobuf
service ChatMessageService {
  rpc GetMessages(GetMessagesRequest) returns (GetMessagesResponse);
  rpc SendMessage(SendMessageRequest) returns (stream SendMessageResponse);
}
```

**SendMessage (Streaming):**
```python
# grpc_server.py
async def SendMessage(self, request, context):
    async for sse_line in message_svc.stream_chat_response(...):
        if "delta" in chunk:
            yield message_pb2.SendMessageResponse(
                token=message_pb2.StreamToken(delta=chunk["delta"])
            )
```

---

## Memory Integration

### Memory Client

```python
# services/memory_client.py
async def search_memories(
    wallet: str,
    query: str,
    top_k: int = 5,
    threshold: float = 0.7,
    types: str | None = None,
) -> list[dict]:
    """Search long-term memories."""

async def store_memory(
    wallet: str,
    memory_type: str,  # "decision" | "meta"
    summary: str,
    extra: dict | None = None,
) -> str | None:
    """Store memory point."""
```

### Context Injection

```python
# services/summary.py
memories = await search_memories(wallet, last_user_msg, top_k=3, threshold=0.72)
if memories:
    memory_context = "[Long-term Memory — relevant past context]:\n" + ...
    messages.append({"role": "system", "content": memory_context})
```

### Action Storage

```python
# services/message.py
if "[ACTION:" in full_response or "[QUERY:" in full_response:
    await store_memory(
        wallet=wallet,
        memory_type="decision" if "[ACTION:" in full_response else "meta",
        summary=f"User: {user_content[:200]}\nAssistant action: {full_response[:300]}",
        extra={"session_id": session_id},
    )
```

---

## Plugin System

### Base Classes

```python
# plugins/base.py
class PluginAction(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def execute(self, params: dict, context: PluginContext) -> PluginResult: ...

class PluginProvider(ABC):
    @abstractmethod
    async def fetch(self, params: dict, context: PluginContext) -> PluginResult: ...

class PluginEvaluator(ABC):
    @abstractmethod
    async def evaluate(self, context: PluginContext, input: str) -> PluginResult: ...

class BasePlugin(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    def actions(self) -> list[PluginAction]: return []
    @property
    def providers(self) -> list[PluginProvider]: return []
    @property
    def evaluators(self) -> list[PluginEvaluator]: return []
```

### Plugin Priority

```python
class PluginPriority(int, Enum):
    CRITICAL = 100   # Always run first
    HIGH = 75
    NORMAL = 50
    LOW = 25
    BACKGROUND = 0   # Run last
```

---

## Character System

### Character Model

```python
# models/character.py
class Character(BaseModel):
    id: str
    name: str
    model_provider: str           # openai, anthropic, etc.
    clients: list[str]            # discord, twitter, telegram
    owner_wallet: str

    bio: str | list[str]          # Biography (randomized if list)
    lore: list[str] | None        # Backstory elements
    knowledge: list[str] | None   # RAG knowledge base
    message_examples: list | None # Sample conversations
    post_examples: list | None    # Sample social posts
    topics: list[str] | None      # Interest topics
    adjectives: list[str] | None  # Character traits
    style: dict | None            # Style guidelines

    settings: dict | None         # Configuration + secrets
    templates: dict | None        # Custom prompts
    system_prompt: str | None     # System prompt override

    is_public: bool = False
    tags: list[str] | None
    created_at: datetime
    updated_at: datetime
```

### Prompt Builder

```python
# services/character/prompt_builder.py
class PromptBuilder:
    def __init__(self, character: Character): ...

    def build_system_prompt(self) -> str: ...
    def build_message_handler_prompt(self) -> str: ...
    def build_twitter_post_prompt(self, topic: str) -> str: ...
```

---

## Streaming Service

### Stream Types

```python
# services/streaming.py
class StreamType(Enum):
    PRICE = "price"
    POSITION = "position"
    NOTIFICATION = "notification"
    TRANSACTION = "transaction"
    MARKET_EVENT = "market_event"
    CHAT = "chat"
```

### Stream Manager

```python
class StreamManager:
    async def subscribe(
        self,
        stream_type: StreamType,
        wallet_address: str | None = None,
        filters: dict | None = None,
    ) -> StreamSubscription: ...

    async def unsubscribe(self, subscription_id: str, stream_type: StreamType) -> bool: ...
    async def broadcast_price(self, token_address: str, price: float, ...) -> None: ...
    async def get_stats(self) -> dict: ...
```

### Price Streaming

```python
class PriceStreamService:
    async def subscribe_prices(
        self,
        token_addresses: list[str],
        interval_ms: int = 1000,
    ) -> AsyncGenerator[dict, None]:
        """Yield price updates for tokens."""
```

---

## Error Handling

### Error Types

```python
# services/message.py
if any(k in exc_str for k in ("rate_limit", "429")):
    error_type = "rate_limit"
    user_msg = "Rate limit reached. Please wait a moment."
elif any(k in exc_str for k in ("timeout",)):
    error_type = "timeout"
    user_msg = "The response timed out. Please try again."
elif any(k in exc_str for k in ("authentication", "api key")):
    error_type = "auth"
    user_msg = "AI service configuration error."
elif any(k in exc_str for k in ("context_length", "token limit")):
    error_type = "context_limit"
    user_msg = "The conversation is too long. Start a new chat."
```

### Retry Logic

```python
_MAX_RETRIES = 2
_RETRYABLE = ("rate_limit", "429", "timeout", "connection", "network")

while _attempt <= _MAX_RETRIES:
    try:
        async for chunk in llm.astream(messages):
            yield chunk
        break
    except Exception as e:
        if is_retryable(e) and _attempt < _MAX_RETRIES:
            _backoff = 2 ** _attempt  # 2s, 4s
            await asyncio.sleep(_backoff)
            continue
        raise
```

---

## Cache Service

### Redis Cache

```python
# services/cache.py
class CacheService:
    async def get(self, cache_type: str, key: str) -> Any: ...
    async def set(self, cache_type: str, key: str, value: Any, ttl: int = 60) -> None: ...
    async def delete(self, cache_type: str, key: str) -> bool: ...
    async def invalidate_yields(self) -> int: ...
    async def invalidate_prices(self) -> int: ...
    async def health_check(self) -> dict: ...
```

### Cache Types

| Type | TTL | Description |
|------|-----|-------------|
| `yields` | 60s | DeFi yield data |
| `prices` | 10s | Token prices |
| `portfolio` | 30s | Portfolio data |
| `token` | 60s | Token metadata |

---

## Prometheus Metrics

```python
REQUEST_COUNT = Counter(
    "chat_service_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "chat_service_request_duration_seconds",
    "Request latency in seconds",
    ["method", "endpoint"],
)
```

---

## Dependencies

```toml
# pyproject.toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncpg>=0.30.0",
    "pydantic-settings>=2.6.0",
    "openai>=1.60.0",
    "langchain-openai>=0.3.0",
    "langchain-core>=0.3.0",
    "httpx>=0.28.0",
    "redis>=5.2.0",
    "grpcio>=1.68.0",
    "grpcio-tools>=1.68.0",
    "prometheus-client>=0.21.0",
]
```

---

## Testing

```bash
# Run all tests
cd services/chat-service-py
pytest app/tests/ -v

# Run specific test file
pytest app/tests/test_session.py -v

# Run with coverage
pytest app/tests/ --cov=app --cov-report=html
```

---

## Alembic Migrations

```bash
# Create migration
alembic revision --autogenerate -m "Add summaries table"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

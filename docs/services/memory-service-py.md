# Memory Service (Python)

Vector-based long-term memory service. Provides semantic search with OpenAI embeddings + Qdrant.

## Quick Start

```bash
cd services/memory-service-py
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload --port 3040
# → HTTP: http://localhost:3040
# → gRPC: localhost:50054
```

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │         MEMORY SERVICE               │
                                    │                                     │
                                    │  ┌─────────────────────────────┐    │
                                    │  │     FastAPI Server (:3040)  │    │
                                    │  │   Memory + Consent Routes   │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    │  ┌──────────────┴──────────────┐    │
                                    │  │     gRPC Server (:50054)    │    │
                                    │  │  Memory + Consent + Summary │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    └─────────────────┼───────────────────┘
                                                      │
         ┌────────────────────────────────────────────┼───────────────────┐
         │                                            │                   │
         ▼                                            ▼                   ▼
    ┌─────────────┐                           ┌─────────────┐       ┌─────────────┐
    │ PostgreSQL  │                           │   Qdrant    │       │   OpenAI    │
    │memory_schema│                           │  (Vectors)  │       │ (Embeddings)│
    └─────────────┘                           └─────────────┘       └─────────────┘
```

---

## File Structure

```
services/memory-service-py/
├── app/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Pydantic settings
│   ├── grpc_server.py             # gRPC servicers
│   │
│   ├── db/
│   │   └── connection.py          # SQLAlchemy async engine
│   │
│   ├── models/
│   │   └── consent.py             # UserConsent model
│   │
│   ├── middleware/
│   │   └── auth.py                # X-User-Wallet + X-Internal-Api-Key
│   │
│   └── services/
│       ├── vector.py              # Qdrant operations
│       ├── embeddings.py          # OpenAI embeddings
│       ├── consent.py             # Consent CRUD
│       └── summary.py             # Conversation summarization
│
├── proto_gen/                     # Generated gRPC stubs
│
└── pyproject.toml
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3040` | HTTP port |
| `GRPC_PORT` | `50054` | gRPC port |
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection |
| `DB_SCHEMA` | `memory_schema` | PostgreSQL schema |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant server URL |
| `QDRANT_API_KEY` | `""` | Qdrant API key (optional) |
| `OPRAI_OPENAI_API_KEY` | *(required)* | OpenAI API key |
| `EMBEDDING_MODEL` | `text-embedding-3-large` | Embedding model |
| `EMBEDDING_DIM` | `3072` | Embedding dimensions |
| `COLLECTION_NAME` | `oprai_memories` | Qdrant collection |
| `OPRAI_INTERNAL_API_KEY` | *(required)* | Inter-service auth |
| `OPRAI_JWT_SECRET` | *(required)* | JWT validation |

---

## Memory Types

| Type | Description | Consent Required |
|------|-------------|------------------|
| `meta` | Session metadata | No (always allowed) |
| `position` | Portfolio positions | Yes |
| `contract` | Smart contract interactions | Yes |
| `strategy` | Trading strategies | Yes |
| `preference` | User preferences | Yes |
| `decision` | Trading decisions | Yes |

---

## Services

### VectorService (Qdrant)

```python
# services/vector.py
class VectorService:
    def __init__(self):
        self._client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            timeout=10,
        )
        self._collection = settings.COLLECTION_NAME
        self._dim = settings.EMBEDDING_DIM  # 3072

    async def ensure_collection(self) -> None:
        """Create collection with COSINE distance if not exists."""

    async def store(
        self,
        payload: dict[str, Any],
        vector: list[float],
        point_id: str | None = None,
    ) -> str:
        """Upsert a point, return point ID."""

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        threshold: float = 0.75,
        filters: dict | None = None,
    ) -> list[dict]:
        """Semantic search, returns [{id, score, payload}, ...]"""

    async def delete(self, point_id: str) -> bool:
        """Delete point by ID."""
```

**Collection Config:**
- Distance: `COSINE`
- Vector size: `3072` (text-embedding-3-large)

**Filter Structure:**
```python
filters = {
    "user_id": "Hx7b8k...",
    "types": ["decision", "strategy"]
}
```

---

### EmbeddingService

```python
# services/embeddings.py
class EmbeddingService:
    def __init__(self):
        self._client = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)
        self._model = "text-embedding-3-large"

    async def embed(self, text: str) -> list[float]:
        """Generate 3072-dim embedding."""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding for multiple texts."""
```

**Model:** `text-embedding-3-large`
**Dimensions:** 3072
**Encoding:** `float`

---

### SummaryService

```python
# services/summary.py
class SummaryService:
    async def summarize(
        self,
        conversation_id: str,
        chunk: str,
        token_count: int = 0,
    ) -> dict:
        """Summarize conversation chunk."""
```

**System Prompt:**
```
You are a conversation summarizer for a DeFi AI assistant on Solana.
Summarize the conversation chunk into a concise summary following this format:
Title: <...> | Date: YYYY-MM-DD | Summary: * bullet * bullet * bullet

Focus on: actions taken, tokens/protocols discussed, user preferences expressed,
decisions made. Keep it under 200 words.
```

**Model:** `gpt-4o-mini`
**Max Tokens:** 300
**Temperature:** 0.3

**Response Format:**
```json
{
  "title": "Swap SOL to USDC",
  "summary": "Title: Swap SOL to USDC | Date: 2024-01-15 | Summary: * User requested swap * ...",
  "tokenCount": 150,
  "timestamp": "2024-01-15T10:00:00Z"
}
```

---

### ConsentService

```python
# services/consent.py
CONSENT_FIELDS = ("position", "contract", "strategy", "preference", "decision")

async def get_consent(db: AsyncSession, user_id: str) -> dict[str, bool]:
    """Fetch consent flags for a user."""

async def update_consent(
    db: AsyncSession,
    user_id: str,
    fields: dict[str, Any],
) -> dict[str, bool]:
    """Create or update consent flags."""

def is_type_allowed(consent: dict[str, bool], memory_type: str) -> bool:
    """Check if memory type is permitted."""
```

**Note:** `meta` type is always allowed regardless of consent.

---

## HTTP Endpoints

### Memory Operations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/memory` | Store memory point |
| `GET` | `/memory/search` | Semantic search |
| `DELETE` | `/memory/{point_id}` | Delete memory |

### Consent Operations

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/consent/{user_id}` | Get consent flags |
| `PUT` | `/consent/{user_id}` | Update consent flags |

### Summarization

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/summarize` | Summarize conversation chunk |

### Health & Metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health |
| `GET` | `/metrics` | Prometheus metrics |

---

## API Details

### Store Memory

```http
POST /memory
Authorization: Bearer <jwt>
X-Internal-Api-Key: <key>

{
  "payload": {
    "type": "decision",
    "summary": "User swapped 1 SOL to USDC via Jupiter",
    "user_id": "Hx7b8k...",
    "session_id": "uuid-...",
    "metadata": { ... }
  }
}
```

**Response:**
```json
{
  "point": {
    "id": "uuid-point-id",
    "payload": { ... }
  },
  "merged": false
}
```

### Search Memory

```http
GET /memory/search?query=swap%20SOL&types=decision,strategy&topK=5&threshold=0.75
Authorization: Bearer <jwt>
X-Internal-Api-Key: <key>
```

**Response:**
```json
{
  "results": [
    {
      "id": "point-uuid-1",
      "score": 0.89,
      "payload": {
        "type": "decision",
        "summary": "User swapped 1 SOL to USDC...",
        "user_id": "Hx7b8k...",
        "timestamp": "2024-01-15T10:00:00Z"
      }
    }
  ]
}
```

### Update Consent

```http
PUT /consent/me
Authorization: Bearer <jwt>
X-Internal-Api-Key: <key>

{
  "position": true,
  "decision": true,
  "strategy": false
}
```

**Response:**
```json
{
  "userId": "Hx7b8k...",
  "consent": {
    "position": true,
    "contract": false,
    "strategy": false,
    "preference": false,
    "decision": true
  }
}
```

---

## gRPC Services

### MemoryService

```protobuf
service MemoryService {
  rpc Store(StoreRequest) returns (StoreResponse);
  rpc Search(SearchRequest) returns (SearchResponse);
  rpc Delete(DeleteRequest) returns (DeleteResponse);
}
```

### ConsentService

```protobuf
service ConsentService {
  rpc GetConsent(GetConsentRequest) returns (GetConsentResponse);
  rpc UpdateConsent(UpdateConsentRequest) returns (UpdateConsentResponse);
}
```

### SummaryService

```protobuf
service SummaryService {
  rpc Summarize(SummarizeRequest) returns (SummarizeResponse);
}
```

---

## Compliance & Security

### Content Denylist

```python
# main.py
DENYLIST = [
    "private key", "mnemonic", "seed phrase", "signed transaction",
    "identity_number", "credit_card", "health_record",
]

def _validate_payload_compliance(summary: str, memory_type: str) -> None:
    """Check that the summary does not contain prohibited content."""
    lower = summary.lower()
    for term in DENYLIST:
        if term in lower:
            raise HTTPException(status_code=400, detail="Payload contains prohibited content")
```

### Minimum Summary Length

```python
if memory_type != "meta" and len(summary) < 30:
    raise HTTPException(
        status_code=400,
        detail="Summary is too short (minimum 30 characters for non-meta types)",
    )
```

---

## Database Model

### UserConsent

```python
# models/consent.py
class UserConsent(Base):
    __tablename__ = "user_consents"
    __table_args__ = {"schema": "memory_schema"}

    user_id: str           # Primary key
    position: bool         # Allow position memories
    contract: bool         # Allow contract memories
    strategy: bool         # Allow strategy memories
    preference: bool       # Allow preference memories
    decision: bool         # Allow decision memories
    created_at: datetime
    updated_at: datetime
```

---

## Qdrant Collection Schema

```python
# Created by ensure_collection()
VectorParams(
    size=3072,              # text-embedding-3-large
    distance=Distance.COSINE,
)
```

**Payload Fields:**
- `user_id` — Wallet address
- `type` — Memory type (meta, position, contract, etc.)
- `summary` — Text summary
- `timestamp` — ISO timestamp
- `session_id` — Optional chat session ID
- `metadata` — Optional extra data

---

## Prometheus Metrics

```python
REQUEST_COUNT = Counter(
    "memory_service_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "memory_service_request_duration_seconds",
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
    "qdrant-client>=1.12.0",
    "httpx>=0.28.0",
    "grpcio>=1.68.0",
    "grpcio-tools>=1.68.0",
    "prometheus-client>=0.21.0",
]
```

---

## Testing

```bash
# Run all tests
cd services/memory-service-py
pytest app/tests/ -v

# Run with coverage
pytest app/tests/ --cov=app --cov-report=html
```

---

## Graceful Degradation

- **Qdrant unavailable:** Service starts but returns 503 on memory operations
- **OpenAI API key missing:** Embedding service disabled, returns 503 on store/search
- **Summary service unavailable:** Falls back to basic preview summary

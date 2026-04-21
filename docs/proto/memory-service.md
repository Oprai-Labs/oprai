# proto/memory/consent.proto & vector.proto

Memory and consent management. Uses Qdrant vector database and OpenAI embeddings.

## Services

### MemoryConsentService
User privacy consent management. Port: **50054 (gRPC)** / **3040 (HTTP)**

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `GetConsent` | GetConsentRequest | ConsentState | Get consent status |
| `UpdateConsent` | UpdateConsentRequest | ConsentState | Update consents |

---

### MemoryVectorService
Vector-based memory management. Same port.

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `StoreMemory` | StoreMemoryRequest | StoreMemoryResponse | Save memory |
| `SearchMemories` | SearchMemoriesRequest | SearchMemoriesResponse | Semantic search |
| `DeleteMemory` | DeleteMemoryRequest | DeleteMemoryResponse | Delete memory |
| `Summarize` | SummarizationInput | SummarizationOutput | Summarize conversation |

---

## Consent System

### ConsentState

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | `string` | User ID |
| `positions` | `bool` | Should position data be saved? |
| `strategies` | `bool` | Should strategy data be saved? |
| `preferences` | `bool` | Should preferences be saved? |
| `decisions` | `bool` | Should decisions be saved? |
| `updated_at` | `Timestamp` | Last update |

**Default:** All fields are `false`

---

### UpdateConsentRequest

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | `string` | User ID |
| `positions` | `optional bool` | Update? |
| `strategies` | `optional bool` | Update? |
| `preferences` | `optional bool` | Update? |
| `decisions` | `optional bool` | Update? |

**Note:** Only specified fields are updated.

---

## Memory Types

### MemoryType (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `MEMORY_TYPE_UNSPECIFIED` | 0 | Unspecified |
| `MEMORY_TYPE_POSITION` | 1 | Portfolio position |
| `MEMORY_TYPE_CONTRACT` | 2 | Smart contract |
| `MEMORY_TYPE_STRATEGY` | 3 | Trading strategy |
| `MEMORY_TYPE_PREFERENCE` | 4 | User preference |
| `MEMORY_TYPE_DECISION` | 5 | Decision made |
| `MEMORY_TYPE_META` | 6 | Meta information |

---

### MemorySource (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `MEMORY_SOURCE_UNSPECIFIED` | 0 | Unspecified |
| `MEMORY_SOURCE_CHAT` | 1 | Derived from chat |
| `MEMORY_SOURCE_MANUAL` | 2 | Manual entry |
| `MEMORY_SOURCE_IMPORT` | 3 | From external source |

---

## Memory Payload

### MemoryPayload

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | `string` | User ID |
| `chat_id` | `string` | Associated chat |
| `type` | `MemoryType` | Memory type |
| `title` | `string` | Title |
| `summary` | `string` | Summary |
| `contract` | `string` | Contract address (optional) |
| `chain` | `string` | Chain (optional) |
| `timestamp` | `Timestamp` | Timestamp |
| `ttl` | `int64` | TTL (seconds, 0 = infinite) |
| `risk_score` | `double` | Risk score (0.0-1.0) |
| `source` | `MemorySource` | Source |
| `version` | `int32` | Version |
| `tags` | `repeated string` | Tags |

---

## StoreMemory

### StoreMemoryRequest

| Field | Type | Description |
|-------|------|-------------|
| `payload` | `MemoryPayload` | Memory data |
| `consent` | `map<string, bool>` | Consent status |
| `overwrite` | `bool` | Upsert behavior |

**Consent Map Example:**
```json
{
  "positions": true,
  "strategies": false,
  "preferences": true,
  "decisions": true
}
```

### StoreMemoryResponse

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Qdrant point ID |
| `stored` | `bool` | Was it saved? |
| `message` | `string` | Status message |

**Consent Rejected:**
```json
{
  "id": "",
  "stored": false,
  "message": "User has not consented to storing position memories"
}
```

---

## SearchMemories

### SearchMemoriesRequest

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | `string` | User ID |
| `query` | `string` | Natural language query |
| `allowed_types` | `repeated MemoryType` | Types to filter |
| `similarity_threshold` | `double` | Similarity threshold (0.0-1.0, default: 0.7) |
| `top_k` | `int32` | Max results (default: 10) |

### SearchMemoriesResponse

| Field | Type | Description |
|-------|------|-------------|
| `results` | `repeated MemorySearchResult` | Search results |

### MemorySearchResult

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Point ID |
| `payload` | `MemoryPayload` | Memory data |
| `similarity` | `double` | Cosine similarity |

---

## Summarize

### SummarizationInput

| Field | Type | Description |
|-------|------|-------------|
| `conversation_id` | `string` | Chat ID |
| `chunk` | `string` | Raw conversation text |
| `token_count` | `int32` | Token count |

### SummarizationOutput

| Field | Type | Description |
|-------|------|-------------|
| `title` | `string` | Summary title |
| `summary` | `string` | Summary content |
| `token_count` | `int32` | Summary token count |
| `timestamp` | `Timestamp` | Time |

---

## Usage Flow

```
+--------------+     +--------------+     +--------------+     +--------------+
| Chat Service |     |Memory Service|     |   OpenAI API  |     |   Qdrant     |
+------+-------+     +------+-------+     +------+-------+     +------+-------+
       |                    |                    |                    |
       | 1. Check Consent   |                    |                    |
       |------------------->|                    |                    |
       |                    | GetConsent         |                    |
       |                    |<-------------------|                    |
       |   { positions: true }                   |                    |
       |<-------------------|                    |                    |
       |                    |                    |                    |
       | 2. User talks about|                    |                    |
       |    their portfolio  |                    |                    |
       |------------------->|                    |                    |
       |                    | 3. Summarize       |                    |
       |                    |------------------->|                    |
       |                    |                    | Create summary     |
       |                    |                    |<-------------------|
       |                    |   { title, summary }                    |
       |                    |<-------------------|                    |
       |                    |                    |                    |
       |                    | 4. Get Embedding   |                    |
       |                    |------------------->|                    |
       |                    |                    | text-embedding-3-small             |
       |                    |   [0.1, 0.5, ...]   |                    |
       |                    |<-------------------|                    |
       |                    |                    |                    |
       |                    | 5. Store in Qdrant |                    |
       |                    |--------------------------------------->|
       |                    |                    |  Upsert point       |
       |                    |  { id: "mem_..." }  |<-------------------|
       |                    |<---------------------------------------|
```

---

## Qdrant Collection Structure

```json
{
  "collection_name": "oprai_memories",
  "vectors": {
    "size": 1536,
    "distance": "Cosine"
  },
  "payload_schema": {
    "user_id": "keyword",
    "chat_id": "keyword",
    "type": "keyword",
    "title": "text",
    "summary": "text",
    "tags": "keyword[]",
    "timestamp": "integer",
    "ttl": "integer",
    "risk_score": "float"
  }
}
```

---

## Backend Implementation (Python)

```python
# services/memory-service-py/app/grpc/memory.py
from qdrant_client import QdrantClient
from openai import AsyncOpenAI

class MemoryVectorServicer(memory_pb2_grpc.MemoryVectorServiceServicer):
    def __init__(self):
        self.qdrant = QdrantClient(url=os.getenv("QDRANT_URL"))
        self.openai = AsyncOpenAI(api_key=os.getenv("OPRAI_OPENAI_API_KEY"))

    async def StoreMemory(self, request, context):
        # 1. Check consent
        memory_type = memory_pb2.MemoryType.Name(request.payload.type).lower()
        if not request.consent.get(memory_type, False):
            return memory_pb2.StoreMemoryResponse(
                id="", stored=False,
                message=f"User has not consented to storing {memory_type} memories"
            )

        # 2. Generate embedding
        text_to_embed = f"{request.payload.title}\n{request.payload.summary}"
        embedding = await self.openai.embeddings.create(
            model="text-embedding-3-small",
            input=text_to_embed
        )

        # 3. Store in Qdrant
        point_id = str(uuid.uuid4())
        self.qdrant.upsert(
            collection_name="oprai_memories",
            points=[{
                "id": point_id,
                "vector": embedding.data[0].embedding,
                "payload": {
                    "user_id": request.payload.user_id,
                    "chat_id": request.payload.chat_id,
                    "type": memory_type,
                    "title": request.payload.title,
                    "summary": request.payload.summary,
                    # ...
                }
            }]
        )

        return memory_pb2.StoreMemoryResponse(
            id=point_id, stored=True, message="Memory stored successfully"
        )

    async def SearchMemories(self, request, context):
        # 1. Embed query
        embedding = await self.openai.embeddings.create(
            model="text-embedding-3-small",
            input=request.query
        )

        # 2. Search Qdrant
        results = self.qdrant.search(
            collection_name="oprai_memories",
            query_vector=embedding.data[0].embedding,
            filter={
                "must": [
                    {"key": "user_id", "match": {"value": request.user_id}}
                ]
            },
            limit=request.top_k or 10,
            score_threshold=request.similarity_threshold or 0.7
        )

        # 3. Build response
        return memory_pb2.SearchMemoriesResponse(
            results=[memory_pb2.MemorySearchResult(
                id=str(r.id),
                payload=self._payload_from_dict(r.payload),
                similarity=r.score
            ) for r in results]
        )
```

---

## Database Schema

**Table:** `memory_schema.user_consents`

| Column | Type | Description |
|--------|------|-------------|
| `user_id` | UUID | FK -> auth_schema.users |
| `positions` | BOOLEAN | Position consent |
| `strategies` | BOOLEAN | Strategy consent |
| `preferences` | BOOLEAN | Preference consent |
| `decisions` | BOOLEAN | Decision consent |
| `updated_at` | TIMESTAMP | Last update |

---

## API Endpoints (Gateway)

| HTTP | Endpoint | gRPC Call |
|------|----------|-----------|
| `GET` | `/memory/consent` | `GetConsent` |
| `PATCH` | `/memory/consent` | `UpdateConsent` |
| `POST` | `/memory/store` | `StoreMemory` |
| `POST` | `/memory/search` | `SearchMemories` |
| `DELETE` | `/memory/:id` | `DeleteMemory` |

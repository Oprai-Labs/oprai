# Memory Service (Python)

Vector-based memory storage using Qdrant and OpenAI embeddings for conversation context.

## Responsibilities

- Store and retrieve conversation embeddings (Qdrant)
- Generate embeddings via OpenAI API
- Semantic search across conversation history
- Conversation summarization
- User consent management for memory storage
- Context retrieval for chat-service

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/memory/store` | Store a memory entry |
| POST | `/memory/search` | Semantic search |
| GET | `/memory/:userId` | Get user memories |
| DELETE | `/memory/:userId` | Clear user memories |
| POST | `/memory/consent` | Set memory consent preference |
| GET | `/memory/consent/:userId` | Get consent status |
| POST | `/memory/summarize` | Generate conversation summary |

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | HTTP port (default: 3040) |
| `GRPC_PORT` | No | gRPC port (default: 50054) |
| `OPRAI_JWT_SECRET` | Yes | JWT validation |
| `OPRAI_INTERNAL_API_KEY` | Yes | Service-to-service auth |
| `DATABASE_URL` | Yes | PostgreSQL (consent storage) |
| `QDRANT_URL` | No | Qdrant URL (default: `http://localhost:6333`) |
| `OPRAI_OPENAI_API_KEY` | Yes | OpenAI API key for embeddings |

## Database Schema

- **Schema**: `memory_schema`
- **Tables**: `user_consents`

## Run

```bash
# Dev (native)
cd services/memory-service-py
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload --port 3040

# Docker
docker compose -f infra/docker-compose.yml up memory-service-py

# Run migrations
cd services/memory-service-py
DATABASE_URL="postgresql+asyncpg://postgres:pass@localhost:5433/oprai" .venv/bin/alembic upgrade head
```

## Project Structure

```
memory-service-py/
├── app/
│   ├── main.py              FastAPI application
│   ├── config.py             Configuration
│   ├── models/               SQLAlchemy models
│   ├── schemas/              Pydantic schemas
│   ├── services/             Business logic (embedding, search)
│   ├── handlers/             Route handlers
│   └── grpc/                 gRPC server
├── alembic/                  Database migrations
├── alembic.ini               Alembic config
├── proto_gen/                Generated gRPC Python stubs
├── pyproject.toml
└── Dockerfile
```

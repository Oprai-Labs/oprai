# Chat Service (Python)

LLM-powered conversational AI with streaming responses and Solana action generation.

## Responsibilities

- Chat session management (create, list, delete)
- Message handling with LLM integration (OpenAI)
- SSE (Server-Sent Events) streaming responses
- Solana action block generation (swap, transfer, stake)
- Conversation summarization
- Integration with memory-service for context

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat/sessions` | Create a new chat session |
| GET | `/chat/sessions` | List user's sessions |
| GET | `/chat/sessions/:id` | Get session with messages |
| DELETE | `/chat/sessions/:id` | Delete session |
| POST | `/chat/sessions/:id/messages` | Send message (returns SSE stream) |
| GET | `/chat/sessions/:id/stream` | Stream endpoint |

## Action Format

The LLM returns action blocks that the frontend parses:

```
[ACTION:swap]
```json
{"inputMint": "SOL", "outputMint": "USDC", "amount": 1}
```

[ACTION:transfer]
```json
{"to": "wallet_address", "amount": 0.5, "token": "SOL"}
```
```

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | HTTP port (default: 3020) |
| `GRPC_PORT` | No | gRPC port (default: 50052) |
| `OPRAI_JWT_SECRET` | Yes | JWT validation |
| `OPRAI_INTERNAL_API_KEY` | Yes | Service-to-service auth |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `OPRAI_OPENAI_API_KEY` | Yes | OpenAI API key |
| `OPRAI_ANTHROPIC_API_KEY` | If using Anthropic | Anthropic API key |
| `OPRAI_LLM_PROVIDER` | No | `openai` (default) or `anthropic` |
| `OPRAI_RESPONDER_MODEL_OPENAI` | No | Main chat model when provider=openai (default: `gpt-5.4-nano`) |
| `OPRAI_RESPONDER_MODEL_ANTHROPIC` | No | Main chat model when provider=anthropic (default: `claude-haiku-4-5`) |
| `OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI` | No | OpenAI fallback on rate limit / 5xx (default: `gpt-4o-mini`) |
| `OPRAI_INTENT_CLASSIFIER_MODEL` | No | Pre-classifier (always OpenAI; default: `gpt-5.4-nano`) |
| `MEMORY_SERVICE_URL` | No | Memory service for context |
| `MEMORY_SERVICE_GRPC` | No | Memory service gRPC address |

## Database Schema

- **Schema**: `chat_schema`
- **Tables**: `chat_sessions`, `chat_messages`

## Run

```bash
# Dev (native)
cd services/chat-service-py
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload --port 3020

# Docker
docker compose -f infra/docker-compose.yml up chat-service-py

# Run migrations
cd services/chat-service-py
DATABASE_URL="postgresql+asyncpg://postgres:pass@localhost:5433/oprai" .venv/bin/alembic upgrade head

# Tests
cd services/chat-service-py && .venv/bin/pytest
```

## Project Structure

```
chat-service-py/
├── app/
│   ├── main.py              FastAPI application
│   ├── config.py             Configuration
│   ├── models/               SQLAlchemy models
│   ├── schemas/              Pydantic schemas
│   ├── services/             Business logic
│   ├── handlers/             Route handlers
│   ├── grpc/                 gRPC server
│   └── prompts/              LLM prompt templates
├── alembic/                  Database migrations
├── alembic.ini               Alembic config
├── proto_gen/                Generated gRPC Python stubs
├── tests/                    pytest tests
├── pyproject.toml
└── Dockerfile
```

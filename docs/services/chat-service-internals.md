# Chat Service Internals

Chat service's critical functions and LLM prompt engineering details.

## LLM Service

### Model Selection

```python
# services/chat-service-py/app/services/llm.py

# Models using the Responses API (reasoning/o-series)
_RESPONSES_API_MODELS = {
    "gpt-5-mini", "gpt-5",
    "o1", "o1-mini", "o1-preview",
    "o3", "o3-mini",
    "o4-mini",
}

# Other models → Chat Completions (LangChain)
# - gpt-4o-mini, gpt-4o, etc.
```

### Streaming Behavior

| Model Type | API | Streaming Format |
|------------|-----|------------------|
| Reasoning (o-series) | `/v1/responses` | `<thinking>reasoning</thinking>output` |
| Standard (gpt-4o) | `/v1/chat/completions` | Direct text |

```python
class LLMService:
    async def astream(messages: list[dict]) -> AsyncGenerator[str, None]:
        # Reasoning models: wrap reasoning in <thinking> tags
        # Standard models: direct text stream

    async def acomplete(messages: list[dict]) -> str:
        # Non-streaming completion
```

---

## Message Flow

### Message CRUD

```python
# services/chat-service-py/app/services/message.py

async def get_messages(db, wallet, session_id, limit, offset) -> list[dict]:
    """Return messages ordered chronologically."""

async def create_message(db, session_id, wallet, role, content, metadata) -> dict:
    """Persist message + update session.updated_at"""

async def update_message_metadata(db, session_id, message_id, wallet, patch) -> bool:
    """Merge patch into message metadata_"""
```

### Message Format

```python
{
    "id": "uuid",
    "speaker": "user" | "assistant",
    "content": "message text",
    "timestamp": "2024-01-15T10:00:00Z",
    "annotations": [...],  # Optional
    "metadata": {
        "action_results": [...],
        "query_snapshots": [...]
    }
}
```

---

## Context Building

### Summary Service

```python
# services/chat-service-py/app/services/summary.py

async def build_llm_context(
    db: AsyncSession,
    wallet: str,
    session_id: str,
    max_messages: int = 50,
) -> list[dict]:
    """
    Build LLM context from messages + summaries.

    Strategy:
    1. Load recent messages (last N)
    2. Load summaries for older messages
    3. Combine: [summaries] + [recent messages]
    """

async def maybe_create_summary(
    db: AsyncSession,
    wallet: str,
    session_id: str,
    force: bool = False,
) -> dict | None:
    """
    Create summary if message count > threshold.

    Uses gpt-4o-mini to compress conversation.
    """
```

### Context Window Management

```
┌────────────────────────────────────────────────────────┐
│                    LLM CONTEXT                         │
├────────────────────────────────────────────────────────┤
│  [System Prompt]                                       │
│  - Agent personality                                   │
│  - Available actions                                   │
│  - Response format rules                               │
├────────────────────────────────────────────────────────┤
│  [Memory Context]                                      │
│  - Recent memories from Qdrant                         │
│  - User preferences                                    │
│  - Previous decisions                                  │
├────────────────────────────────────────────────────────┤
│  [Summary Context]                                     │
│  - Compressed older conversation                       │
│  - Key points extracted                                │
├────────────────────────────────────────────────────────┤
│  [Recent Messages]                                     │
│  - Last N messages (uncompressed)                      │
│  - Full conversation context                           │
└────────────────────────────────────────────────────────┘
```

---

## System Prompts

### Main Agent Prompt

```python
# Character-based system prompt structure

SOLANA_AGENT_PROMPT = """
You are {character_name}, a DeFi assistant for Solana.

## Capabilities
- Execute swaps via Jupiter
- Manage staking positions
- Provide portfolio analysis
- Track whale movements

## Response Format
When you need to execute an action, respond with:
[ACTION:type] {"param1": "value1", ...}

For queries:
[QUERY:type] {"param1": "value1", ...}

For disambiguation:
[CLARIFY:category] {"question": "...", "options": [...]}

## Available Actions
{list_of_available_actions}

## Safety Rules
- Always confirm large swaps (>1000 USDC)
- Warn about high slippage (>3%)
- Never share private keys
"""
```

### Action Generation Rules

```
1. Use JSON format for parameters (preferred)
   [ACTION:swap] {"inputMint": "SOL", "outputMint": "USDC", "amount": "1"}

2. Legacy key=value format still supported
   [ACTION:swap] inputMint=SOL outputMint=USDC amount=1

3. Chain actions with chain parameter
   [ACTION:swap] {..., "chain": "true"}
   [ACTION:stake] {...}  # Uses swap output as input
```

---

## Streaming Response Flow

```
User Message
     │
     ▼
┌─────────────────┐
│ Context Builder │ ← Load messages + summaries + memories
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  LLM Service    │ ← Stream to SSE connection
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Intent Parser   │ ← Extract [ACTION]/[QUERY] blocks
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Memory Service  │ ← Store conversation summary
└─────────────────┘
```

### SSE Response Format

```
data: {"delta": "Hello", "sessionId": "uuid"}
data: {"delta": ", how", "sessionId": "uuid"}
data: {"delta": " can I help?", "sessionId": "uuid", "messageId": "uuid"}
data: [DONE]
```

---

## Key Services Summary

| Service | File | Purpose |
|---------|------|---------|
| `LLMService` | `services/llm.py` | OpenAI API wrapper, streaming |
| `message.py` | `services/message.py` | CRUD for chat_messages |
| `summary.py` | `services/summary.py` | Context compression |
| `streaming.py` | `services/streaming.py` | Real-time price/position streams |
| `cache.py` | `services/cache.py` | Redis caching |
| `analytics.py` | `services/analytics.py` | Usage tracking |

---

## Specialized Services

### Whale Tracking

```python
# services/whale_tracking.py

async def track_whale_wallet(wallet: str) -> dict:
    """Track whale wallet activity."""

async def get_whale_alerts(threshold_usd: float = 10000) -> list:
    """Get recent large transactions."""
```

### Token Security

```python
# services/token_security.py

async def analyze_token(mint: str) -> dict:
    """
    Returns:
    - mint_authority: bool (frozen)
    - freeze_authority: bool
    - top_holders: list
    - liquidity_locked: bool
    - honeypot_risk: float
    """
```

### Yield Aggregator

```python
# services/yield_aggregator.py

async def get_best_yields(token: str) -> list[dict]:
    """
    Compare yields across:
    - Kamino lending
    - Solend
    - LP pools
    """
```

### Portfolio Optimizer

```python
# services/portfolio_optimizer.py

async def optimize_portfolio(
    wallet: str,
    risk_tolerance: str,  # "low" | "medium" | "high"
) -> dict:
    """Suggest rebalancing based on risk profile."""
```

---

## Database Models

### ChatMessage

```python
# models/message.py

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = {"schema": "chat_schema"}

    id: UUID
    session_id: UUID  # FK → chat_sessions
    wallet_address: str
    role: str  # "user" | "assistant"
    content: str
    metadata_: dict  # JSONB
    created_at: datetime
```

### ChatSession

```python
# models/session.py

class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = {"schema": "chat_schema"}

    id: UUID
    wallet_address: str
    title: str | None
    summary: str | None
    created_at: datetime
    updated_at: datetime
```

"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Chat service configuration.

    All values can be overridden via environment variables.
    Pydantic-settings reads them case-insensitively.
    """

    # Server
    PORT: int = 3020
    GRPC_PORT: int = 50052
    BIND_HOST: str = "127.0.0.1"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:@localhost:5433/oprai"
    DB_SCHEMA: str = "chat_schema"

    # LLM provider — "openai" or "anthropic". Switches the entire LLMService
    # backend. Anthropic models (claude-haiku-4-5, claude-sonnet-4-6, …) don't
    # use the Harmony channel format, so the tool-call leakage that plagued
    # the smaller gpt-5.4-nano under tool_choice="auto" is impossible there.
    OPRAI_LLM_PROVIDER: str = "openai"

    # ─────────────────────────────────────────────────────────────────────────
    # LLM model configuration
    # ─────────────────────────────────────────────────────────────────────────
    # Models are named by ROLE first, provider suffix second. The two roles:
    #   - intent_classifier: small fast pre-classifier (always OpenAI)
    #   - responder:         main chat response (provider-switchable)
    # OPRAI_LLM_PROVIDER picks which RESPONDER_MODEL_* is active.
    OPRAI_OPENAI_API_KEY: str = ""
    OPRAI_ANTHROPIC_API_KEY: str = ""

    # Responder — main chat model. Active one is chosen by OPRAI_LLM_PROVIDER.
    # Bumped nano → mini (2026-05-17): nano hedged hard on ambiguous turns
    # (preferring `request_clarification` + "I cannot access" prose over
    # committing) and lost tool-chain context (calling sns_resolve then
    # failing to pass `owner` to the next call). mini commits more confidently
    # and chains tools correctly. Cost delta is ~3x per token but turn count
    # drops because we stop re-asking — net cost similar.
    OPRAI_RESPONDER_MODEL_OPENAI: str = "gpt-5.4-mini"
    OPRAI_RESPONDER_MODEL_ANTHROPIC: str = "claude-haiku-4-5"

    # Responder fallback (used when the primary OpenAI model fails with
    # rate-limit / 5xx). Must be a Chat Completions model if primary is a
    # Responses API model. Anthropic has no fallback yet — single provider.
    OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI: str = "gpt-4o-mini"

    # Optional analysis upgrade — when a turn looks like deep analysis
    # (long message, "compare X vs Y", "analyze wallet", multi-step), the
    # router can route to a more capable model. Empty = no override (use
    # the primary model for everything). Examples: "gpt-5.4" (full size),
    # "claude-sonnet-4-6" with provider=anthropic. Off by default to keep
    # cost predictable; enable per environment.
    OPRAI_RESPONDER_MODEL_OPENAI_ANALYSIS: str = ""
    OPRAI_RESPONDER_MODEL_ANTHROPIC_ANALYSIS: str = ""

    # Character-length threshold above which a turn is considered analysis-
    # grade and the analysis model (if configured) is used. Most chat turns
    # are <200 chars; deep-dive prompts ("analyze this wallet — holdings,
    # PnL, sectors, …") routinely exceed 300.
    OPRAI_RESPONDER_ANALYSIS_LENGTH_THRESHOLD: int = 250

    # OpenAI Chat Completions tuning (does not apply to Responses API models).
    OPRAI_GPT_REASONING_EFFORT: str = "medium"  # "high" caused empty replies — on reasoning models max_output_tokens covers reasoning + visible, and "high" consumed the entire budget. Keep at "medium" and rely on prompt enforcement for compliance.
    OPRAI_GPT_MAX_TOKENS: int = 24000  # raised from 16000 — on reasoning models max_output_tokens covers chain-of-thought + visible response. Composite wallet/token deep-dives saw the visible section truncated mid-sentence when reasoning consumed ~10K and only ~6K was left for the 9-section report. 24000 leaves ~10-12K for reasoning + ~12-14K for visible, which covers a full Nansen-style report (~3000-4000 visible tokens) with ample margin.

    # Intent classifier — runs a tiny OpenAI call before the main responder so
    # the tool list can be narrowed by intent (action vs query vs advice). The
    # router uses `max_completion_tokens` for reasoning models (gpt-5.x / o-series)
    # and `max_tokens` for classic chat models (gpt-4*); both work. Defaults to
    # gpt-5.4-nano for multilingual native parsing. Requires OPRAI_OPENAI_API_KEY.
    OPRAI_INTENT_CLASSIFIER_MODEL: str = "gpt-5.4-nano"

    # Summarizer — block summaries are compressed history that future turns
    # treat as ground truth, so we want a model that follows the structured
    # JSON schema strictly. gpt-4o-mini handles this better than the nano
    # tier and is cheap enough for a per-block call. Override with
    # OPRAI_SUMMARIZER_MODEL when changing providers. Requires OPRAI_OPENAI_API_KEY.
    OPRAI_SUMMARIZER_MODEL: str = "gpt-4o-mini"

    # Number of summary blocks to keep at full resolution. Once this threshold
    # is exceeded, the oldest blocks beyond it are condensed into a single
    # "executive meta-summary" so context size stays bounded as conversations
    # grow. 0 disables hierarchical summarisation.
    OPRAI_SUMMARY_RECENT_BLOCKS: int = 6

    # Inter-service
    MEMORY_SERVICE_URL: str = "http://localhost:3040"
    GATEWAY_URL: str = "http://localhost:3001"
    OPRAI_INTERNAL_API_KEY: str = ""
    OPRAI_JWT_SECRET: str = ""

    # Solana RPC endpoint for on-chain lookups (balance injection etc.)
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"

    # Redis cache (optional; service runs without if unreachable)
    REDIS_URL: str = "redis://localhost:6379"

    # Qdrant vector database
    QDRANT_URL: str = "http://localhost:6333"
    # Per-request timeout for Qdrant HTTP calls. The default httpx timeout
    # (5s) is too short for on-disk collections after a cold start: Qdrant
    # has to page the HNSW index + INT8 quantization codebook into RAM on
    # the first query, which can take 10-15s on the 26k-vector knowledge
    # base. After warmup, queries complete in ~50ms — the longer timeout
    # only matters on cold paths.
    QDRANT_TIMEOUT_SEC: float = 30.0

    # Knowledge RAG (blockchain/DeFi/Solana Q&A)
    KNOWLEDGE_RAG_ENABLED: bool = True
    KNOWLEDGE_RAG_TOP_K: int = 50
    KNOWLEDGE_RAG_RERANK_TOP_N: int = 5
    KNOWLEDGE_RAG_TOKEN_BUDGET: int = 1500

    # Magic Eden NFT marketplace API
    MAGIC_EDEN_API_KEY: str = ""

    # CORS: comma-separated list of allowed origins.
    # Empty string = allow localhost:3000 and localhost:4200 only.
    CORS_ALLOWED_ORIGINS: str = ""

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # "json" for production, "console" for development

    # Environment
    NODE_ENV: str = "development"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

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

    # OpenAI / LLM
    OPRAI_OPENAI_API_KEY: str = ""
    OPRAI_OPENAI_MODEL: str = "gpt-5.4-nano"
    OPRAI_GPT_REASONING_EFFORT: str = "medium"
    OPRAI_GPT_MAX_TOKENS: int = 4096  # Chat Completions only; Responses API has no token cap

    # OpenAI fallback model (used when primary model fails with rate-limit / 5xx)
    # Must be a Chat Completions model if primary is a Responses API model.
    OPRAI_OPENAI_FALLBACK_MODEL: str = "gpt-4o-mini"

    # Inter-service
    MEMORY_SERVICE_URL: str = "http://localhost:3040"
    GATEWAY_URL: str = "http://localhost:3001"
    OPRAI_INTERNAL_API_KEY: str = ""
    OPRAI_JWT_SECRET: str = ""

    # Solana RPC endpoint for on-chain lookups (balance injection etc.)
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"

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

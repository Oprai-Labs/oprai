"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Memory service configuration.

    All values can be overridden via environment variables.
    """

    # Server
    PORT: int = 3040
    GRPC_PORT: int = 50054
    BIND_HOST: str = "127.0.0.1"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:@localhost:5433/oprai"
    DB_SCHEMA: str = "memory_schema"

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""

    # OpenAI / Embeddings
    OPRAI_OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    EMBEDDING_DIM: int = 3072
    COLLECTION_NAME: str = "oprai_memories"

    # Inter-service
    OPRAI_INTERNAL_API_KEY: str = ""
    OPRAI_JWT_SECRET: str = ""

    # CORS — comma-separated list of allowed origins.
    # Must be explicit (not "*") when allow_credentials=True.
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]

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

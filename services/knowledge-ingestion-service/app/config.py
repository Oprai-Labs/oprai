"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    PORT: int = 3070
    BIND_HOST: str = "127.0.0.1"

    # Database (crawl-state)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:@localhost:5433/oprai"
    DB_SCHEMA: str = "ingestion_schema"

    # Vector store
    QDRANT_URL: str = "http://localhost:6333"

    # OpenAI
    OPRAI_OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    EMBEDDING_BATCH_SIZE: int = 64

    # GitHub token for crawler rate limits
    GITHUB_TOKEN: str = ""

    # Crawl safety
    DEFAULT_CRAWL_DELAY_S: float = 1.0
    MAX_CONCURRENT_PER_HOST: int = 2
    MAX_PAGES_PER_SOURCE: int = 5000

    # Cost cap (USD/month for embeddings)
    MONTHLY_BUDGET_USD: float = 50.0

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # CORS
    CORS_ALLOWED_ORIGINS: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

"""Application settings loaded from environment variables.

Mirrors the polyglot convention (pydantic-settings, case-insensitive env).
Reads the monorepo-root .env first, then a service-local .env override.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Telegram bot configuration.

    Every value can be overridden via environment variables. The bot itself is
    custodial-key FREE: it never sees private keys. Key custody + signing live
    entirely in the isolated Rust signer (OPRAI_TG_SIGNER_URL). The bot builds
    intent, calls the existing gateway action pipeline, and hands unsigned
    transactions to the signer.
    """

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Telegram ─────────────────────────────────────────────────────────────
    # Bot token from @BotFather. SECRET — lives only in prod .env, never repo.
    # Same token auth-service already uses to HMAC-verify Login Widget payloads,
    # so the bot and the account-linking widget share one identity.
    OPRAI_TELEGRAM_BOT_TOKEN: str = ""
    # Dev uses long-polling. Set a webhook URL in prod (Faz 4).
    OPRAI_TG_WEBHOOK_URL: str = ""
    OPRAI_TG_WEBHOOK_SECRET: str = ""

    # ── Downstream services ──────────────────────────────────────────────────
    # The isolated signer (Rust) — the ONLY component that touches keys/Vault.
    OPRAI_TG_SIGNER_URL: str = "http://127.0.0.1:3060"
    # The existing API gateway — the bot authenticates on-behalf via SIWS/SIWE
    # (signing the auth nonce through the signer) and then calls it as any
    # client, so no gateway changes are needed.
    GATEWAY_URL: str = "http://127.0.0.1:3001"
    OPRAI_INTERNAL_API_KEY: str = ""
    # Must match the auth-service APP_DOMAIN in prod (empty in dev). The bot
    # builds the SIWS/SIWE sign-in message with this as the first-line domain.
    OPRAI_TG_APP_DOMAIN: str = ""

    # ── Database (tg_schema) ─────────────────────────────────────────────────
    # Plain asyncpg DSN (NOT the SQLAlchemy "+asyncpg" form). _pg_dsn() strips
    # a "+asyncpg" suffix if one is inherited from a shared DATABASE_URL.
    DATABASE_URL: str = "postgresql://postgres:@localhost:5433/oprai"
    DB_SCHEMA: str = "tg_schema"

    # ── Vault (used by the signer; referenced here only for health/wiring) ────
    VAULT_ADDR: str = ""
    VAULT_TOKEN: str = ""

    # ── Tokenomics / credits ─────────────────────────────────────────────────
    # Credit top-ups are paid in $OPRAI. For now 100% goes to the dev/collection
    # wallet; the burn split is intentionally left off (manual, off-bot) but the
    # ratio hook stays configurable so it can be enabled later with no refactor.
    OPRAI_TG_DEV_WALLET: str = "0xb0E580Cf95E2B045b99b31ddF3137D3D88d55b8E"
    OPRAI_TG_BURN_BPS: int = 0  # basis points burned on top-up; 0 = no auto-burn

    # ── Server ───────────────────────────────────────────────────────────────
    PORT: int = 3055
    BIND_HOST: str = "127.0.0.1"
    LOG_LEVEL: str = "INFO"

    def pg_dsn(self) -> str:
        """asyncpg-compatible DSN (strip any SQLAlchemy driver suffix)."""
        return self.DATABASE_URL.replace("+asyncpg", "", 1)


settings = Settings()

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
    # Needed to build deep links (account linking, claimable transfers).
    OPRAI_TG_BOT_USERNAME: str = "Oprai_Labs_Bot"
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
    # chain-intel real-time signal feed (alpha alerts). Internal service on the
    # docker network; the alert worker polls /signals/* here.
    CHAIN_INTEL_URL: str = "http://rh-chain-intel-api:3160"
    # Alpha alert tuning (env-overridable).
    ALERT_POLL_SECONDS: int = 8          # how often the worker polls the feed
    ALERT_COOLDOWN_MINUTES: int = 30     # per (subscriber, token) re-alert cooldown
    # Copy-trade. The watcher must read OUR node directly (sequencer-feed fed, ~1s
    # fresh) — never the index — so a copy fires ~1-2s behind the leader.
    COPY_NODE_RPC: str = "http://rh-nitro:8547"
    COPY_POLL_MS: int = 400
    COPY_SLIPPAGE_PCT: float = 15.0
    COPY_ETH_USD_FALLBACK: float = 2500.0   # daily-cap accounting when no live price
    # Must match the auth-service APP_DOMAIN in prod (empty in dev). The bot
    # builds the SIWS/SIWE sign-in message with this as the first-line domain.
    OPRAI_TG_APP_DOMAIN: str = ""

    # ── Robinhood Chain RPC (read-only balances) ─────────────────────────────
    # The gateway's /rpc proxy is browser-origin-gated, so the bot (a trusted
    # backend) reads balances straight from the Robinhood Chain RPC — in prod
    # OUR self-hosted Nitro full node (set ROBINHOOD_RPC in prod .env), so a
    # balance is the current on-chain state with no indexer lag. Public default
    # for dev. Actions still go through the gateway with the on-behalf JWT.
    ROBINHOOD_RPC: str = "https://rpc.mainnet.chain.robinhood.com"
    # Optional explicit override (e.g. an internal node URL).
    OPRAI_TG_RPC_OVERRIDE: str = ""
    # Where to go when our own node isn't answering. It is a full node doing
    # real work: it prunes, it re-syncs, it traverses its trie database for an
    # hour at a time, and it does not serve RPC while it does. Without a
    # fallback the bot goes blind for that hour — balances, deposits, copy
    # trades, everything — for a reason that has nothing to do with the chain.
    OPRAI_TG_RPC_FALLBACK: str = "https://rpc.mainnet.chain.robinhood.com"

    def robinhood_rpc(self) -> str:
        return self.OPRAI_TG_RPC_OVERRIDE or self.ROBINHOOD_RPC

    # ── Database (tg_schema) ─────────────────────────────────────────────────
    # Plain asyncpg DSN (NOT the SQLAlchemy "+asyncpg" form). _pg_dsn() strips
    # a "+asyncpg" suffix if one is inherited from a shared DATABASE_URL.
    DATABASE_URL: str = "postgresql://postgres:@localhost:5433/oprai"
    DB_SCHEMA: str = "tg_schema"

    # ── Vault (used by the signer; referenced here only for health/wiring) ────
    VAULT_ADDR: str = ""
    VAULT_TOKEN: str = ""

    # ── Wallets ──────────────────────────────────────────────────────────────
    OPRAI_TG_DEV_WALLET: str = "0xb0E580Cf95E2B045b99b31ddF3137D3D88d55b8E"

    # Free conversation allowance, refilled on a rolling window. A group gets
    # more than one person because the quota is shared by the whole room.
    #
    # Sized from what people actually do, not from a guess: across 110 active
    # wallet-days the median day is 3 questions and the 90th percentile is 21.
    # Ten a day leaves nine in ten days entirely free while bounding the tail —
    # the old 25 was never reached by anyone, so the paywall did not exist.
    OPRAI_TG_FREE_USER_CREDITS: int = 10
    OPRAI_TG_FREE_GROUP_CREDITS: int = 25
    OPRAI_TG_FREE_WINDOW_HOURS: int = 24

    # $OPRAI on Robinhood Chain. Verified on chain: symbol OPRAI, 18 decimals.
    # Not a payment asset — subscriptions are paid in ETH and the accumulated
    # ETH is what buys $OPRAI back.
    OPRAI_TG_TOKEN_ADDRESS: str = "0xd98e1e5a25702930b2fc92c15f3fef6d2987b5ac"

    # USDG on Robinhood Chain — the anchor the live ETH price is read from.
    # Its deepest pool holds $8.1M against $OPRAI's $29k, and a subscription
    # must stay sellable even if our own token's pool thins out.
    OPRAI_TG_STABLE_ADDRESS: str = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"

    # ── Subscription ─────────────────────────────────────────────────────────
    # Priced in DOLLARS, paid in ETH at the live rate. Revenue accumulates in
    # its own wallet so that "what came in" and "what was spent buying back
    # $OPRAI" are two numbers anyone can read off the chain — which is not
    # true of a wallet that also pays for everything else.
    OPRAI_TG_SUB_PRICE_USD: float = 9.99
    OPRAI_TG_SUB_DAYS: int = 30
    # A subscriber's daily ceiling. Not a budget — a runaway loop stopper. The
    # busiest day any real wallet has ever had is 60 questions.
    OPRAI_TG_SUB_DAILY_CREDITS: int = 200
    # And the ceiling that actually bounds the bill. 200/day is 6,000 a month;
    # at what a question costs that is hundreds of dollars against a $9.99
    # subscription. The busiest month any real wallet has had is 213.
    OPRAI_TG_SUB_MONTHLY_CREDITS: int = 1_000
    # Falls back to the dev wallet until a dedicated address is set.
    OPRAI_TG_TREASURY_WALLET: str = ""

    def treasury_wallet(self) -> str:
        return self.OPRAI_TG_TREASURY_WALLET or self.OPRAI_TG_DEV_WALLET

    # ── Server ───────────────────────────────────────────────────────────────
    PORT: int = 3055
    BIND_HOST: str = "127.0.0.1"
    LOG_LEVEL: str = "INFO"

    def pg_dsn(self) -> str:
        """asyncpg-compatible DSN (strip any SQLAlchemy driver suffix)."""
        return self.DATABASE_URL.replace("+asyncpg", "", 1)


settings = Settings()

-- Telegram Bot Schema DDL
-- Idempotent: uses IF NOT EXISTS.
-- Run via: make migrate  (or psql -f this file)
--
-- The bot holds NO private keys. tg_wallets stores only the wallet ADDRESS and
-- an opaque handle (enc_key_ref) that the isolated Rust signer resolves against
-- Vault-Transit-encrypted key material. Nothing here is a secret on its own.

CREATE SCHEMA IF NOT EXISTS tg_schema;

SET search_path TO tg_schema, public;

-- ── Identity ────────────────────────────────────────────────────────────────
-- One row per Telegram user. linked_account_id binds to the shared multichain
-- account (auth_schema.linked_identities model) once the user links via the
-- deep-link flow (0.7); NULL until then.
CREATE TABLE IF NOT EXISTS tg_users (
    telegram_id       BIGINT PRIMARY KEY,
    linked_account_id UUID,
    username          TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Custodial wallets ───────────────────────────────────────────────────────
-- chain: 'solana' | 'evm'. address is the public address; enc_key_ref is the
-- signer's handle to the encrypted private key (the ciphertext itself lives on
-- the signer side / Vault, never here).
CREATE TABLE IF NOT EXISTS tg_wallets (
    id            BIGSERIAL PRIMARY KEY,
    telegram_id   BIGINT NOT NULL REFERENCES tg_users(telegram_id) ON DELETE CASCADE,
    chain         TEXT   NOT NULL CHECK (chain IN ('solana', 'evm')),
    address       TEXT   NOT NULL,
    enc_key_ref   TEXT   NOT NULL,
    imported      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (telegram_id, chain)
);

-- ── Account-linking deep-link tokens ────────────────────────────────────────
-- Issued by the web app; consumed by /start <token> to bind a Telegram user to
-- an existing OPRAI account. Single-use, short-lived.
CREATE TABLE IF NOT EXISTS tg_link_tokens (
    token       TEXT PRIMARY KEY,
    account_id  UUID NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tg_link_tokens_account ON tg_link_tokens(account_id);

-- ── Audit trail ─────────────────────────────────────────────────────────────
-- Every meaningful interaction (start, wallet ops, actions) leaves a row.
CREATE TABLE IF NOT EXISTS tg_audit (
    id           BIGSERIAL PRIMARY KEY,
    telegram_id  BIGINT NOT NULL,
    kind         TEXT   NOT NULL,
    meta         JSONB  NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tg_audit_user_time ON tg_audit(telegram_id, created_at DESC);

-- ── Alpha alerts (real-time signal feed) ────────────────────────────────────
-- Wallets a user tracks — a buy by one pings the user. cursor_block is the last
-- block already alerted for THIS subscription, so polling is idempotent and
-- gap-free across bot restarts (seeded to the index tip when first added).
CREATE TABLE IF NOT EXISTS tg_tracked_wallets (
    id           BIGSERIAL PRIMARY KEY,
    telegram_id  BIGINT NOT NULL REFERENCES tg_users(telegram_id) ON DELETE CASCADE,
    address      TEXT   NOT NULL,
    label        TEXT,
    cursor_block BIGINT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (telegram_id, address)
);

CREATE INDEX IF NOT EXISTS idx_tg_tracked_user ON tg_tracked_wallets(telegram_id);

-- Per-user smart-money DISCOVERY feed preference (one row per user).
CREATE TABLE IF NOT EXISTS tg_alert_subs (
    telegram_id   BIGINT PRIMARY KEY REFERENCES tg_users(telegram_id) ON DELETE CASCADE,
    smart_alerts  BOOLEAN NOT NULL DEFAULT FALSE,
    min_smart     INT     NOT NULL DEFAULT 3,
    new_only      BOOLEAN NOT NULL DEFAULT FALSE,
    cursor_block  BIGINT  NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Dedup / cooldown: what (user, token, kind) we've already pinged and when, so a
-- token that keeps getting bought doesn't spam the same user.
CREATE TABLE IF NOT EXISTS tg_alert_sent (
    telegram_id  BIGINT NOT NULL,
    token        TEXT   NOT NULL,
    kind         TEXT   NOT NULL,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (telegram_id, token, kind)
);

-- ── Copy-trade ──────────────────────────────────────────────────────────────
-- Per-user, per-leader copy configuration. Auto-execution moves real money, so
-- every row carries its own risk limits (sizing, per-trade clamp, daily USD cap).
CREATE TABLE IF NOT EXISTS tg_copy_subs (
    id                 BIGSERIAL PRIMARY KEY,
    telegram_id        BIGINT NOT NULL REFERENCES tg_users(telegram_id) ON DELETE CASCADE,
    leader             TEXT   NOT NULL,
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    mode               TEXT    NOT NULL DEFAULT 'fixed' CHECK (mode IN ('fixed','proportional')),
    amount_eth         NUMERIC NOT NULL DEFAULT 0.01,
    max_per_trade_eth  NUMERIC NOT NULL DEFAULT 0.05,
    min_per_trade_eth  NUMERIC NOT NULL DEFAULT 0.001,
    daily_cap_usd      NUMERIC NOT NULL DEFAULT 100,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (telegram_id, leader)
);

CREATE INDEX IF NOT EXISTS idx_tg_copy_leader ON tg_copy_subs(leader) WHERE enabled;

-- Every copy fill (or failed attempt) — the daily-cap ledger and the receipt trail.
CREATE TABLE IF NOT EXISTS tg_copy_fills (
    id           BIGSERIAL PRIMARY KEY,
    telegram_id  BIGINT NOT NULL,
    leader       TEXT   NOT NULL,
    token        TEXT   NOT NULL,
    amount_eth   NUMERIC NOT NULL,
    usd          NUMERIC NOT NULL DEFAULT 0,
    leader_tx    TEXT,
    our_tx       TEXT,
    status       TEXT   NOT NULL DEFAULT 'sent',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tg_copy_fills_user_day ON tg_copy_fills(telegram_id, created_at DESC);
-- ── Token registry (Robinhood Chain) ────────────────────────────────────────
-- Symbol/name -> ERC-20 contract, so "send 5 NVDA" resolves without the user
-- pasting an address. Seeded from Robinhood's official stock-token registry
-- (api.robinhood.com/rhj/assets, chainId 4663) plus well-known base assets;
-- `decimals` is read from the chain, never assumed.
CREATE TABLE IF NOT EXISTS tg_token_registry (
    address     TEXT PRIMARY KEY,          -- EIP-55 checksummed
    symbol      TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    decimals    INT  NOT NULL,
    is_stock    BOOLEAN NOT NULL DEFAULT FALSE,
    source      TEXT NOT NULL DEFAULT 'robinhood',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tg_token_symbol ON tg_token_registry (upper(symbol));
CREATE INDEX IF NOT EXISTS idx_tg_token_name   ON tg_token_registry (lower(name));

-- ── Incoming deposits ───────────────────────────────────────────────────────
-- We run the chain's node, so money arriving should be noticed without the user
-- asking. Native ETH is caught by watching each wallet's balance (one batched
-- read per cycle); ERC-20 arrivals are caught from Transfer logs (one filtered
-- getLogs per cycle), so cost is constant in the number of users.
CREATE TABLE IF NOT EXISTS tg_balance_watch (
    telegram_id BIGINT PRIMARY KEY REFERENCES tg_users(telegram_id) ON DELETE CASCADE,
    wei         NUMERIC NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Last block already scanned for token transfers, so a restart neither
-- re-announces nor skips.
CREATE TABLE IF NOT EXISTS tg_deposit_cursor (
    id          INT PRIMARY KEY DEFAULT 1,
    last_block  BIGINT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tg_deposit_cursor_single CHECK (id = 1)
);

-- Announced token deposits, so a re-scan can't double-notify.
CREATE TABLE IF NOT EXISTS tg_deposit_seen (
    tx_hash     TEXT   NOT NULL,
    log_index   INT    NOT NULL,
    telegram_id BIGINT NOT NULL,
    seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tx_hash, log_index)
);

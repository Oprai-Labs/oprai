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

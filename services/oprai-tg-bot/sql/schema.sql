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

-- ── Conversation credits ────────────────────────────────────────────────────
-- Credits meter the MODEL only — asking OPRAI a question, an analysis, a
-- strategy. On-chain actions are never metered here: they already pay OPRAI's
-- swap/trade commission, and charging twice for one intent would be a way to
-- make people avoid the assistant that makes the product worth using.
--
-- A balance is per *scope*: a private chat is its own scope, and a group is a
-- single shared scope, because in a group the quota belongs to the room rather
-- than to whoever happens to speak. Both live in one table so a group top-up
-- and a personal top-up can never disagree about who was charged.
CREATE TABLE IF NOT EXISTS tg_credits (
    scope_id   BIGINT PRIMARY KEY,   -- telegram chat id: user id, or negative group id
    is_group   BOOLEAN NOT NULL DEFAULT FALSE,
    -- Free and purchased credits are counted apart on purpose. If they shared
    -- one number, an idle scope would carry its unused free allowance into the
    -- next window and accumulate for ever; free credits are a per-window
    -- allowance, purchased credits are property and never expire.
    free_used  BIGINT  NOT NULL DEFAULT 0,   -- spent from THIS window's allowance
    paid       BIGINT  NOT NULL DEFAULT 0,   -- purchased / topped up, carries over
    window_start TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every grant and every spend, so a disputed balance can be reconstructed
-- rather than argued about. reason: 'free_window' | 'topup' | 'spend' | 'admin'.
CREATE TABLE IF NOT EXISTS tg_credit_ledger (
    id          BIGSERIAL PRIMARY KEY,
    scope_id    BIGINT NOT NULL,
    telegram_id BIGINT,               -- who caused it (NULL for automatic grants)
    delta       BIGINT NOT NULL,      -- + granted, - spent
    reason      TEXT   NOT NULL,
    detail      JSONB  NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tg_credit_ledger_scope
    ON tg_credit_ledger (scope_id, created_at DESC);

-- ── Conversation threads ────────────────────────────────────────────────────
-- One OPRAI chat session per Telegram scope, so follow-up questions keep their
-- context ("and what about TSLA?") instead of starting cold every message.
CREATE TABLE IF NOT EXISTS tg_chat_sessions (
    scope_id     BIGINT PRIMARY KEY,
    telegram_id  BIGINT NOT NULL,     -- the session's owner (wallet + JWT used)
    session_id   TEXT   NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A top-up is paid on-chain, and the same transaction must never be credited
-- twice — not by a retry, not by a restart, not by someone sending the hash
-- again. The chain's own transaction hash is the key, so the guarantee comes
-- from the database rather than from the code remembering.
CREATE TABLE IF NOT EXISTS tg_topups (
    tx_hash     TEXT   PRIMARY KEY,
    scope_id    BIGINT NOT NULL,
    telegram_id BIGINT NOT NULL,
    oprai_wei   NUMERIC NOT NULL,
    credits     BIGINT  NOT NULL,
    -- 'pending' until the transfer has a receipt. A payment that is sent but
    -- not yet mined must not be lost (the user paid) and must not be credited
    -- (it may still revert), so it waits here and a reconciler settles it.
    status      TEXT   NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'credited', 'failed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tg_topups_pending
    ON tg_topups (created_at) WHERE status = 'pending';

-- Which tokens a wallet has actually touched.
--
-- Listing holdings by reading every registered token was ~200 contract calls
-- per /portfolio — slow enough to time out and enough to be rate-limited. The
-- deposit watcher already sees every ERC-20 arrival (a swap's output included,
-- since it lands as a Transfer to the wallet), so it records the token here and
-- the portfolio reads only this handful.
CREATE TABLE IF NOT EXISTS tg_wallet_tokens (
    telegram_id BIGINT NOT NULL REFERENCES tg_users(telegram_id) ON DELETE CASCADE,
    address     TEXT   NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (telegram_id, address)
);

-- A Telegram handle belongs to one account at a time. People rename, and the
-- freed handle can be taken by someone else — so without this, two rows end up
-- claiming the same handle and `/send … @name` resolves to whichever comes back
-- first, which is how money reaches the person who used to own the name.
-- Application code releases the handle from the old row; this makes the
-- database refuse to hold two.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tg_users_username_unique
    ON tg_users (lower(username)) WHERE username IS NOT NULL;

-- ── Claimable transfers ─────────────────────────────────────────────────────
-- Sending to a @handle that has never used the bot used to be a dead end: the
-- recipient has no wallet, and Telegram will not let a bot message someone
-- first, so we cannot even tell them. Instead the sender's intent is recorded
-- and the sender forwards a link; the transfer runs when the recipient opens
-- it and a wallet exists to receive it.
--
-- Nothing is escrowed. The funds stay in the sender's wallet until the claim,
-- which means a claim can fail because they were spent — honest and
-- recoverable, and far better than a pooled custody wallet nobody asked for.
-- The claim is bound to the HANDLE, not to whoever holds the link, so it
-- delivers to the person the sender named.
CREATE TABLE IF NOT EXISTS tg_claims (
    token            TEXT PRIMARY KEY,
    from_telegram_id BIGINT NOT NULL REFERENCES tg_users(telegram_id) ON DELETE CASCADE,
    to_username      TEXT   NOT NULL,
    token_address    TEXT,                -- NULL means native ETH
    symbol           TEXT   NOT NULL,
    amount_base      NUMERIC NOT NULL,
    decimals         INT    NOT NULL,
    status           TEXT   NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'claimed', 'expired', 'failed')),
    tx_hash          TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tg_claims_pending
    ON tg_claims (lower(to_username)) WHERE status = 'pending';

-- ── Wallet lifecycle ────────────────────────────────────────────────────────
-- Creating a fresh wallet must never strand the old one. `UNIQUE (telegram_id,
-- chain)` allowed exactly one, so a new wallet could only exist by replacing —
-- and whatever the old address still held would be unreachable, since its key
-- lives nowhere but that row. Archived wallets stay here, still exportable.
ALTER TABLE tg_wallets ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE tg_wallets DROP CONSTRAINT IF EXISTS tg_wallets_telegram_id_chain_key;

-- One ACTIVE wallet per chain; any number of archived ones behind it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tg_wallets_active
    ON tg_wallets (telegram_id, chain) WHERE archived_at IS NULL;

-- The balance watch belongs to an ADDRESS, not a person.
--
-- Keyed by telegram_id it broke the moment someone could have more than one
-- wallet: the watcher saw every row (archived included), so one cycle wrote
-- the active wallet's balance and the next wrote the archived wallet's zero —
-- and the cycle after that read that zero as a fresh deposit. The same amount
-- was announced every few seconds, for ever. Switching wallets had the same
-- shape: the new address's balance compared against the old one's.
DROP TABLE IF EXISTS tg_balance_watch;

CREATE TABLE IF NOT EXISTS tg_balance_watch (
    address     TEXT   PRIMARY KEY,
    telegram_id BIGINT NOT NULL REFERENCES tg_users(telegram_id) ON DELETE CASCADE,
    wei         NUMERIC NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A wallet's number is its creation order, and its name is the user's.
--
-- The list was sorted active-first, so switching wallets renumbered them:
-- W1 became W2 and back again. A label that moves is worse than no label —
-- it is the one thing someone uses to tell two addresses apart.
ALTER TABLE tg_wallets ADD COLUMN IF NOT EXISTS label TEXT;

-- Turns that were in flight when the process stopped.
--
-- A restart — a deploy, a crash — leaves the "Thinking…" placeholder on screen
-- for ever: the task that would have answered it is gone, and so is the task
-- that would have reported the failure. The row is written when the wait
-- starts and deleted when it ends, so whatever is still here on boot is
-- something a person is still staring at.
CREATE TABLE IF NOT EXISTS tg_inflight (
    chat_id     BIGINT NOT NULL,
    message_id  BIGINT NOT NULL,
    telegram_id BIGINT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, message_id)
);

-- What a top-up was actually paid in, and what it was sold for.
--
-- Payments started in $OPRAI and are now taken in ETH, so an amount alone no
-- longer says what was received: 0.004 of one asset is not 0.004 of another.
-- The USD figure is the price the pack was sold at, frozen at the moment of
-- sale — a later move in either asset must not rewrite what someone paid.
ALTER TABLE tg_topups ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'OPRAI';
ALTER TABLE tg_topups ADD COLUMN IF NOT EXISTS usd NUMERIC;
ALTER TABLE tg_topups ADD COLUMN IF NOT EXISTS rate_usd NUMERIC;

-- Immutable append-only log of every state transition for each transaction.
-- Allows full reconstruction of the quote → build → submit → confirm → error flow.

CREATE TABLE IF NOT EXISTS solana_schema.transaction_events (
    id               UUID         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    transaction_id   UUID         NOT NULL REFERENCES solana_schema.transactions(id) ON DELETE CASCADE,
    event_type       VARCHAR(32)  NOT NULL,          -- created|built|submitted|confirmed|failed|cancelled|timeout
    from_status      VARCHAR(20),                    -- previous status (NULL for initial creation)
    to_status        VARCHAR(20)  NOT NULL,           -- new status after this event
    tx_hash          TEXT,
    slot             BIGINT,
    block_height     BIGINT,
    error_code       VARCHAR(64),
    error_message    TEXT,
    error_category   VARCHAR(32),                    -- network|rpc|validation|insufficient_funds|slippage|simulation|timeout|unknown
    metadata         JSONB        NOT NULL DEFAULT '{}',
    duration_ms      INTEGER,                        -- wall-clock time of this step in milliseconds
    ip_address       VARCHAR(45),
    user_agent       TEXT,
    request_id       VARCHAR(64),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tx_events_transaction_id
    ON solana_schema.transaction_events (transaction_id, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_tx_events_event_type
    ON solana_schema.transaction_events (event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tx_events_to_status
    ON solana_schema.transaction_events (to_status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tx_events_failures
    ON solana_schema.transaction_events (error_category, created_at DESC)
    WHERE error_category IS NOT NULL;

-- Immutability: audit rows cannot be modified or deleted after insertion.
CREATE OR REPLACE FUNCTION solana_schema.transaction_events_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'transaction_events is immutable — rows cannot be modified or deleted (event_type: %)',
        COALESCE(OLD.event_type, '?');
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tx_events_immutable ON solana_schema.transaction_events;
CREATE TRIGGER trg_tx_events_immutable
    BEFORE UPDATE OR DELETE ON solana_schema.transaction_events
    FOR EACH ROW
    EXECUTE FUNCTION solana_schema.transaction_events_immutable();

// Diesel table definition matching the existing solana_schema.transactions table.
//
// Column types mirror the PostgreSQL migration in
//   services/solana-service/migrations/002_create_transactions.ts

diesel::table! {
    /// The `solana_schema.transaction_events` append-only audit table.
    solana_schema.transaction_events (id) {
        id             -> Uuid,
        transaction_id -> Uuid,
        event_type     -> Varchar,
        from_status    -> Nullable<Varchar>,
        to_status      -> Varchar,
        tx_hash        -> Nullable<Text>,
        slot           -> Nullable<Int8>,
        block_height   -> Nullable<Int8>,
        error_code     -> Nullable<Varchar>,
        error_message  -> Nullable<Text>,
        error_category -> Nullable<Varchar>,
        metadata       -> Jsonb,
        duration_ms    -> Nullable<Int4>,
        ip_address     -> Nullable<Varchar>,
        user_agent     -> Nullable<Text>,
        request_id     -> Nullable<Varchar>,
        created_at     -> Timestamptz,
    }
}

diesel::table! {
    /// The `solana_schema.transactions` table.
    solana_schema.transactions (id) {
        id            -> Uuid,
        user_id       -> Uuid,
        user_wallet   -> Varchar,
        chat_session_id -> Nullable<Uuid>,
        chat_message_id -> Nullable<Uuid>,
        tx_hash       -> Nullable<Varchar>,
        chain         -> Varchar,
        status        -> Varchar,
        action        -> Varchar,
        protocol      -> Nullable<Varchar>,
        parameters    -> Jsonb,
        estimated_fee -> Nullable<Varchar>,
        actual_fee    -> Nullable<Varchar>,
        error_message -> Nullable<Text>,
        initiated_at  -> Nullable<Timestamptz>,
        submitted_at  -> Nullable<Timestamptz>,
        confirmed_at  -> Nullable<Timestamptz>,
        created_at    -> Timestamptz,
        updated_at    -> Timestamptz,
    }
}

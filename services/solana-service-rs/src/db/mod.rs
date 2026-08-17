pub mod connection;
pub mod economics;
pub mod models;
pub mod schema;
pub mod tx_events;

use diesel_async::SimpleAsyncConnection;

use connection::DbPool;

/// Run the solana_schema DDL migration on startup.
///
/// Uses `batch_execute` (via `SimpleAsyncConnection`) so the multi-statement
/// migration file is executed correctly — a single `sql_query` call only
/// handles one statement.  All statements use IF NOT EXISTS so this is safe
/// to call on every boot.
pub async fn run_migrations(pool: &DbPool) -> Result<(), Box<dyn std::error::Error>> {
    const MIGRATION_001: &str = include_str!("../../migrations/001_create_schema.sql");
    const MIGRATION_002: &str = include_str!("../../migrations/002_create_transaction_events.sql");
    const MIGRATION_003: &str = include_str!("../../migrations/003_create_tx_economics.sql");
    const MIGRATION_004: &str = include_str!("../../migrations/004_add_cashback.sql");

    let mut conn = pool.get().await?;
    conn.batch_execute(MIGRATION_001).await?;
    conn.batch_execute(MIGRATION_002).await?;
    conn.batch_execute(MIGRATION_003).await?;
    conn.batch_execute(MIGRATION_004).await?;

    tracing::info!("solana_schema migrations applied (001 + 002 + 003 + 004)");
    Ok(())
}

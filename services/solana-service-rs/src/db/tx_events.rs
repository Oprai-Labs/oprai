/// Fire-and-forget helper that inserts a `NewTransactionEvent` row.
///
/// Errors are logged at ERROR level and silently swallowed — audit failures
/// must never surface to the caller or interrupt the operation being audited.
use diesel_async::RunQueryDsl;

use crate::db::connection::DbPool;
use crate::db::models::NewTransactionEvent;
use crate::db::schema::transaction_events;

pub async fn log_event(pool: &DbPool, evt: NewTransactionEvent) {
    match pool.get().await {
        Err(e) => {
            tracing::error!(
                error = %e,
                event_type = %evt.event_type,
                "audit: failed to acquire DB connection for transaction_event"
            );
        }
        Ok(mut conn) => {
            if let Err(e) = diesel::insert_into(transaction_events::table)
                .values(&evt)
                .execute(&mut conn)
                .await
            {
                tracing::error!(
                    error = %e,
                    event_type = %evt.event_type,
                    transaction_id = %evt.transaction_id,
                    "audit: failed to insert transaction_event"
                );
            }
        }
    }
}

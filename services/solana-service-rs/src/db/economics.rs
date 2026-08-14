//! Per-transaction economics ledger (fee + volume).
//!
//! Writes `solana_schema.tx_economics` and, on confirmation, the wallet + daily
//! rollups. The OPRAI commission (`fee_bps`) is ALWAYS recomputed server-side
//! (`services::fees::swap_fee_bps`) — never taken from client-reported values —
//! so a client cannot understate what it owed. All calls are fire-and-forget:
//! a ledger failure must never break the underlying transaction flow.

use diesel::sql_types::{Double, Integer, Nullable, Text};
use diesel_async::RunQueryDsl;
use uuid::Uuid;

use crate::db::connection::DbPool;

/// Insert the `pending` economics row at record time (POST /transactions).
#[allow(clippy::too_many_arguments)]
pub async fn record_pending(
    pool: &DbPool,
    transaction_id: Uuid,
    user_wallet: String,
    protocol: Option<String>,
    action: String,
    input_mint: Option<String>,
    output_mint: Option<String>,
    input_amount: Option<String>,
    output_amount: Option<String>,
    notional_usd: Option<f64>,
    fee_bps: i32,
    fee_mint: Option<String>,
    fee_usd: Option<f64>,
    usd_price_source: Option<String>,
) {
    let mut conn = match pool.get().await {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!(error = %e, "tx_economics: pool.get failed (record_pending)");
            return;
        }
    };

    const SQL: &str = r#"
        INSERT INTO solana_schema.tx_economics
          (transaction_id, user_wallet, protocol, action, input_mint, output_mint,
           input_amount, output_amount, notional_usd, fee_bps, fee_mint, fee_usd,
           usd_price_source, outcome)
        VALUES ($1::uuid, $2, $3, $4, $5, $6,
                NULLIF($7,'')::numeric, NULLIF($8,'')::numeric,
                $9::numeric, $10, $11, $12::numeric, $13, 'pending')
        ON CONFLICT (transaction_id) DO NOTHING
    "#;

    let res = diesel::sql_query(SQL)
        .bind::<Text, _>(transaction_id.to_string())
        .bind::<Text, _>(user_wallet)
        .bind::<Nullable<Text>, _>(protocol)
        .bind::<Text, _>(action)
        .bind::<Nullable<Text>, _>(input_mint)
        .bind::<Nullable<Text>, _>(output_mint)
        .bind::<Text, _>(input_amount.unwrap_or_default())
        .bind::<Text, _>(output_amount.unwrap_or_default())
        .bind::<Nullable<Double>, _>(notional_usd)
        .bind::<Integer, _>(fee_bps)
        .bind::<Nullable<Text>, _>(fee_mint)
        .bind::<Nullable<Double>, _>(fee_usd)
        .bind::<Nullable<Text>, _>(usd_price_source)
        .execute(&mut conn)
        .await;

    if let Err(e) = res {
        tracing::warn!(error = %e, tx = %transaction_id, "tx_economics: record_pending failed");
    }
}

/// Finalize a confirmed transaction: flip `pending`→`confirmed` (idempotent) and,
/// only if this call performed the transition, fold it into the wallet + daily rollups.
pub async fn finalize_confirmed(pool: &DbPool, transaction_id: Uuid, tx_signature: Option<String>) {
    let mut conn = match pool.get().await {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!(error = %e, "tx_economics: pool.get failed (finalize_confirmed)");
            return;
        }
    };

    // Guard: only transition rows not already confirmed (prevents double-counting on retries).
    let affected = diesel::sql_query(
        r#"UPDATE solana_schema.tx_economics
           SET outcome = 'confirmed', tx_signature = $2, confirmed_at = now()
           WHERE transaction_id = $1::uuid AND outcome <> 'confirmed'"#,
    )
    .bind::<Text, _>(transaction_id.to_string())
    .bind::<Nullable<Text>, _>(tx_signature)
    .execute(&mut conn)
    .await;

    match affected {
        Ok(0) => return, // already confirmed, or no ledger row — nothing to roll up
        Ok(_) => {}
        Err(e) => {
            tracing::warn!(error = %e, tx = %transaction_id, "tx_economics: confirm update failed");
            return;
        }
    }

    // Wallet cumulative rollup (source of truth for tiers / points / top-wallets).
    if let Err(e) = diesel::sql_query(
        r#"INSERT INTO solana_schema.wallet_economics_rollup
             (user_wallet, lifetime_notional_usd, lifetime_fee_usd, confirmed_tx_count,
              first_tx_at, last_tx_at, updated_at)
           SELECT user_wallet, COALESCE(notional_usd,0), COALESCE(fee_usd,0), 1, now(), now(), now()
           FROM solana_schema.tx_economics WHERE transaction_id = $1::uuid
           ON CONFLICT (user_wallet) DO UPDATE SET
             lifetime_notional_usd = wallet_economics_rollup.lifetime_notional_usd + EXCLUDED.lifetime_notional_usd,
             lifetime_fee_usd      = wallet_economics_rollup.lifetime_fee_usd      + EXCLUDED.lifetime_fee_usd,
             confirmed_tx_count    = wallet_economics_rollup.confirmed_tx_count    + 1,
             last_tx_at = now(), updated_at = now()"#,
    )
    .bind::<Text, _>(transaction_id.to_string())
    .execute(&mut conn)
    .await
    {
        tracing::warn!(error = %e, tx = %transaction_id, "tx_economics: wallet rollup failed");
    }

    // Daily per-protocol rollup (revenue / volume trends).
    if let Err(e) = diesel::sql_query(
        r#"INSERT INTO solana_schema.daily_economics_rollup
             (stat_date, protocol, volume_usd, fee_usd, tx_count, updated_at)
           SELECT (now() AT TIME ZONE 'UTC')::date, COALESCE(protocol,'unknown'),
                  COALESCE(notional_usd,0), COALESCE(fee_usd,0), 1, now()
           FROM solana_schema.tx_economics WHERE transaction_id = $1::uuid
           ON CONFLICT (stat_date, protocol) DO UPDATE SET
             volume_usd = daily_economics_rollup.volume_usd + EXCLUDED.volume_usd,
             fee_usd    = daily_economics_rollup.fee_usd    + EXCLUDED.fee_usd,
             tx_count   = daily_economics_rollup.tx_count   + 1,
             updated_at = now()"#,
    )
    .bind::<Text, _>(transaction_id.to_string())
    .execute(&mut conn)
    .await
    {
        tracing::warn!(error = %e, tx = %transaction_id, "tx_economics: daily rollup failed");
    }
}

/// Finalize a failed/cancelled transaction — marks the ledger row (kept as
/// "attempted" for funnel/conversion) without touching the confirmed rollups.
pub async fn finalize_other(pool: &DbPool, transaction_id: Uuid, outcome: &str) {
    let mut conn = match pool.get().await {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!(error = %e, "tx_economics: pool.get failed (finalize_other)");
            return;
        }
    };

    if let Err(e) = diesel::sql_query(
        r#"UPDATE solana_schema.tx_economics
           SET outcome = $2, confirmed_at = now()
           WHERE transaction_id = $1::uuid AND outcome = 'pending'"#,
    )
    .bind::<Text, _>(transaction_id.to_string())
    .bind::<Text, _>(outcome.to_string())
    .execute(&mut conn)
    .await
    {
        tracing::warn!(error = %e, tx = %transaction_id, "tx_economics: finalize_other failed");
    }
}

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
    chain: String,
    account_id: Option<String>,
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
           usd_price_source, chain, account_id, outcome)
        VALUES ($1::uuid, $2, $3, $4, $5, $6,
                NULLIF($7,'')::numeric, NULLIF($8,'')::numeric,
                $9::numeric, $10, $11, $12::numeric, $13, $14, $15, 'pending')
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
        .bind::<Text, _>(chain)
        .bind::<Nullable<Text>, _>(account_id)
        .execute(&mut conn)
        .await;

    if let Err(e) = res {
        tracing::warn!(error = %e, tx = %transaction_id, "tx_economics: record_pending failed");
    }
}

/// Finalize a confirmed transaction: flip `pending`→`confirmed` (idempotent) and,
/// only if this call performed the transition, fold it into the wallet + daily rollups.
///
/// The volume (`notional_usd`) is recomputed here from the *confirmed on-chain
/// transaction* — never from anything the client reported — because it feeds the
/// tier / points / fee-discount system, which must not be forgeable. See
/// [`crate::services::onchain_value`]. If the chain can't be read the row keeps
/// its `NULL` notional and rolls up as zero: undercount, never inflate.
pub async fn finalize_confirmed(
    pool: &DbPool,
    http: &reqwest::Client,
    transaction_id: Uuid,
    tx_signature: Option<String>,
    wallet: String,
) {
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
    .bind::<Nullable<Text>, _>(tx_signature.clone())
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

    // The account that owns this wallet — cashback tier is computed from the
    // account's POOLED volume across all its wallets/chains (the "volume is
    // common to all chains" rule), read from within solana_schema.
    let account_id = account_id_for_tx(pool, transaction_id).await;

    // Server-authoritative volume AND fee: read both off the chain and overwrite
    // the row before the rollups see it. Only for rows that carry a signature.
    //
    // The fee has to be read, not computed. On the paths where the commission is
    // a plain transfer instruction, a user signing their own transaction can drop
    // it and the trade still settles. Deriving fee_usd from the rate we intended
    // booked revenue that never arrived — and, because cashback is a percentage
    // of that number, paid real SOL out against it.
    if let Some(sig) = tx_signature.as_deref() {
        if let Some(v) =
            crate::services::onchain_value::confirmed_trade_value(http, sig, &wallet).await
        {
            // Cashback earned on this trade = tier % of the fee actually paid.
            // The tier comes from the account's pooled volume BEFORE this tx is
            // rolled up, i.e. the tier it held while trading.
            let cashback_pct = crate::services::fees::cashback_pct_for_volume(
                account_tier_volume_usd(pool, account_id.as_deref(), &wallet).await,
            ) as f64;

            // `None` = no fee wallet configured, so nothing was meant to be
            // charged; fall back to the declared rate rather than recording a
            // shortfall that does not exist.
            let (fee_usd, observed) = match v.fee_paid_usd {
                Some(paid) => (paid, true),
                None => (f64::NAN, false),
            };

            let res = if observed {
                diesel::sql_query(
                    r#"UPDATE solana_schema.tx_economics
                       SET notional_usd = $2::numeric,
                           fee_usd = $4::numeric,
                           cashback_usd = $4::numeric * $3::numeric / 100.0,
                           usd_price_source = 'onchain'
                       WHERE transaction_id = $1::uuid"#,
                )
                .bind::<Text, _>(transaction_id.to_string())
                .bind::<Double, _>(v.notional_usd)
                .bind::<Double, _>(cashback_pct)
                .bind::<Double, _>(fee_usd)
                .execute(&mut conn)
                .await
            } else {
                diesel::sql_query(
                    r#"UPDATE solana_schema.tx_economics
                       SET notional_usd = $2::numeric,
                           fee_usd = ($2::numeric * fee_bps / 10000.0),
                           cashback_usd = ($2::numeric * fee_bps / 10000.0) * $3::numeric / 100.0,
                           usd_price_source = 'onchain'
                       WHERE transaction_id = $1::uuid"#,
                )
                .bind::<Text, _>(transaction_id.to_string())
                .bind::<Double, _>(v.notional_usd)
                .bind::<Double, _>(cashback_pct)
                .execute(&mut conn)
                .await
            };
            if let Err(e) = res {
                tracing::warn!(error = %e, tx = %transaction_id, "tx_economics: onchain notional update failed");
            }

            // Expected-versus-landed. Logged rather than acted on: a shortfall
            // can be a stripped instruction, but it can also be a route whose
            // fee the protocol itself takes in a form this read does not see, so
            // the first thing it needs is a rate we can look at.
            if observed {
                let expected = expected_fee_usd(pool, transaction_id, v.notional_usd).await;
                if expected > 0.0 && fee_usd < expected * 0.5 {
                    tracing::warn!(
                        tx = %transaction_id, wallet = %wallet, signature = %sig,
                        expected_usd = expected, paid_usd = fee_usd,
                        "fee_shortfall: commission that landed is under half the declared rate"
                    );
                }
            }
        }
    }

    // Wallet cumulative rollup, keyed per (wallet, chain) — one row per chain a
    // wallet trades on, so per-chain cashback views populate correctly. account_id
    // is carried so the pooled-tier query can sum a whole account in one place.
    if let Err(e) = diesel::sql_query(
        r#"INSERT INTO solana_schema.wallet_economics_rollup
             (user_wallet, chain, account_id, lifetime_notional_usd, lifetime_fee_notional_usd,
              lifetime_fee_usd, lifetime_cashback_usd, confirmed_tx_count, first_tx_at, last_tx_at, updated_at)
           SELECT user_wallet, COALESCE(chain,'solana'), account_id,
                  COALESCE(notional_usd,0),
                  CASE WHEN COALESCE(fee_usd,0) > 0 THEN COALESCE(notional_usd,0) ELSE 0 END,
                  COALESCE(fee_usd,0),
                  COALESCE(cashback_usd,0), 1, now(), now(), now()
           FROM solana_schema.tx_economics WHERE transaction_id = $1::uuid
           ON CONFLICT (user_wallet, chain) DO UPDATE SET
             lifetime_notional_usd = wallet_economics_rollup.lifetime_notional_usd + EXCLUDED.lifetime_notional_usd,
             lifetime_fee_notional_usd = wallet_economics_rollup.lifetime_fee_notional_usd + EXCLUDED.lifetime_fee_notional_usd,
             lifetime_fee_usd      = wallet_economics_rollup.lifetime_fee_usd      + EXCLUDED.lifetime_fee_usd,
             lifetime_cashback_usd = wallet_economics_rollup.lifetime_cashback_usd + EXCLUDED.lifetime_cashback_usd,
             confirmed_tx_count    = wallet_economics_rollup.confirmed_tx_count    + 1,
             account_id = COALESCE(EXCLUDED.account_id, wallet_economics_rollup.account_id),
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

/// A wallet's lifetime confirmed volume in USD (0 if it has never traded).
///
/// This is server-owned data (`solana_schema`, written only by the on-chain
/// confirm path), so it is safe to read directly and safe to trust — it is what
/// the fee-discount tier is computed from. A DB miss returns 0, i.e. no
/// discount, which is the correct fail-safe.
pub async fn wallet_volume_usd(pool: &DbPool, wallet: &str) -> f64 {
    #[derive(diesel::QueryableByName)]
    struct Row {
        #[diesel(sql_type = Double)]
        v: f64,
    }
    let mut conn = match pool.get().await {
        Ok(c) => c,
        Err(_) => return 0.0,
    };
    diesel::sql_query(
        r#"SELECT COALESCE(lifetime_notional_usd, 0)::double precision AS v
           FROM solana_schema.wallet_economics_rollup
           WHERE user_wallet = $1"#,
    )
    .bind::<Text, _>(wallet)
    .get_result::<Row>(&mut conn)
    .await
    .map(|r| r.v)
    .unwrap_or(0.0)
}

/// The account_id recorded on a tx's economics row, if any.
async fn account_id_for_tx(pool: &DbPool, transaction_id: Uuid) -> Option<String> {
    #[derive(diesel::QueryableByName)]
    struct Row {
        #[diesel(sql_type = Nullable<Text>)]
        account_id: Option<String>,
    }
    let mut conn = pool.get().await.ok()?;
    diesel::sql_query(
        r#"SELECT account_id FROM solana_schema.tx_economics WHERE transaction_id = $1::uuid"#,
    )
    .bind::<Text, _>(transaction_id.to_string())
    .get_result::<Row>(&mut conn)
    .await
    .ok()
    .and_then(|r| r.account_id)
}

/// The commission this row declared at build time, in USD, for `notional_usd`.
///
/// Read back from `fee_bps` — the rate the server chose when it built the
/// transaction — so the comparison is against what we asked for, not against
/// whatever the row now says was collected.
async fn expected_fee_usd(pool: &DbPool, transaction_id: uuid::Uuid, notional_usd: f64) -> f64 {
    #[derive(diesel::QueryableByName)]
    struct Row {
        #[diesel(sql_type = Integer)]
        fee_bps: i32,
    }
    let mut conn = match pool.get().await {
        Ok(c) => c,
        Err(_) => return 0.0,
    };
    let bps = diesel::sql_query(
        r#"SELECT COALESCE(fee_bps, 0) AS fee_bps
           FROM solana_schema.tx_economics
           WHERE transaction_id = $1::uuid"#,
    )
    .bind::<Text, _>(transaction_id.to_string())
    .get_result::<Row>(&mut conn)
    .await
    .map(|r| r.fee_bps)
    .unwrap_or(0);
    if bps <= 0 {
        return 0.0;
    }
    notional_usd * (bps as f64) / 10_000.0
}

/// The volume (USD) the cashback tier is computed from — the account's POOLED
/// lifetime volume across all its wallets and chains when an account_id is known,
/// else the single wallet's own volume. This keeps a user's tier "common to all
/// chains": trading on Base counts toward the tier that sets Solana cashback and
/// vice-versa. Stays inside solana_schema (no cross-schema read) because the
/// rollup now carries account_id.
pub async fn account_tier_volume_usd(pool: &DbPool, account_id: Option<&str>, wallet: &str) -> f64 {
    #[derive(diesel::QueryableByName)]
    struct Row {
        #[diesel(sql_type = Double)]
        v: f64,
    }
    let mut conn = match pool.get().await {
        Ok(c) => c,
        Err(_) => return 0.0,
    };
    // Tier is driven by FEE-PAYING volume only (lifetime_fee_notional_usd), pooled
    // across the account when known, else the single wallet.
    let (sql, key) = match account_id.filter(|a| !a.is_empty()) {
        Some(acct) => (
            "SELECT COALESCE(sum(lifetime_fee_notional_usd), 0)::double precision AS v \
             FROM solana_schema.wallet_economics_rollup WHERE account_id = $1",
            acct.to_string(),
        ),
        None => (
            "SELECT COALESCE(sum(lifetime_fee_notional_usd), 0)::double precision AS v \
             FROM solana_schema.wallet_economics_rollup WHERE user_wallet = $1",
            wallet.to_string(),
        ),
    };
    diesel::sql_query(sql)
        .bind::<Text, _>(key)
        .get_result::<Row>(&mut conn)
        .await
        .map(|r| r.v)
        .unwrap_or(0.0)
}

/// Record a CONFIRMED EVM (Relay) swap's economics in one authoritative call.
///
/// EVM swaps have no Solana `transactions` lifecycle (no quote→build→sign→confirm
/// through this service) and their volume can't be read from a Solana RPC, so the
/// dedicated `/actions/relay/record` handler verifies the fill with Relay, takes
/// `notional_usd` + `fee_usd` from Relay's own quote, and calls this. It inserts
/// a `transactions` row (for history), a confirmed `tx_economics` row, and folds
/// both into the wallet + daily rollups — the same rollups the Solana path writes,
/// so tiers/points/rewards see every chain uniformly.
///
/// `cashback_usd` is computed here from the account's pooled tier. Idempotent on
/// `tx_signature` (the EVM tx hash): a replayed confirm inserts nothing twice.
#[allow(clippy::too_many_arguments)]
pub async fn record_evm_confirmed(
    pool: &DbPool,
    user_wallet: String,
    account_id: Option<String>,
    chain: String,
    tx_signature: String,
    protocol: String,
    action: String,
    input_symbol: Option<String>,
    output_symbol: Option<String>,
    notional_usd: f64,
    fee_bps: i32,
    fee_usd: f64,
) -> Result<Uuid, String> {
    let mut conn = pool
        .get()
        .await
        .map_err(|e| format!("pool.get failed: {e}"))?;

    // Idempotency: if this EVM tx hash was already recorded, return its id and
    // roll up nothing more.
    #[derive(diesel::QueryableByName)]
    struct IdRow {
        #[diesel(sql_type = Text)]
        id: String,
    }
    if let Ok(existing) = diesel::sql_query(
        r#"SELECT transaction_id::text AS id FROM solana_schema.tx_economics
           WHERE tx_signature = $1 AND chain = $2 LIMIT 1"#,
    )
    .bind::<Text, _>(&tx_signature)
    .bind::<Text, _>(&chain)
    .get_result::<IdRow>(&mut conn)
    .await
    {
        if let Ok(id) = Uuid::parse_str(&existing.id) {
            return Ok(id);
        }
    }

    // Pooled-tier cashback, from the account's cross-chain volume BEFORE this tx.
    let cashback_pct = crate::services::fees::cashback_pct_for_volume(
        account_tier_volume_usd(pool, account_id.as_deref(), &user_wallet).await,
    ) as f64;
    let cashback_usd = fee_usd * cashback_pct / 100.0;

    // A transactions row for history. user_id is required (NOT NULL, UUID); the
    // owning account is a UUID, so use it, falling back to nil for a wallet with
    // no account yet.
    let user_id = account_id
        .as_deref()
        .and_then(|a| Uuid::parse_str(a).ok())
        .unwrap_or(Uuid::nil());
    let tx_id = Uuid::new_v4();
    if let Err(e) = diesel::sql_query(
        r#"INSERT INTO solana_schema.transactions
             (id, user_id, user_wallet, tx_hash, chain, status, action, protocol,
              submitted_at, confirmed_at)
           VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'confirmed', $6, $7, now(), now())"#,
    )
    .bind::<Text, _>(tx_id.to_string())
    .bind::<Text, _>(user_id.to_string())
    .bind::<Text, _>(&user_wallet)
    .bind::<Text, _>(&tx_signature)
    .bind::<Text, _>(&chain)
    .bind::<Text, _>(&action)
    .bind::<Text, _>(&protocol)
    .execute(&mut conn)
    .await
    {
        return Err(format!("transactions insert failed: {e}"));
    }

    // Confirmed economics row.
    if let Err(e) = diesel::sql_query(
        r#"INSERT INTO solana_schema.tx_economics
             (transaction_id, tx_signature, user_wallet, protocol, action,
              input_mint, output_mint, notional_usd, fee_bps, fee_usd, cashback_usd,
              usd_price_source, chain, account_id, outcome, confirmed_at)
           VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::numeric, $9, $10::numeric,
                   $11::numeric, 'relay', $12, $13, 'confirmed', now())
           ON CONFLICT (transaction_id) DO NOTHING"#,
    )
    .bind::<Text, _>(tx_id.to_string())
    .bind::<Text, _>(&tx_signature)
    .bind::<Text, _>(&user_wallet)
    .bind::<Text, _>(&protocol)
    .bind::<Text, _>(&action)
    .bind::<Nullable<Text>, _>(input_symbol)
    .bind::<Nullable<Text>, _>(output_symbol)
    .bind::<Double, _>(notional_usd)
    .bind::<Integer, _>(fee_bps)
    .bind::<Double, _>(fee_usd)
    .bind::<Double, _>(cashback_usd)
    .bind::<Text, _>(&chain)
    .bind::<Nullable<Text>, _>(account_id.clone())
    .execute(&mut conn)
    .await
    {
        return Err(format!("tx_economics insert failed: {e}"));
    }

    // Fold into the same rollups the Solana path writes (per wallet+chain, daily).
    let _ = diesel::sql_query(
        r#"INSERT INTO solana_schema.wallet_economics_rollup
             (user_wallet, chain, account_id, lifetime_notional_usd, lifetime_fee_notional_usd,
              lifetime_fee_usd, lifetime_cashback_usd, confirmed_tx_count, first_tx_at, last_tx_at, updated_at)
           SELECT user_wallet, COALESCE(chain,'solana'), account_id,
                  COALESCE(notional_usd,0),
                  CASE WHEN COALESCE(fee_usd,0) > 0 THEN COALESCE(notional_usd,0) ELSE 0 END,
                  COALESCE(fee_usd,0),
                  COALESCE(cashback_usd,0), 1, now(), now(), now()
           FROM solana_schema.tx_economics WHERE transaction_id = $1::uuid
           ON CONFLICT (user_wallet, chain) DO UPDATE SET
             lifetime_notional_usd = wallet_economics_rollup.lifetime_notional_usd + EXCLUDED.lifetime_notional_usd,
             lifetime_fee_notional_usd = wallet_economics_rollup.lifetime_fee_notional_usd + EXCLUDED.lifetime_fee_notional_usd,
             lifetime_fee_usd      = wallet_economics_rollup.lifetime_fee_usd      + EXCLUDED.lifetime_fee_usd,
             lifetime_cashback_usd = wallet_economics_rollup.lifetime_cashback_usd + EXCLUDED.lifetime_cashback_usd,
             confirmed_tx_count    = wallet_economics_rollup.confirmed_tx_count    + 1,
             account_id = COALESCE(EXCLUDED.account_id, wallet_economics_rollup.account_id),
             last_tx_at = now(), updated_at = now()"#,
    )
    .bind::<Text, _>(tx_id.to_string())
    .execute(&mut conn)
    .await
    .map_err(|e| tracing::warn!(error = %e, "evm rollup: wallet failed"));

    let _ = diesel::sql_query(
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
    .bind::<Text, _>(tx_id.to_string())
    .execute(&mut conn)
    .await
    .map_err(|e| tracing::warn!(error = %e, "evm rollup: daily failed"));

    Ok(tx_id)
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

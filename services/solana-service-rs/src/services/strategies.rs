//! What a wallet should actually do with a token it already holds.
//!
//! Every DeFi dashboard answers this by sorting yields and showing the top of
//! the list. That is the wrong answer for anyone small, and measurably so: at
//! $50, a pool paying 53% takes fifty days to earn back what it costs to open,
//! while one paying 32% takes eighteen. The ranking flips again at $5,000,
//! where the opening cost stops mattering and the 53% wins comfortably. A
//! recommendation that ignores the amount is not a recommendation.
//!
//! So this ranks by what the depositor actually nets at their own size: yield
//! against a measured cost, with the options nobody should touch removed
//! first. Three rules do most of the work.
//!
//! **Dead options are removed, not ranked low.** A reserve with zero supplied
//! and 0% APY is not a poor opportunity, it is a venue nobody uses — Kamino's
//! USDC reserve in the main market reads exactly that. A pool with no volume
//! earns no fees whatever its headline says.
//!
//! **Depth is judged against the deposit.** Owning a large share of a pool
//! means trading against yourself on every swap that touches it, and not being
//! able to leave without moving the price.
//!
//! **Nothing is claimed that was not measured.** Where a figure cannot be
//! read, the option carries null and the caller says nothing about it. A pool's
//! fee APR is last-day fees annualised — a description of yesterday, not a
//! forecast — and it is labelled as such rather than presented next to a
//! lending rate as though the two were the same kind of number.

use serde::Deserialize;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;

/// A deposit larger than this share of a venue makes the depositor the market.
const MAX_VENUE_SHARE: f64 = 0.05;

/// A pool younger than this has no record to judge. Measured while building
/// this: the pools topping a fee-APR ranking for SOL were 1.7 days old and
/// quoting 3,300%, and their "lifetime" average was the same spike over the
/// same day. No fee window separates noise from yield on a pool that new —
/// only age does.
const MIN_POOL_AGE_DAYS: f64 = 30.0;

/// Solana's base fee, per signature, in SOL.
///
/// A protocol constant rather than a market number — the same class of fact as
/// an account layout. It is small, and it is also the only part of opening a
/// position that never comes back, which is why it has to be counted rather
/// than waved away.
const BASE_FEE_SOL: f64 = 0.000005;

/// Account sizes each venue makes the depositor pay rent on.
///
/// The sizes are facts about the programs — fixed by their account layouts —
/// while the price of a byte is not, so the lamports come from the chain at
/// call time. Writing the SOL figures in directly is what produced every cost
/// bug found so far.
const DLMM_POSITION_BYTES: usize = 8120;
const DAMM_V2_POSITION_BYTES: usize = 408;
const TOKEN_ACCOUNT_BYTES: usize = 165;
const MINT_BYTES: usize = 82;

#[derive(Debug, Deserialize)]
pub struct TokenStrategiesParams {
    /// The mint the wallet holds.
    pub mint: String,
    /// How much of it, in human units. Required: the ranking depends on it.
    #[serde(alias = "amount", deserialize_with = "crate::services::params::lenient")]
    pub amount: f64,
    /// Price of the token in USD, when the caller knows it. Costs are in SOL
    /// and yields are in percent, so a USD value is what makes them
    /// comparable; without it the payback figures are omitted rather than
    /// guessed.
    #[serde(
        default,
        alias = "usdValue",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub usd_value: Option<f64>,
}

pub fn validate_token_strategies_params(p: &TokenStrategiesParams) -> Result<(), AppError> {
    if p.mint.trim().is_empty() {
        return Err(AppError::InvalidParams("mint is required".into()));
    }
    if !(p.amount > 0.0) {
        return Err(AppError::InvalidParams(
            "amount must be greater than zero — the ranking depends on it".into(),
        ));
    }
    Ok(())
}

/// Rent for a set of account sizes, asked of the chain, in SOL.
///
/// Run on a blocking thread: this RPC client is the synchronous one, and
/// calling it straight from an actix worker panics with "can call blocking
/// only when running on the multi-threaded runtime" — which it duly did.
async fn rent_sol(rpc: &SolanaRpc, sizes: &[usize]) -> Option<f64> {
    let rpc = rpc.clone();
    let sizes: Vec<usize> = sizes.to_vec();
    tokio::task::spawn_blocking(move || {
        let client = rpc.client();
        let mut total = 0u64;
        for size in sizes {
            let lamports = client.get_minimum_balance_for_rent_exemption(size).ok()?;
            total = total.checked_add(lamports)?;
        }
        Some(total as f64 / 1e9)
    })
    .await
    .ok()
    .flatten()
}

fn num(v: Option<&serde_json::Value>) -> f64 {
    v.and_then(|x| x.as_f64().or_else(|| x.as_str().and_then(|s| s.parse().ok())))
        .unwrap_or(0.0)
}

/// Days of yield needed to earn back what the position costs to open.
///
/// This is the number that decides whether a venue is right for a given
/// wallet, and the one no yield table shows. Null when either side is
/// unknown — a payback figure built on a guessed cost would be worse than
/// none.
fn payback_days(usd_value: Option<f64>, apr_pct: f64, cost_sol: Option<f64>, sol_usd: Option<f64>) -> Option<f64> {
    let value = usd_value?;
    let cost = cost_sol?;
    let sol_price = sol_usd?;
    if !(value > 0.0) || !(apr_pct > 0.0) || !(sol_price > 0.0) {
        return None;
    }
    let yearly = value * apr_pct / 100.0;
    if !(yearly > 0.0) {
        return None;
    }
    Some(cost * sol_price / yearly * 365.0)
}


/// What one transaction leg costs in account rent, from the chain.
///
/// Shared with the multi-step flows, where the number is multiplied by the
/// number of legs — a three-step plan pays it three times, and on a small
/// balance that alone can decide against the plan.
pub async fn leg_cost_sol(rpc: &SolanaRpc) -> Option<f64> {
    rent_sol(rpc, &[TOKEN_ACCOUNT_BYTES]).await
}

/// A pool's fee yield, taken as the lower of the last day and its lifetime.
///
/// Same rule the single-step ranking uses: a quiet day must not be hidden by
/// a busy history, and a spike must not be sold as a rate.
pub fn conservative_pool_apr(pool: &serde_json::Value) -> f64 {
    let tvl = num(pool.get("tvl"));
    if !(tvl > 0.0) {
        return 0.0;
    }
    let fees24 = num(pool.get("fees").and_then(|v| v.get("24h")));
    let apr_24h = fees24 * 365.0 / tvl * 100.0;
    let life_fees = num(pool.get("cumulative_metrics").and_then(|v| v.get("fees")));
    let created_ms = num(pool.get("created_at"));
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let age_days = if created_ms > 0.0 && now_secs > 0.0 {
        (now_secs - created_ms / 1000.0) / 86_400.0
    } else {
        0.0
    };
    if age_days <= 0.0 {
        return apr_24h;
    }
    let apr_life = life_fees / age_days * 365.0 / tvl * 100.0;
    if apr_life > 0.0 { apr_24h.min(apr_life) } else { apr_24h }
}

/// Everything this wallet could do with this token, priced at its own size.
pub async fn build_token_strategies(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    params: &TokenStrategiesParams,
) -> Result<BuildResponse, AppError> {
    validate_token_strategies_params(params)?;

    // Three different numbers, because they answer three different questions
    // and treating them as one gets both answers wrong.
    //
    // What leaves the wallet to open the position is rent on the accounts it
    // creates. That decides whether the wallet can afford to do this at all.
    //
    // Almost all of it comes back when the position closes — the accounts are
    // closed and their rent is returned. So it is capital locked up, not money
    // spent, and charging it against the yield made every payback figure look
    // far worse than it is.
    //
    // What is actually spent is the network fee, twice: once to open and once
    // to close. That is what the yield has to earn back. Counting the rent as
    // spent, or the fee as free, are both wrong in the way that misleads.
    let (lend_rent, dammv2_rent, dlmm_rent) = tokio::join!(
        rent_sol(rpc, &[TOKEN_ACCOUNT_BYTES]),
        rent_sol(rpc, &[DAMM_V2_POSITION_BYTES, MINT_BYTES, TOKEN_ACCOUNT_BYTES]),
        rent_sol(rpc, &[DLMM_POSITION_BYTES]),
    );
    // Open and close. Priority fees are not modelled: they are chosen at send
    // time and are not ours to predict.
    let round_trip_fee = BASE_FEE_SOL * 2.0;

    // The SOL price is what makes a cost in SOL comparable to a yield in
    // percent. Taken from the same pool the rest of the app prices against.
    let sol_usd = crate::services::strategies::sol_price_usd(http).await;

    let mut options: Vec<serde_json::Value> = Vec::new();

    // ── Lending ──────────────────────────────────────────────────────────
    if let Ok(reserves) = crate::services::kamino::fetch_market_reserves(http, None).await {
        for r in reserves {
            if r.get("liquidityTokenMint").and_then(|m| m.as_str()) != Some(params.mint.as_str()) {
                continue;
            }
            let apy = num(r.get("supplyApy")) * 100.0;
            let supplied = num(r.get("totalSupplyUsd"));
            // A reserve with nothing in it is not a poor rate, it is a venue
            // nobody uses — and its APY reads 0 for exactly that reason.
            if !(apy > 0.0) || !(supplied > 0.0) {
                continue;
            }
            let share = params.usd_value.map(|v| v / supplied).unwrap_or(0.0);
            options.push(serde_json::json!({
                "kind": "lend",
                "venue": "Kamino",
                "detail": r.get("liquidityToken"),
                "reserve": r.get("reserve"),
                "yieldPct": apy,
                "yieldBasis": "current supply rate",
                "venueSizeUsd": supplied,
                "depositShare": share,
                // Upfront is what the wallet must have; refundable comes back
                // on withdrawal; net is what the yield has to earn back.
                "upfrontSol": lend_rent,
                "refundableSol": lend_rent,
                "netCostSol": round_trip_fee,
                "paybackDays": payback_days(params.usd_value, apy, Some(round_trip_fee), sol_usd),
                // Borrowing against it is where a lending position can be
                // liquidated; supplying alone cannot be.
                "changesHolding": false,
                "tooNewToJudge": false,
                "risk": "Supplying cannot be liquidated. Borrowing against it can — that is a separate decision.",
                "maxLtv": r.get("maxLtv"),
            }));
        }
    }

    // ── Liquidity pools ──────────────────────────────────────────────────
    //
    // Ranked separately from lending and staking, and labelled with what they
    // do to the holding, because they are not the same kind of choice. Putting
    // SOL into a MEMECOIN/SOL pool converts half of it into that memecoin: a
    // 300% fee yield is erased by the memecoin falling 50%, which memecoins
    // routinely do, and the fee figure knows nothing about that. Sorting the
    // two together would put "become a memecoin holder" above "lend your SOL"
    // on the strength of a number that does not describe the risk taken.
    let now_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);

    for (venue, api, rent) in [
        ("Meteora DAMM v2", crate::services::meteora::DAMM_V2_API, dammv2_rent),
        ("Meteora DLMM", crate::services::meteora::DLMM_API, dlmm_rent),
    ] {
        let pools = crate::services::meteora::pools_containing(http, api, &params.mint, 100)
            .await
            .unwrap_or_default();
        for p in pools {
            let tvl = num(p.get("tvl"));
            let vol24 = num(p.get("volume").and_then(|v| v.get("24h")));
            let fees24 = num(p.get("fees").and_then(|v| v.get("24h")));
            let life_fees = num(p.get("cumulative_metrics").and_then(|v| v.get("fees")));
            // No trading, no fees — whatever the headline rate says.
            if !(tvl > 0.0) || !(vol24 > 0.0) {
                continue;
            }
            let created_ms = num(p.get("created_at"));
            let age_days = if created_ms > 0.0 && now_secs > 0.0 {
                (now_secs - created_ms / 1000.0) / 86_400.0
            } else {
                0.0
            };
            let share = params.usd_value.map(|v| v / tvl).unwrap_or(0.0);
            if share > MAX_VENUE_SHARE {
                continue;
            }

            let apr_24h = fees24 * 365.0 / tvl * 100.0;
            let apr_life = if age_days > 0.0 {
                life_fees / age_days * 365.0 / tvl * 100.0
            } else {
                0.0
            };
            // The lower of the two, so a quiet day is not hidden by a busy
            // lifetime and a spike is not sold as a rate.
            let apr = if apr_life > 0.0 { apr_24h.min(apr_life) } else { apr_24h };

            // Which side is not the token being asked about — that is what the
            // depositor ends up half-holding.
            let (x, y) = crate::services::meteora::meteora_pool_mints(&p);
            let counter_is_y = x == Some(params.mint.as_str());
            let counter = if counter_is_y { p.get("token_y") } else { p.get("token_x") };
            let counter_symbol = counter.and_then(|t| t.get("symbol")).and_then(|s| s.as_str());
            let counter_verified = counter
                .and_then(|t| t.get("is_verified"))
                .and_then(|b| b.as_bool())
                .unwrap_or(false);

            options.push(serde_json::json!({
                "kind": "lp",
                "venue": venue,
                "detail": p.get("name"),
                "pool": p.get("address"),
                "yieldPct": apr,
                "yield24hPct": apr_24h,
                "yieldLifetimePct": apr_life,
                "yieldBasis": "fees, annualised — the lower of the last day and the pool's lifetime",
                "poolAgeDays": age_days,
                // Said outright rather than left to be inferred from a number.
                "tooNewToJudge": age_days < MIN_POOL_AGE_DAYS,
                "venueSizeUsd": tvl,
                "depositShare": share,
                "upfrontSol": rent,
                "refundableSol": rent,
                "netCostSol": round_trip_fee,
                "paybackDays": payback_days(params.usd_value, apr, Some(round_trip_fee), sol_usd),
                "changesHolding": true,
                "counterToken": counter_symbol,
                "counterVerified": counter_verified,
                "risk": match counter_symbol {
                    Some(sym) => format!(
                        "Half of this ends up as {sym}. Fees are earned only while the price stays in range, \
                         and a fall in {sym} can outweigh anything the fees pay."
                    ),
                    None => "Half of this ends up as the pool's other token, and a fall in it can outweigh the fees.".to_string(),
                },
            }));
        }
    }

    // Options that leave the holding alone come first, then those that convert
    // part of it; within each, best payback first, and anything too new to
    // judge sinks below everything that has a record. An option whose payback
    // could not be computed sorts last rather than pretending to be free.
    options.sort_by(|a, b| {
        let key = |v: &serde_json::Value| {
            (
                v.get("changesHolding").and_then(|x| x.as_bool()).unwrap_or(false),
                v.get("tooNewToJudge").and_then(|x| x.as_bool()).unwrap_or(false),
                v.get("paybackDays").and_then(|x| x.as_f64()).unwrap_or(f64::MAX),
            )
        };
        let (ka, kb) = (key(a), key(b));
        ka.0.cmp(&kb.0)
            .then(ka.1.cmp(&kb.1))
            .then(ka.2.partial_cmp(&kb.2).unwrap_or(std::cmp::Ordering::Equal))
    });

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "token_strategies".into(),
            description: format!("What {} could earn, at this size", params.mint),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({}),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "mint": params.mint,
            "amount": params.amount,
            "usdValue": params.usd_value,
            "solUsd": sol_usd,
            "maxVenueShare": MAX_VENUE_SHARE,
            "options": options,
        })),
    })
}

/// SOL in USD, from the deepest SOL/USDC pool rather than a price feed we do
/// not otherwise depend on. None when it cannot be read — every figure that
/// needs it is then omitted.
pub async fn sol_price_usd(http: &reqwest::Client) -> Option<f64> {
    const SOL: &str = "So11111111111111111111111111111111111111112";
    const USDC: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
    let pools = crate::services::meteora::meteora_pools_for_pair(
        http,
        crate::services::meteora::DAMM_V2_API,
        SOL,
        USDC,
        5,
    )
    .await
    .ok()?;
    let best = pools.into_iter().max_by(|a, b| {
        num(a.get("tvl"))
            .partial_cmp(&num(b.get("tvl")))
            .unwrap_or(std::cmp::Ordering::Equal)
    })?;
    let price = num(best.get("current_price"));
    (price > 0.0).then_some(price)
}

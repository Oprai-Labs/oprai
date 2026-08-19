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
async fn rent_sol(rpc: &SolanaRpc, sizes: &[usize]) -> Option<f64> {
    let mut total = 0u64;
    for &size in sizes {
        let client = rpc.client();
        let lamports = client.get_minimum_balance_for_rent_exemption(size).ok()?;
        total = total.checked_add(lamports)?;
    }
    Some(total as f64 / 1e9)
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

/// Everything this wallet could do with this token, priced at its own size.
pub async fn build_token_strategies(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    params: &TokenStrategiesParams,
) -> Result<BuildResponse, AppError> {
    validate_token_strategies_params(params)?;

    // Costs, from the chain. Lending opens no position; the venues that do
    // pay rent on a position account and the token accounts around it.
    let (lend_cost, dammv2_cost, dlmm_cost) = tokio::join!(
        rent_sol(rpc, &[TOKEN_ACCOUNT_BYTES]),
        rent_sol(rpc, &[DAMM_V2_POSITION_BYTES, MINT_BYTES, TOKEN_ACCOUNT_BYTES]),
        rent_sol(rpc, &[DLMM_POSITION_BYTES]),
    );

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
                "costSol": lend_cost,
                "paybackDays": payback_days(params.usd_value, apy, lend_cost, sol_usd),
                // Borrowing against it is where a lending position can be
                // liquidated; supplying alone cannot be.
                "risk": "Supplying cannot be liquidated. Borrowing against it can — that is a separate decision.",
                "maxLtv": r.get("maxLtv"),
            }));
        }
    }

    // ── Liquidity pools ──────────────────────────────────────────────────
    for (venue, api, cost) in [
        ("Meteora DAMM v2", crate::services::meteora::DAMM_V2_API, dammv2_cost),
        ("Meteora DLMM", crate::services::meteora::DLMM_API, dlmm_cost),
    ] {
        let pools = crate::services::meteora::pools_containing(http, api, &params.mint, 40)
            .await
            .unwrap_or_default();
        for p in pools {
            let tvl = num(p.get("tvl"));
            let vol24 = num(p.get("volume").and_then(|v| v.get("24h")));
            let fees24 = num(p.get("fees").and_then(|v| v.get("24h")));
            // No trading, no fees — whatever the headline rate says.
            if !(tvl > 0.0) || !(vol24 > 0.0) {
                continue;
            }
            let share = params.usd_value.map(|v| v / tvl).unwrap_or(0.0);
            if share > MAX_VENUE_SHARE {
                continue;
            }
            let apr = fees24 * 365.0 / tvl * 100.0;
            options.push(serde_json::json!({
                "kind": "lp",
                "venue": venue,
                "detail": p.get("name"),
                "pool": p.get("address"),
                "yieldPct": apr,
                // Said plainly, because this is not the same kind of number as
                // a lending rate and must not be compared to one silently.
                "yieldBasis": "last 24h of fees, annualised",
                "venueSizeUsd": tvl,
                "depositShare": share,
                "costSol": cost,
                "paybackDays": payback_days(params.usd_value, apr, cost, sol_usd),
                "risk": "Earns only while the price stays in the position's range, and the mix withdrawn differs from the mix deposited.",
            }));
        }
    }

    // Best payback first; an option whose payback could not be computed sorts
    // last rather than pretending to be free.
    options.sort_by(|a, b| {
        let d = |v: &serde_json::Value| v.get("paybackDays").and_then(|x| x.as_f64()).unwrap_or(f64::MAX);
        d(a).partial_cmp(&d(b)).unwrap_or(std::cmp::Ordering::Equal)
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

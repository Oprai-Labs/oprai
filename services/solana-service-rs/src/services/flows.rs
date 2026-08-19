//! Multi-step strategies, tested against the simple option rather than sold.
//!
//! The flows people are told to run — stake, post the receipt as collateral,
//! borrow, stake again; or stake and then LP the liquid token against SOL —
//! are arithmetic, and the arithmetic changes daily. Measured on the day this
//! was written, both lose to doing nothing clever:
//!
//!   leveraged staking   5.55 + 0.00 − 6.94 = −1.39% per extra turn,
//!                       so 2x returns 4.16% against 5.55% unlevered
//!   stake then LP       half the position stops earning the staking yield and
//!                       the correlated pair pays 0.18% in fees → 2.70%
//!   simple stake                                                → 5.55%
//!
//! Neither is a bad idea in general. Leverage works the moment the borrow rate
//! drops under the staking yield, and LP wins on a pair with real fees. They
//! are bad *today*, which is a fact about today's rates and cannot be written
//! down anywhere — it has to be recomputed on every question.
//!
//! So this builds the flows from live rates, checks each leg can actually
//! connect to the next, charges every leg its cost, and then says plainly
//! whether the result beats the one-step answer. Someone who has never done
//! this before is not served by a clever-looking plan; they are served by
//! being told the clever plan earns them less.

use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;

const SOL_MINT: &str = "So11111111111111111111111111111111111111112";

/// How much of the borrowing power to actually use.
///
/// Borrowing to the limit is liquidated by the first move against you. Two
/// thirds leaves room for the collateral to fall before anything is forced.
const LTV_SAFETY: f64 = 0.66;

fn num(v: Option<&serde_json::Value>) -> f64 {
    v.and_then(|x| x.as_f64().or_else(|| x.as_str().and_then(|s| s.parse().ok())))
        .unwrap_or(0.0)
}

/// A liquid-staking token, its yield, and the mint the yield is paid in.
struct Lst {
    symbol: &'static str,
    mint: &'static str,
    apy: f64,
}

/// Every flow worth testing for a wallet holding `mint`, priced today.
pub async fn build_strategy_flows(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    mint: &str,
    usd_value: Option<f64>,
) -> Result<BuildResponse, AppError> {
    if mint != SOL_MINT {
        // The flows below are all SOL-shaped. Saying so beats returning an
        // empty list that reads as "there is nothing you can do".
        return Ok(flows_response(
            mint,
            serde_json::json!([]),
            Some("Multi-step flows are currently only modelled for SOL."),
        ));
    }

    // ── Live inputs ──────────────────────────────────────────────────────
    let (yields, reserves, sol_usd) = tokio::join!(
        crate::services::marinade::query_stake_yields(http),
        crate::services::kamino::fetch_market_reserves(http, None),
        crate::services::strategies::sol_price_usd(http),
    );

    let stake_apys: Vec<Lst> = {
        let mut v = Vec::new();
        if let Ok(resp) = yields {
            let rows = resp
                .data
                .as_ref()
                .and_then(|d| d.get("yields"))
                .and_then(|y| y.as_array())
                .cloned()
                .unwrap_or_default();
            for r in rows {
                let id = r.get("id").and_then(|x| x.as_str()).unwrap_or_default();
                let apy = r.get("apy").and_then(|x| x.as_f64()).unwrap_or(0.0);
                if apy <= 0.0 {
                    continue;
                }
                match id {
                    "jito" => v.push(Lst { symbol: "jitoSOL", mint: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", apy }),
                    "marinade" => v.push(Lst { symbol: "mSOL", mint: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", apy }),
                    _ => {}
                }
            }
        }
        v
    };
    let reserves = reserves.unwrap_or_default();
    let reserve_for = |symbol: &str| -> Option<&serde_json::Value> {
        reserves.iter().find(|r| {
            r.get("liquidityToken")
                .and_then(|t| t.as_str())
                .map(|t| t.eq_ignore_ascii_case(symbol))
                .unwrap_or(false)
        })
    };

    // Costs, from the chain, once.
    let leg_cost = crate::services::strategies::leg_cost_sol(rpc).await;

    let mut flows: Vec<serde_json::Value> = Vec::new();

    // ── Baseline: the one-step answers everything else must beat ─────────
    let mut best_simple = 0.0f64;
    let mut best_simple_label = String::new();
    for lst in &stake_apys {
        if lst.apy > best_simple {
            best_simple = lst.apy;
            best_simple_label = format!("Stake it for {}", lst.symbol);
        }
    }
    if let Some(sol_res) = reserve_for("SOL") {
        let supply = num(sol_res.get("supplyApy")) * 100.0;
        if supply > best_simple {
            best_simple = supply;
            best_simple_label = "Lend it on Kamino".to_string();
        }
    }
    if best_simple > 0.0 {
        flows.push(serde_json::json!({
            "name": best_simple_label,
            "steps": 1,
            "netApr": best_simple,
            "costSol": leg_cost,
            "isBaseline": true,
            "beatsSimple": true,
            "why": "One step, nothing borrowed, nothing converted.",
        }));
    }

    // ── Flow: stake, post as collateral, borrow SOL, stake again ─────────
    let sol_borrow = reserve_for("SOL").map(|r| num(r.get("borrowApy")) * 100.0);
    for lst in &stake_apys {
        let Some(res) = reserve_for(lst.symbol) else { continue };
        let Some(borrow_apr) = sol_borrow else { continue };
        let collateral_supply = num(res.get("supplyApy")) * 100.0;
        let max_ltv = num(res.get("maxLtv"));
        if !(max_ltv > 0.0) {
            continue;
        }
        // What one extra turn of the loop earns, before it is levered up.
        let per_turn = lst.apy + collateral_supply - borrow_apr;
        let leverage = 1.0 / (1.0 - max_ltv * LTV_SAFETY);
        let net = lst.apy + per_turn * (leverage - 1.0);
        // A price fall of this much wipes out the safety margin.
        let liq_drop_pct = (1.0 - LTV_SAFETY) * 100.0;

        flows.push(serde_json::json!({
            "name": format!("Leveraged {} staking", lst.symbol),
            "steps": 4,
            "legs": [
                format!("Stake SOL for {}", lst.symbol),
                format!("Supply {} to Kamino as collateral", lst.symbol),
                "Borrow SOL against it",
                "Stake the borrowed SOL",
            ],
            "netApr": net,
            "perTurnApr": per_turn,
            "leverage": leverage,
            "costSol": leg_cost.map(|c| c * 4.0),
            "isBaseline": false,
            "beatsSimple": net > best_simple,
            "liquidationDropPct": liq_drop_pct,
            "why": if per_turn < 0.0 {
                format!(
                    "Each turn of the loop costs {:.2}% more to borrow than it earns, so leverage lowers the return instead of raising it.",
                    -per_turn
                )
            } else {
                format!("Each turn adds {per_turn:.2}%, levered {leverage:.1}x.")
            },
        }));
    }

    // ── Flow: stake, then LP the liquid token against SOL ────────────────
    for lst in &stake_apys {
        let mut best_pool: Option<(f64, serde_json::Value, &str)> = None;
        for api in [
            crate::services::meteora::DLMM_API,
            crate::services::meteora::DAMM_V2_API,
        ] {
            let pools = crate::services::meteora::meteora_pools_for_pair(http, api, lst.mint, SOL_MINT, 10)
                .await
                .unwrap_or_default();
            for p in pools {
                let apr = crate::services::strategies::conservative_pool_apr(&p);
                if apr <= 0.0 {
                    continue;
                }
                if best_pool.as_ref().map(|(a, _, _)| apr > *a).unwrap_or(true) {
                    best_pool = Some((apr, p, api));
                }
            }
        }
        let Some((lp_apr, pool, _)) = best_pool else { continue };
        // Half the position stops being the liquid token, so only half the
        // staking yield survives — the part people leave out.
        let net = lst.apy * 0.5 + lp_apr;
        flows.push(serde_json::json!({
            "name": format!("Stake for {}, then LP {}/SOL", lst.symbol, lst.symbol),
            "steps": 2,
            "legs": [
                format!("Stake SOL for {}", lst.symbol),
                format!("Add {}/SOL liquidity", lst.symbol),
            ],
            "netApr": net,
            "lpApr": lp_apr,
            "pool": pool.get("address"),
            "costSol": leg_cost.map(|c| c * 2.0),
            "isBaseline": false,
            "beatsSimple": net > best_simple,
            "why": format!(
                "Half the position becomes SOL again, so only half the {:.2}% staking yield survives, and the pair pays {lp_apr:.2}% in fees.",
                lst.apy
            ),
        }));
    }

    flows.sort_by(|a, b| {
        let n = |v: &serde_json::Value| v.get("netApr").and_then(|x| x.as_f64()).unwrap_or(f64::MIN);
        n(b).partial_cmp(&n(a)).unwrap_or(std::cmp::Ordering::Equal)
    });

    let note = if flows.iter().any(|f| {
        !f.get("isBaseline").and_then(|b| b.as_bool()).unwrap_or(false)
            && f.get("beatsSimple").and_then(|b| b.as_bool()).unwrap_or(false)
    }) {
        None
    } else {
        Some("No multi-step flow beats the one-step answer at today's rates.")
    };

    let mut resp = flows_response(mint, serde_json::Value::Array(flows), note);
    if let Some(d) = resp.data.as_mut() {
        d["solUsd"] = serde_json::json!(sol_usd);
        d["usdValue"] = serde_json::json!(usd_value);
        d["bestSimpleApr"] = serde_json::json!(best_simple);
    }
    Ok(resp)
}

fn flows_response(
    mint: &str,
    flows: serde_json::Value,
    note: Option<&str>,
) -> BuildResponse {
    BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "strategy_flows".into(),
            description: "Multi-step strategies, priced against the simple one".into(),
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
            "mint": mint,
            "flows": flows,
            "note": note,
        })),
    }
}

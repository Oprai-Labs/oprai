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

/// Kamino main market — the one every rate above is read from.
const KAMINO_MAIN_MARKET: &str = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF";

fn num(v: Option<&serde_json::Value>) -> f64 {
    v.and_then(|x| {
        x.as_f64()
            .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
    })
    .unwrap_or(0.0)
}

/// A liquid-staking token, its yield, and the mint the yield is paid in.
struct Lst {
    symbol: &'static str,
    mint: &'static str,
    apy: f64,
}

/// One reserve's rate history, keyed by timestamp.
///
/// The endpoint answers with a bare array when unfiltered and with
/// `{reserve, history}` once a window is given — reading only the first shape
/// returns an empty range for every real call, which looks like "no history"
/// rather than like a bug. Both are accepted here.
async fn reserve_history(
    http: &reqwest::Client,
    reserve: &str,
    market: &str,
    days: i64,
) -> Option<std::collections::HashMap<String, (f64, f64)>> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs() as i64;
    let p: crate::services::kamino::KaminoMarketReserveHistoryParams =
        serde_json::from_value(serde_json::json!({
            "reserve": reserve,
            "market": market,
            "start": now - days * 86_400,
            "end": now,
        }))
        .ok()?;
    let resp = crate::services::kamino::build_kamino_market_reserve_history(http, "", &p)
        .await
        .ok()?;
    let data = resp.data?;
    let rows = data
        .get("history")
        .and_then(|h| h.as_array())
        .or_else(|| data.as_array())?;

    let out: std::collections::HashMap<String, (f64, f64)> = rows
        .iter()
        .filter_map(|r| {
            let ts = r.get("timestamp")?.as_str()?.to_string();
            let m = r.get("metrics")?;
            let borrow = m.get("borrowInterestAPY").and_then(|v| v.as_f64())? * 100.0;
            let supply = m
                .get("supplyInterestAPY")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0)
                * 100.0;
            Some((ts, (borrow, supply)))
        })
        .collect();
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

/// What the leveraged loop would have paid at every hour of the last `days`.
///
/// A single snapshot is half an answer. Measured over 700 hours to the day
/// this was written, the loop's per-turn spread ran from −2.26% at worst to
/// −0.00% at best: it was not profitable in a single hour of the month, and
/// its best moment was exactly break-even. That is a far stronger and more
/// useful thing to tell someone than that it happens to lose 2.05% today,
/// which invites them to wait for a better week that the data says does not
/// come.
///
/// Borrow and supply rates both move with utilisation, so they are read at
/// the *same* timestamp — pairing one leg's low point with the other leg's
/// value today would manufacture a profitable moment that never existed. The
/// staking yield is held at its current value and labelled as such: it is
/// inflation-driven and moves on epoch boundaries, not hourly, and no
/// per-hour history for it is available to join against.
///
/// None when either history cannot be read — the flow then carries today's
/// number alone rather than an invented range.
async fn loop_spread_history(
    http: &reqwest::Client,
    collateral_reserve: &str,
    borrow_reserve: &str,
    market: &str,
    stake_apy: f64,
    days: i64,
) -> Option<serde_json::Value> {
    let (coll, borrow) = tokio::join!(
        reserve_history(http, collateral_reserve, market, days),
        reserve_history(http, borrow_reserve, market, days),
    );
    let (coll, borrow) = (coll?, borrow?);

    let mut out = summarise_spread(&coll, &borrow, stake_apy)?;
    out["days"] = serde_json::json!(days);
    Some(out)
}

/// The arithmetic of the above, separated so it can be tested without a network.
///
/// The one thing that must not go wrong here is the join: a timestamp present
/// in one series and missing from the other has to be dropped, not paired with
/// whatever sits next to it. Pairing across time invents a spread that never
/// existed, and it would invent it in the flattering direction — the cheap
/// borrowing hour against a rich collateral hour.
fn summarise_spread(
    coll: &std::collections::HashMap<String, (f64, f64)>,
    borrow: &std::collections::HashMap<String, (f64, f64)>,
    stake_apy: f64,
) -> Option<serde_json::Value> {
    let mut series: Vec<f64> = borrow
        .iter()
        .filter_map(|(ts, (borrow_apy, _))| {
            let (_, coll_supply) = coll.get(ts)?;
            Some(stake_apy + coll_supply - borrow_apy)
        })
        .collect();
    if series.is_empty() {
        return None;
    }
    let hours = series.len();
    let profitable = series.iter().filter(|v| **v > 0.0).count();
    series.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    Some(serde_json::json!({
        "hours": hours,
        "worstPerTurnPct": series.first(),
        "medianPerTurnPct": series[hours / 2],
        "bestPerTurnPct": series.last(),
        // The headline. "Profitable in none of the last 700 hours" settles the
        // question that "it loses today" leaves open.
        "profitableHours": profitable,
        "profitableSharePct": 100.0 * profitable as f64 / hours as f64,
        "stakeApyHeldAtPct": stake_apy,
    }))
}

#[cfg(test)]
mod spread_tests {
    use super::*;
    use std::collections::HashMap;

    fn m(rows: &[(&str, f64, f64)]) -> HashMap<String, (f64, f64)> {
        rows.iter()
            .map(|(t, b, s)| (t.to_string(), (*b, *s)))
            .collect()
    }

    #[test]
    fn unmatched_timestamps_are_dropped_not_paired() {
        // Borrow is cheap at 09:00 but the collateral series has no reading
        // there. Pairing it with 10:00's collateral would report a profitable
        // hour that never happened.
        let borrow = m(&[("09:00", 1.0, 0.0), ("10:00", 9.0, 0.0)]);
        let coll = m(&[("10:00", 0.0, 0.0)]);
        let out = summarise_spread(&coll, &borrow, 5.0).unwrap();
        assert_eq!(out["hours"], 1, "only the matched hour may be counted");
        assert_eq!(out["profitableHours"], 0);
        assert_eq!(out["bestPerTurnPct"], -4.0);
    }

    #[test]
    fn never_profitable_reports_a_zero_share() {
        // The measured case: 5.55% staking, 0% collateral yield, borrow always
        // above the staking yield.
        let borrow = m(&[("1", 5.6, 0.0), ("2", 6.0, 0.0), ("3", 7.8, 0.0)]);
        let coll = m(&[("1", 0.0, 0.0), ("2", 0.0, 0.0), ("3", 0.0, 0.0)]);
        let out = summarise_spread(&coll, &borrow, 5.55).unwrap();
        assert_eq!(out["profitableSharePct"], 0.0);
        assert_eq!(out["hours"], 3);
        // Best is the least-bad hour, and it is still negative.
        assert!(out["bestPerTurnPct"].as_f64().unwrap() < 0.0);
    }

    #[test]
    fn collateral_yield_counts_toward_the_spread() {
        // A collateral leg that actually pays can carry the loop into profit;
        // dropping it would understate every flow that has one.
        let borrow = m(&[("1", 6.0, 0.0)]);
        let coll = m(&[("1", 0.0, 3.0)]);
        let out = summarise_spread(&coll, &borrow, 5.0).unwrap();
        assert_eq!(out["bestPerTurnPct"], 2.0);
        assert_eq!(out["profitableSharePct"], 100.0);
    }

    #[test]
    fn no_overlap_yields_no_history_rather_than_a_fake_one() {
        let borrow = m(&[("1", 6.0, 0.0)]);
        let coll = m(&[("2", 0.0, 0.0)]);
        assert!(summarise_spread(&coll, &borrow, 5.0).is_none());
    }
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
                    "jito" => v.push(Lst {
                        symbol: "jitoSOL",
                        mint: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
                        apy,
                    }),
                    "marinade" => v.push(Lst {
                        symbol: "mSOL",
                        mint: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
                        apy,
                    }),
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
    let sol_reserve_addr = reserve_for("SOL")
        .and_then(|r| r.get("reserve"))
        .and_then(|v| v.as_str())
        .map(str::to_string);
    for lst in &stake_apys {
        let Some(res) = reserve_for(lst.symbol) else {
            continue;
        };
        let Some(borrow_apr) = sol_borrow else {
            continue;
        };
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

        // How the loop has actually paid over the last month, rather than at
        // this hour alone. Read at matched timestamps so the two legs are
        // never mixed across time.
        let spread_history = match (
            res.get("reserve").and_then(|v| v.as_str()),
            sol_reserve_addr.as_deref(),
        ) {
            (Some(coll), Some(borrow_res)) => {
                loop_spread_history(http, coll, borrow_res, KAMINO_MAIN_MARKET, lst.apy, 30).await
            }
            _ => None,
        };

        // The sentence a reader acts on. "It loses today" invites waiting
        // for a better week; "it was not profitable in any of the last 700
        // hours" answers whether that week is coming.
        let why = {
            let today = if per_turn < 0.0 {
                format!(
                    "Each turn of the loop costs {:.2}% more to borrow than it earns, so leverage lowers the return instead of raising it.",
                    -per_turn
                )
            } else {
                format!("Each turn adds {per_turn:.2}%, levered {leverage:.1}x.")
            };
            match spread_history.as_ref() {
                Some(h) => {
                    let hours = h.get("hours").and_then(|v| v.as_u64()).unwrap_or(0);
                    let share = h
                        .get("profitableSharePct")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0);
                    let best = h.get("bestPerTurnPct").and_then(|v| v.as_f64());
                    let worst = h.get("worstPerTurnPct").and_then(|v| v.as_f64());
                    let history = if share <= 0.0 {
                        match best {
                            // -0.37% is not break-even, and calling it that
                            // reads as "nearly worth it" for a flow that has
                            // lost money every hour of the month. Only a
                            // genuinely flat best hour gets the softer word.
                            Some(b) if b >= -0.05 => format!(
                                " Over the last {hours} hours it was never profitable — at its best moment it only broke even, so this is not a matter of waiting for a better week."
                            ),
                            Some(b) => format!(
                                " Over the last {hours} hours it was never profitable — even at its best it still lost {:.2}% per turn, so this is not a matter of waiting for a better week.",
                                -b
                            ),
                            None => format!(" Over the last {hours} hours it was never profitable."),
                        }
                    } else {
                        match (worst, best) {
                            (Some(w), Some(b)) => format!(
                                " Over the last {hours} hours it paid between {w:+.2}% and {b:+.2}% per turn, and was profitable {share:.0}% of the time."
                            ),
                            _ => format!(" It was profitable {share:.0}% of the last {hours} hours."),
                        }
                    };
                    format!("{today}{history}")
                }
                None => today,
            }
        };

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
            // Today's number is one hour of the month; this is the month.
            "spreadHistory": spread_history,
            // The sentence a reader acts on. "It loses today" invites waiting
            // for a better week; "it was not profitable in any of the last 700
            // hours" answers whether that week is coming.
            "why": why,
        }));
    }

    // ── Flow: stake, then LP the liquid token against SOL ────────────────
    for lst in &stake_apys {
        let mut best_pool: Option<(f64, serde_json::Value, &str)> = None;
        for api in [
            crate::services::meteora::DLMM_API,
            crate::services::meteora::DAMM_V2_API,
        ] {
            let pools =
                crate::services::meteora::meteora_pools_for_pair(http, api, lst.mint, SOL_MINT, 10)
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
        let Some((lp_apr, pool, _)) = best_pool else {
            continue;
        };
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
        let n =
            |v: &serde_json::Value| v.get("netApr").and_then(|x| x.as_f64()).unwrap_or(f64::MIN);
        n(b).partial_cmp(&n(a)).unwrap_or(std::cmp::Ordering::Equal)
    });

    let note = if flows.iter().any(|f| {
        !f.get("isBaseline")
            .and_then(|b| b.as_bool())
            .unwrap_or(false)
            && f.get("beatsSimple")
                .and_then(|b| b.as_bool())
                .unwrap_or(false)
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

fn flows_response(mint: &str, flows: serde_json::Value, note: Option<&str>) -> BuildResponse {
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

/// Exercises the real endpoint and the real parse.
///
/// Everything else about this feature is unit-tested, but the one failure it
/// could not catch already happened once: the endpoint answers with a bare
/// array unfiltered and with `{reserve, history}` once a window is given, and
/// reading only the first shape returned an empty range that looked exactly
/// like "this reserve has no history". Only a call against the live API
/// distinguishes those. Ignored by default so the suite stays offline.
#[cfg(test)]
mod live_history_tests {
    use super::*;

    const SOL_RESERVE: &str = "d4A2prbA2whesmvHaL88BH6Ewn5N4bTSU2Ze8P6Bc4Q";
    const JITOSOL_RESERVE: &str = "EVbyPKrHG6WBfm4dLxLMJpUDY43cCAcHSpV3KYjKsktW";

    #[tokio::test]
    #[ignore = "hits the live Kamino API"]
    async fn history_parses_and_the_join_lines_up() {
        let http = reqwest::Client::new();
        let sol = reserve_history(&http, SOL_RESERVE, KAMINO_MAIN_MARKET, 30)
            .await
            .expect("SOL reserve history should parse");
        assert!(
            sol.len() > 100,
            "30 days of hourly points should be hundreds, got {}",
            sol.len()
        );
        for (_, (borrow, _)) in sol.iter() {
            assert!(
                (0.0..100.0).contains(borrow),
                "borrow rate {borrow} is outside any plausible range — check the \
                 percent conversion"
            );
        }

        let out = loop_spread_history(
            &http,
            JITOSOL_RESERVE,
            SOL_RESERVE,
            KAMINO_MAIN_MARKET,
            5.55,
            30,
        )
        .await
        .expect("joined spread history should be produced");

        let hours = out["hours"].as_u64().unwrap();
        assert!(
            hours > 100,
            "the join dropped almost everything: {hours} hours"
        );
        let worst = out["worstPerTurnPct"].as_f64().unwrap();
        let best = out["bestPerTurnPct"].as_f64().unwrap();
        assert!(worst <= best);
        eprintln!(
            "loop spread over {hours}h: worst {worst:+.2}%  median {:+.2}%  best {best:+.2}%  profitable {:.1}% of the time",
            out["medianPerTurnPct"].as_f64().unwrap(),
            out["profitableSharePct"].as_f64().unwrap(),
        );
    }
}

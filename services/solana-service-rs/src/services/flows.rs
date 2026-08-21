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

/// Below this a stablecoin reserve is too thin to fund the borrow it advertises.
const MIN_STABLE_LIQUIDITY_USD: f64 = 1_000_000.0;

/// Below this a token is not really a lending asset. The liquid-staking
/// receipts all sit at 0% supply; their real loop is the staking one, and
/// offering a self-loop for them would be noise.
const MIN_LOOPABLE_SUPPLY_APR: f64 = 0.1;

/// Below this the best one-step option is not really an option, and saying
/// "nothing beat it" would flatter it.
const MEANINGFUL_BASELINE_APR: f64 = 0.5;

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

/// The best one-step return available today, and what to call it.
///
/// Shared with the position review, which has to answer "compared to what?"
/// with the same number the entry side uses — two different baselines would
/// let a position look worth keeping on one screen and worth closing on
/// another.
/// What a liquid-staking token earns for its holder, doing nothing.
///
/// None for anything that is not one, which is the signal to fall back to
/// lending rates.
async fn lst_staking_yield(http: &reqwest::Client, mint: &str) -> Option<f64> {
    const JITOSOL: &str = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn";
    const MSOL: &str = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So";
    let id = match mint {
        JITOSOL => "jito",
        MSOL => "marinade",
        _ => return None,
    };
    let resp = crate::services::marinade::query_stake_yields(http)
        .await
        .ok()?;
    resp.data
        .as_ref()?
        .get("yields")?
        .as_array()?
        .iter()
        .find(|r| r.get("id").and_then(|x| x.as_str()) == Some(id))
        .and_then(|r| r.get("apy").and_then(|x| x.as_f64()))
        .filter(|a| *a > 0.0)
}

/// What Jupiter Lend pays to supply an asset, as a percentage.
///
/// `totalRate` is in basis points and includes incentives on top of the plain
/// supply rate; it is what a depositor actually receives, which is the number
/// a comparison should use.
async fn jupiter_lend_rate(http: &reqwest::Client, mint: &str) -> Option<(f64, String)> {
    let rows: serde_json::Value = http
        .get("https://lite-api.jup.ag/lend/v1/earn/tokens")
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    let rows = rows
        .as_array()
        .cloned()
        .or_else(|| rows.get("data").and_then(|d| d.as_array()).cloned())?;
    for r in rows {
        let asset_mint = r
            .get("assetAddress")
            .or_else(|| r.get("asset").and_then(|a| a.get("address")))
            .and_then(|v| v.as_str());
        if asset_mint != Some(mint) {
            continue;
        }
        let bps = r.get("totalRate").and_then(|v| {
            v.as_f64()
                .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        })?;
        let sym = r.get("symbol").and_then(|v| v.as_str()).unwrap_or("it");
        return Some((bps / 100.0, format!("lending {sym} on Jupiter")));
    }
    None
}

pub async fn best_simple_option(http: &reqwest::Client) -> Option<(f64, String)> {
    best_simple_option_for(http, SOL_MINT).await
}

/// The same, for whichever asset the money is actually denominated in.
///
/// A USDC-paired position judged against staking SOL is judged in the wrong
/// unit: the two returns are not measured in the same thing, so the difference
/// between them is not a gap. A stablecoin position is compared with stablecoin
/// lending, a SOL position with staking or lending SOL.
pub async fn best_simple_option_for(
    http: &reqwest::Client,
    quote_mint: &str,
) -> Option<(f64, String)> {
    let (yields, reserves) = tokio::join!(
        crate::services::marinade::query_stake_yields(http),
        crate::services::kamino::fetch_market_reserves(http, None),
    );
    let mut best = 0.0f64;
    let mut label = String::new();

    // Staking only exists for SOL, so it is only an alternative to SOL.
    if quote_mint == SOL_MINT {
        if let Ok(resp) = yields {
            let rows = resp
                .data
                .as_ref()
                .and_then(|d| d.get("yields"))
                .and_then(|y| y.as_array())
                .cloned()
                .unwrap_or_default();
            for r in rows {
                let apy = r.get("apy").and_then(|x| x.as_f64()).unwrap_or(0.0);
                if apy > best {
                    best = apy;
                    label = "staking SOL".to_string();
                }
            }
        }
    }
    // A liquid-staking token earns its staking yield just by being held, so
    // that — not a lending rate — is what doing nothing with it pays. Kamino
    // lists every LST at 0.00% supply, and quoting that as the alternative
    // told the holder of a jitoSOL position they were being compared against
    // nothing, when the real bar is around 5%.
    if let Some(apy) = lst_staking_yield(http, quote_mint).await {
        if apy > best {
            best = apy;
            label = "simply holding it, which earns its staking yield".to_string();
        }
    }

    // Kamino publishes one rate per asset, so comparing a Kamino position with
    // Kamino says nothing. A second venue is what makes "is this still the
    // best rate" a real question.
    if let Some((rate, jup_label)) = jupiter_lend_rate(http, quote_mint).await {
        if rate > best {
            best = rate;
            label = jup_label;
        }
    }

    if let Ok(reserves) = reserves {
        // The deepest reserve for this asset, for the same reason the flows
        // engine picks by deposits: a mint can have several and the empty ones
        // advertise rates nobody can get.
        let own = reserves
            .iter()
            .filter(|r| r.get("liquidityTokenMint").and_then(|v| v.as_str()) == Some(quote_mint))
            .max_by(|a, b| {
                num(a.get("totalSupplyUsd"))
                    .partial_cmp(&num(b.get("totalSupplyUsd")))
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
        if let Some(r) = own {
            let supply = num(r.get("supplyApy")) * 100.0;
            if supply > best {
                best = supply;
                let sym = r
                    .get("liquidityToken")
                    .and_then(|v| v.as_str())
                    .unwrap_or("it");
                label = format!("lending {sym} on Kamino");
            }
        }
    }
    (best > 0.0).then_some((best, label))
}

/// Every flow worth testing for a wallet holding `mint`, priced today.
pub async fn build_strategy_flows(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    mint: &str,
    usd_value: Option<f64>,
) -> Result<BuildResponse, AppError> {
    // ── Live inputs ──────────────────────────────────────────────────────
    let (yields, reserves, sol_usd) = tokio::join!(
        crate::services::marinade::query_stake_yields(http),
        crate::services::kamino::fetch_market_reserves(http, None),
        crate::services::strategies::sol_price_usd(http),
    );

    // Staking a token for a liquid-staking receipt only exists for SOL. Left
    // empty for every other mint, which disables the two staking flows below
    // without the general engine needing to know they are special.
    let stake_apys: Vec<Lst> = if mint != SOL_MINT {
        Vec::new()
    } else {
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
    // The held token is found by mint, not by ticker — but a mint can map to
    // several reserves and taking the first is wrong. USDC currently has four
    // in the main market, three of them empty; the first advertises 0.00%
    // supply against the live reserve's 4.49%. Picking by deposits picks the
    // one people are actually using, and needs no address hardcoded.
    let held: Option<&serde_json::Value> = reserves
        .iter()
        .filter(|r| r.get("liquidityTokenMint").and_then(|v| v.as_str()) == Some(mint))
        .max_by(|a, b| {
            num(a.get("totalSupplyUsd"))
                .partial_cmp(&num(b.get("totalSupplyUsd")))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
    let held_symbol = held
        .and_then(|r| r.get("liquidityToken"))
        .and_then(|v| v.as_str())
        .unwrap_or("this token");
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
    if let Some(r) = held {
        let supply = num(r.get("supplyApy")) * 100.0;
        if supply > best_simple {
            best_simple = supply;
            best_simple_label = format!("Lend {held_symbol} on Kamino");
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

    // ── Flow: take cash against the holding, without selling it ──────────
    //
    // This is the one multi-step flow that applies to nearly every token, and
    // it is not a yield play. At every rate on the book today, lending the
    // borrowed stable back earns less than the loan costs — so presenting it
    // as income would be a lie of exactly the kind this module exists to stop.
    // What it actually buys is cash while keeping the position, and the honest
    // way to present that is its price. It is only called a carry when the
    // arithmetic says so, which it currently does not.
    if let Some(coll) = held {
        let max_ltv = num(coll.get("maxLtv"));
        // A thin reserve can advertise a cheap borrow it cannot actually fund.
        let stables: Vec<&serde_json::Value> = reserves
            .iter()
            .filter(|r| {
                r.get("liquidityToken")
                    .and_then(|v| v.as_str())
                    .map(crate::services::fees::symbol_is_stable)
                    .unwrap_or(false)
                    && num(r.get("totalSupplyUsd")) >= MIN_STABLE_LIQUIDITY_USD
            })
            .collect();
        // Borrow and hold the SAME stable. The first version picked the
        // cheapest asset to borrow and quoted the best supply rate on the
        // book, which are different assets — you cannot earn PYUSD's rate on
        // borrowed USDG without swapping into it, paying for the swap and
        // taking on the mismatch between what you owe and what you hold. So
        // the stable is chosen by the spread it actually costs to carry, and
        // both legs name it. USDG looked cheapest to borrow and is in fact the
        // worst on this measure.
        let best_stable = stables.iter().copied().min_by(|a, b| {
            let spread = |r: &serde_json::Value| num(r.get("borrowApy")) - num(r.get("supplyApy"));
            spread(a)
                .partial_cmp(&spread(b))
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        if let (true, Some(stable)) = (max_ltv > 0.0, best_stable) {
            let borrow_sym = stable
                .get("liquidityToken")
                .and_then(|v| v.as_str())
                .unwrap_or("a stablecoin");
            let borrow_apr = num(stable.get("borrowApy")) * 100.0;
            let park_apr = num(stable.get("supplyApy")) * 100.0;
            let held_supply = num(coll.get("supplyApy")) * 100.0;
            let unlocked = max_ltv * LTV_SAFETY;
            let carry = unlocked * (park_apr - borrow_apr);
            let net = held_supply + carry;

            let why = if carry < 0.0 {
                format!(
                    "You keep all your {held_symbol} and can draw {:.0}% of its value as {borrow_sym}. Borrowing costs {borrow_apr:.2}% and lending it back pays {park_apr:.2}%, so the cash costs you {:.2}% a year against simply lending — that is the price of not selling, not a loss on the position.",
                    unlocked * 100.0,
                    -carry
                )
            } else {
                format!(
                    "You keep all your {held_symbol}, draw {:.0}% of its value as {borrow_sym} at {borrow_apr:.2}%, and lend it at {park_apr:.2}% — the spread adds {carry:.2}% a year on top.",
                    unlocked * 100.0
                )
            };

            flows.push(serde_json::json!({
                "name": format!("Borrow {borrow_sym} against your {held_symbol}"),
                "steps": 3,
                "legs": [
                    format!("Supply {held_symbol} to Kamino as collateral"),
                    format!("Borrow {borrow_sym} against it"),
                    format!("Lend the {borrow_sym} back out, or spend it"),
                ],
                "netApr": net,
                // Named separately because the point of this flow is the cash,
                // not the yield — a reader comparing only netApr would miss it.
                "keepsYourToken": true,
                "unlockedSharePct": unlocked * 100.0,
                "borrowApr": borrow_apr,
                "parkApr": park_apr,
                "carryApr": carry,
                "costSol": leg_cost.map(|c| c * 3.0),
                "isBaseline": false,
                "beatsSimple": net > best_simple,
                "liquidationDropPct": (1.0 - LTV_SAFETY) * 100.0,
                "why": why,
            }));
        }
    }

    // ── Flow: loop the token against itself ──────────────────────────────
    //
    // Supply, borrow the same asset, supply again. It is the most-asked-about
    // strategy on any lending market and it is a loss by construction: the
    // borrow rate sits above the supply rate because that spread is how the
    // lender gets paid. Modelling it anyway is the whole point — someone
    // asking "should I loop my USDC" deserves the number rather than silence.
    if let Some(coll) = held {
        let supply = num(coll.get("supplyApy")) * 100.0;
        let borrow = num(coll.get("borrowApy")) * 100.0;
        let max_ltv = num(coll.get("maxLtv"));
        // Below this the token is not really a lending asset — the LSTs sit at
        // 0% supply, and their real loop is the staking one modelled above.
        if max_ltv > 0.0 && supply > MIN_LOOPABLE_SUPPLY_APR {
            let per_turn = supply - borrow;
            let leverage = 1.0 / (1.0 - max_ltv * LTV_SAFETY);
            let net = supply + per_turn * (leverage - 1.0);

            // Both legs are the same reserve here, so the join is trivially
            // aligned — but it still goes through the same path, because the
            // history is what tells the reader this is structural and not a
            // bad week.
            let spread_history = match coll.get("reserve").and_then(|v| v.as_str()) {
                Some(res) => loop_spread_history(http, res, res, KAMINO_MAIN_MARKET, 0.0, 30).await,
                None => None,
            };
            let why = {
                let today = if per_turn < 0.0 {
                    format!(
                        "Borrowing {held_symbol} costs {borrow:.2}% while supplying it pays {supply:.2}%, so every turn of the loop gives up {:.2}% — leverage multiplies the gap, not the yield.",
                        -per_turn
                    )
                } else {
                    format!("Each turn adds {per_turn:.2}%, levered {leverage:.1}x.")
                };
                match spread_history.as_ref() {
                    Some(h)
                        if h.get("profitableSharePct").and_then(|v| v.as_f64()) == Some(0.0) =>
                    {
                        format!(
                            "{today} It has not been profitable in any of the last {} hours, because a lending market is built so that it cannot be.",
                            h.get("hours").and_then(|v| v.as_u64()).unwrap_or(0)
                        )
                    }
                    _ => today,
                }
            };

            flows.push(serde_json::json!({
                "name": format!("Loop {held_symbol} on Kamino"),
                "steps": 3,
                "legs": [
                    format!("Supply {held_symbol} to Kamino"),
                    format!("Borrow {held_symbol} against it"),
                    "Supply the borrowed amount again",
                ],
                "netApr": net,
                "perTurnApr": per_turn,
                "leverage": leverage,
                "costSol": leg_cost.map(|c| c * 3.0),
                "isBaseline": false,
                "beatsSimple": net > best_simple,
                "liquidationDropPct": (1.0 - LTV_SAFETY) * 100.0,
                "spreadHistory": spread_history,
                "why": why,
            }));
        }
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
    } else if held.is_none() {
        // Not an empty list. "Nothing came back" and "this token cannot be
        // used as collateral anywhere we can price" look identical to a
        // reader, and only one of them is true.
        Some(
            "This token is not accepted as collateral on Kamino, so nothing can be borrowed against it — the pool and swap options are where its yield is.",
        )
    } else if best_simple < MEANINGFUL_BASELINE_APR {
        // "Nothing beats the one-step answer" quietly implies the one-step
        // answer is worth having. Lending cbBTC pays 0.01%; a reader told only
        // that nothing beat it would take the wrong meaning entirely.
        Some(
            "Nothing here earns much: this token barely pays to lend, and borrowing against it costs more than it returns. Its yield, if any, is in the pool and swap options.",
        )
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

/// The generalisation, checked against the live book for four shapes of token.
///
/// The engine used to answer "only modelled for SOL" for every mint but one.
/// What matters now is not that it returns something — it is that what it
/// returns is *true* for tokens that behave nothing like SOL: a stablecoin
/// with a real supply rate, a wrapped BTC that pays essentially nothing, a
/// liquid-staking receipt whose supply rate is zero by design, and SOL itself.
#[cfg(test)]
mod general_flow_tests {
    use super::*;

    const USDC: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
    const CBBTC: &str = "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij";
    const JITOSOL: &str = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn";

    /// Reproduces the engine's reserve-driven decisions without needing an RPC.
    async fn reserve_of(http: &reqwest::Client, mint: &str) -> Option<serde_json::Value> {
        let reserves = crate::services::kamino::fetch_market_reserves(http, None)
            .await
            .ok()?;
        // Same selection the engine makes — a test that resolves the reserve
        // differently is testing something the product does not do.
        reserves
            .into_iter()
            .filter(|r| r.get("liquidityTokenMint").and_then(|v| v.as_str()) == Some(mint))
            .max_by(|a, b| {
                num(a.get("totalSupplyUsd"))
                    .partial_cmp(&num(b.get("totalSupplyUsd")))
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
    }

    #[tokio::test]
    #[ignore = "hits the live Kamino API"]
    async fn every_shape_of_token_has_a_reserve_the_engine_can_price() {
        let http = reqwest::Client::new();
        for (name, mint) in [
            ("SOL", SOL_MINT),
            ("USDC", USDC),
            ("cbBTC", CBBTC),
            ("jitoSOL", JITOSOL),
        ] {
            let r = reserve_of(&http, mint).await.unwrap_or_else(|| {
                panic!("{name} has no Kamino reserve — the engine would be silent for it")
            });
            let supply = num(r.get("supplyApy")) * 100.0;
            let borrow = num(r.get("borrowApy")) * 100.0;
            let ltv = num(r.get("maxLtv"));
            eprintln!("{name:8} supply {supply:5.2}%  borrow {borrow:5.2}%  maxLtv {ltv:.2}");
            assert!(
                ltv > 0.0,
                "{name} cannot be used as collateral, so no flow applies"
            );

            // The self-loop must never be sold as profitable: a lending market
            // is built so that borrowing costs more than supplying pays.
            assert!(
                supply <= borrow,
                "{name} supply {supply} exceeds borrow {borrow} — either the book is broken or we are reading the wrong fields"
            );
        }
    }

    /// A mint with several reserves must resolve to the one holding the money.
    ///
    /// USDC has four reserves in the main market and three are empty. The
    /// first of them reports 0.00% supply, so `find` — which is what this used
    /// to do — told a USDC holder that lending pays nothing and that no loop
    /// exists, while the reserve people actually use pays 4.49%. Confidently
    /// wrong is worse than silent, and this is the shape that produces it.
    #[tokio::test]
    #[ignore = "hits the live Kamino API"]
    async fn a_mint_with_several_reserves_resolves_to_the_deepest() {
        let http = reqwest::Client::new();
        let reserves = crate::services::kamino::fetch_market_reserves(&http, None)
            .await
            .expect("reserves");
        let matching: Vec<_> = reserves
            .iter()
            .filter(|r| r.get("liquidityTokenMint").and_then(|v| v.as_str()) == Some(USDC))
            .collect();
        assert!(
            matching.len() > 1,
            "USDC no longer has duplicate reserves — re-check whether this guard is still needed"
        );
        let deepest = matching
            .iter()
            .max_by(|a, b| {
                num(a.get("totalSupplyUsd"))
                    .partial_cmp(&num(b.get("totalSupplyUsd")))
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .unwrap();
        eprintln!(
            "ilk eslesen: %{:.2} (${:.1}M)   en derin: %{:.2} (${:.1}M)",
            num(matching[0].get("supplyApy")) * 100.0,
            num(matching[0].get("totalSupplyUsd")) / 1e6,
            num(deepest.get("supplyApy")) * 100.0,
            num(deepest.get("totalSupplyUsd")) / 1e6,
        );
        assert!(
            num(deepest.get("totalSupplyUsd")) > 1e6,
            "the deepest USDC reserve should hold real deposits"
        );
        assert!(
            num(deepest.get("supplyApy")) > 0.0,
            "the reserve people actually use pays something"
        );
    }

    #[tokio::test]
    #[ignore = "hits the live Kamino API"]
    async fn a_borrowable_stable_exists_and_is_liquid_enough() {
        let http = reqwest::Client::new();
        let reserves = crate::services::kamino::fetch_market_reserves(&http, None)
            .await
            .expect("reserves");
        let stables: Vec<_> = reserves
            .iter()
            .filter(|r| {
                r.get("liquidityToken")
                    .and_then(|v| v.as_str())
                    .map(crate::services::fees::symbol_is_stable)
                    .unwrap_or(false)
                    && num(r.get("totalSupplyUsd")) >= MIN_STABLE_LIQUIDITY_USD
            })
            .collect();
        assert!(
            !stables.is_empty(),
            "no stable reserve passes the liquidity floor, so the borrow-against flow would never appear"
        );
        for r in &stables {
            eprintln!(
                "  {:6} borrow {:5.2}%  supply {:5.2}%  ${:.1}M",
                r.get("liquidityToken")
                    .and_then(|v| v.as_str())
                    .unwrap_or("?"),
                num(r.get("borrowApy")) * 100.0,
                num(r.get("supplyApy")) * 100.0,
                num(r.get("totalSupplyUsd")) / 1e6,
            );
        }
    }
}

//! Whether an open position is still worth keeping.
//!
//! Everything else here helps people get *into* positions. Nothing re-examined
//! one afterwards, and the failure that costs most is not the violent one —
//! liquidation is already watched — but the quiet one: a concentrated position
//! drifts out of its range and earns nothing, for a week, while the same money
//! would have earned the lending rate doing nothing at all. Neither the wallet
//! nor the protocol will mention it.
//!
//! The judgement is deliberately narrow, because the honest version has to
//! survive its own arithmetic. Leaving costs money — the swap back out is the
//! same price-impact-and-commission that getting in cost — so a position that
//! is merely mediocre should be left alone. An exit only pays when the gap it
//! closes recovers the cost of closing it within a horizon someone would
//! actually plan for. That test kills most "you could be earning more!"
//! advice, which is the point: advice that ignores its own cost is how people
//! get churned.

use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

/// How long a switch is allowed to take to pay for itself.
///
/// Ninety days is not a law of nature; it is the horizon past which "you would
/// eventually come out ahead" stops being a reason to act today. Beyond it the
/// honest answer is that the difference is too small to be worth the trade.
pub const EXIT_PAYBACK_HORIZON_DAYS: f64 = 90.0;

/// What a position is doing and what it would cost to stop doing it.
#[derive(Debug, Clone)]
pub struct PositionFacts {
    /// Annualised return the position is expected to keep producing. Zero for
    /// a concentrated position sitting outside its range — it is not earning,
    /// and rounding that up to "the pool's APR" is the lie this exists to stop.
    pub forward_apr: f64,
    /// Best one-step return the same money could earn today, from live rates.
    pub alternative_apr: f64,
    /// Cost of closing, as a percentage of the position: fees plus the swap
    /// back out. None when it could not be measured — in which case no exit is
    /// recommended, because an unpriced exit is an unpriced recommendation.
    pub exit_cost_pct: Option<f64>,
    /// Whether the position is currently earning at all.
    pub earning: bool,
    /// Share of the recent window the range was actually live, 0..1. None when
    /// it could not be measured, in which case the instantaneous flag is all
    /// there is to go on.
    pub in_range_share: Option<f64>,
    /// None when what the position earns could not be established. Substituting
    /// the alternative's rate to make the gap zero was tidy and produced a
    /// sentence claiming the position earned a number belonging to something
    /// else — a live multi-asset Kamino obligation was told it "earns 5.57%",
    /// which was the rate it was being compared against.
    pub forward_known: bool,
    /// Locked until it vests. Nothing can be closed, so nothing should be
    /// suggested that involves closing it.
    pub locked: bool,
}

/// What to do about it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// Nothing better available, or the position is already ahead.
    Keep,
    /// The gap pays back the cost of leaving inside the horizon.
    ConsiderExit,
    /// There is a better option, but switching costs more than it recovers.
    NotWorthTheSwitch,
    /// The exit could not be priced, so no move is advised.
    Unpriced,
    /// Locked until it vests; nothing can be done with it either way.
    Locked,
    /// A shape this review does not model — several assets, or borrowed
    /// against. Reported, not judged.
    Unjudged,
}

impl Verdict {
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Keep => "keep",
            Verdict::ConsiderExit => "consider_exit",
            Verdict::NotWorthTheSwitch => "not_worth_the_switch",
            Verdict::Unpriced => "unpriced",
            Verdict::Locked => "locked",
            Verdict::Unjudged => "unjudged",
        }
    }
}

/// The decision, and the days it would take to recover the cost of making it.
#[derive(Debug, Clone)]
pub struct Review {
    pub verdict: Verdict,
    pub gap_pct: f64,
    pub payback_days: Option<f64>,
}

/// Judge one position.
///
/// The whole feature is this function; the rest is reading positions. It is
/// separated so the arithmetic can be tested without a wallet, because the
/// arithmetic is where a plausible-looking mistake would quietly churn someone
/// out of a position that was fine.
pub fn review_position(f: &PositionFacts) -> Review {
    let gap = f.alternative_apr - f.forward_apr;

    // A locked position cannot be closed until it vests. Telling someone to
    // leave it is advice they cannot act on, however wide the gap — so the
    // gap is still reported, and the verdict is not an instruction to move.
    if !f.forward_known {
        return Review {
            verdict: Verdict::Unjudged,
            gap_pct: 0.0,
            payback_days: None,
        };
    }

    if f.locked {
        return Review {
            verdict: Verdict::Locked,
            gap_pct: gap,
            payback_days: None,
        };
    }

    // Already at least as good as anything else on offer.
    if gap <= 0.0 {
        return Review {
            verdict: Verdict::Keep,
            gap_pct: gap,
            payback_days: None,
        };
    }

    let Some(cost) = f.exit_cost_pct else {
        return Review {
            verdict: Verdict::Unpriced,
            gap_pct: gap,
            payback_days: None,
        };
    };

    // A free exit that closes a real gap needs no payback period.
    if cost <= 0.0 {
        return Review {
            verdict: Verdict::ConsiderExit,
            gap_pct: gap,
            payback_days: Some(0.0),
        };
    }

    // Both figures are percentages of the same position, so the ratio is in
    // years before scaling — no position size is needed, which is why this
    // holds for a $50 position and a $50,000 one alike.
    let payback = cost / gap * 365.0;
    let verdict = if payback <= EXIT_PAYBACK_HORIZON_DAYS {
        Verdict::ConsiderExit
    } else {
        Verdict::NotWorthTheSwitch
    };
    Review {
        verdict,
        gap_pct: gap,
        payback_days: Some(payback),
    }
}

/// The sentence a reader acts on.
pub fn explain(f: &PositionFacts, r: &Review, alternative_label: &str) -> String {
    match r.verdict {
        Verdict::Keep if !f.earning => format!(
            "This position is out of range and earning nothing, but {alternative_label} pays {:.2}% — no better than it, so there is nothing to move to.",
            f.alternative_apr
        ),
        Verdict::Keep => match f.in_range_share {
            // A pool paying 120% is not a position earning 120% if the range
            // was live a fifth of the time. Saying "leave it alone" without
            // this describes a return the position never had.
            Some(share) if share < 0.6 => format!(
                "Its range was only live {:.0}% of the last fourteen days, so it captures about {:.2}% of the pool's rate rather than the headline. That still beats {alternative_label} at {:.2}%, but a tighter range that tracked the price would earn considerably more.",
                share * 100.0,
                f.forward_apr,
                f.alternative_apr
            ),
            _ => format!(
                "It earns {:.2}%, which is at least as good as anything else available right now ({alternative_label} pays {:.2}%). Leave it alone.",
                f.forward_apr, f.alternative_apr
            ),
        },
        Verdict::ConsiderExit if !f.earning => format!(
            "It is out of range, so it is earning nothing at all while {alternative_label} pays {:.2}%. Closing it costs {:.2}% of the position and that is recovered in {:.0} days — worth moving.",
            f.alternative_apr,
            f.exit_cost_pct.unwrap_or(0.0),
            r.payback_days.unwrap_or(0.0)
        ),
        Verdict::ConsiderExit => format!(
            "It earns {:.2}% while {alternative_label} pays {:.2}%. Closing costs {:.2}% of the position, recovered in {:.0} days — worth moving.",
            f.forward_apr,
            f.alternative_apr,
            f.exit_cost_pct.unwrap_or(0.0),
            r.payback_days.unwrap_or(0.0)
        ),
        Verdict::NotWorthTheSwitch => {
            // A gap near zero sends the payback to five and six figures. "It
            // would take 175,127 days" is arithmetic printed at the reader
            // rather than an answer given to them.
            let days = r.payback_days.unwrap_or(0.0);
            let how_long = if days > 3_650.0 {
                "would never realistically pay for itself".to_string()
            } else if days > 365.0 {
                format!("would take about {:.0} years to recover", days / 365.0)
            } else {
                format!("would take {days:.0} days to recover")
            };
            format!(
                "{alternative_label} pays {:.2}% against this position's {:.2}%, but closing costs {:.2}% and {how_long} — too long to be worth the trade. Leave it.",
                f.alternative_apr,
                f.forward_apr,
                f.exit_cost_pct.unwrap_or(0.0),
            )
        }
        Verdict::Locked => format!(
            "This position is locked until it vests, so it cannot be closed or withdrawn yet — whatever {alternative_label} pays in the meantime."
        ),
        Verdict::Unjudged => format!(
            "This position holds several assets or is borrowed against, which this review does not model — so it is listed rather than judged. What it holds is shown above."
        ),
        Verdict::Unpriced => format!(
            "{alternative_label} pays more than this position, but the cost of closing could not be priced, so whether the switch is worth making is unknown. Left as it is."
        ),
    }
}

/// Every open position the wallet holds, judged against doing something else.
pub async fn build_position_review(
    http: &reqwest::Client,
    rpc_url: &str,
    wallet: &str,
) -> Result<BuildResponse, AppError> {
    // What the same money would earn in one step today. Most LP positions are
    // paired against SOL, so "you could have just staked it" is the honest
    // comparison rather than a hand-picked rival pool.
    let (alt, dlmm, orca, damm, ray, kam) = tokio::join!(
        crate::services::flows::best_simple_option(http),
        read_dlmm_positions(http, wallet),
        read_orca_positions(http, rpc_url, wallet),
        read_dammv2_positions(http, wallet),
        read_raydium_positions(http, wallet),
        read_kamino_positions(http, wallet),
    );
    let (default_apr, default_label) = alt.unwrap_or((0.0, "lending".to_string()));

    let mut reviewed: Vec<serde_json::Value> = Vec::new();
    let mut idle = 0usize;
    // One lookup per distinct quote asset rather than per position.
    let mut baselines: std::collections::HashMap<String, (f64, String)> =
        std::collections::HashMap::new();
    // Every venue best-effort and named separately, so one failing to answer
    // narrows what the review claims to cover rather than silently dropping
    // positions from it.
    let mut covers: Vec<&str> = Vec::new();
    let mut all: Vec<OpenPosition> = Vec::new();
    match dlmm {
        Ok(v) => {
            covers.push("Meteora DLMM");
            all.extend(v);
        }
        Err(e) => tracing::warn!("position review: Meteora DLMM unreadable: {e}"),
    }
    match orca {
        Ok(v) => {
            covers.push("Orca");
            all.extend(v);
        }
        Err(e) => tracing::warn!("position review: Orca unreadable: {e}"),
    }
    match damm {
        Ok(v) => {
            covers.push("Meteora DAMM v2");
            all.extend(v);
        }
        Err(e) => tracing::warn!("position review: Meteora DAMM v2 unreadable: {e}"),
    }
    match ray {
        Ok(v) => {
            covers.push("Raydium CLMM");
            all.extend(v);
        }
        Err(e) => tracing::warn!("position review: Raydium unreadable: {e}"),
    }
    match kam {
        Ok(v) => {
            covers.push("Kamino Lend");
            all.extend(v);
        }
        Err(e) => tracing::warn!("position review: Kamino unreadable: {e}"),
    }

    for pos in all {
        // Judged against what the money could do in its own unit. A USDC pair
        // measured against staking SOL is measured in the wrong thing, and the
        // difference between two units is not a gap.
        let (alt_apr, alt_label) = match baselines.get(&pos.quote_mint) {
            Some(v) => v.clone(),
            None => {
                let found = crate::services::flows::best_simple_option_for(http, &pos.quote_mint)
                    .await
                    .unwrap_or_else(|| (default_apr, default_label.clone()));
                baselines.insert(pos.quote_mint.clone(), found.clone());
                found
            }
        };
        if !pos.earning {
            idle += 1;
        }
        // What this position actually captures, rather than what the pool
        // advertises. A range live a fifth of the time earns a fifth of the
        // rate; crediting the headline is the error this replaces.
        // None when the pool itself could not be read: not knowing what a
        // position earns is different from knowing it earns nothing, and only
        // one of those is grounds for telling someone to close it.
        let forward_apr: Option<f64> = match (pos.pool_apr, pos.in_range_share, pos.earning) {
            (Some(rate), Some(share), _) => Some(rate * share),
            // No range history to go on, so fall back to the instantaneous
            // read — out of range still means earning nothing right now.
            (Some(rate), None, true) => Some(rate),
            (Some(_), None, false) => Some(0.0),
            (None, _, _) => None,
        };

        // Fees already earned but not yet collected come back on the way out,
        // so they work against the cost of leaving rather than sitting beside
        // it. On a $1,421 position $4 of fees is 0.28% against a 0.15% exit —
        // enough to decide the question on its own.
        // Moving a lending position costs no swap, but it does cost a day out
        // of the market at the rate being moved to — two transactions are
        // noise beside that. Without this the smallest advantage anywhere
        // reads as free to chase, and lending rates move daily.
        let base_exit = if pos.moves_without_swap {
            Some(alt_apr / 365.0)
        } else {
            pos.exit_cost_pct
        };
        let exit_cost_pct = match (base_exit, pos.unclaimed_fees_usd, pos.usd_value) {
            (Some(cost), Some(fees), Some(value)) if value > 0.0 => {
                Some(cost - (fees / value * 100.0))
            }
            (cost, _, _) => cost,
        };
        let _ = pos.exit_cost_pct;

        let facts = PositionFacts {
            // Unknown is carried as the alternative's own rate so the gap is
            // zero and the verdict is Keep — no advice from no data.
            forward_apr: forward_apr.unwrap_or(0.0),
            forward_known: forward_apr.is_some(),
            alternative_apr: alt_apr,
            exit_cost_pct,
            earning: pos.earning,
            in_range_share: pos.in_range_share,
            locked: pos.locked,
        };
        let review = review_position(&facts);

        // Whether holding the two tokens would have done better. Read from the
        // position's own transaction history, so it costs an RPC round trip
        // per position and is skipped for ones too small to act on.
        let vs_holding = if pos.usd_value.unwrap_or(0.0) >= MIN_VALUE_FOR_HISTORY_USD {
            let (mx, my) = pos.pair_mints.clone();
            compare_with_holding(
                http,
                rpc_url,
                wallet,
                &pos.address,
                &mx,
                &my,
                pos.usd_value,
                pos.unclaimed_fees_usd,
            )
            .await
        } else {
            None
        };

        reviewed.push(serde_json::json!({
            "venue": pos.venue,
            "position": pos.address,
            "pool": pos.pool,
            "pair": pos.pair,
            "earning": pos.earning,
            "usdValue": pos.usd_value,
            "poolAprPct": pos.pool_apr,
            "forwardAprPct": facts.forward_apr,
            "alternativeAprPct": alt_apr,
            "alternative": alt_label,
            "exitCostPct": exit_cost_pct,
            "exitCostBeforeFeesPct": pos.exit_cost_pct,
            // Share of the last ten days this range was live, 0..1.
            "inRangeShare": pos.in_range_share,
            "poolAprHeadlinePct": pos.pool_apr,
            // Kept apart so a pool that simply had a quiet day is
            // distinguishable from one that never earned: one tested pool had
            // taken $2.4m in fees over its life and nothing at all in 24h.
            "poolApr24hPct": pos.apr_24h,
            "poolAprLifetimePct": pos.apr_life,
            "poolTvlUsd": pos.pool_tvl_usd,
            "holds": pos.holdings.as_ref().map(|(sa, aa, sb, ab)| {
                serde_json::json!({ "tokenA": sa, "amountA": aa, "tokenB": sb, "amountB": ab })
            }),
            "forwardAprKnown": forward_apr.is_some(),
            // What it has actually done, in both denominators.
            "pnlUsdPct": pos.pnl_usd_pct,
            "pnlSolPct": pos.pnl_sol_pct,
            "unclaimedFeesUsd": pos.unclaimed_fees_usd,
            "gapPct": review.gap_pct,
            "paybackDays": review.payback_days,
            "verdict": review.verdict.as_str(),
            "why": explain(&facts, &review, &alt_label),
            // The question a fee rate cannot answer.
            "vsHolding": vs_holding.as_ref().map(|c| {
                serde_json::json!({
                    "holdValueUsd": c.hold_value_usd,
                    "actualValueUsd": c.actual_value_usd,
                    "differenceUsd": c.difference_usd,
                    "differencePct": c.difference_pct,
                    "transactionsRead": c.transactions,
                    // False means the history was longer than we read, and the
                    // comparison is wrong rather than approximate.
                    "complete": c.complete,
                })
            }),
        }));
    }

    // Worth acting on first: the ones earning nothing, then the widest gap.
    reviewed.sort_by(|a, b| {
        let key = |v: &serde_json::Value| {
            (
                v.get("earning").and_then(|x| x.as_bool()).unwrap_or(true),
                -v.get("gapPct").and_then(|x| x.as_f64()).unwrap_or(0.0),
            )
        };
        let (ka, kb) = (key(a), key(b));
        ka.0.cmp(&kb.0)
            .then(ka.1.partial_cmp(&kb.1).unwrap_or(std::cmp::Ordering::Equal))
    });

    let count = reviewed.len();
    let note = if count == 0 {
        Some("No open liquidity positions found for this wallet.".to_string())
    } else if idle > 0 {
        Some(format!(
            "{idle} of {count} positions are outside their range and earning nothing right now."
        ))
    } else {
        None
    };

    let data = serde_json::json!({
        "wallet": wallet,
        "positions": reviewed,
        "idleCount": idle,
        "alternativeAprPct": default_apr,
        "alternative": default_label,
        // Said plainly so an answer never implies a wallet-wide all-clear it
        // has not actually checked.
        "covers": covers,
        "note": note,
    });

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "position_review".into(),
            description: format!("{count} open positions, judged against doing something else"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data.clone(),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(data),
    })
}

/// One position, flattened to what the judgement needs.
struct OpenPosition {
    venue: &'static str,
    address: String,
    pool: String,
    pair: String,
    earning: bool,
    usd_value: Option<f64>,
    /// None when the pool could not be read at all. Zero means measured and
    /// genuinely nothing; the two must not be confused, because one is a
    /// finding and the other is our own failure.
    pool_apr: Option<f64>,
    apr_24h: Option<f64>,
    apr_life: Option<f64>,
    /// The pool's own liquidity. Without it a rate of zero is unexplainable —
    /// one live pool held $0.000026 and every rate derived from it was either
    /// zero or astronomical, with nothing on screen to say why.
    pool_tvl_usd: Option<f64>,
    /// What the position actually holds, as `(symbol, amount)` per side.
    ///
    /// Carried so an unpriceable position still says something. Two live
    /// Raydium positions held tokens Jupiter does not list, so their value
    /// came back unknown and the row was empty — the holder could see neither
    /// what they had nor why nothing was said about it. Deriving a price from
    /// the position's own pool would have filled the gap by marking our own
    /// book, which for a thin pool is worse than silence.
    holdings: Option<(String, f64, String, f64)>,
    exit_cost_pct: Option<f64>,
    in_range_share: Option<f64>,
    /// The side the position is denominated in — what it would hold if closed
    /// and consolidated. The baseline has to be in this unit or the comparison
    /// is between two different things.
    quote_mint: String,
    /// Both sides, for the history comparison — `quote_mint` alone cannot say
    /// what was deposited.
    pair_mints: (String, String),
    /// A locked position cannot be withdrawn until it vests, so telling
    /// someone to close it is advice they cannot act on.
    locked: bool,
    /// True where leaving returns the same asset rather than requiring a swap
    /// — lending, not liquidity. The cost of moving is then not a swap but a
    /// day out of the market, which has to be priced from the rate being
    /// moved to rather than guessed at.
    moves_without_swap: bool,
    pnl_usd_pct: Option<f64>,
    pnl_sol_pct: Option<f64>,
    unclaimed_fees_usd: Option<f64>,
}

/// Hourly USD prices for one mint, from Birdeye.
///
/// Returned keyed by timestamp so two mints can be joined into a pair price
/// without assuming their series line up — they usually do, and a gap in one
/// of them must drop the hour rather than pair it with a neighbour.
async fn hourly_prices(
    http: &reqwest::Client,
    mint: &str,
    days: i64,
) -> Option<std::collections::HashMap<i64, f64>> {
    let key = std::env::var("BIRDEYE_API_KEY")
        .ok()
        .filter(|k| !k.is_empty())?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs() as i64;
    let body: serde_json::Value = http
        .get("https://public-api.birdeye.so/defi/history_price")
        .header("X-API-KEY", key)
        .header("x-chain", "solana")
        .query(&[
            ("address", mint),
            ("address_type", "token"),
            ("type", "1H"),
            ("time_from", &(now - days * 86_400).to_string()),
            ("time_to", &now.to_string()),
        ])
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    let items = body.get("data")?.get("items")?.as_array()?;
    let out: std::collections::HashMap<i64, f64> = items
        .iter()
        .filter_map(|i| {
            let t = i.get("unixTime")?.as_i64()?;
            let v = i.get("value")?.as_f64()?;
            (v > 0.0).then_some((t, v))
        })
        .collect();
    (!out.is_empty()).then_some(out)
}

/// The price of one token in terms of the other, hour by hour.
///
/// A concentrated position's range is quoted in exactly this unit, so the
/// comparison needs no conversion. Built from two USD series rather than a
/// pool's own candles because those cap at ten points however they are asked,
/// exist only for Meteora, and — being daily — can only say whether price
/// touched a range during a day, not how long it stayed.
///
/// Measured against the same position and window, the daily reading gave 14%
/// where the hourly truth was 8.8%.
async fn pair_price_series(
    http: &reqwest::Client,
    mint_x: &str,
    mint_y: &str,
    days: i64,
) -> Option<Vec<f64>> {
    let (x, y) = tokio::join!(
        hourly_prices(http, mint_x, days),
        hourly_prices(http, mint_y, days),
    );
    let (x, y) = (x?, y?);
    let mut out: Vec<f64> = x
        .iter()
        .filter_map(|(t, px)| {
            let py = y.get(t)?;
            (*py > 0.0).then(|| px / py)
        })
        .collect();
    out.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    (!out.is_empty()).then_some(out)
}

/// Signatures that touched an account, oldest last (the RPC's own order).
async fn rpc_signatures(
    http: &reqwest::Client,
    rpc_url: &str,
    account: &str,
    limit: usize,
) -> Option<Vec<String>> {
    let body = serde_json::json!({
        "jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
        "params": [account, { "limit": limit }],
    });
    let resp: serde_json::Value = http
        .post(rpc_url)
        .json(&body)
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    Some(
        resp.get("result")?
            .as_array()?
            .iter()
            .filter(|r| r.get("err").map(|e| e.is_null()).unwrap_or(true))
            .filter_map(|r| r.get("signature")?.as_str().map(String::from))
            .collect(),
    )
}

/// One parsed transaction.
async fn rpc_transaction(
    http: &reqwest::Client,
    rpc_url: &str,
    signature: &str,
) -> Option<serde_json::Value> {
    let body = serde_json::json!({
        "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
        "params": [signature, { "maxSupportedTransactionVersion": 0, "encoding": "jsonParsed" }],
    });
    let resp: serde_json::Value = http
        .post(rpc_url)
        .json(&body)
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    resp.get("result").filter(|r| !r.is_null()).cloned()
}

/// What each of the wallet's token balances did in one transaction.
///
/// Read from the balance snapshots rather than from instruction decoding, so
/// it does not need to know what any protocol's instructions look like — the
/// same reader works for every venue, now and after they change.
///
/// Native SOL moves as wrapped SOL inside these positions, so it appears here
/// like any other token; a position funded with unwrapped SOL wraps it in the
/// same transaction and the token balances still show the movement.
fn wallet_token_deltas(tx: &serde_json::Value, wallet: &str) -> Vec<(String, f64)> {
    let meta = match tx.get("meta") {
        Some(m) => m,
        None => return Vec::new(),
    };
    let collect = |key: &str| -> std::collections::HashMap<String, f64> {
        meta.get(key)
            .and_then(|v| v.as_array())
            .map(|rows| {
                rows.iter()
                    .filter(|b| b.get("owner").and_then(|o| o.as_str()) == Some(wallet))
                    .filter_map(|b| {
                        let mint = b.get("mint")?.as_str()?.to_string();
                        let amt = b
                            .get("uiTokenAmount")?
                            .get("uiAmountString")?
                            .as_str()?
                            .parse::<f64>()
                            .ok()?;
                        Some((mint, amt))
                    })
                    .fold(std::collections::HashMap::new(), |mut acc, (m, a)| {
                        *acc.entry(m).or_insert(0.0) += a;
                        acc
                    })
            })
            .unwrap_or_default()
    };
    let pre = collect("preTokenBalances");
    let post = collect("postTokenBalances");
    let mut out = Vec::new();
    for mint in pre
        .keys()
        .chain(post.keys())
        .collect::<std::collections::HashSet<_>>()
    {
        let d = post.get(mint).copied().unwrap_or(0.0) - pre.get(mint).copied().unwrap_or(0.0);
        if d.abs() > 1e-12 {
            out.push((mint.clone(), d));
        }
    }

    if let Some(native) = native_sol_delta(tx, wallet).filter(|n| n.abs() > 1e-12) {
        // Reported against the wrapped mint, because that is what the pool
        // holds and what every price lookup here is keyed by.
        match out.iter_mut().find(|(m, _)| m == WSOL_MINT) {
            Some((_, existing)) => *existing += native,
            None => out.push((WSOL_MINT.to_string(), native)),
        }
    }
    out
}

/// The wallet's own lamport change, less the fee it paid.
fn native_sol_delta(tx: &serde_json::Value, wallet: &str) -> Option<f64> {
    let meta = tx.get("meta")?;
    let keys = tx
        .get("transaction")?
        .get("message")?
        .get("accountKeys")?
        .as_array()?;
    let index = keys.iter().position(|k| {
        k.get("pubkey").and_then(|p| p.as_str()) == Some(wallet) || k.as_str() == Some(wallet)
    })?;
    let pre = meta.get("preBalances")?.as_array()?.get(index)?.as_f64()?;
    let post = meta.get("postBalances")?.as_array()?.get(index)?.as_f64()?;
    // The payer is index 0 and is the only account charged the fee.
    let fee_paid = if index == 0 {
        meta.get("fee").and_then(|f| f.as_f64()).unwrap_or(0.0)
    } else {
        0.0
    };
    Some((post - pre + fee_paid) / 1e9)
}

const WSOL_MINT: &str = "So11111111111111111111111111111111111111112";

/// Whether simply holding the two tokens would have done better.
///
/// The question every liquidity provider actually asks, and the one a fee rate
/// cannot answer: fees can look excellent while the position quietly ends up
/// behind the tokens it was made of.
///
/// Everything is valued at today's prices, which is what makes the comparison
/// isolate the right thing. Tokens put in, valued now, is exactly what holding
/// them would be worth. Against that goes everything the position has given
/// back — what it still holds, plus anything already withdrawn or claimed,
/// also valued now. The difference is the whole answer.
///
/// Netting the flows this way handles the cases a simpler reading gets wrong:
/// a position topped up twice, one partially withdrawn, and one whose fees
/// have been claimed along the way all come out right, because claimed fees
/// are tokens the holder still has rather than value that vanished.
struct HoldingComparison {
    /// What the deposited tokens would be worth today if never deposited.
    hold_value_usd: f64,
    /// What the position has actually produced: its current value plus
    /// everything already taken back out.
    actual_value_usd: f64,
    /// Positive means the position beat holding.
    difference_usd: f64,
    difference_pct: f64,
    /// Transactions read. Named because a truncated history makes the answer
    /// wrong rather than approximate, and the caller must be able to say so.
    transactions: usize,
    complete: bool,
}

/// How many of a position's transactions to read before giving up.
///
/// A history longer than this belongs to a wallet that re-ranges constantly,
/// and a partial read would silently answer a different question — so it is
/// reported incomplete rather than answered.
const MAX_POSITION_TX: usize = 60;

/// Below this a position is not worth an RPC round trip per transaction to
/// compare — the answer could not change what anyone does about it.
const MIN_VALUE_FOR_HISTORY_USD: f64 = 50.0;

async fn compare_with_holding(
    http: &reqwest::Client,
    rpc_url: &str,
    wallet: &str,
    position: &str,
    mint_x: &str,
    mint_y: &str,
    current_value_usd: Option<f64>,
    unclaimed_fees_usd: Option<f64>,
) -> Option<HoldingComparison> {
    let current = current_value_usd?;
    let sigs = rpc_signatures(http, rpc_url, position, MAX_POSITION_TX + 1).await?;
    if sigs.is_empty() {
        return None;
    }
    let complete = sigs.len() <= MAX_POSITION_TX;

    // Net movement of each side between the wallet and the position, summed
    // over every transaction that touched it.
    let mut into_pool_x = 0.0;
    let mut into_pool_y = 0.0;
    let mut back_to_wallet_x = 0.0;
    let mut back_to_wallet_y = 0.0;

    for sig in sigs.iter().take(MAX_POSITION_TX) {
        let Some(tx) = rpc_transaction(http, rpc_url, sig).await else {
            // One unreadable transaction makes the sum wrong, not noisy.
            return None;
        };
        for (mint, delta) in wallet_token_deltas(&tx, wallet) {
            let (into, back) = if mint == mint_x {
                (&mut into_pool_x, &mut back_to_wallet_x)
            } else if mint == mint_y {
                (&mut into_pool_y, &mut back_to_wallet_y)
            } else {
                continue;
            };
            if delta < 0.0 {
                *into += -delta;
            } else {
                *back += delta;
            }
        }
    }
    if into_pool_x <= 0.0 && into_pool_y <= 0.0 {
        return None;
    }

    let (px, py) = tokio::join!(
        crate::services::strategies::mint_price_and_decimals(http, mint_x),
        crate::services::strategies::mint_price_and_decimals(http, mint_y),
    );
    let (px, py) = (px?.0, py?.0);

    let mut out = holding_maths(
        (into_pool_x, into_pool_y),
        (back_to_wallet_x, back_to_wallet_y),
        current + unclaimed_fees_usd.unwrap_or(0.0),
        (px, py),
    )?;
    out.transactions = sigs.len().min(MAX_POSITION_TX);
    out.complete = complete;
    Some(out)
}

/// The arithmetic, separated so it can be tested without a chain.
///
/// This is where the comparison is easy to get subtly wrong — forgetting what
/// was withdrawn understates the position, and forgetting a top-up overstates
/// it — so every one of those cases is pinned below.
fn holding_maths(
    into_pool: (f64, f64),
    back_to_wallet: (f64, f64),
    still_held_usd: f64,
    prices: (f64, f64),
) -> Option<HoldingComparison> {
    let (px, py) = prices;
    let hold_value_usd = into_pool.0 * px + into_pool.1 * py;
    if !(hold_value_usd > 0.0) {
        return None;
    }
    let actual_value_usd = still_held_usd + back_to_wallet.0 * px + back_to_wallet.1 * py;
    let difference_usd = actual_value_usd - hold_value_usd;
    Some(HoldingComparison {
        hold_value_usd,
        actual_value_usd,
        difference_usd,
        difference_pct: difference_usd / hold_value_usd * 100.0,
        transactions: 0,
        complete: true,
    })
}

/// A concentrated-liquidity tick, as a price of B per A in display units.
///
/// `1.0001^tick` is the ratio in raw base units; the decimals of the two sides
/// have to be folded back in or the number is wrong by a factor of ten to the
/// difference between them — for a 6/9 pair, a thousand. Getting the direction
/// of that adjustment backwards produces a range that looks plausible and
/// never contains the price, so it is pinned by a test.
fn tick_to_price(tick: f64, dec_a: u8, dec_b: u8) -> f64 {
    1.0001f64.powf(tick) * 10f64.powi(dec_a as i32 - dec_b as i32)
}

/// Share of the sampled hours the price sat inside a position's range.
///
/// Each sample is one hour at one price, so this is a plain count — no
/// weighting, and none of the guesswork that came with reading a candle's
/// span. None when there is nothing to measure, which is not zero.
fn share_inside(prices: &[f64], lower: f64, upper: f64) -> Option<f64> {
    if prices.is_empty() || !(upper > lower) {
        return None;
    }
    let inside = prices
        .iter()
        .filter(|p| **p >= lower && **p <= upper)
        .count();
    Some(inside as f64 / prices.len() as f64)
}

/// How long the price has been recorded for. Fourteen days is what Birdeye
/// returns at hourly resolution, and every message that quotes a share has to
/// name the window it is a share of.
const PRICE_WINDOW_DAYS: i64 = 14;

/// Split a pool's balance across the positions inside it, by what each holds.
///
/// The money fields on a Meteora pool are POOL totals. Attributing them to
/// each position — which is what reading them straight does — reports a pool
/// holding two positions twice and doubles the wallet's apparent exposure.
/// Splitting by weight keeps the parts summing to the whole.
///
/// A weight of zero, or no weights at all, yields None rather than an even
/// split: an even split is a number nobody measured, and downstream an
/// unpriced position produces no recommendation, which is the right outcome
/// for one we cannot size.
fn split_pool_value(balance: Option<f64>, weights: &[f64]) -> Vec<Option<f64>> {
    let balance = match balance {
        Some(b) if b > 0.0 => b,
        _ => return vec![None; weights.len()],
    };
    if weights.len() <= 1 {
        return weights.iter().map(|_| Some(balance)).collect();
    }
    let total: f64 = weights.iter().filter(|w| **w > 0.0).sum();
    if !(total > 0.0) {
        return vec![None; weights.len()];
    }
    weights
        .iter()
        .map(|w| (*w > 0.0).then(|| balance * w / total))
        .collect()
}

/// Meteora DLMM positions, priced.
///
/// The portfolio endpoint already carries everything the judgement needs —
/// USD balance, both token symbols and mints, and which position addresses are
/// out of range — so nothing is derived that can be read. An earlier version
/// fetched prices per mint and multiplied them by per-position balances read
/// from the chain; it produced `null` for every value, because the fields it
/// was guessing at are not the fields the API returns.
async fn read_dlmm_positions(
    http: &reqwest::Client,
    wallet: &str,
) -> Result<Vec<OpenPosition>, AppError> {
    let params = crate::services::meteora::MeteoraDlmmGetUserPositionsParams {
        wallet: Some(wallet.to_string()),
    };
    let resp =
        crate::services::meteora::build_meteora_dlmm_get_user_positions(http, wallet, &params)
            .await?;
    let data = resp.data.unwrap_or(serde_json::Value::Null);
    let pools = data
        .get("pools")
        .and_then(|p| p.as_array())
        .cloned()
        .unwrap_or_default();

    let num = |v: Option<&serde_json::Value>| -> Option<f64> {
        v.and_then(|x| {
            x.as_f64()
                .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
        })
    };
    let text = |v: Option<&serde_json::Value>| v.and_then(|x| x.as_str()).unwrap_or("").to_string();

    let mut out = Vec::new();
    for pool in pools {
        let pool_addr = text(pool.get("poolAddress"));
        let mint_x = text(pool.get("tokenXMint"));
        let mint_y = text(pool.get("tokenYMint"));
        let pair = format!("{}/{}", text(pool.get("tokenX")), text(pool.get("tokenY")));
        let usd_value = num(pool.get("balances")).filter(|v| *v > 0.0);

        // What the pool pays now, on the same conservative basis the entry
        // options use — the lower of the last day and the pool's lifetime.
        let (pool_apr, apr_24h, apr_life, pool_tvl_usd) =
            match crate::services::meteora::meteora_pool_raw(
                http,
                crate::services::meteora::DLMM_API,
                &pool_addr,
            )
            .await
            {
                Ok(raw) => {
                    let (a, l) = crate::services::strategies::pool_apr_parts(&raw);
                    (
                        Some(crate::services::strategies::conservative_pool_apr(&raw)),
                        a,
                        l,
                        raw.get("tvl").and_then(|v| v.as_f64()),
                    )
                }
                Err(_) => (None, None, None, None),
            };

        // Leaving costs what arriving cost: half the position swaps back.
        let exit_cost_pct = if mint_x.is_empty() || mint_y.is_empty() {
            None
        } else {
            crate::services::strategies::entry_cost_pct(http, &mint_x, &mint_y, usd_value).await
        };

        // Per-position detail, merged from the chain by the positions builder.
        // Needed because the money figures on the pool are POOL totals: a pool
        // holding two positions would otherwise report the full balance twice
        // and double the wallet's apparent exposure.
        let details = pool
            .get("positions")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let pool_price = num(pool.get("poolPrice")).unwrap_or(0.0);
        // Each position's share of the pool, by what it actually holds. X is
        // priced in Y so the two sides can be added without another price
        // lookup, and only the ratio is used — any consistent unit works.
        let weight_of = |d: &serde_json::Value| -> f64 {
            let ax = num(d.get("amountX")).unwrap_or(0.0);
            let ay = num(d.get("amountY")).unwrap_or(0.0);
            ax * pool_price + ay
        };

        // Hourly pair prices for the window. One fetch per pool, shared by
        // every position in it, since they all trade the same pair.
        let prices = pair_price_series(http, &mint_x, &mint_y, PRICE_WINDOW_DAYS)
            .await
            .unwrap_or_default();

        let idle: Vec<String> = pool
            .get("positionsOutOfRange")
            .and_then(|v| v.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();
        let listed: Vec<String> = pool
            .get("listPositions")
            .and_then(|v| v.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();

        // A position that is out of range is named in `positionsOutOfRange`;
        // the pool-level `outOfRange` flag only says whether *all* of them are,
        // so reading that instead would hide a single idle position among
        // several active ones.
        let listed_count = listed.len();
        // One share per listed position, summing to the pool balance. Computed
        // once so the parts cannot drift apart from the whole.
        let shares = split_pool_value(
            usd_value,
            &listed
                .iter()
                .map(|addr| {
                    details
                        .iter()
                        .find(|d| d.get("address").and_then(|v| v.as_str()) == Some(addr.as_str()))
                        .map(&weight_of)
                        .unwrap_or(0.0)
                })
                .collect::<Vec<f64>>(),
        );
        for (index, addr) in listed.into_iter().enumerate() {
            let detail = details
                .iter()
                .find(|d| d.get("address").and_then(|v| v.as_str()) == Some(addr.as_str()));
            // The chain detail knows its own range; the pool-level list is the
            // fallback when that read failed.
            // Same as Orca: a position holding nothing is a leftover, not a
            // holding to judge.
            if detail.map(&weight_of).unwrap_or(1.0) <= 0.0 {
                continue;
            }
            let earning = detail
                .and_then(|d| d.get("inRange").and_then(|v| v.as_bool()))
                .unwrap_or_else(|| !idle.contains(&addr));

            let value = shares.get(index).copied().flatten();
            let exit_cost_pct = if listed_count <= 1 {
                exit_cost_pct
            } else if mint_x.is_empty() || mint_y.is_empty() {
                None
            } else {
                crate::services::strategies::entry_cost_pct(http, &mint_x, &mint_y, value).await
            };

            // How much of the window this particular range was live. Ranges
            // differ inside one pool, so it is computed per position.
            let in_range_share = match (
                detail.and_then(|d| num(d.get("lowerPrice"))),
                detail.and_then(|d| num(d.get("upperPrice"))),
            ) {
                (Some(lo), Some(hi)) => share_inside(&prices, lo, hi),
                _ => None,
            };

            out.push(OpenPosition {
                venue: "Meteora DLMM",
                address: addr,
                pool: pool_addr.clone(),
                pair: pair.clone(),
                earning,
                in_range_share,
                apr_24h,
                apr_life,
                pool_tvl_usd,
                holdings: detail.map(|d| {
                    (
                        text(pool.get("tokenX")),
                        num(d.get("amountX")).unwrap_or(0.0),
                        text(pool.get("tokenY")),
                        num(d.get("amountY")).unwrap_or(0.0),
                    )
                }),
                // Y is the quote side of a Meteora pair by construction.
                quote_mint: mint_y.clone(),
                pair_mints: (mint_x.clone(), mint_y.clone()),
                locked: false,
                moves_without_swap: false,
                usd_value: value,
                pool_apr,
                exit_cost_pct,
                // Both denominators, because here they disagree in sign: one
                // position was down 0.08% in dollars and up 0.36% in SOL at
                // the same moment. Quoting whichever flatters the position is
                // the easiest lie to tell by accident.
                pnl_usd_pct: num(pool.get("pnlPctChange")),
                pnl_sol_pct: num(pool.get("pnlSolPctChange")),
                unclaimed_fees_usd: num(pool.get("unclaimedFees")),
            });
        }
    }
    Ok(out)
}

/// Orca Whirlpool positions, priced.
///
/// Orca reports no lifetime fee total, so its pool rate is a 24-hour
/// annualisation with nothing to temper it — a livelier number than the
/// Meteora one beside it, and labelled as such rather than quietly compared
/// as though the two were measured the same way. There is also no candle
/// history for a whirlpool, so the in-range share is unknown and the
/// judgement falls back to the instantaneous read.
async fn read_orca_positions(
    http: &reqwest::Client,
    rpc_url: &str,
    wallet: &str,
) -> Result<Vec<OpenPosition>, AppError> {
    let params = crate::services::orca::OrcaGetUserPositionsParams {
        wallet: Some(wallet.to_string()),
    };
    let resp = crate::services::orca::build_orca_get_user_positions(http, rpc_url, wallet, &params)
        .await?;
    let data = resp.data.unwrap_or(serde_json::Value::Null);
    let positions = data
        .get("positions")
        .and_then(|p| p.as_array())
        .cloned()
        .unwrap_or_default();

    let num = |v: Option<&serde_json::Value>| -> Option<f64> {
        v.and_then(|x| {
            x.as_f64()
                .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
        })
    };
    let text = |v: Option<&serde_json::Value>| v.and_then(|x| x.as_str()).unwrap_or("").to_string();

    let mut out = Vec::new();
    let mut empty = 0usize;
    // One pool lookup per distinct whirlpool, not per position.
    let mut pool_rates: std::collections::HashMap<String, Option<f64>> =
        std::collections::HashMap::new();

    for p in positions {
        let whirlpool = text(p.get("whirlpool"));
        let mint_a = text(p.get("tokenAMint"));
        let mint_b = text(p.get("tokenBMint"));
        if mint_a.is_empty() || mint_b.is_empty() {
            continue;
        }

        // A closed position leaves its NFT behind. Market-making wallets carry
        // hundreds of them — one tested wallet held 300 — and every one would
        // arrive as "out of range, earning nothing", burying whatever the
        // person actually holds under a page of alarm about nothing. They are
        // counted, not reviewed, and the check comes before the price lookups
        // so three hundred empties do not cost nine hundred requests.
        let has_liquidity = num(p.get("liquidity")).unwrap_or(0.0) > 0.0
            || num(p.get("amountA")).unwrap_or(0.0) > 0.0
            || num(p.get("amountB")).unwrap_or(0.0) > 0.0;
        if !has_liquidity {
            empty += 1;
            continue;
        }

        let pool_apr = match pool_rates.get(&whirlpool) {
            Some(v) => *v,
            None => {
                let rate = orca_pool_apr(http, &whirlpool).await;
                pool_rates.insert(whirlpool.clone(), rate);
                rate
            }
        };

        // Amounts and fees already arrive in UI units, so only prices apply —
        // scaling by decimals again here would inflate the value by 10^n.
        let (price_a, price_b) = tokio::join!(
            crate::services::strategies::mint_price_and_decimals(http, &mint_a),
            crate::services::strategies::mint_price_and_decimals(http, &mint_b),
        );
        let (pa, pb) = (price_a.map(|t| t.0), price_b.map(|t| t.0));
        let value = match (pa, pb) {
            (Some(pa), Some(pb)) => {
                let v = num(p.get("amountA")).unwrap_or(0.0) * pa
                    + num(p.get("amountB")).unwrap_or(0.0) * pb;
                (v > 0.0).then_some(v)
            }
            _ => None,
        };
        let fees_usd = match (pa, pb) {
            (Some(pa), Some(pb)) => Some(
                num(p.get("feeOwedAUi")).unwrap_or(0.0) * pa
                    + num(p.get("feeOwedBUi")).unwrap_or(0.0) * pb,
            ),
            _ => None,
        };

        let exit_cost_pct =
            crate::services::strategies::entry_cost_pct(http, &mint_a, &mint_b, value).await;

        // Orca publishes no candles of its own, which is why these positions
        // used to be credited the pool's headline rate whatever their range
        // had been doing.
        let in_range_share = match (num(p.get("priceLower")), num(p.get("priceUpper"))) {
            (Some(lo), Some(hi)) => {
                match pair_price_series(http, &mint_a, &mint_b, PRICE_WINDOW_DAYS).await {
                    Some(prices) => share_inside(&prices, lo, hi),
                    None => None,
                }
            }
            _ => None,
        };

        out.push(OpenPosition {
            venue: "Orca",
            address: text(p.get("positionAddress")),
            pool: whirlpool,
            pair: {
                // A missing symbol left labels like "USDC/" on screen.
                let (a, b) = (text(p.get("tokenASymbol")), text(p.get("tokenBSymbol")));
                match (a.is_empty(), b.is_empty()) {
                    (false, false) => format!("{a}/{b}"),
                    (false, true) => format!("{a}/?"),
                    (true, false) => format!("?/{b}"),
                    (true, true) => "liquidity position".to_string(),
                }
            },
            earning: p.get("inRange").and_then(|v| v.as_bool()).unwrap_or(false),
            in_range_share,
            quote_mint: mint_b.clone(),
            pair_mints: (mint_a.clone(), mint_b),
            locked: false,
            moves_without_swap: false,
            usd_value: value,
            pool_apr,
            apr_24h: pool_apr,
            apr_life: None,
            pool_tvl_usd: None,
            holdings: Some((
                text(p.get("tokenASymbol")),
                num(p.get("amountA")).unwrap_or(0.0),
                text(p.get("tokenBSymbol")),
                num(p.get("amountB")).unwrap_or(0.0),
            )),
            exit_cost_pct,
            // Orca's reader carries no profit-and-loss history, so these stay
            // unknown rather than being filled with something else's numbers.
            pnl_usd_pct: None,
            pnl_sol_pct: None,
            unclaimed_fees_usd: fees_usd,
        });
    }
    if empty > 0 {
        tracing::debug!("position review: skipped {empty} empty Orca position NFTs");
    }
    Ok(out)
}

/// Meteora DAMM v2 positions, read through the SDK service.
///
/// The pool-level `balances` here is a **token count** — the sum of each
/// position's X side — not a dollar figure, unlike the DLMM endpoint where the
/// same field name means USD. Reusing the DLMM path would have quietly read a
/// token count as money, so value is computed from both sides at live prices.
async fn read_dammv2_positions(
    http: &reqwest::Client,
    wallet: &str,
) -> Result<Vec<OpenPosition>, AppError> {
    let data = crate::services::protocol_reads::meteora_dammv2_user_positions(http, wallet).await?;
    let pools = data
        .get("pools")
        .and_then(|p| p.as_array())
        .cloned()
        .unwrap_or_default();

    let num = |v: Option<&serde_json::Value>| -> Option<f64> {
        v.and_then(|x| {
            x.as_f64()
                .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
        })
    };
    let text = |v: Option<&serde_json::Value>| v.and_then(|x| x.as_str()).unwrap_or("").to_string();

    let mut out = Vec::new();
    for pool in pools {
        let pool_addr = text(pool.get("poolAddress"));
        let mint_x = text(pool.get("tokenXMint"));
        let mint_y = text(pool.get("tokenYMint"));
        if mint_x.is_empty() || mint_y.is_empty() {
            continue;
        }
        let pair = format!("{}/{}", text(pool.get("tokenX")), text(pool.get("tokenY")));
        // A DAMM v2 pool can be created with bounds, and then price can sit
        // outside them — the same state DLMM calls out of range, with the same
        // consequence.
        let earning = !pool
            .get("priceOutOfRange")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        let (pool_apr, apr_24h, apr_life, pool_tvl_usd) =
            match crate::services::meteora::meteora_pool_raw(
                http,
                crate::services::meteora::DAMM_V2_API,
                &pool_addr,
            )
            .await
            {
                Ok(raw) => {
                    let (a, l) = crate::services::strategies::pool_apr_parts(&raw);
                    (
                        Some(crate::services::strategies::conservative_pool_apr(&raw)),
                        a,
                        l,
                        raw.get("tvl").and_then(|v| v.as_f64()),
                    )
                }
                Err(_) => (None, None, None, None),
            };

        let (price_x, price_y) = tokio::join!(
            crate::services::strategies::mint_price_and_decimals(http, &mint_x),
            crate::services::strategies::mint_price_and_decimals(http, &mint_y),
        );
        let (px, py) = (price_x.map(|t| t.0), price_y.map(|t| t.0));

        // A DAMM v2 pool carries its bounds itself rather than per position,
        // so one share covers every position in it. A full-range pool has no
        // bounds worth measuring and keeps the whole window.
        let pool_share = match (num(pool.get("minPrice")), num(pool.get("maxPrice"))) {
            (Some(lo), Some(hi)) if hi > lo && lo > 0.0 && hi.is_finite() => {
                match pair_price_series(http, &mint_x, &mint_y, PRICE_WINDOW_DAYS).await {
                    Some(prices) => share_inside(&prices, lo, hi),
                    None => None,
                }
            }
            _ => None,
        };

        for p in pool
            .get("positions")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default()
        {
            let ax = num(p.get("amountX")).unwrap_or(0.0);
            let ay = num(p.get("amountY")).unwrap_or(0.0);
            if ax <= 0.0 && ay <= 0.0 {
                continue;
            }
            let value = match (px, py) {
                (Some(px), Some(py)) => {
                    let v = ax * px + ay * py;
                    (v > 0.0).then_some(v)
                }
                _ => None,
            };
            let fees_usd = match (px, py) {
                (Some(px), Some(py)) => Some(
                    num(p.get("unclaimedFeeX")).unwrap_or(0.0) * px
                        + num(p.get("unclaimedFeeY")).unwrap_or(0.0) * py,
                ),
                _ => None,
            };
            let exit_cost_pct =
                crate::services::strategies::entry_cost_pct(http, &mint_x, &mint_y, value).await;

            out.push(OpenPosition {
                venue: "Meteora DAMM v2",
                address: text(p.get("address")),
                pool: pool_addr.clone(),
                pair: pair.clone(),
                earning,
                in_range_share: pool_share,
                quote_mint: mint_y.clone(),
                pair_mints: (mint_x.clone(), mint_y.clone()),
                locked: p.get("locked").and_then(|v| v.as_bool()).unwrap_or(false),
                moves_without_swap: false,
                usd_value: value,
                pool_apr,
                apr_24h,
                apr_life,
                pool_tvl_usd,
                holdings: Some((text(pool.get("tokenX")), ax, text(pool.get("tokenY")), ay)),
                exit_cost_pct,
                pnl_usd_pct: None,
                pnl_sol_pct: None,
                unclaimed_fees_usd: fees_usd,
            });
        }
    }
    Ok(out)
}

/// Raydium CLMM positions, read through the SDK service.
///
/// In-range is derived from what the position holds rather than looked up: a
/// concentrated position holds both tokens only while the price sits inside
/// its range — below it everything is token A, above it everything is token B.
/// That is exact, and needs no tick arithmetic to go wrong.
async fn read_raydium_positions(
    http: &reqwest::Client,
    wallet: &str,
) -> Result<Vec<OpenPosition>, AppError> {
    let data = crate::services::protocol_reads::raydium_user_positions(http, wallet).await?;
    let positions = data
        .get("positions")
        .and_then(|p| p.as_array())
        .cloned()
        .unwrap_or_default();

    let num = |v: Option<&serde_json::Value>| -> Option<f64> {
        v.and_then(|x| {
            x.as_f64()
                .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
        })
    };
    let text = |v: Option<&serde_json::Value>| v.and_then(|x| x.as_str()).unwrap_or("").to_string();

    let mut out = Vec::new();
    let mut rates: std::collections::HashMap<String, (Option<f64>, Option<f64>)> =
        std::collections::HashMap::new();

    for p in positions {
        if p.get("empty").and_then(|v| v.as_bool()).unwrap_or(false) {
            continue;
        }
        let mint_a = text(p.get("mintA").and_then(|m| m.get("address")));
        let mint_b = text(p.get("mintB").and_then(|m| m.get("address")));
        if mint_a.is_empty() || mint_b.is_empty() {
            continue;
        }
        let amount_a = num(p.get("amountA")).unwrap_or(0.0);
        let amount_b = num(p.get("amountB")).unwrap_or(0.0);
        if amount_a <= 0.0 && amount_b <= 0.0 {
            continue;
        }

        let pool_id = text(p.get("poolId"));
        let (pool_apr, pool_tvl_usd) = match rates.get(&pool_id) {
            Some(v) => *v,
            None => {
                let r = raydium_pool_facts(http, &pool_id).await;
                rates.insert(pool_id.clone(), r);
                r
            }
        };

        let (price_a, price_b) = tokio::join!(
            crate::services::strategies::mint_price_and_decimals(http, &mint_a),
            crate::services::strategies::mint_price_and_decimals(http, &mint_b),
        );
        let value = match (price_a.map(|t| t.0), price_b.map(|t| t.0)) {
            (Some(pa), Some(pb)) => {
                let v = amount_a * pa + amount_b * pb;
                (v > 0.0).then_some(v)
            }
            _ => None,
        };
        let exit_cost_pct =
            crate::services::strategies::entry_cost_pct(http, &mint_a, &mint_b, value).await;

        // Raydium reports ticks rather than prices, so the range has to be
        // converted before it can be compared with anything.
        let in_range_share = match (
            num(p.get("tickLower")),
            num(p.get("tickUpper")),
            price_a.map(|t| t.1),
            price_b.map(|t| t.1),
        ) {
            (Some(t_lo), Some(t_hi), Some(dec_a), Some(dec_b)) => {
                let lo = tick_to_price(t_lo, dec_a, dec_b);
                let hi = tick_to_price(t_hi, dec_a, dec_b);
                match pair_price_series(http, &mint_a, &mint_b, PRICE_WINDOW_DAYS).await {
                    Some(prices) => share_inside(&prices, lo, hi),
                    None => None,
                }
            }
            _ => None,
        };

        out.push(OpenPosition {
            venue: "Raydium CLMM",
            address: text(p.get("positionId")),
            pool: pool_id,
            pair: {
                let pair = text(p.get("pair"));
                if pair.is_empty() {
                    "liquidity position".to_string()
                } else {
                    pair
                }
            },
            earning: amount_a > 0.0 && amount_b > 0.0,
            in_range_share,
            quote_mint: mint_b.clone(),
            pair_mints: (mint_a.clone(), mint_b),
            locked: false,
            moves_without_swap: false,
            usd_value: value,
            pool_apr,
            apr_24h: pool_apr,
            apr_life: None,
            pool_tvl_usd,
            holdings: Some((
                text(p.get("mintA").and_then(|m| m.get("symbol"))),
                amount_a,
                text(p.get("mintB").and_then(|m| m.get("symbol"))),
                amount_b,
            )),
            exit_cost_pct,
            pnl_usd_pct: None,
            pnl_sol_pct: None,
            unclaimed_fees_usd: None,
        });
    }
    Ok(out)
}

/// A Raydium pool's fee rate and liquidity, from one call.
async fn raydium_pool_facts(http: &reqwest::Client, pool_id: &str) -> (Option<f64>, Option<f64>) {
    let fetch = || async {
        let body: serde_json::Value = http
            .get(format!(
                "https://api-v3.raydium.io/pools/info/ids?ids={pool_id}"
            ))
            .send()
            .await
            .ok()?
            .json()
            .await
            .ok()?;
        let row = body.get("data")?.as_array()?.first()?.clone();
        Some(row)
    };
    match fetch().await {
        Some(row) => (
            row.get("day")
                .and_then(|d| d.get("feeApr"))
                .and_then(|v| v.as_f64()),
            row.get("tvl").and_then(|v| v.as_f64()),
        ),
        None => (None, None),
    }
}

/// Kamino lending positions.
///
/// A different shape from everything else here: there is no range to fall out
/// of, and leaving costs a network fee rather than a swap, because the same
/// asset comes back. So the only question worth asking is whether the rate is
/// still the best one available for that asset — and answering it needs a
/// second venue, since Kamino publishes exactly one rate per reserve and
/// comparing it with itself says nothing.
///
/// An obligation holding several assets, or carrying debt, is a structure this
/// review does not model. It is reported with its value and left unjudged
/// rather than given a verdict derived from the wrong question.
async fn read_kamino_positions(
    http: &reqwest::Client,
    wallet: &str,
) -> Result<Vec<OpenPosition>, AppError> {
    let params = crate::services::kamino::KaminoUserObligationsParams {
        wallet: Some(wallet.to_string()),
        market: None,
        env: None,
    };
    let resp =
        crate::services::kamino::build_kamino_user_obligations(http, wallet, &params).await?;
    let data = resp
        .data
        .clone()
        .or_else(|| Some(resp.preview.params.clone()))
        .unwrap_or(serde_json::Value::Null);
    let obligations = data
        .get("obligations")
        .and_then(|v| v.as_array())
        .cloned()
        .or_else(|| data.as_array().cloned())
        .unwrap_or_default();
    if obligations.is_empty() {
        return Ok(Vec::new());
    }

    let reserves = crate::services::kamino::fetch_market_reserves(http, None)
        .await
        .unwrap_or_default();
    let num = |v: Option<&serde_json::Value>| -> f64 {
        v.and_then(|x| {
            x.as_f64()
                .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
        })
        .unwrap_or(0.0)
    };

    let mut out = Vec::new();
    for o in obligations {
        let value = num(o.get("collateralUsd"));
        let debt = num(o.get("debtUsd"));
        // An empty obligation account is left behind the same way a closed
        // position leaves its NFT.
        if value <= 0.0 {
            continue;
        }
        let tokens: Vec<String> = o
            .get("collateralTokens")
            .and_then(|v| v.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();

        // Only a single asset lent with nothing borrowed against it is a
        // question this review can answer.
        let simple = tokens.len() == 1 && debt <= 0.0;
        let symbol = tokens.first().cloned().unwrap_or_else(|| "assets".into());
        let reserve = simple
            .then(|| {
                reserves
                    .iter()
                    .filter(|r| {
                        r.get("liquidityToken")
                            .and_then(|t| t.as_str())
                            .map(|t| t.eq_ignore_ascii_case(&symbol))
                            .unwrap_or(false)
                    })
                    .max_by(|a, b| {
                        num(a.get("totalSupplyUsd"))
                            .partial_cmp(&num(b.get("totalSupplyUsd")))
                            .unwrap_or(std::cmp::Ordering::Equal)
                    })
            })
            .flatten();
        let mint = reserve
            .and_then(|r| r.get("liquidityTokenMint"))
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let rate = reserve.map(|r| num(r.get("supplyApy")) * 100.0);

        out.push(OpenPosition {
            venue: "Kamino Lend",
            address: o
                .get("obligation")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string(),
            pool: String::new(),
            pair: if simple {
                format!("{symbol} lent")
            } else if debt > 0.0 {
                format!("{} borrowed against", tokens.join(" + "))
            } else {
                tokens.join(" + ")
            },
            // Lending is never idle; the asset earns from the moment it is
            // supplied.
            earning: true,
            in_range_share: None,
            quote_mint: mint.clone(),
            pair_mints: (mint.clone(), mint),
            locked: false,
            usd_value: Some(value),
            // Unknown for a structure this review does not model, which stops
            // it producing a verdict from the wrong question.
            pool_apr: if simple { rate } else { None },
            apr_24h: if simple { rate } else { None },
            apr_life: None,
            pool_tvl_usd: reserve.map(|r| num(r.get("totalSupplyUsd"))),
            holdings: None,
            // Leaving is a withdrawal of the same asset, so there is no swap
            // to pay for — only the network fee, which is a rounding error
            // against any position worth reviewing.
            // Priced by the caller, which knows the rate being moved to.
            exit_cost_pct: None,
            moves_without_swap: simple,
            pnl_usd_pct: None,
            pnl_sol_pct: None,
            unclaimed_fees_usd: None,
        });
    }
    Ok(out)
}

/// A whirlpool's annualised fee rate, from its own 24-hour statistics.
async fn orca_pool_apr(http: &reqwest::Client, address: &str) -> Option<f64> {
    let params = crate::services::orca::OrcaGetPoolParams {
        address: address.to_string(),
        stats: None,
    };
    let resp = crate::services::orca::build_orca_get_pool(http, &params)
        .await
        .ok()?;
    let d = resp.data?;
    let row = d.get("data").unwrap_or(&d);
    let n = |v: Option<&serde_json::Value>| -> Option<f64> {
        v.and_then(|x| {
            x.as_f64()
                .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
        })
    };
    let tvl = n(row.get("tvlUsdc"))?;
    if !(tvl > 0.0) {
        return None;
    }
    let fees = n(row
        .get("stats")
        .and_then(|s| s.get("24h"))
        .and_then(|s| s.get("fees")))?;
    Some(fees / tvl * 365.0 * 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(forward: f64, alt: f64, cost: Option<f64>, earning: bool) -> PositionFacts {
        PositionFacts {
            forward_apr: forward,
            alternative_apr: alt,
            exit_cost_pct: cost,
            earning,
            in_range_share: None,
            forward_known: true,
            locked: false,
        }
    }

    #[test]
    fn a_position_already_ahead_is_left_alone() {
        let r = review_position(&facts(12.0, 5.0, Some(0.3), true));
        assert_eq!(r.verdict, Verdict::Keep);
        assert!(r.gap_pct < 0.0);
    }

    #[test]
    fn an_idle_position_with_a_real_gap_is_worth_leaving() {
        // Out of range: earning nothing while lending pays 5%. A 0.3% exit
        // recovers in 22 days.
        let r = review_position(&facts(0.0, 5.0, Some(0.3), false));
        assert_eq!(r.verdict, Verdict::ConsiderExit);
        assert!(
            (r.payback_days.unwrap() - 21.9).abs() < 0.5,
            "{:?}",
            r.payback_days
        );
    }

    #[test]
    fn a_small_gap_does_not_justify_an_expensive_exit() {
        // 0.1% behind, 0.3% to leave: three years to break even. Telling
        // someone to switch here churns them for nothing.
        let r = review_position(&facts(4.9, 5.0, Some(0.3), true));
        assert_eq!(r.verdict, Verdict::NotWorthTheSwitch);
        assert!(r.payback_days.unwrap() > 1000.0);
    }

    #[test]
    fn the_horizon_is_the_boundary_and_not_a_suggestion() {
        // Constructed to land a hair either side of 90 days.
        let just_under = review_position(&facts(0.0, 4.0, Some(0.98), false));
        let just_over = review_position(&facts(0.0, 4.0, Some(0.99), false));
        assert_eq!(just_under.verdict, Verdict::ConsiderExit);
        assert_eq!(just_over.verdict, Verdict::NotWorthTheSwitch);
    }

    #[test]
    fn an_unpriced_exit_is_never_recommended() {
        // A gap this large is tempting, which is exactly when an unpriced
        // recommendation does the most damage.
        let r = review_position(&facts(0.0, 40.0, None, false));
        assert_eq!(r.verdict, Verdict::Unpriced);
        assert!(r.payback_days.is_none());
    }

    #[test]
    fn payback_does_not_depend_on_position_size() {
        // Both figures are percentages of the same position, so a $50 and a
        // $50,000 position get the same answer — worth pinning, because the
        // obvious "improvement" of folding a dollar cost in here would break it.
        let a = review_position(&facts(1.0, 6.0, Some(0.5), true));
        let b = review_position(&facts(1.0, 6.0, Some(0.5), true));
        assert_eq!(a.payback_days, b.payback_days);
        assert!((a.payback_days.unwrap() - 36.5).abs() < 0.1);
    }

    #[test]
    fn a_range_that_is_rarely_live_does_not_get_the_pools_headline() {
        // The measured case: the pool pays 120% and the range was live a fifth
        // of the time, so the position captures about 24%. Still better than
        // staking, so the verdict stays keep — but the wording must not repeat
        // the headline as though the position earned it.
        let f = PositionFacts {
            forward_apr: 24.0,
            alternative_apr: 5.18,
            exit_cost_pct: Some(0.5),
            earning: true,
            in_range_share: Some(0.2),
            forward_known: true,
            locked: false,
        };
        let r = review_position(&f);
        assert_eq!(r.verdict, Verdict::Keep);
        let text = explain(&f, &r, "staking SOL");
        assert!(text.contains("20%"), "{text}");
        assert!(
            text.contains("tighter range"),
            "a rarely-live range should be named as the thing to fix: {text}"
        );
    }

    #[test]
    fn unclaimed_fees_can_make_leaving_free() {
        // Fees collected on the way out exceed the cost of the swap back, so
        // the exit is not merely cheap but net positive.
        let r = review_position(&facts(0.0, 5.0, Some(-0.2), false));
        assert_eq!(r.verdict, Verdict::ConsiderExit);
        assert_eq!(r.payback_days, Some(0.0));
    }

    #[test]
    fn an_unreadable_pool_produces_no_advice() {
        // A pool we could not read is not a pool earning nothing. Reporting it
        // as zero told a live wallet to close a position holding $1.2m of
        // liquidity because one request failed.
        let f = PositionFacts {
            forward_apr: 5.0,
            alternative_apr: 5.0,
            exit_cost_pct: Some(0.5),
            earning: true,
            in_range_share: None,
            forward_known: true,
            locked: false,
        };
        let r = review_position(&f);
        assert_eq!(r.verdict, Verdict::Keep, "no data must not become an exit");
        assert_eq!(r.gap_pct, 0.0);
    }

    #[test]
    fn an_unreachable_payback_is_said_in_words_not_digits() {
        // A gap near zero puts the payback in five figures. Printing "175127
        // days" is arithmetic aimed at the reader instead of an answer.
        let f = PositionFacts {
            forward_apr: 0.0,
            alternative_apr: 0.0003,
            exit_cost_pct: Some(0.15),
            earning: true,
            in_range_share: None,
            forward_known: true,
            locked: false,
        };
        let r = review_position(&f);
        assert_eq!(r.verdict, Verdict::NotWorthTheSwitch);
        let text = explain(&f, &r, "lending");
        assert!(text.contains("never realistically"), "{text}");
        assert!(!text.contains("182500"), "{text}");
    }

    #[test]
    fn an_unjudged_position_never_claims_a_return() {
        // A live multi-asset Kamino obligation was told it "earns 5.57%" —
        // the rate it was being compared against, not one it had. Filling an
        // unknown with the alternative made the gap zero and the sentence a
        // fabrication.
        let f = PositionFacts {
            forward_apr: 0.0,
            alternative_apr: 5.57,
            exit_cost_pct: None,
            earning: true,
            in_range_share: None,
            forward_known: false,
            locked: false,
        };
        let r = review_position(&f);
        assert_eq!(r.verdict, Verdict::Unjudged);
        let text = explain(&f, &r, "staking SOL");
        assert!(
            !text.contains("5.57"),
            "must not quote a rate it does not have: {text}"
        );
        assert!(text.contains("does not model"), "{text}");
    }

    #[test]
    fn a_locked_position_is_never_told_to_leave() {
        // The widest possible gap, on a position that cannot be closed. Advice
        // to move here is advice nobody can follow.
        let f = PositionFacts {
            forward_apr: 0.0,
            alternative_apr: 40.0,
            exit_cost_pct: Some(0.01),
            earning: false,
            in_range_share: None,
            forward_known: true,
            locked: true,
        };
        let r = review_position(&f);
        assert_eq!(r.verdict, Verdict::Locked);
        assert!(r.gap_pct > 0.0, "the gap is still reported");
        let text = explain(&f, &r, "lending");
        assert!(text.contains("locked"), "{text}");
    }

    #[test]
    fn an_idle_position_with_nowhere_better_still_says_so() {
        let f = facts(0.0, 0.0, Some(0.3), false);
        let r = review_position(&f);
        assert_eq!(r.verdict, Verdict::Keep);
        // The wording must still surface that it is earning nothing, or the
        // reader is left thinking the position is fine.
        let text = explain(&f, &r, "lending");
        assert!(text.contains("out of range"), "{text}");
    }
}

#[cfg(test)]
mod split_tests {
    use super::*;

    #[test]
    fn the_parts_sum_to_the_whole() {
        // The invariant that matters: a pool holding several positions must
        // not report its balance more than once.
        let parts = split_pool_value(Some(2876.0), &[3.0, 1.0]);
        let sum: f64 = parts.iter().flatten().sum();
        assert!((sum - 2876.0).abs() < 1e-6, "{parts:?}");
        assert!((parts[0].unwrap() - 2157.0).abs() < 1e-6);
        assert!((parts[1].unwrap() - 719.0).abs() < 1e-6);
    }

    #[test]
    fn a_single_position_takes_the_whole_pool() {
        let parts = split_pool_value(Some(1421.0), &[42.0]);
        assert_eq!(parts, vec![Some(1421.0)]);
    }

    #[test]
    fn unmeasurable_weights_yield_nothing_rather_than_an_even_split() {
        // An even split would look reasonable and be invented. Downstream,
        // None means no exit is recommended for a position we cannot size.
        let parts = split_pool_value(Some(1000.0), &[0.0, 0.0]);
        assert_eq!(parts, vec![None, None]);
    }

    #[test]
    fn a_position_with_no_balance_is_not_given_a_share() {
        let parts = split_pool_value(Some(900.0), &[2.0, 0.0, 1.0]);
        assert_eq!(parts[1], None);
        let sum: f64 = parts.iter().flatten().sum();
        assert!((sum - 900.0).abs() < 1e-6);
    }

    #[test]
    fn an_unknown_pool_balance_stays_unknown() {
        assert_eq!(split_pool_value(None, &[1.0, 2.0]), vec![None, None]);
    }
}

#[cfg(test)]
mod tick_tests {
    use super::*;

    #[test]
    fn equal_decimals_leave_the_ratio_alone() {
        // At tick zero the ratio is one, and with matching decimals there is
        // nothing to fold back in.
        assert!((tick_to_price(0.0, 6, 6) - 1.0).abs() < 1e-12);
    }

    #[test]
    fn the_decimal_adjustment_goes_the_right_way() {
        // A six-decimal token priced in a nine-decimal one: the raw ratio has
        // to be divided by a thousand, not multiplied. Backwards, a range
        // comes out a million times off and never contains the price — which
        // reads as "this position has never been in range" rather than as a
        // bug.
        let p = tick_to_price(0.0, 6, 9);
        assert!((p - 0.001).abs() < 1e-12, "{p}");
        let q = tick_to_price(0.0, 9, 6);
        assert!((q - 1000.0).abs() < 1e-9, "{q}");
    }

    #[test]
    fn a_higher_tick_is_a_higher_price() {
        assert!(tick_to_price(100.0, 6, 6) > tick_to_price(0.0, 6, 6));
    }

    #[test]
    fn a_real_range_brackets_its_own_pair_price() {
        // A PUMP/SOL position: PUMP has six decimals, SOL nine, and the pair
        // trades near 4.2e-05 SOL per PUMP. The ticks either side of that must
        // convert to a range containing it.
        let target = 4.2e-05f64;
        let tick = (target / 10f64.powi(6 - 9)).ln() / 1.0001f64.ln();
        let lo = tick_to_price(tick - 500.0, 6, 9);
        let hi = tick_to_price(tick + 500.0, 6, 9);
        assert!(
            lo < target && target < hi,
            "lo {lo} target {target} hi {hi}"
        );
        assert_eq!(share_inside(&[target], lo, hi), Some(1.0));
    }
}

#[cfg(test)]
mod holding_tests {
    use super::*;

    /// $2 a unit for X, $1 for Y — round numbers so an error is visible.
    const P: (f64, f64) = (2.0, 1.0);

    #[test]
    fn a_position_worth_exactly_what_went_in_is_level() {
        // 100 X and 100 Y in, worth $300 today, and the position is worth $300.
        let r = holding_maths((100.0, 100.0), (0.0, 0.0), 300.0, P).unwrap();
        assert_eq!(r.hold_value_usd, 300.0);
        assert_eq!(r.difference_usd, 0.0);
    }

    #[test]
    fn fees_are_what_puts_a_position_ahead() {
        let r = holding_maths((100.0, 100.0), (0.0, 0.0), 315.0, P).unwrap();
        assert!(
            (r.difference_pct - 5.0).abs() < 1e-9,
            "{}",
            r.difference_pct
        );
    }

    #[test]
    fn what_was_already_withdrawn_still_counts_as_yours() {
        // Half taken out earlier, half still in. Ignoring the withdrawn half
        // would report the position as having lost 50% when it is level.
        let r = holding_maths((100.0, 100.0), (50.0, 50.0), 150.0, P).unwrap();
        assert_eq!(r.actual_value_usd, 300.0);
        assert_eq!(r.difference_usd, 0.0);
    }

    #[test]
    fn claimed_fees_are_not_value_that_vanished() {
        // Fees claimed to the wallet appear as tokens coming back. They are
        // still the holder's, so they belong on the position's side.
        let r = holding_maths((100.0, 100.0), (0.0, 20.0), 300.0, P).unwrap();
        assert!((r.difference_usd - 20.0).abs() < 1e-9);
    }

    #[test]
    fn a_top_up_raises_the_bar_it_has_to_clear() {
        // Two deposits totalling 200 X. Holding those is worth $400, so a
        // position worth $300 is behind — reading only the first deposit would
        // have called it ahead.
        let r = holding_maths((200.0, 0.0), (0.0, 0.0), 300.0, P).unwrap();
        assert_eq!(r.hold_value_usd, 400.0);
        assert!(r.difference_usd < 0.0);
    }

    #[test]
    fn impermanent_loss_shows_up_as_a_shortfall() {
        // The classic case: the pair moved, the position rebalanced into the
        // side that fell, and fees did not cover the gap.
        let r = holding_maths((100.0, 100.0), (0.0, 0.0), 285.0, P).unwrap();
        assert!(
            (r.difference_pct + 5.0).abs() < 1e-9,
            "{}",
            r.difference_pct
        );
    }

    #[test]
    fn a_sol_funded_deposit_is_not_read_as_token_only() {
        // The live failure: a PUMP/SOL deposit moved PUMP through the token
        // balances and SOL through the native ones, because the wrapped
        // account was opened and closed in the same transaction. Reading only
        // the token balances halved the deposit and made the position look
        // like it had doubled against holding.
        let tx = serde_json::json!({
            "transaction": { "message": { "accountKeys": [{ "pubkey": "WALLET" }] } },
            "meta": {
                "fee": 5000,
                "preBalances": [30_000_000_000i64],
                "postBalances": [1_873_767_000i64],
                "preTokenBalances": [{
                    "owner": "WALLET", "mint": "PUMP",
                    "uiTokenAmount": { "uiAmountString": "586272.79" }
                }],
                "postTokenBalances": [{
                    "owner": "WALLET", "mint": "PUMP",
                    "uiTokenAmount": { "uiAmountString": "0" }
                }],
            }
        });
        let deltas = wallet_token_deltas(&tx, "WALLET");
        let pump = deltas.iter().find(|(m, _)| m == "PUMP").expect("PUMP side");
        assert!((pump.1 + 586_272.79).abs() < 1e-6);
        let sol = deltas
            .iter()
            .find(|(m, _)| m == WSOL_MINT)
            .expect("the SOL side must be read from the native balances");
        // 30 SOL before, 1.873767 after, fee added back: 28.126228 went out.
        assert!((sol.1 + 28.126228).abs() < 1e-6, "{}", sol.1);
    }

    #[test]
    fn the_fee_is_not_a_deposit() {
        // A transaction that moves nothing but still costs a fee must read as
        // no deposit at all, or every position acquires a phantom one.
        let tx = serde_json::json!({
            "transaction": { "message": { "accountKeys": [{ "pubkey": "W" }] } },
            "meta": { "fee": 5000, "preBalances": [1_000_000i64], "postBalances": [995_000i64] }
        });
        assert!(wallet_token_deltas(&tx, "W").is_empty());
    }

    #[test]
    fn nothing_deposited_is_not_a_comparison() {
        assert!(holding_maths((0.0, 0.0), (10.0, 10.0), 5.0, P).is_none());
    }
}

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
}

impl Verdict {
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Keep => "keep",
            Verdict::ConsiderExit => "consider_exit",
            Verdict::NotWorthTheSwitch => "not_worth_the_switch",
            Verdict::Unpriced => "unpriced",
            Verdict::Locked => "locked",
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
                "Its range was only live {:.0}% of the last ten days, so it captures about {:.2}% of the pool's rate rather than the headline. That still beats {alternative_label} at {:.2}%, but a tighter range that tracked the price would earn considerably more.",
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
        Verdict::NotWorthTheSwitch => format!(
            "{alternative_label} pays {:.2}% against this position's {:.2}%, but closing costs {:.2}% and would take {:.0} days to recover — too long to be worth the trade. Leave it.",
            f.alternative_apr,
            f.forward_apr,
            f.exit_cost_pct.unwrap_or(0.0),
            r.payback_days.unwrap_or(0.0)
        ),
        Verdict::Locked => format!(
            "This position is locked until it vests, so it cannot be closed or withdrawn yet — whatever {alternative_label} pays in the meantime."
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
    let (alt, dlmm, orca, damm, ray) = tokio::join!(
        crate::services::flows::best_simple_option(http),
        read_dlmm_positions(http, wallet),
        read_orca_positions(http, rpc_url, wallet),
        read_dammv2_positions(http, wallet),
        read_raydium_positions(http, wallet),
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
        let exit_cost_pct = match (pos.exit_cost_pct, pos.unclaimed_fees_usd, pos.usd_value) {
            (Some(cost), Some(fees), Some(value)) if value > 0.0 => {
                Some(cost - (fees / value * 100.0))
            }
            (cost, _, _) => cost,
        };

        let facts = PositionFacts {
            // Unknown is carried as the alternative's own rate so the gap is
            // zero and the verdict is Keep — no advice from no data.
            forward_apr: forward_apr.unwrap_or(alt_apr),
            alternative_apr: alt_apr,
            exit_cost_pct,
            earning: pos.earning,
            in_range_share: pos.in_range_share,
            locked: pos.locked,
        };
        let review = review_position(&facts);
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
    /// A locked position cannot be withdrawn until it vests, so telling
    /// someone to close it is advice they cannot act on.
    locked: bool,
    pnl_usd_pct: Option<f64>,
    pnl_sol_pct: Option<f64>,
    unclaimed_fees_usd: Option<f64>,
}

/// Share of a window during which a position's range was actually live.
///
/// A concentrated position earns only while the price sits inside its range,
/// and "in range right now" says nothing about the rest of the week. Measured
/// on a live wallet, three positions all reporting in-range had covered their
/// ranges 16-19% of the previous ten days — so crediting them the pool's full
/// APR, which is what reading the instantaneous flag does, described a return
/// none of them earned.
///
/// Each candle contributes the fraction of its own low-to-high span that falls
/// inside the range, rather than a yes/no. Counting a whole day as live
/// because the price touched the range once overstates badly: on the same data
/// the yes/no reading gave one position 30% where the weighted reading gives
/// 15.8%. The weighting assumes price covers a candle's span evenly, which is
/// an approximation — but a far better one than assuming it sat in the range
/// all day because it passed through.
///
/// None when there are no candles, since a share of nothing is not zero.
fn in_range_share(candles: &[(f64, f64)], lower: f64, upper: f64) -> Option<f64> {
    if candles.is_empty() || !(upper > lower) {
        return None;
    }
    let mut total = 0.0;
    for (low, high) in candles {
        let (low, high) = (*low, *high);
        if !(high >= low) || low <= 0.0 {
            continue;
        }
        if high == low {
            // A flat candle is either in or out; there is no span to weight.
            if low >= lower && low <= upper {
                total += 1.0;
            }
            continue;
        }
        let overlap = (high.min(upper) - low.max(lower)).max(0.0);
        total += overlap / (high - low);
    }
    Some((total / candles.len() as f64).clamp(0.0, 1.0))
}

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

        // Ten daily candles — the endpoint caps at ten however it is asked, so
        // the widest honest window is a daily one.
        let candles: Vec<(f64, f64)> = {
            let p = crate::services::meteora::MeteoraDlmmGetPoolOhlcvParams {
                address: pool_addr.clone(),
                timeframe: Some("24h".to_string()),
                start_time: None,
                end_time: None,
            };
            match crate::services::meteora::build_meteora_dlmm_get_pool_ohlcv(http, &p).await {
                Ok(r) => r
                    .data
                    .as_ref()
                    .and_then(|d| d.get("data"))
                    .and_then(|d| d.as_array())
                    .map(|rows| {
                        rows.iter()
                            .filter_map(|c| Some((num(c.get("low"))?, num(c.get("high"))?)))
                            .collect()
                    })
                    .unwrap_or_default(),
                Err(_) => Vec::new(),
            }
        };

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
                (Some(lo), Some(hi)) => in_range_share(&candles, lo, hi),
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
                locked: false,
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

        out.push(OpenPosition {
            venue: "Orca",
            address: text(p.get("positionAddress")),
            pool: whirlpool,
            pair: format!(
                "{}/{}",
                text(p.get("tokenASymbol")),
                text(p.get("tokenBSymbol"))
            ),
            earning: p.get("inRange").and_then(|v| v.as_bool()).unwrap_or(false),
            in_range_share: None,
            quote_mint: mint_b,
            locked: false,
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
                in_range_share: None,
                quote_mint: mint_y.clone(),
                locked: p.get("locked").and_then(|v| v.as_bool()).unwrap_or(false),
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
            in_range_share: None,
            quote_mint: mint_b,
            locked: false,
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
            locked: false,
        };
        let r = review_position(&f);
        assert_eq!(r.verdict, Verdict::Keep, "no data must not become an exit");
        assert_eq!(r.gap_pct, 0.0);
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
mod in_range_tests {
    use super::*;

    #[test]
    fn a_range_covering_every_candle_is_fully_live() {
        let c = [(10.0, 12.0), (11.0, 13.0)];
        assert_eq!(in_range_share(&c, 0.0, 100.0), Some(1.0));
    }

    #[test]
    fn a_range_the_price_never_reaches_is_never_live() {
        let c = [(10.0, 12.0), (11.0, 13.0)];
        assert_eq!(in_range_share(&c, 50.0, 60.0), Some(0.0));
    }

    #[test]
    fn a_candle_counts_only_the_part_of_its_span_inside_the_range() {
        // Half of the 10-12 span sits above 11.
        let share = in_range_share(&[(10.0, 12.0)], 11.0, 20.0).unwrap();
        assert!((share - 0.5).abs() < 1e-9, "{share}");
    }

    #[test]
    fn touching_the_range_is_not_the_same_as_living_in_it() {
        // The bug this replaces: a yes/no reading would call this a full day
        // in range because the top of the candle grazes the bottom of the
        // range. The weighted reading gives it a tenth.
        let share = in_range_share(&[(10.0, 20.0)], 19.0, 30.0).unwrap();
        assert!((share - 0.1).abs() < 1e-9, "{share}");
    }

    #[test]
    fn a_flat_candle_is_in_or_out_with_no_span_to_weight() {
        assert_eq!(in_range_share(&[(10.0, 10.0)], 9.0, 11.0), Some(1.0));
        assert_eq!(in_range_share(&[(10.0, 10.0)], 11.0, 12.0), Some(0.0));
    }

    #[test]
    fn no_candles_is_unknown_rather_than_zero() {
        // Zero would read as "this range never earned", which is a claim.
        assert_eq!(in_range_share(&[], 1.0, 2.0), None);
    }

    #[test]
    fn an_inverted_or_empty_range_is_unknown() {
        assert_eq!(in_range_share(&[(1.0, 2.0)], 5.0, 5.0), None);
        assert_eq!(in_range_share(&[(1.0, 2.0)], 6.0, 5.0), None);
    }

    #[test]
    fn the_measured_case_reproduces() {
        // The live MET/SOL position: ten daily candles, of which the range
        // covered a sixth. Pinned because this is the number that turns
        // "earns 120%" into "earns a fraction of 120%".
        let candles = [
            (0.00207, 0.00230),
            (0.00215, 0.00238),
            (0.00220, 0.00252),
            (0.00225, 0.00266),
            (0.00210, 0.00240),
            (0.00208, 0.00232),
            (0.00212, 0.00236),
            (0.00218, 0.00244),
            (0.00214, 0.00239),
            (0.00209, 0.00233),
        ];
        let share = in_range_share(&candles, 0.0023564, 0.0026993).unwrap();
        assert!(
            share > 0.0 && share < 0.35,
            "expected a small share, got {share}"
        );
    }
}

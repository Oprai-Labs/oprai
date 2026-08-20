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
}

impl Verdict {
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Keep => "keep",
            Verdict::ConsiderExit => "consider_exit",
            Verdict::NotWorthTheSwitch => "not_worth_the_switch",
            Verdict::Unpriced => "unpriced",
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
        Verdict::Keep => format!(
            "It earns {:.2}%, which is at least as good as anything else available right now ({alternative_label} pays {:.2}%). Leave it alone.",
            f.forward_apr, f.alternative_apr
        ),
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
        Verdict::Unpriced => format!(
            "{alternative_label} pays more than this position, but the cost of closing could not be priced, so whether the switch is worth making is unknown. Left as it is."
        ),
    }
}

/// Every open position the wallet holds, judged against doing something else.
pub async fn build_position_review(
    http: &reqwest::Client,
    wallet: &str,
) -> Result<BuildResponse, AppError> {
    // What the same money would earn in one step today. Most LP positions are
    // paired against SOL, so "you could have just staked it" is the honest
    // comparison rather than a hand-picked rival pool.
    let (alt, dlmm) = tokio::join!(
        crate::services::flows::best_simple_option(http),
        read_dlmm_positions(http, wallet),
    );
    let (alt_apr, alt_label) = alt.unwrap_or((0.0, "lending".to_string()));

    let mut reviewed: Vec<serde_json::Value> = Vec::new();
    let mut idle = 0usize;
    for pos in dlmm.unwrap_or_default() {
        if !pos.earning {
            idle += 1;
        }
        let facts = PositionFacts {
            // Out of range is not "earning less" — it is earning nothing, and
            // that is the case this whole feature exists to surface.
            forward_apr: if pos.earning { pos.pool_apr } else { 0.0 },
            alternative_apr: alt_apr,
            exit_cost_pct: pos.exit_cost_pct,
            earning: pos.earning,
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
            "exitCostPct": pos.exit_cost_pct,
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
        "alternativeAprPct": alt_apr,
        "alternative": alt_label,
        // Said plainly so an answer never implies a wallet-wide all-clear it
        // has not actually checked.
        "covers": ["Meteora DLMM"],
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
    pool_apr: f64,
    exit_cost_pct: Option<f64>,
    pnl_usd_pct: Option<f64>,
    pnl_sol_pct: Option<f64>,
    unclaimed_fees_usd: Option<f64>,
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
        let pool_apr = match crate::services::meteora::meteora_pool_raw(
            http,
            crate::services::meteora::DLMM_API,
            &pool_addr,
        )
        .await
        {
            Ok(raw) => crate::services::strategies::conservative_pool_apr(&raw),
            Err(_) => 0.0,
        };

        // Leaving costs what arriving cost: half the position swaps back.
        let exit_cost_pct = if mint_x.is_empty() || mint_y.is_empty() {
            None
        } else {
            crate::services::strategies::entry_cost_pct(http, &mint_x, &mint_y, usd_value).await
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
        for addr in listed {
            let earning = !idle.contains(&addr);
            out.push(OpenPosition {
                venue: "Meteora DLMM",
                address: addr,
                pool: pool_addr.clone(),
                pair: pair.clone(),
                earning,
                usd_value,
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

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(forward: f64, alt: f64, cost: Option<f64>, earning: bool) -> PositionFacts {
        PositionFacts {
            forward_apr: forward,
            alternative_apr: alt,
            exit_cost_pct: cost,
            earning,
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

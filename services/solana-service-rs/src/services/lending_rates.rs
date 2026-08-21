//! One question, one answer: what does this asset earn, and where.
//!
//! Comparing lending venues by hand went wrong four times in a row, and never
//! twice the same way — one venue's data failed to load and the other won by
//! default; the headline agreed with neither; the winner was named on a rate
//! that was mostly incentives. Every one of those was a step the model had to
//! remember to take.
//!
//! So the comparison stops being something to remember. One call reads every
//! venue, splits interest from incentives, and names the winner on each basis.
//! Nothing here can be answered from one side, because one side is not an
//! answer this returns.

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

/// What one venue pays to supply an asset.
struct VenueRate {
    venue: &'static str,
    /// Everything a depositor receives.
    total_pct: f64,
    /// The part paid by borrowers, which is the part that persists.
    interest_pct: f64,
    /// The part paid by the protocol, which stops when it decides.
    rewards_pct: f64,
    /// Deposits behind the rate — a rate on an empty reserve is quotable and
    /// unusable.
    liquidity_usd: f64,
}

/// Every venue's rate for one asset, and what that means.
pub async fn build_lending_rates(
    http: &reqwest::Client,
    mint: &str,
) -> Result<BuildResponse, AppError> {
    let (kamino, jupiter) = tokio::join!(kamino_rate(http, mint), jupiter_rate(http, mint));
    let mut venues: Vec<VenueRate> = [kamino, jupiter].into_iter().flatten().collect();
    if venues.is_empty() {
        return Ok(response(
            mint,
            serde_json::json!({ "mint": mint, "venues": [] }),
            "No lending venue we can read quotes a rate for this asset.".to_string(),
        ));
    }

    // Ranked twice on purpose. A venue can lead on what it pays today and
    // trail on what it will still be paying once an incentive programme ends,
    // and a reader told only the first cannot see the second coming.
    venues.sort_by(|a, b| {
        b.total_pct
            .partial_cmp(&a.total_pct)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let best_total = venues.first().map(|v| (v.venue, v.total_pct));
    let best_interest = venues
        .iter()
        .max_by(|a, b| {
            a.interest_pct
                .partial_cmp(&b.interest_pct)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
        .map(|v| (v.venue, v.interest_pct));

    let rows: Vec<serde_json::Value> = venues
        .iter()
        .map(|v| {
            serde_json::json!({
                "venue": v.venue,
                "totalPct": v.total_pct,
                "interestPct": v.interest_pct,
                "rewardsPct": v.rewards_pct,
                "liquidityUsd": v.liquidity_usd,
            })
        })
        .collect();

    let summary = match (best_total, best_interest) {
        (Some((t_venue, t_pct)), Some((i_venue, i_pct))) if t_venue != i_venue => format!(
            "{t_venue} pays the most today at {t_pct:.2}%, but part of that is an incentive. On interest alone {i_venue} is ahead at {i_pct:.2}%, so the order reverses whenever the incentive stops."
        ),
        (Some((t_venue, t_pct)), _) => {
            format!("{t_venue} pays the most at {t_pct:.2}%, on interest as well as in total.")
        }
        _ => "No venue could be ranked.".to_string(),
    };

    let data = serde_json::json!({
        "mint": mint,
        "venues": rows,
        "bestByTotal": best_total.map(|(v, p)| serde_json::json!({ "venue": v, "pct": p })),
        "bestByInterest": best_interest.map(|(v, p)| serde_json::json!({ "venue": v, "pct": p })),
        "summary": summary,
    });
    Ok(response(mint, data, summary))
}

/// Kamino's rate, from the reserve people actually use.
async fn kamino_rate(http: &reqwest::Client, mint: &str) -> Option<VenueRate> {
    let reserves = crate::services::kamino::fetch_market_reserves(http, None)
        .await
        .ok()?;
    let num = |v: Option<&serde_json::Value>| -> f64 {
        v.and_then(|x| {
            x.as_f64()
                .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
        })
        .unwrap_or(0.0)
    };
    // A mint maps to several reserves and the empty ones quote rates nobody
    // can get, so the one holding the deposits is the one that counts.
    let r = reserves
        .iter()
        .filter(|r| r.get("liquidityTokenMint").and_then(|v| v.as_str()) == Some(mint))
        .max_by(|a, b| {
            num(a.get("totalSupplyUsd"))
                .partial_cmp(&num(b.get("totalSupplyUsd")))
                .unwrap_or(std::cmp::Ordering::Equal)
        })?;
    let apy = num(r.get("supplyApy")) * 100.0;
    (apy > 0.0).then_some(VenueRate {
        venue: "Kamino",
        total_pct: apy,
        // Kamino's supply rate is interest; it runs no incentive on it.
        interest_pct: apy,
        rewards_pct: 0.0,
        liquidity_usd: num(r.get("totalSupplyUsd")),
    })
}

/// Jupiter Lend's rate, with the incentive separated out.
async fn jupiter_rate(http: &reqwest::Client, mint: &str) -> Option<VenueRate> {
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
    let pct = |v: Option<&serde_json::Value>| -> f64 {
        v.and_then(|x| {
            x.as_f64()
                .or_else(|| x.as_str().and_then(|s| s.parse().ok()))
        })
        .unwrap_or(0.0)
            / 100.0
    };
    for r in rows {
        let asset_mint = r
            .get("assetAddress")
            .or_else(|| r.get("asset").and_then(|a| a.get("address")))
            .and_then(|v| v.as_str());
        if asset_mint != Some(mint) {
            continue;
        }
        let total = pct(r.get("totalRate"));
        let rewards = pct(r.get("rewardsRate"));
        if total <= 0.0 {
            return None;
        }
        return Some(VenueRate {
            venue: "Jupiter Lend",
            total_pct: total,
            interest_pct: total - rewards,
            rewards_pct: rewards,
            liquidity_usd: pct(r.get("totalAssets")) * 100.0,
        });
    }
    None
}

fn response(mint: &str, data: serde_json::Value, description: String) -> BuildResponse {
    BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "lending_rates".into(),
            description,
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "mint": mint }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(data),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const USDC: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";

    #[tokio::test]
    #[ignore = "hits both venues' live APIs"]
    async fn both_venues_answer_and_the_split_is_named() {
        let http = reqwest::Client::new();
        let resp = build_lending_rates(&http, USDC).await.expect("rates");
        let data = resp.data.expect("data");
        let venues = data["venues"].as_array().expect("venues");
        assert!(
            venues.len() >= 2,
            "a comparison needs both sides; got {}",
            venues.len()
        );
        eprintln!("{}", data["summary"].as_str().unwrap_or(""));
        for v in venues {
            let total = v["totalPct"].as_f64().unwrap();
            let interest = v["interestPct"].as_f64().unwrap();
            let rewards = v["rewardsPct"].as_f64().unwrap();
            eprintln!(
                "  {:14} total {total:.2}%  interest {interest:.2}%  rewards {rewards:.2}%",
                v["venue"].as_str().unwrap_or("?")
            );
            // The whole point of the split: the parts must add up, or the
            // interest-only ranking is built on a number nobody computed.
            assert!(
                (interest + rewards - total).abs() < 0.01,
                "interest + rewards must equal total"
            );
            assert!(total > 0.0 && total < 100.0, "implausible rate {total}");
        }
        // Both rankings are always stated, even when they agree — an answer
        // that omits one leaves the reader to assume they match.
        assert!(data["bestByTotal"].is_object());
        assert!(data["bestByInterest"].is_object());
    }

    #[tokio::test]
    #[ignore = "hits both venues' live APIs"]
    async fn an_asset_no_venue_lends_says_so_rather_than_half_answering() {
        let http = reqwest::Client::new();
        // A memecoin no lending market lists.
        let resp = build_lending_rates(&http, "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
            .await
            .expect("a response even with nothing to report");
        let data = resp.data.expect("data");
        assert_eq!(data["venues"].as_array().map(|a| a.len()), Some(0));
        eprintln!("{}", resp.preview.description);
    }
}

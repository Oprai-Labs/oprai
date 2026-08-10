use serde::{Deserialize, Serialize};
use solana_client::rpc_client::RpcClient;

use crate::error::AppError;

const LAMPORTS_PER_SOL: f64 = 1_000_000_000.0;
const MAX_COMMISSION: u8 = 10; // exclude validators charging > 10%
/// Enough that searching by name is worth doing. The picker filters locally.
const TOP_N: usize = 200;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ValidatorInfo {
    pub vote_account: String,
    pub commission: u8,
    pub activated_stake_sol: f64,
    /// Measured, not modelled — and absent when nobody has measured it.
    ///
    /// This used to be `7.0 * (1 - commission)`: a constant dressed as a yield.
    /// Two validators with the same commission got the same number no matter
    /// how they actually performed, and the card printed it as "~6.65% APY" as
    /// though it had been observed. When the real figure is unavailable the
    /// field is now empty and the card shows nothing rather than a guess.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub apy_estimate_pct: Option<f64>,
    pub epoch_credits_recent: u64,
    /// Who the validator is, when they have said. A list of base58 vote
    /// accounts is not a choice anyone can make.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub icon: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uptime_pct: Option<f64>,
    #[serde(default)]
    pub is_jito: bool,
}

/// Stakewiz's public validator index. Names, logos and a real APY, none of
/// which the Solana RPC knows: `getVoteAccounts` returns identities and stake,
/// and nothing that tells a person who they are delegating to.
#[derive(Debug, Deserialize)]
struct StakewizValidator {
    vote_identity: String,
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    image: Option<String>,
    /// Percent — usually. Stakewiz reports some validators in basis points
    /// (400, 500, 10000 all appear), and a `u8` cannot hold 400, so serde
    /// rejected the ENTIRE array over a handful of rows and the picker fell
    /// back to the anonymous RPC list every time. Read wide, normalise after.
    #[serde(default)]
    commission: Option<f64>,
    #[serde(default)]
    activated_stake: Option<f64>,
    #[serde(default)]
    apy_estimate: Option<f64>,
    #[serde(default)]
    uptime: Option<f64>,
    #[serde(default)]
    delinquent: Option<bool>,
    #[serde(default)]
    is_jito: Option<bool>,
}

/// The validators a user can sensibly delegate to.
///
/// Stakewiz first, because it is the only source that can name them. The RPC
/// is the fallback and gives a usable-but-anonymous list, so a Stakewiz outage
/// degrades the picker rather than emptying it.
pub async fn get_top_validators(
    rpc: &RpcClient,
    http: &reqwest::Client,
) -> Result<Vec<ValidatorInfo>, AppError> {
    match from_stakewiz(http).await {
        Ok(list) if !list.is_empty() => Ok(list),
        Ok(_) => from_rpc(rpc),
        Err(e) => {
            tracing::warn!(error = %e, "Stakewiz unavailable, falling back to RPC vote accounts");
            from_rpc(rpc)
        }
    }
}

/// Percent, whichever unit it arrived in.
///
/// Anything above 100 cannot be a percentage, and basis points is the only
/// other unit a commission is quoted in — 400 becomes 4%, 10000 becomes 100%
/// and is then filtered out like any other validator taking everything.
fn normalise_commission(raw: Option<f64>) -> u8 {
    let pct = match raw {
        Some(c) if c > 100.0 => c / 100.0,
        Some(c) => c,
        None => 100.0,
    };
    pct.clamp(0.0, 100.0).round() as u8
}

async fn from_stakewiz(http: &reqwest::Client) -> Result<Vec<ValidatorInfo>, AppError> {
    let rows: Vec<StakewizValidator> = http
        .get("https://api.stakewiz.com/validators")
        .timeout(std::time::Duration::from_secs(12))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("stakewiz: {e}")))?
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("stakewiz decode: {e}")))?;

    let mut out: Vec<ValidatorInfo> = rows
        .into_iter()
        // A delinquent validator is not voting, so delegating to it earns
        // nothing. It has no place in a list offered as a choice.
        .filter(|v| !v.delinquent.unwrap_or(false))
        .filter(|v| normalise_commission(v.commission) <= MAX_COMMISSION)
        .map(|v| ValidatorInfo {
            commission: normalise_commission(v.commission),
            vote_account: v.vote_identity,
            activated_stake_sol: (v.activated_stake.unwrap_or(0.0) * 100.0).round() / 100.0,
            apy_estimate_pct: v.apy_estimate.map(|a| (a * 100.0).round() / 100.0),
            epoch_credits_recent: 0,
            name: v.name.filter(|n| !n.trim().is_empty()),
            icon: v.image.filter(|i| i.starts_with("http")),
            uptime_pct: v.uptime.map(|u| (u * 100.0).round() / 100.0),
            is_jito: v.is_jito.unwrap_or(false),
        })
        .collect();

    // Most stake first: the same ordering the old endpoint used, and the one
    // that puts established operators at the top of an unfiltered list.
    out.sort_by(|a, b| {
        b.activated_stake_sol
            .partial_cmp(&a.activated_stake_sol)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    out.truncate(TOP_N);
    Ok(out)
}

fn from_rpc(rpc: &RpcClient) -> Result<Vec<ValidatorInfo>, AppError> {
    let vote_accounts = rpc
        .get_vote_accounts()
        .map_err(|e| AppError::SolanaRpcError(format!("get_vote_accounts: {e}")))?;

    let mut validators: Vec<ValidatorInfo> = vote_accounts
        .current
        .iter()
        .filter(|v| v.commission <= MAX_COMMISSION)
        .map(|v| ValidatorInfo {
            vote_account: v.vote_pubkey.clone(),
            commission: v.commission,
            activated_stake_sol: ((v.activated_stake as f64 / LAMPORTS_PER_SOL) * 100.0).round()
                / 100.0,
            // No APY here: nothing in this response measures yield.
            apy_estimate_pct: None,
            epoch_credits_recent: v
                .epoch_credits
                .last()
                .map(|&(_, credits, prev)| credits.saturating_sub(prev))
                .unwrap_or(0),
            name: None,
            icon: None,
            uptime_pct: None,
            is_jito: false,
        })
        .collect();

    validators.sort_by(|a, b| {
        let sa = a.activated_stake_sol * (1.0 - a.commission as f64 / 100.0);
        let sb = b.activated_stake_sol * (1.0 - b.commission as f64 / 100.0);
        sb.partial_cmp(&sa).unwrap_or(std::cmp::Ordering::Equal)
    });

    validators.truncate(TOP_N);
    Ok(validators)
}

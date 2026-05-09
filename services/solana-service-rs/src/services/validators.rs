use serde::Serialize;
use solana_client::rpc_client::RpcClient;

use crate::error::AppError;

const BASE_APY_PCT: f64 = 7.0; // approximate Solana network staking APY
const LAMPORTS_PER_SOL: f64 = 1_000_000_000.0;
const MAX_COMMISSION: u8 = 10; // exclude validators charging > 10%
const TOP_N: usize = 20;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ValidatorInfo {
    pub vote_account: String,
    pub commission: u8,
    pub activated_stake_sol: f64,
    pub apy_estimate_pct: f64,
    pub epoch_credits_recent: u64,
}

/// Fetch top validators from the Solana RPC and return them sorted by score
/// (stake × (1 − commission)), filtered to commission ≤ 10%.
pub fn get_top_validators(rpc: &RpcClient) -> Result<Vec<ValidatorInfo>, AppError> {
    let vote_accounts = rpc
        .get_vote_accounts()
        .map_err(|e| AppError::SolanaRpcError(format!("get_vote_accounts: {e}")))?;

    let mut validators: Vec<ValidatorInfo> = vote_accounts
        .current
        .iter()
        .filter(|v| v.commission <= MAX_COMMISSION)
        .map(|v| {
            let activated_stake_sol = v.activated_stake as f64 / LAMPORTS_PER_SOL;
            let apy_estimate_pct = (1.0 - v.commission as f64 / 100.0) * BASE_APY_PCT;
            // credits earned in the most recent completed epoch
            let epoch_credits_recent = v
                .epoch_credits
                .last()
                .map(|&(_, credits, prev)| credits.saturating_sub(prev))
                .unwrap_or(0);

            ValidatorInfo {
                vote_account: v.vote_pubkey.clone(),
                commission: v.commission,
                activated_stake_sol: (activated_stake_sol * 100.0).round() / 100.0,
                apy_estimate_pct: (apy_estimate_pct * 100.0).round() / 100.0,
                epoch_credits_recent,
            }
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

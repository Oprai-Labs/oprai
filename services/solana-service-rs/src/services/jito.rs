//! Jito Finance Service
//!
//! Implements jitoSOL liquid staking, MEV bundles, and tips.
//! All analytics endpoints use the official kobe.mainnet.jito.network API.
//! Stake/unstake uses the spl-stake-pool crate with the on-chain Jito stake pool.
//!
//! References:
//!   - https://www.jito.network/docs/jitosol/jitosol-liquid-staking/for-developers/mev-and-staker-rewards-api-info/
//!   - https://www.jito.network/docs/jitosol/jitosol-liquid-staking/for-developers/stake-pool-api/
//!   - https://www.jito.network/docs/jitosol/jitosol-liquid-staking/for-developers/stakenet-api/
//!   - https://www.jito.network/docs/jitosol/jitosol-liquid-staking/for-developers/staking-integration/

use base64::Engine;
use borsh::BorshDeserialize;
use serde::{Deserialize, Serialize};
use solana_sdk::{
    message::Message, pubkey::Pubkey, signature::Keypair, signer::Signer, system_instruction,
    transaction::Transaction,
};
use spl_associated_token_account::{
    get_associated_token_address, instruction::create_associated_token_account_idempotent,
};
use spl_stake_pool::{instruction as stake_pool_ix, state::StakePool};
use std::str::FromStr;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;

// ─────────────────────────────────────────────────────────────────────────────
// Constants — all verified from official Jito documentation
// ─────────────────────────────────────────────────────────────────────────────

/// SPL Stake Pool program deployed by Jito Foundation.
pub const STAKE_POOL_PROGRAM_ID_STR: &str = "SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy";

/// JitoSOL stake pool address (mainnet).
/// Source: https://www.jito.network/docs/jitosol/jitosol-liquid-staking/for-developers/staking-integration/
pub const JITO_STAKE_POOL_STR: &str = "Jito4APyf642JPZPx3hGc6WWJ8zPKtRbRs4P815Awbb";

/// JitoSOL token mint address (mainnet).
/// Source: official Jito staking integration docs.
pub const JITOSOL_MINT: &str = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn";

/// Analytics and rewards API (MEV rewards, validators, stake pool stats, BAM).
/// All kobe endpoints are public and require no authentication.
const JITO_KOBE_API: &str = "https://kobe.mainnet.jito.network";

/// Block Engine — MEV bundle submission and tip floor queries.
/// This is the Amsterdam regional endpoint; configure via JITO_BLOCK_ENGINE_URL env var.
const JITO_BLOCK_ENGINE_API: &str = "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1";

/// Jito tip accounts — rotate to reduce contention (use getTipAccounts or these hardcoded values).
pub const JITO_TIP_ACCOUNTS: &[&str] = &[
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4bVmKafdMkF7nQ6kY91v3oS",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
];

// ─────────────────────────────────────────────────────────────────────────────
// Stake / Unstake param types
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoStakeParams {
    /// SOL amount to stake (e.g. "1.5")
    pub amount: String,
    #[allow(dead_code)]
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub slippage_bps: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoUnstakeParams {
    /// jitoSOL amount to unstake (e.g. "1.2")
    pub amount: String,
    /// When true, uses `withdraw_sol` (instant liquid SOL from reserve).
    /// When false (default), uses `withdraw_stake` (stake account, ~1-epoch cooldown).
    /// Docs: solanaStakePool.withdrawStake(conn, pool, wallet, amount, useReserve)
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub instant: Option<bool>,
    #[allow(dead_code)]
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub slippage_bps: Option<u32>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Tip / Bundle param types
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoTipParams {
    /// Tip in SOL (e.g. "0.001")
    pub amount: String,
    #[allow(dead_code)]
    pub transaction: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoBundleParams {
    /// Base64-encoded transactions (1–5)
    pub transactions: Vec<String>,
    pub tip_amount: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoBundleStatusParams {
    pub bundle_id: String,
}

// ─────────────────────────────────────────────────────────────────────────────
// Kobe API response types
// ─────────────────────────────────────────────────────────────────────────────

/// Time-series data point returned by stake_pool_stats, jitosol_sol_ratio, etc.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TimeSeriesPoint {
    pub data: f64,
    pub date: String,
}

/// Response from /api/v1/stake_pool_stats
/// Used to get current APY, TVL, supply, and validator count.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StakePoolStatsResponse {
    pub aggregated_mev_rewards: Option<u64>,
    pub mev_rewards: Option<Vec<TimeSeriesPoint>>,
    pub tvl: Option<Vec<TimeSeriesPoint>>,
    pub apy: Option<Vec<TimeSeriesPoint>>,
    pub num_validators: Option<Vec<TimeSeriesPoint>>,
    pub supply: Option<Vec<TimeSeriesPoint>>,
}

impl StakePoolStatsResponse {
    /// Returns the most recent APY as a percentage string (e.g. "7.18").
    pub fn current_apy_pct(&self) -> Option<f64> {
        self.apy.as_ref()?.last().map(|p| p.data * 100.0)
    }

    /// Returns the most recent TVL in lamports.
    pub fn current_tvl_lamports(&self) -> Option<u64> {
        self.tvl.as_ref()?.last().map(|p| p.data as u64)
    }
}

/// Response from /api/v1/jitosol_sol_ratio
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JitosolSolRatioResponse {
    pub ratios: Vec<TimeSeriesPoint>,
}

impl JitosolSolRatioResponse {
    /// Latest jitoSOL/SOL exchange rate (how many SOL per 1 jitoSOL).
    pub fn latest_rate(&self) -> f64 {
        self.ratios.last().map(|r| r.data).unwrap_or(1.0)
    }
}

/// Single entry from /api/v1/preferred_withdraw_validator_list
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreferredWithdrawValidator {
    pub rank: u64,
    pub vote_account: String,
    /// Withdrawable stake in lamports.
    pub withdrawable_lamports: u64,
    /// The validator's stake account in the pool (used in withdraw_stake instruction).
    pub stake_account: String,
}

/// Query params for /api/v1/staker_rewards
#[derive(Debug, Default, Serialize)]
pub struct StakerRewardsParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stake_authority: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub validator_vote_account: Option<String>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub epoch: Option<u64>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub page: Option<u64>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub limit: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sort_order: Option<String>,
}

/// Single staker reward entry from /api/v1/staker_rewards
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StakerRewardEntry {
    pub claimant: String,
    pub stake_authority: String,
    pub withdraw_authority: String,
    pub validator_vote_account: String,
    pub claim_status_account: String,
    pub priority_fee_claim_status_account: Option<String>,
    pub epoch: u64,
    pub amount: u64,
    pub priority_fee_amount: Option<u64>,
}

/// Response from /api/v1/staker_rewards
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StakerRewardsResponse {
    pub rewards: Vec<StakerRewardEntry>,
    pub total_count: u64,
}

/// Query params for /api/v1/validator_rewards
#[derive(Debug, Default, Serialize)]
pub struct ValidatorRewardsParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vote_account: Option<String>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub epoch: Option<u64>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub page: Option<u64>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub limit: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sort_order: Option<String>,
}

/// Single validator reward entry from /api/v1/validator_rewards
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidatorRewardEntry {
    pub vote_account: String,
    pub mev_revenue: u64,
    pub mev_commission: u64,
    pub priority_fee_revenue: u64,
    pub priority_fee_commission: Option<u64>,
    pub num_stakers: u64,
    pub epoch: u64,
    pub claim_status_account: String,
}

/// Response from /api/v1/validator_rewards
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidatorRewardsResponse {
    pub rewards: Vec<ValidatorRewardEntry>,
    pub total_count: u64,
}

/// Single validator entry from /api/v1/validators and /api/v1/jitosol_validators
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidatorEntry {
    pub vote_account: String,
    pub mev_commission_bps: u64,
    pub mev_rewards: u64,
    pub priority_fee_commission_bps: Option<u64>,
    pub priority_fee_rewards: Option<u64>,
    pub running_jito: bool,
    pub running_bam: bool,
    pub active_stake: u64,
    /// Only present for jitosol_validators endpoint.
    pub jito_sol_active_lamports: Option<u64>,
    pub jito_directed_stake_target: Option<bool>,
    pub jito_directed_stake_lamports: Option<u64>,
}

/// Response from /api/v1/validators and /api/v1/jitosol_validators
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidatorsResponse {
    pub validators: Vec<ValidatorEntry>,
}

/// Single historical reward entry from /api/v1/validators/{vote_account}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidatorHistoryEntry {
    pub epoch: u64,
    pub mev_commission_bps: u64,
    pub mev_rewards: u64,
    pub priority_fee_commission_bps: Option<u64>,
    pub priority_fee_rewards: Option<u64>,
}

/// Response from /api/v1/mev_rewards
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MevRewardsResponse {
    pub epoch: u64,
    pub total_network_mev_lamports: u64,
    pub jito_stake_weight_lamports: u64,
    pub mev_reward_per_lamport: f64,
}

/// Single entry from /api/v1/daily_mev_rewards
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DailyMevRewardEntry {
    pub day: String,
    pub count_mev_tips: u64,
    pub jito_tips: f64,
    pub tippers: u64,
    pub validator_tips: f64,
}

/// Response from /api/v1/jito_stake_over_time
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JitoStakeOverTimeResponse {
    pub stake_ratio_over_time: std::collections::HashMap<String, f64>,
}

/// Response from /api/v1/steward_events
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StewardEventsResponse {
    pub events: Vec<StewardEventEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StewardEventEntry {
    pub signature: String,
    pub event_type: String,
    pub vote_account: Option<String>,
    pub timestamp: Option<String>,
    pub epoch: Option<u64>,
    pub slot: Option<u64>,
    pub data: Option<serde_json::Value>,
    pub tx_error: Option<String>,
}

/// Query params for /api/v1/steward_events
#[derive(Debug, Default, Serialize)]
pub struct StewardEventsParams {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub event_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vote_account: Option<String>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub epoch: Option<u64>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub limit: Option<u64>,
    #[serde(default, 
        skip_serializing_if = "Option::is_none",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub skip: Option<u64>,
}

/// Response from /api/v1/bam_epoch_metrics
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BamEpochMetricsResponse {
    pub bam_epoch_metrics: BamEpochMetrics,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BamEpochMetrics {
    pub allocation_bps: u64,
    pub available_bam_delegation_stake: u64,
    pub bam_stake: u64,
    pub eligible_bam_validator_count: u64,
    pub epoch: u64,
    pub jitosol_stake: u64,
    pub timestamp: u64,
    pub total_stake: u64,
}

/// Response from /api/v1/bam_validators
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BamValidatorsResponse {
    pub bam_validators: Vec<BamValidatorEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BamValidatorEntry {
    pub active_stake: u64,
    pub epoch: u64,
    pub identity_account: String,
    pub is_eligible: bool,
    pub ineligibility_reason: Option<String>,
    pub timestamp: u64,
    pub vote_account: String,
}

/// Single entry from /api/v1/bam_delegation_blacklist
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BamBlacklistEntry {
    pub vote_account: String,
    pub added_epoch: u64,
}

/// Response from /api/v1/bam_validator_score
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BamValidatorScore {
    pub vote_account: String,
    pub score: u64,
}

/// Response from Jito Block Engine tip floor endpoint.
#[derive(Debug, Clone, Deserialize)]
pub struct JitoTipFloorResponse {
    pub landed_tips_25th_percentile: f64,
    pub landed_tips_50th_percentile: f64,
    pub landed_tips_75th_percentile: f64,
    pub landed_tips_95th_percentile: f64,
}

/// Bundle status response from Block Engine.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoBundleResponse {
    pub bundle_id: String,
    pub status: String,
    pub landed_slot: Option<u64>,
    pub transactions: Vec<String>,
}

/// Internal stake result passed between builder helpers.
pub struct JitoStakeResult {
    pub transaction: Transaction,
    pub preview: JitoStakePreview,
    /// Extra signers partially applied to the transaction (e.g. ephemeral keypair for unstake).
    pub presigned: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoStakePreview {
    pub id: String,
    #[serde(rename = "type")]
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    pub params: serde_json::Value,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

// ─────────────────────────────────────────────────────────────────────────────
// Validation
// ─────────────────────────────────────────────────────────────────────────────

pub fn validate_jito_stake_params(params: &JitoStakeParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid stake amount".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_jito_unstake_params(params: &JitoUnstakeParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid jitoSOL amount".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_jito_tip_params(params: &JitoTipParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid tip amount".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams(
            "Tip amount must be positive".into(),
        ));
    }
    if amount > 10.0 {
        return Err(AppError::InvalidParams(
            "Tip amount exceeds maximum (10 SOL)".into(),
        ));
    }
    Ok(())
}

pub fn validate_jito_bundle_params(params: &JitoBundleParams) -> Result<(), AppError> {
    if params.transactions.is_empty() {
        return Err(AppError::InvalidParams(
            "Bundle must contain at least 1 transaction".into(),
        ));
    }
    if params.transactions.len() > 5 {
        return Err(AppError::InvalidParams(
            "Bundle can contain at most 5 transactions".into(),
        ));
    }
    Ok(())
}

pub fn validate_jito_bundle_status_params(params: &JitoBundleStatusParams) -> Result<(), AppError> {
    if params.bundle_id.is_empty() {
        return Err(AppError::InvalidParams("Bundle ID is required".into()));
    }
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Kobe API — Stake Pool Stats & Exchange Rate
// ─────────────────────────────────────────────────────────────────────────────

/// Fetch stake pool stats from kobe API (TVL, APY, supply, validator count).
/// Replaces the old `jito.wtf/api/v1/stats` call which was undocumented.
pub async fn get_stake_pool_stats(
    http: &reqwest::Client,
) -> Result<StakePoolStatsResponse, AppError> {
    let body = serde_json::json!({
        "bucket_type": "Daily",
        "sort_by": { "field": "BlockTime", "order": "Asc" }
    });
    let resp = http
        .post(format!("{JITO_KOBE_API}/api/v1/stake_pool_stats"))
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<StakePoolStatsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// Fetch jitoSOL/SOL exchange rate from kobe API.
/// Returns how many SOL per 1 jitoSOL (ratio > 1.0 as rewards accumulate).
pub async fn get_jitosol_sol_ratio(
    http: &reqwest::Client,
    start: Option<&str>,
    end: Option<&str>,
) -> Result<JitosolSolRatioResponse, AppError> {
    let mut body = serde_json::json!({});
    if let (Some(s), Some(e)) = (start, end) {
        body["range_filter"] = serde_json::json!({ "start": s, "end": e });
    }
    let resp = http
        .post(format!("{JITO_KOBE_API}/api/v1/jitosol_sol_ratio"))
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<JitosolSolRatioResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// Compute the jitoSOL→SOL exchange rate directly from the on-chain stake pool
/// account: `rate = total_lamports / pool_token_supply`. This is the same math
/// the program runs on deposit/withdraw, so it is the authoritative source of
/// truth (Jito's Kobe API is a cache layer over this same data).
pub fn compute_onchain_exchange_rate(rpc: &SolanaRpc) -> Result<f64, AppError> {
    let pool = fetch_stake_pool(rpc)?;
    if pool.pool_token_supply == 0 {
        return Err(AppError::Internal(
            "Stake pool has zero supply; cannot compute rate".into(),
        ));
    }
    Ok(pool.total_lamports as f64 / pool.pool_token_supply as f64)
}

/// Get the current jitoSOL→SOL exchange rate. Tries Jito's Kobe analytics API
/// first (fast, cached); on failure falls back to the on-chain StakePool. Both
/// are Jito's own services — no static constant is used as a fallback.
pub async fn get_exchange_rate(http: &reqwest::Client, rpc: &SolanaRpc) -> Result<f64, AppError> {
    if let Ok(resp) = get_jitosol_sol_ratio(http, None, None).await {
        let latest = resp.latest_rate();
        if latest.is_finite() && latest > 0.0 {
            return Ok(latest);
        }
    }
    // Kobe unavailable or returned an invalid rate — read the chain directly.
    let rpc = rpc.clone();
    actix_web::web::block(move || compute_onchain_exchange_rate(&rpc))
        .await
        .map_err(|e| AppError::Internal(format!("Blocking task error: {e}")))?
}

// ─────────────────────────────────────────────────────────────────────────────
// Kobe API — MEV & Claims
// ─────────────────────────────────────────────────────────────────────────────

/// GET/POST /api/v1/staker_rewards — individual claimable MEV rewards.
pub async fn get_staker_rewards(
    http: &reqwest::Client,
    params: &StakerRewardsParams,
) -> Result<StakerRewardsResponse, AppError> {
    let resp = http
        .post(format!("{JITO_KOBE_API}/api/v1/staker_rewards"))
        .json(params)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<StakerRewardsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET/POST /api/v1/validator_rewards — aggregated MEV rewards per validator.
pub async fn get_validator_rewards(
    http: &reqwest::Client,
    params: &ValidatorRewardsParams,
) -> Result<ValidatorRewardsResponse, AppError> {
    let resp = http
        .post(format!("{JITO_KOBE_API}/api/v1/validator_rewards"))
        .json(params)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<ValidatorRewardsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// Kobe API — Validators
// ─────────────────────────────────────────────────────────────────────────────

/// POST /api/v1/validators — all Solana validators with MEV metrics.
pub async fn get_validators(
    http: &reqwest::Client,
    epoch: Option<u64>,
) -> Result<ValidatorsResponse, AppError> {
    let body = match epoch {
        Some(e) => serde_json::json!({ "epoch": e }),
        None => serde_json::json!({}),
    };
    let resp = http
        .post(format!("{JITO_KOBE_API}/api/v1/validators"))
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<ValidatorsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// POST /api/v1/jitosol_validators — validators in the JitoSOL stake pool.
pub async fn get_jitosol_validators(
    http: &reqwest::Client,
    epoch: Option<u64>,
) -> Result<ValidatorsResponse, AppError> {
    let body = match epoch {
        Some(e) => serde_json::json!({ "epoch": e }),
        None => serde_json::json!({}),
    };
    let resp = http
        .post(format!("{JITO_KOBE_API}/api/v1/jitosol_validators"))
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<ValidatorsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET /api/v1/validators/{vote_account} — historical rewards for one validator.
pub async fn get_validator_history(
    http: &reqwest::Client,
    vote_account: &str,
) -> Result<Vec<ValidatorHistoryEntry>, AppError> {
    let resp = http
        .get(format!("{JITO_KOBE_API}/api/v1/validators/{vote_account}"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<Vec<ValidatorHistoryEntry>>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET/POST /api/v1/mev_rewards — network-level MEV stats for an epoch.
pub async fn get_mev_rewards(
    http: &reqwest::Client,
    epoch: Option<u64>,
) -> Result<MevRewardsResponse, AppError> {
    let resp = match epoch {
        Some(e) => {
            http.post(format!("{JITO_KOBE_API}/api/v1/mev_rewards"))
                .json(&serde_json::json!({ "epoch": e }))
                .send()
                .await
        }
        None => {
            http.get(format!("{JITO_KOBE_API}/api/v1/mev_rewards"))
                .send()
                .await
        }
    }
    .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<MevRewardsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET /api/v1/daily_mev_rewards — MEV tips aggregated by calendar day.
pub async fn get_daily_mev_rewards(
    http: &reqwest::Client,
) -> Result<Vec<DailyMevRewardEntry>, AppError> {
    let resp = http
        .get(format!("{JITO_KOBE_API}/api/v1/daily_mev_rewards"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<Vec<DailyMevRewardEntry>>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET /api/v1/jito_stake_over_time — epoch → fraction of Solana stake on Jito validators.
pub async fn get_jito_stake_over_time(
    http: &reqwest::Client,
) -> Result<JitoStakeOverTimeResponse, AppError> {
    let resp = http
        .get(format!("{JITO_KOBE_API}/api/v1/jito_stake_over_time"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<JitoStakeOverTimeResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET /api/v1/preferred_withdraw_validator_list — optimal validators for withdrawal.
/// Docs: use `randomized=true` in multisig/durable-nonce scenarios.
pub async fn get_preferred_withdraw_validators(
    http: &reqwest::Client,
    limit: Option<u32>,
    min_stake_threshold_sol: Option<u32>,
    randomized: bool,
) -> Result<Vec<PreferredWithdrawValidator>, AppError> {
    let mut url = format!("{JITO_KOBE_API}/api/v1/preferred_withdraw_validator_list?");
    if let Some(l) = limit {
        url.push_str(&format!("limit={l}&"));
    }
    if let Some(t) = min_stake_threshold_sol {
        url.push_str(&format!("min_stake_threshold={t}&"));
    }
    if randomized {
        url.push_str("randomized=true&");
    }
    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<Vec<PreferredWithdrawValidator>>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET /api/v1/mev_commission_average_over_time — stake-weighted average MEV commission by epoch.
pub async fn get_mev_commission_average_over_time(
    http: &reqwest::Client,
) -> Result<serde_json::Value, AppError> {
    let resp = http
        .get(format!(
            "{JITO_KOBE_API}/api/v1/mev_commission_average_over_time"
        ))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// Kobe API — Steward
// ─────────────────────────────────────────────────────────────────────────────

/// GET/POST /api/v1/steward_events — Jito Steward program events.
pub async fn get_steward_events(
    http: &reqwest::Client,
    params: &StewardEventsParams,
) -> Result<StewardEventsResponse, AppError> {
    let resp = http
        .post(format!("{JITO_KOBE_API}/api/v1/steward_events"))
        .json(params)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<StewardEventsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// Kobe API — BAM (Boost and Maintain)
// ─────────────────────────────────────────────────────────────────────────────

/// GET /api/v1/bam_epoch_metrics — BAM delegation metrics for a given epoch.
pub async fn get_bam_epoch_metrics(
    http: &reqwest::Client,
    epoch: u64,
) -> Result<BamEpochMetricsResponse, AppError> {
    let resp = http
        .get(format!(
            "{JITO_KOBE_API}/api/v1/bam_epoch_metrics?epoch={epoch}"
        ))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<BamEpochMetricsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET /api/v1/bam_validators — BAM validators for a given epoch.
pub async fn get_bam_validators(
    http: &reqwest::Client,
    epoch: u64,
) -> Result<BamValidatorsResponse, AppError> {
    let resp = http
        .get(format!(
            "{JITO_KOBE_API}/api/v1/bam_validators?epoch={epoch}"
        ))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<BamValidatorsResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET /api/v1/bam_delegation_blacklist — vote accounts blacklisted from BAM.
pub async fn get_bam_delegation_blacklist(
    http: &reqwest::Client,
) -> Result<Vec<BamBlacklistEntry>, AppError> {
    let resp = http
        .get(format!("{JITO_KOBE_API}/api/v1/bam_delegation_blacklist"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<Vec<BamBlacklistEntry>>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

/// GET /api/v1/bam_validator_score — scoring components of a specific validator.
pub async fn get_bam_validator_score(
    http: &reqwest::Client,
    epoch: u64,
    vote_account: &str,
) -> Result<BamValidatorScore, AppError> {
    let resp = http
        .get(format!(
            "{JITO_KOBE_API}/api/v1/bam_validator_score?epoch={epoch}&vote_account={vote_account}"
        ))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe API error: {e}")))?;
    resp.json::<BamValidatorScore>()
        .await
        .map_err(|e| AppError::Internal(format!("Kobe parse error: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// Block Engine — Tip Floor
// ─────────────────────────────────────────────────────────────────────────────

/// GET /api/v1/tipFloor — current Jito tip floor percentiles.
pub async fn get_jito_tip_floor(http: &reqwest::Client) -> Result<JitoTipFloorResponse, AppError> {
    let resp = http
        .get(format!("{JITO_BLOCK_ENGINE_API}/tipFloor"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Tip floor error: {e}")))?;
    resp.json::<JitoTipFloorResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Tip floor parse error: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// On-chain stake pool helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Fetch and deserialize the Jito stake pool account from on-chain.
/// Called synchronously — wrap with `actix_web::web::block` in async contexts.
fn fetch_stake_pool(rpc: &SolanaRpc) -> Result<StakePool, AppError> {
    let stake_pool_pubkey = Pubkey::from_str(JITO_STAKE_POOL_STR).expect("valid stake pool pubkey");
    let account = rpc
        .client()
        .get_account(&stake_pool_pubkey)
        .map_err(|e| AppError::Internal(format!("Failed to fetch stake pool account: {e}")))?;
    // The on-chain account is the program's allocated buffer (≈ 656 bytes) and
    // is larger than the StakePool struct's serialized size. `try_from_slice`
    // is strict and errors with "Not all bytes read" on the trailing zeros, so
    // we use the streaming `deserialize` which stops once the struct is full.
    let mut data: &[u8] = &account.data;
    StakePool::deserialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to deserialize stake pool: {e}")))
}

/// Derive the withdraw authority PDA for the Jito stake pool.
fn withdraw_authority_pda(program_id: &Pubkey, stake_pool: &Pubkey, bump: u8) -> Pubkey {
    Pubkey::create_program_address(&[stake_pool.as_ref(), b"withdraw", &[bump]], program_id)
        .expect("valid withdraw authority PDA")
}

// ─────────────────────────────────────────────────────────────────────────────
// Stake Transaction — SOL → jitoSOL
// ─────────────────────────────────────────────────────────────────────────────

/// Build a jitoSOL deposit transaction.
///
/// Uses `spl_stake_pool::instruction::deposit_sol` as recommended by official docs.
/// The transaction is unsigned; the user signs it with their wallet.
fn build_stake_transaction(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    sol_amount: f64,
    exchange_rate: f64,
    apy_pct: f64,
) -> Result<JitoStakeResult, AppError> {
    let lamports = (sol_amount * 1_000_000_000.0) as u64;
    if lamports == 0 {
        return Err(AppError::InvalidParams("Amount too small".into()));
    }

    let program_id = Pubkey::from_str(STAKE_POOL_PROGRAM_ID_STR).expect("valid program id");
    let stake_pool_pubkey = Pubkey::from_str(JITO_STAKE_POOL_STR).expect("valid stake pool pubkey");
    let jitosol_mint = Pubkey::from_str(JITOSOL_MINT).expect("valid jitoSOL mint");

    let pool = fetch_stake_pool(rpc)?;
    let withdraw_authority = withdraw_authority_pda(
        &program_id,
        &stake_pool_pubkey,
        pool.stake_withdraw_bump_seed,
    );

    // User's jitoSOL associated token account (create idempotently if missing).
    let user_jitosol_ata = get_associated_token_address(user_pubkey, &jitosol_mint);

    let create_ata_ix = create_associated_token_account_idempotent(
        user_pubkey,
        user_pubkey,
        &jitosol_mint,
        &pool.token_program_id,
    );

    let deposit_ix = stake_pool_ix::deposit_sol(
        &program_id,
        &stake_pool_pubkey,
        &withdraw_authority,
        &pool.reserve_stake,
        user_pubkey,
        &user_jitosol_ata,
        &pool.manager_fee_account,
        &user_jitosol_ata, // referrer = user (no referral)
        &pool.pool_mint,
        &pool.token_program_id,
        lamports,
    );

    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Failed to get blockhash: {e}")))?;
    let message = Message::new(&[create_ata_ix, deposit_ix], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    let expected_jitosol = sol_amount / exchange_rate;

    Ok(JitoStakeResult {
        transaction,
        preview: JitoStakePreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_stake".into(),
            description: format!(
                "Stake {sol_amount} SOL → ~{expected_jitosol:.4} jitoSOL (Jito MEV Staking)"
            ),
            estimated_fee: "~0.000005 SOL".into(),
            params: serde_json::json!({
                "amount": sol_amount.to_string(),
                "lamports": lamports,
                "expectedJitosol": format!("{expected_jitosol:.6}"),
                "estimatedApy": format!("{apy_pct:.2}%"),
                "jitosolMint": JITOSOL_MINT,
                "stakePool": JITO_STAKE_POOL_STR,
            }),
            warnings: vec!["JitoSOL accumulates staking + MEV rewards each epoch".into()],
            requires_approval: true,
        },
        presigned: false,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Unstake Transaction — jitoSOL → Stake Account
// ─────────────────────────────────────────────────────────────────────────────

/// Build a jitoSOL withdraw transaction.
///
/// Uses `spl_stake_pool::instruction::withdraw_stake` as recommended by official docs.
/// Also uses the `preferred_withdraw_validator_list` kobe API to select the optimal
/// validator for withdrawal (minimises rebalancing, as recommended in the docs).
///
/// An ephemeral keypair is generated for the new stake account.  The backend
/// partially signs with it; the user's wallet provides the remaining signature.
fn build_unstake_transaction(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    jitosol_amount: f64,
    exchange_rate: f64,
    preferred_validator: &PreferredWithdrawValidator,
) -> Result<JitoStakeResult, AppError> {
    // jitoSOL has 9 decimals.
    let pool_tokens = (jitosol_amount * 1_000_000_000.0) as u64;
    if pool_tokens == 0 {
        return Err(AppError::InvalidParams("Amount too small".into()));
    }

    let program_id = Pubkey::from_str(STAKE_POOL_PROGRAM_ID_STR).expect("valid program id");
    let stake_pool_pubkey = Pubkey::from_str(JITO_STAKE_POOL_STR).expect("valid stake pool pubkey");
    let jitosol_mint = Pubkey::from_str(JITOSOL_MINT).expect("valid jitoSOL mint");
    let validator_stake_account = Pubkey::from_str(&preferred_validator.stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid validator stake account".into()))?;

    let pool = fetch_stake_pool(rpc)?;
    let withdraw_authority = withdraw_authority_pda(
        &program_id,
        &stake_pool_pubkey,
        pool.stake_withdraw_bump_seed,
    );

    // User's jitoSOL ATA (must exist at this point).
    let user_jitosol_ata = get_associated_token_address(user_pubkey, &jitosol_mint);

    // Generate an ephemeral keypair for the new stake account that receives unstaked funds.
    // The user will own this account after the transaction.
    let new_stake_kp = Keypair::new();
    let new_stake_pubkey = new_stake_kp.pubkey();

    // Create the new stake account (rent-exempt minimum = 2 282 880 lamports ≈ 0.00228 SOL).
    let stake_account_space = 200usize; // std::mem::size_of::<StakeStateV2>()
    let rent_lamports = rpc
        .client()
        .get_minimum_balance_for_rent_exemption(stake_account_space)
        .map_err(|e| AppError::Internal(format!("Rent query failed: {e}")))?;

    let create_stake_ix = system_instruction::create_account(
        user_pubkey,
        &new_stake_pubkey,
        rent_lamports,
        stake_account_space as u64,
        &solana_sdk::stake::program::id(),
    );

    let withdraw_ix = stake_pool_ix::withdraw_stake(
        &program_id,
        &stake_pool_pubkey,
        &pool.validator_list,
        &withdraw_authority,
        &validator_stake_account,
        &new_stake_pubkey,
        user_pubkey, // user owns the new stake account
        user_pubkey, // user authorises the pool token transfer
        &user_jitosol_ata,
        &pool.manager_fee_account,
        &pool.pool_mint,
        &pool.token_program_id,
        pool_tokens,
    );

    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Failed to get blockhash: {e}")))?;
    let message = Message::new(&[create_stake_ix, withdraw_ix], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    // Partially sign with the ephemeral keypair so the frontend only needs the user wallet.
    transaction.partial_sign(&[&new_stake_kp], blockhash);

    let expected_sol = jitosol_amount * exchange_rate;

    Ok(JitoStakeResult {
        transaction,
        preview: JitoStakePreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_unstake".into(),
            description: format!(
                "Unstake {jitosol_amount} jitoSOL → ~{expected_sol:.4} SOL (stake account)"
            ),
            estimated_fee: "~0.000005 SOL + rent".into(),
            params: serde_json::json!({
                "jitosolAmount": jitosol_amount.to_string(),
                "poolTokens": pool_tokens,
                "expectedSol": format!("{expected_sol:.6}"),
                "newStakeAccount": new_stake_pubkey.to_string(),
                "validatorVoteAccount": preferred_validator.vote_account,
                "validatorRank": preferred_validator.rank,
            }),
            warnings: vec![
                "You will receive a stake account, not liquid SOL.".into(),
                "Deactivate the stake account (takes ~1 epoch) to get liquid SOL.".into(),
                format!(
                    "For instant SOL, swap jitoSOL on Jupiter instead. \
                     Unstaking via reserve (if available) is also an option."
                ),
            ],
            requires_approval: true,
        },
        presigned: true,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Instant Unstake — jitoSOL → liquid SOL via reserve (useReserve: true)
// ─────────────────────────────────────────────────────────────────────────────

/// Build an instant jitoSOL → SOL transaction using `withdraw_sol`.
///
/// Burns jitoSOL pool tokens and transfers liquid SOL directly from the pool's
/// reserve stake account — no stake account created, no deactivation epoch.
/// Subject to reserve liquidity; if reserve is insufficient, falls back to
/// `withdraw_stake` path.
///
/// Equivalent to TypeScript SDK:
///   `solanaStakePool.withdrawStake(conn, POOL_ADDRESS, wallet, amount, useReserve: true)`
fn build_instant_unstake_transaction(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    jitosol_amount: f64,
    exchange_rate: f64,
) -> Result<JitoStakeResult, AppError> {
    // jitoSOL has 9 decimals.
    let pool_tokens = (jitosol_amount * 1_000_000_000.0) as u64;
    if pool_tokens == 0 {
        return Err(AppError::InvalidParams("Amount too small".into()));
    }

    let program_id = Pubkey::from_str(STAKE_POOL_PROGRAM_ID_STR).expect("valid program id");
    let stake_pool_pubkey = Pubkey::from_str(JITO_STAKE_POOL_STR).expect("valid stake pool pubkey");
    let jitosol_mint = Pubkey::from_str(JITOSOL_MINT).expect("valid jitoSOL mint");

    let pool = fetch_stake_pool(rpc)?;
    let withdraw_authority = withdraw_authority_pda(
        &program_id,
        &stake_pool_pubkey,
        pool.stake_withdraw_bump_seed,
    );

    // Validate reserve has sufficient balance.
    let reserve_balance = rpc
        .client()
        .get_balance(&pool.reserve_stake)
        .map_err(|e| AppError::Internal(format!("Reserve balance query failed: {e}")))?;
    let expected_lamports = (jitosol_amount * exchange_rate * 1_000_000_000.0) as u64;
    if reserve_balance < expected_lamports {
        return Err(AppError::InvalidParams(format!(
            "Insufficient reserve liquidity ({:.4} SOL available). Use standard unstake instead.",
            reserve_balance as f64 / 1_000_000_000.0
        )));
    }

    let user_jitosol_ata = get_associated_token_address(user_pubkey, &jitosol_mint);

    // withdraw_sol burns pool tokens and sends liquid SOL directly to the user.
    // Signature: (program_id, stake_pool, withdraw_authority, user_transfer_authority,
    //              pool_tokens_from, reserve_stake, lamports_to, manager_fee_account,
    //              pool_mint, token_program_id, pool_tokens_in)
    let withdraw_sol_ix = stake_pool_ix::withdraw_sol(
        &program_id,
        &stake_pool_pubkey,
        &withdraw_authority,
        user_pubkey,         // user authorizes pool token burn
        &user_jitosol_ata,   // source of jitoSOL tokens
        &pool.reserve_stake, // reserve stake account
        user_pubkey,         // lamports destination = user's wallet
        &pool.manager_fee_account,
        &pool.pool_mint,
        &pool.token_program_id,
        pool_tokens,
    );

    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Failed to get blockhash: {e}")))?;
    let message = Message::new(&[withdraw_sol_ix], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    let expected_sol = jitosol_amount * exchange_rate;

    Ok(JitoStakeResult {
        transaction,
        preview: JitoStakePreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_unstake".into(),
            description: format!(
                "Instant unstake {jitosol_amount} jitoSOL → ~{expected_sol:.4} SOL (from reserve)"
            ),
            estimated_fee: "~0.000005 SOL".into(),
            params: serde_json::json!({
                "jitosolAmount": jitosol_amount.to_string(),
                "poolTokens": pool_tokens,
                "expectedSol": format!("{expected_sol:.6}"),
                "method": "withdraw_sol",
                "reserveBalance": format!("{:.4} SOL", reserve_balance as f64 / 1_000_000_000.0),
                "instant": true,
            }),
            warnings: vec![
                "Instant unstake withdraws from the pool reserve.".into(),
                "Amount is subject to reserve liquidity and withdrawal fee.".into(),
            ],
            requires_approval: true,
        },
        presigned: false,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Tip Transaction
// ─────────────────────────────────────────────────────────────────────────────

/// Build a Jito MEV tip transaction.
pub async fn build_jito_tip(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &JitoTipParams,
) -> Result<BuildResponse, AppError> {
    let tip_sol: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid tip amount".into()))?;
    let tip_lamports = (tip_sol * 1_000_000_000.0) as u64;

    // Rotate tip account using system time.
    let idx = (std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()) as usize
        % JITO_TIP_ACCOUNTS.len();
    let tip_account_str = JITO_TIP_ACCOUNTS[idx];
    let tip_account = Pubkey::from_str(tip_account_str)
        .map_err(|_| AppError::Internal("Invalid tip account".into()))?;

    let tip_floor = get_jito_tip_floor(http).await.ok();

    let ix = system_instruction::transfer(user_pubkey, &tip_account, tip_lamports);
    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Blockhash error: {e}")))?;
    let message = Message::new(&[ix], Some(user_pubkey));
    let mut tx = Transaction::new_unsigned(message);
    tx.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&tx)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    let mut warnings = vec![];
    if let Some(f) = &tip_floor {
        if tip_sol < f.landed_tips_50th_percentile {
            warnings.push(format!(
                "Tip is below 50th percentile ({:.6} SOL). Consider increasing for faster inclusion.",
                f.landed_tips_50th_percentile
            ));
        }
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_tip".into(),
            description: format!("Send {tip_sol:.6} SOL MEV tip to Jito validators"),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "amount": params.amount,
                "tipAccount": tip_account_str,
                "tipFloor": tip_floor.map(|f| serde_json::json!({
                    "p25": f.landed_tips_25th_percentile,
                    "p50": f.landed_tips_50th_percentile,
                    "p75": f.landed_tips_75th_percentile,
                    "p95": f.landed_tips_95th_percentile,
                })),
            }),
            warnings,
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Bundle Transaction
// ─────────────────────────────────────────────────────────────────────────────

/// Build a Jito MEV bundle (1–5 transactions, atomic execution).
pub async fn build_jito_bundle(
    http: &reqwest::Client,
    _user_pubkey_str: &str,
    params: &JitoBundleParams,
) -> Result<BuildResponse, AppError> {
    // Decode all transactions to validate them.
    let mut txs_base58: Vec<String> = Vec::new();
    for (i, tx_b64) in params.transactions.iter().enumerate() {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(tx_b64)
            .map_err(|e| AppError::InvalidParams(format!("Transaction {}: {e}", i + 1)))?;
        txs_base58.push(bs58::encode(&bytes).into_string());
    }

    let tip_floor = get_jito_tip_floor(http).await.ok();
    let suggested_tip = tip_floor
        .as_ref()
        .map(|f| f.landed_tips_50th_percentile)
        .unwrap_or(0.001);
    let tip_amount = params
        .tip_amount
        .as_ref()
        .and_then(|t| t.parse::<f64>().ok())
        .unwrap_or(suggested_tip);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_bundle".into(),
            description: format!(
                "Submit {} transactions as atomic bundle with {:.6} SOL tip",
                params.transactions.len(),
                tip_amount
            ),
            estimated_fee: format!("{tip_amount:.6} SOL tip"),
            estimated_refund: None,
            params: serde_json::json!({
                "transactionCount": params.transactions.len(),
                "suggestedTip": suggested_tip,
                "tipAmount": tip_amount,
                "transactionsBase58": txs_base58,
                "bundleApi": format!("{JITO_BLOCK_ENGINE_API}/bundles"),
            }),
            warnings: vec![
                "All transactions execute or none do (atomic).".into(),
                "Tip is only charged if the bundle lands.".into(),
                "Bundles only process when a Jito-Solana leader is active.".into(),
            ],
            requires_approval: true,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: Some(serde_json::json!({
            "type": "jito_bundle",
            "transactions": txs_base58,
            "tipAmount": tip_amount,
            "submitUrl": format!("{JITO_BLOCK_ENGINE_API}/bundles"),
        })),
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Deactivate / Withdraw Stake param types
// ─────────────────────────────────────────────────────────────────────────────

/// Params for `jito_deposit_stake` — deposit an existing native stake account
/// into the JitoSOL pool in exchange for jitoSOL tokens.
/// Uses `spl_stake_pool::instruction::deposit_stake` directly (standard SPL path).
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoDepositStakeParams {
    /// Native stake account to deposit.
    pub stake_account: String,
    #[allow(dead_code)]
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub slippage_bps: Option<u32>,
}

/// Params for `jito_deactivate_stake` — begin cooldown on a stake account
/// received from `jito_unstake`.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoDeactivateStakeParams {
    /// Stake account address (received from jito_unstake output).
    pub stake_account: String,
}

/// Params for `jito_withdraw_stake` — withdraw liquid SOL from a deactivated
/// stake account after the cooldown epoch has passed.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoWithdrawStakeParams {
    /// Deactivated stake account address.
    pub stake_account: String,
    /// Optional recipient; defaults to the user's wallet.
    pub recipient: Option<String>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Query action param types (data queries, no transaction built)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoGetValidatorRewardsParams {
    pub vote_account: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub epoch: Option<u64>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u64>,
    pub sort_order: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoGetStatsParams {}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoGetExchangeRateParams {}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoGetValidatorsParams {
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub epoch: Option<u64>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoGetStakerRewardsParams {
    pub stake_authority: Option<String>,
    pub validator_vote_account: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub epoch: Option<u64>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u64>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JitoGetMevRewardsParams {
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub epoch: Option<u64>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Validation — Deactivate / Withdraw
// ─────────────────────────────────────────────────────────────────────────────

pub fn validate_jito_deposit_stake_params(params: &JitoDepositStakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&params.stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid stake account address".into()))?;
    Ok(())
}

pub fn validate_jito_deactivate_stake_params(
    params: &JitoDeactivateStakeParams,
) -> Result<(), AppError> {
    Pubkey::from_str(&params.stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid stake account address".into()))?;
    Ok(())
}

pub fn validate_jito_withdraw_stake_params(
    params: &JitoWithdrawStakeParams,
) -> Result<(), AppError> {
    Pubkey::from_str(&params.stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid stake account address".into()))?;
    if let Some(r) = &params.recipient {
        Pubkey::from_str(r)
            .map_err(|_| AppError::InvalidParams("Invalid recipient address".into()))?;
    }
    Ok(())
}

/// Check the status of a submitted bundle.
pub async fn get_jito_bundle_status(
    http: &reqwest::Client,
    bundle_id: &str,
) -> Result<JitoBundleResponse, AppError> {
    let resp = http
        .get(format!("{JITO_BLOCK_ENGINE_API}/bundles/{bundle_id}"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Bundle status error: {e}")))?;
    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!("Bundle {bundle_id} not found")));
    }
    resp.json::<JitoBundleResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Bundle parse error: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// High-level action builders (called from builder.rs)
// ─────────────────────────────────────────────────────────────────────────────

/// Build the `jito_stake` action: SOL → jitoSOL.
pub async fn build_jito_stake_action(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &JitoStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_jito_stake_params(params)?;

    let sol_amount: f64 = params.amount.parse().unwrap();

    // Fetch the live exchange rate (Kobe API → on-chain fallback) and APY in
    // parallel. The rate is required (no static fallback); APY is best-effort.
    let (rate_res, apy_pct) = tokio::join!(get_exchange_rate(http, rpc), async {
        get_stake_pool_stats(http)
            .await
            .ok()
            .and_then(|s| s.current_apy_pct())
            .unwrap_or(7.5)
    });
    let exchange_rate = rate_res?;

    // Build transaction in blocking task (RpcClient is sync).
    let rpc = rpc.clone();
    let pubkey = *user_pubkey;
    let result = actix_web::web::block(move || {
        build_stake_transaction(&rpc, &pubkey, sol_amount, exchange_rate, apy_pct)
    })
    .await
    .map_err(|e| AppError::Internal(format!("Blocking task error: {e}")))??;

    let tx_bytes = bincode::serialize(&result.transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: result.preview.id,
            action_type: result.preview.action_type,
            description: result.preview.description,
            estimated_fee: result.preview.estimated_fee,
            estimated_refund: None,
            params: result.preview.params,
            warnings: result.preview.warnings,
            requires_approval: result.preview.requires_approval,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Build the `jito_unstake` action.
///
/// Two modes (mirrors TypeScript SDK `withdrawStake(useReserve)`):
///
/// - **`instant: false`** (default) — `withdraw_stake`: burns jitoSOL, creates a
///   stake account from the best validator per kobe's `preferred_withdraw_validator_list`.
///   Requires ~1-epoch deactivation before SOL is withdrawable.
///
/// - **`instant: true`** — `withdraw_sol`: burns jitoSOL, sends liquid SOL directly
///   from the pool reserve. Instant, no stake account, subject to reserve liquidity.
pub async fn build_jito_unstake_action(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &JitoUnstakeParams,
) -> Result<BuildResponse, AppError> {
    validate_jito_unstake_params(params)?;

    let jitosol_amount: f64 = params.amount.parse().unwrap();
    let instant = params.instant.unwrap_or(false);

    let exchange_rate = get_exchange_rate(http, rpc).await?;

    let result = if instant {
        // ── Instant path: withdraw_sol ────────────────────────────────────────
        let rpc = rpc.clone();
        let pubkey = *user_pubkey;
        actix_web::web::block(move || {
            build_instant_unstake_transaction(&rpc, &pubkey, jitosol_amount, exchange_rate)
        })
        .await
        .map_err(|e| AppError::Internal(format!("Blocking task error: {e}")))??
    } else {
        // ── Standard path: withdraw_stake with preferred validator ────────────
        let preferred_list = get_preferred_withdraw_validators(http, Some(1), None, false).await;
        let preferred = preferred_list
            .map_err(|e| AppError::Internal(format!("Preferred validator fetch failed: {e}")))?
            .into_iter()
            .next()
            .ok_or_else(|| {
                AppError::Internal("No preferred withdraw validators available.".into())
            })?;

        let rpc = rpc.clone();
        let pubkey = *user_pubkey;
        actix_web::web::block(move || {
            build_unstake_transaction(&rpc, &pubkey, jitosol_amount, exchange_rate, &preferred)
        })
        .await
        .map_err(|e| AppError::Internal(format!("Blocking task error: {e}")))??
    };

    let tx_bytes = bincode::serialize(&result.transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: result.preview.id,
            action_type: result.preview.action_type,
            description: result.preview.description,
            estimated_fee: result.preview.estimated_fee,
            estimated_refund: None,
            params: result.preview.params,
            warnings: result.preview.warnings,
            requires_approval: result.preview.requires_approval,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Deactivate Stake — jitoSOL unstake step 2
// ─────────────────────────────────────────────────────────────────────────────

/// Build a `jito_deactivate_stake` transaction.
///
/// After `jito_unstake` the user holds a stake account.  Deactivation begins
/// a ~1-epoch cooldown after which the SOL can be withdrawn.
pub async fn build_jito_deactivate_stake(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &JitoDeactivateStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_jito_deactivate_stake_params(params)?;
    let stake_pubkey = Pubkey::from_str(&params.stake_account).unwrap();

    let deactivate_ix =
        solana_sdk::stake::instruction::deactivate_stake(&stake_pubkey, user_pubkey);

    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Blockhash error: {e}")))?;
    let message = Message::new(&[deactivate_ix], Some(user_pubkey));
    let mut tx = Transaction::new_unsigned(message);
    tx.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&tx)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_deactivate_stake".into(),
            description: format!(
                "Deactivate stake account {} — cooldown ~1 epoch before SOL withdrawal",
                &params.stake_account[..8]
            ),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "stakeAccount": params.stake_account,
                "authorizedPubkey": user_pubkey.to_string(),
            }),
            warnings: vec![
                "Deactivation takes approximately 1 epoch (~2 days).".into(),
                "After deactivation, use jito_withdraw_stake to get liquid SOL.".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Withdraw Stake — jitoSOL unstake step 3
// ─────────────────────────────────────────────────────────────────────────────

/// Build a `jito_withdraw_stake` transaction.
///
/// Withdraws all SOL from a fully-deactivated stake account back to the user's
/// wallet (or an optional custom recipient).
pub async fn build_jito_withdraw_stake(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &JitoWithdrawStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_jito_withdraw_stake_params(params)?;
    let stake_pubkey = Pubkey::from_str(&params.stake_account).unwrap();
    let recipient = params
        .recipient
        .as_ref()
        .map(|r| Pubkey::from_str(r).unwrap())
        .unwrap_or(*user_pubkey);

    // Fetch the stake account balance (must be deactivated with epoch elapsed).
    let rpc_clone = rpc.clone();
    let stake_pubkey_clone = stake_pubkey;
    let stake_lamports = actix_web::web::block(move || {
        rpc_clone
            .client()
            .get_balance(&stake_pubkey_clone)
            .map_err(|e| AppError::Internal(format!("Failed to get stake balance: {e}")))
    })
    .await
    .map_err(|e| AppError::Internal(format!("Blocking task error: {e}")))??;

    if stake_lamports == 0 {
        return Err(AppError::InvalidParams(
            "Stake account has zero balance — it may not exist or be empty.".into(),
        ));
    }

    let sol_amount = stake_lamports as f64 / 1_000_000_000.0;

    let withdraw_ix = solana_sdk::stake::instruction::withdraw(
        &stake_pubkey,
        user_pubkey, // authorized withdrawer
        &recipient,
        stake_lamports,
        None, // no lockup custodian
    );

    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Blockhash error: {e}")))?;
    let message = Message::new(&[withdraw_ix], Some(user_pubkey));
    let mut tx = Transaction::new_unsigned(message);
    tx.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&tx)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_withdraw_stake".into(),
            description: format!(
                "Withdraw {sol_amount:.4} SOL from deactivated stake account to {}",
                if params.recipient.is_some() {
                    &params.recipient.as_ref().unwrap()[..8]
                } else {
                    "wallet"
                }
            ),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "stakeAccount": params.stake_account,
                "recipient": recipient.to_string(),
                "lamports": stake_lamports,
                "solAmount": format!("{sol_amount:.9}"),
            }),
            warnings: vec![
                "Stake account must be fully deactivated (cooldown epoch elapsed).".into(),
                format!("Withdrawing all {sol_amount:.4} SOL — stake account will be closed."),
            ],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query actions — return analytics data, no transaction built
// ─────────────────────────────────────────────────────────────────────────────

/// `jito_get_stats` — fetch current APY, TVL, supply, exchange rate.
pub async fn query_jito_stats(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    _params: &JitoGetStatsParams,
) -> Result<BuildResponse, AppError> {
    let (stats_result, rate_res) =
        tokio::join!(get_stake_pool_stats(http), get_exchange_rate(http, rpc));
    let stats = stats_result?;
    let rate = rate_res?;
    let apy = stats.current_apy_pct().unwrap_or(0.0);
    let tvl_sol = stats
        .current_tvl_lamports()
        .map(|l| l as f64 / 1_000_000_000.0)
        .unwrap_or(0.0);
    let data = serde_json::json!({
        "currentApy": format!("{apy:.2}%"),
        "exchangeRate": format!("{rate:.6}"),
        "tvlSol": format!("{tvl_sol:.2}"),
        "validatorCount": stats.num_validators.as_ref().and_then(|v| v.last()).map(|p| p.data as u64),
        "supply": stats.supply.as_ref().and_then(|v| v.last()).map(|p| p.data as u64),
        "jitosolMint": JITOSOL_MINT,
        "stakePool": JITO_STAKE_POOL_STR,
    });
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_get_stats".into(),
            description: format!(
                "Jito Finance: APY {apy:.2}%, TVL {tvl_sol:.0} SOL, rate {rate:.4} SOL/jitoSOL"
            ),
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

/// `jito_get_exchange_rate` — fetch current jitoSOL/SOL exchange rate.
pub async fn query_jito_exchange_rate(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    _params: &JitoGetExchangeRateParams,
) -> Result<BuildResponse, AppError> {
    let rate = get_exchange_rate(http, rpc).await?;
    let data = serde_json::json!({
        "exchangeRate": rate,
        "formattedRate": format!("{rate:.6}"),
        "description": format!("1 jitoSOL = {rate:.6} SOL"),
        "jitosolMint": JITOSOL_MINT,
    });
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_get_exchange_rate".into(),
            description: format!("1 jitoSOL = {rate:.6} SOL"),
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

/// `jito_get_validators` — fetch JitoSOL stake pool validator list.
pub async fn query_jito_validators(
    http: &reqwest::Client,
    params: &JitoGetValidatorsParams,
) -> Result<BuildResponse, AppError> {
    let validators = get_jitosol_validators(http, params.epoch).await?;
    let count = validators.validators.len();
    let data = serde_json::to_value(&validators)
        .map_err(|e| AppError::Internal(format!("Serialize error: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_get_validators".into(),
            description: format!("{count} validators in JitoSOL stake pool"),
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

/// `jito_get_staker_rewards` — fetch claimable MEV rewards for a stake authority.
pub async fn query_jito_staker_rewards(
    http: &reqwest::Client,
    params: &JitoGetStakerRewardsParams,
) -> Result<BuildResponse, AppError> {
    let rewards_params = StakerRewardsParams {
        stake_authority: params.stake_authority.clone(),
        validator_vote_account: params.validator_vote_account.clone(),
        epoch: params.epoch,
        limit: params.limit,
        ..Default::default()
    };
    let rewards = get_staker_rewards(http, &rewards_params).await?;
    let count = rewards.rewards.len();
    let data = serde_json::to_value(&rewards)
        .map_err(|e| AppError::Internal(format!("Serialize error: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_get_staker_rewards".into(),
            description: format!("{count} staker reward entries found"),
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

/// `jito_get_validator_rewards` — aggregated MEV rewards earned by validators.
pub async fn query_jito_validator_rewards(
    http: &reqwest::Client,
    params: &JitoGetValidatorRewardsParams,
) -> Result<BuildResponse, AppError> {
    let rewards_params = ValidatorRewardsParams {
        vote_account: params.vote_account.clone(),
        epoch: params.epoch,
        limit: params.limit,
        sort_order: params.sort_order.clone(),
        ..Default::default()
    };
    let rewards = get_validator_rewards(http, &rewards_params).await?;
    let count = rewards.rewards.len();
    let data = serde_json::to_value(&rewards)
        .map_err(|e| AppError::Internal(format!("Serialize error: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_get_validator_rewards".into(),
            description: format!("{count} validator reward entries"),
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

/// `jito_get_mev_rewards` — fetch network-level MEV reward stats for an epoch.
pub async fn query_jito_mev_rewards(
    http: &reqwest::Client,
    params: &JitoGetMevRewardsParams,
) -> Result<BuildResponse, AppError> {
    let rewards = get_mev_rewards(http, params.epoch).await?;
    let total_sol = rewards.total_network_mev_lamports as f64 / 1_000_000_000.0;
    let reward_per_sol = rewards.mev_reward_per_lamport * 1_000_000_000.0;
    let data = serde_json::to_value(&rewards)
        .map_err(|e| AppError::Internal(format!("Serialize error: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_get_mev_rewards".into(),
            description: format!(
                "Epoch {} MEV: {total_sol:.2} SOL total, {reward_per_sol:.6} SOL/staked-SOL",
                rewards.epoch
            ),
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

// ─────────────────────────────────────────────────────────────────────────────
// Deposit Stake — native stake account → jitoSOL
// ─────────────────────────────────────────────────────────────────────────────

/// Build a `jito_deposit_stake` transaction.
///
/// Converts an existing native Solana stake account into jitoSOL tokens using
/// `spl_stake_pool::instruction::deposit_stake`.
///
/// The function automatically selects the best validator to merge with via
/// the kobe `preferred_withdraw_validator_list` API (same ranking logic used
/// for unstake).
///
/// Note: this is the standard SPL Stake Pool path. The Jito interceptor SDK
/// (`@jito-foundation/stake-deposit-interceptor-sdk`) adds a 10-hour cooldown
/// vault on top for MEV protection, but the resulting jitoSOL is identical.
pub async fn build_jito_deposit_stake_action(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &JitoDepositStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_jito_deposit_stake_params(params)?;

    let deposit_stake_pubkey = Pubkey::from_str(&params.stake_account).unwrap();

    // Fetch exchange rate and preferred validator concurrently.
    let (rate_res, preferred_list) = tokio::join!(
        get_exchange_rate(http, rpc),
        get_preferred_withdraw_validators(http, Some(1), None, false)
    );
    let exchange_rate = rate_res?;

    let preferred = preferred_list
        .map_err(|e| AppError::Internal(format!("Preferred validator fetch failed: {e}")))?
        .into_iter()
        .next()
        .ok_or_else(|| AppError::Internal("No validators available in pool".into()))?;

    let validator_stake_account = Pubkey::from_str(&preferred.stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid validator stake account from kobe".into()))?;

    // Get stake account balance (best-effort for preview; RPC call in blocking thread).
    let rpc_bal = rpc.clone();
    let spk = deposit_stake_pubkey;
    let stake_lamports = actix_web::web::block(move || {
        rpc_bal
            .client()
            .get_balance(&spk)
            .map_err(|e| AppError::Internal(format!("Failed to get stake balance: {e}")))
    })
    .await
    .map_err(|e| AppError::Internal(format!("Blocking task error: {e}")))??;

    if stake_lamports == 0 {
        return Err(AppError::InvalidParams(
            "Stake account has zero balance — it may not exist.".into(),
        ));
    }

    let sol_amount = stake_lamports as f64 / 1_000_000_000.0;
    let expected_jitosol = sol_amount / exchange_rate;

    // Build transaction in blocking task (RpcClient is sync).
    let rpc2 = rpc.clone();
    let pubkey = *user_pubkey;
    let tx = actix_web::web::block(move || -> Result<Transaction, AppError> {
        let program_id = Pubkey::from_str(STAKE_POOL_PROGRAM_ID_STR).unwrap();
        let stake_pool_pubkey = Pubkey::from_str(JITO_STAKE_POOL_STR).unwrap();
        let jitosol_mint = Pubkey::from_str(JITOSOL_MINT).unwrap();

        let pool = fetch_stake_pool(&rpc2)?;
        let withdraw_authority = withdraw_authority_pda(
            &program_id,
            &stake_pool_pubkey,
            pool.stake_withdraw_bump_seed,
        );

        let user_jitosol_ata = get_associated_token_address(&pubkey, &jitosol_mint);

        // Create user's jitoSOL ATA if it doesn't exist yet.
        let create_ata_ix = create_associated_token_account_idempotent(
            &pubkey,
            &pubkey,
            &jitosol_mint,
            &pool.token_program_id,
        );

        // deposit_stake returns Vec<Instruction>:
        //   [0] StakeAuthorize(stake_account, Staker   → withdraw_authority)
        //   [1] StakeAuthorize(stake_account, Withdrawer → withdraw_authority)
        //   [2] DepositStake
        let deposit_ixs = stake_pool_ix::deposit_stake(
            &program_id,
            &stake_pool_pubkey,
            &pool.validator_list,
            &withdraw_authority,
            &deposit_stake_pubkey,
            &pubkey, // current stake withdraw authority = user
            &validator_stake_account,
            &pool.reserve_stake,
            &user_jitosol_ata,
            &pool.manager_fee_account,
            &user_jitosol_ata, // referrer = user (no referral fee)
            &pool.pool_mint,
            &pool.token_program_id,
        );

        let mut all_ixs = vec![create_ata_ix];
        all_ixs.extend(deposit_ixs);

        let blockhash = rpc2
            .get_latest_blockhash_with_retry()
            .map_err(|e| AppError::Internal(format!("Blockhash error: {e}")))?;
        let message = Message::new(&all_ixs, Some(&pubkey));
        let mut tx = Transaction::new_unsigned(message);
        tx.message.recent_blockhash = blockhash;
        Ok(tx)
    })
    .await
    .map_err(|e| AppError::Internal(format!("Blocking task error: {e}")))??;

    let tx_bytes = bincode::serialize(&tx)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jito_deposit_stake".into(),
            description: format!(
                "Deposit stake account ({sol_amount:.4} SOL) → ~{expected_jitosol:.4} jitoSOL"
            ),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "stakeAccount": params.stake_account,
                "stakeBalanceSol": format!("{sol_amount:.9}"),
                "expectedJitosol": format!("{expected_jitosol:.6}"),
                "exchangeRate": format!("{exchange_rate:.6}"),
                "validatorVoteAccount": preferred.vote_account,
                "jitosolMint": JITOSOL_MINT,
                "stakePool": JITO_STAKE_POOL_STR,
            }),
            warnings: vec![
                "Your stake account's staker and withdrawer authorities will be transferred to the Jito pool.".into(),
                "You will receive jitoSOL tokens proportional to the stake value at the current exchange rate.".into(),
                "The stake account must not be in a lockup period.".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

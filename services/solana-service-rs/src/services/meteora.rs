//! Meteora DLMM Service — complete integration based on official SDK v0.11.0
//!
//! Dynamic Liquidity Market Maker with discrete bin-based concentrated liquidity.
//! Implements all 12 action types and full GET service coverage.
//!
//! Transaction building uses direct Anchor instruction encoding against
//! the DLMM program (LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo).
//! All transactions use real on-chain blockhashes fetched via RPC.
//!
//! PDA seeds (from SDK v0.11.0 source):
//!   lb_pair:          [token_x_mint, token_y_mint, bin_step_u16_le]
//!   reserve:          [token_mint, lb_pair]  ← note: mint FIRST
//!   oracle:           [b"oracle", lb_pair]
//!   bin_array:        [b"bin_array", lb_pair, index_i64_le]
//!   bitmap_ext:       [b"bitmap", lb_pair]
//!   position_pda:     [b"position", lb_pair, owner, lower_bin_id_i32_le, width_i32_le]
//!   preset_parameter: [b"preset_parameter", bin_step_u16_le]
//!   reward_vault:     [lb_pair, reward_index_u64_le]
//!   farm_user:        [b"user_staking", farm, user]
//!   farm_reward_vault:[b"reward_vault", farm]

use base64::Engine;
use borsh::BorshSerialize;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::str::FromStr;
use uuid::Uuid;

use solana_rpc_client::nonblocking::rpc_client::RpcClient as AsyncRpc;
use solana_sdk::{
    commitment_config::CommitmentConfig,
    instruction::{AccountMeta, Instruction},
    message::{v0, VersionedMessage},
    pubkey::Pubkey,
    signature::Signature,
    system_program,
    sysvar,
    transaction::VersionedTransaction,
};
use spl_associated_token_account::{
    get_associated_token_address,
    instruction::create_associated_token_account_idempotent,
};

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

pub const METEORA_DLMM_PROGRAM_ID: &str = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo";
pub const METEORA_FARM_PROGRAM_ID: &str = "FARMnXaEpBcjBEQiFdYGCJXCGqQHcC9PmF2Cb5U6MHGU";
pub const METEORA_DAMM_V1_PROGRAM_ID: &str = "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EkAW7vAr";
pub const METEORA_VAULT_PROGRAM_ID: &str = "24Uqj9JCLxUeoC3hGfh5W3s9FM9uCHDS2SG3LYwBpyTi";
pub const METEORA_S2E_PROGRAM_ID: &str = "FEESngU3neckdwib9X3KWqdL7Mjmqk9XNp3uh5JbP4KP";

const TOKEN_PROGRAM_ID: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const MEMO_PROGRAM_ID: &str = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";

/// DLMM REST API (pool data, positions, analytics).
///
/// Meteora migrated the DLMM data API in early 2026 from `dlmm-api.meteora.ag`
/// (now 404 on every path) to `dlmm.datapi.meteora.ag`. The new API also
/// re-pathed several endpoints — `/pair/{x}` → `/pools/{x}`, `/position/user/{w}`
/// → `/portfolio/open?user={w}`, `/lb_pair/.../active_bin` removed (callers
/// derive the bin from `current_price` + `pool_config.bin_step` in the pool
/// detail). The old `fetch_pair` / `fetch_pos` helpers used by tx construction
/// still hit the deprecated paths; those flows need a follow-up rewrite.
const DLMM_API: &str = "https://dlmm.datapi.meteora.ag";
/// Alias kept for backwards compatibility with existing references; the stats
/// endpoints live on the same host as everything else now.
const DLMM_STATS_API: &str = "https://dlmm.datapi.meteora.ag";
const DAMM_V2_API: &str = "https://damm-v2.datapi.meteora.ag";
const DAMM_V1_API: &str = "https://amm.meteora.ag";
const STAKE2EARN_API: &str = "https://stake2earn.meteora.ag";
const VAULT_API: &str = "https://vault-api.meteora.ag";

/// Maximum bins per bin-array account (authoritative SDK constant).
const MAX_BIN_PER_ARRAY: i32 = 70;

// ─────────────────────────────────────────────────────────────────────────────
// Internal: DLMM API Response Types
// ─────────────────────────────────────────────────────────────────────────────

/// Nested token block returned by the new datapi DLMM API
/// (`dlmm.datapi.meteora.ag/pools/{x}`). The legacy `dlmm-api.meteora.ag`
/// flattened these into `mint_x` / `token_x_decimals` etc.; the datapi
/// host wraps them in a `token_x` / `token_y` object. We accept both via
/// custom field aliases / a nested `DlmmTokenInfo` struct.
#[derive(Debug, Deserialize)]
struct DlmmTokenInfo {
    address: String,
    #[serde(default)]
    decimals: u8,
}

#[derive(Debug, Deserialize)]
struct DlmmPoolConfig {
    #[serde(default)]
    bin_step: u32,
}

#[derive(Debug, Deserialize)]
struct DlmmPairInfo {
    /// Optional flat-shaped fields (legacy `dlmm-api.meteora.ag` host).
    /// When the response comes from the new datapi host these are None
    /// and we fall back to the nested `token_x` / `token_y` blocks below.
    #[serde(default)]
    mint_x: Option<String>,
    #[serde(default)]
    mint_y: Option<String>,
    #[serde(default)]
    active_id: Option<i32>,
    #[serde(default, rename = "token_x_decimals")]
    legacy_token_x_decimals: Option<u8>,
    #[serde(default, rename = "token_y_decimals")]
    legacy_token_y_decimals: Option<u8>,
    #[serde(default, rename = "bin_step")]
    legacy_bin_step: Option<u32>,

    /// New datapi shape — nested token info + pool_config.
    #[serde(default)]
    token_x: Option<DlmmTokenInfo>,
    #[serde(default)]
    token_y: Option<DlmmTokenInfo>,
    #[serde(default)]
    pool_config: Option<DlmmPoolConfig>,
    /// Spot price (Y per X) — used to derive active_id when the API
    /// doesn't expose it directly.
    #[serde(default)]
    current_price: Option<f64>,

    #[serde(default)]
    #[allow(dead_code)]
    name: Option<String>,
    /// Reward mints — old API used a `reward_mints: []` array; new datapi
    /// returns separate `reward_mint_x` / `reward_mint_y` strings. Accept
    /// both, normalise via the `reward_mints()` accessor.
    #[serde(default)]
    legacy_reward_mints: Option<Vec<String>>,
    #[serde(default)]
    reward_mint_x: Option<String>,
    #[serde(default)]
    reward_mint_y: Option<String>,
}

impl DlmmPairInfo {
    /// X-token mint, regardless of API shape. Returns "" when missing
    /// from both shapes — downstream `Pubkey::from_str` will surface a
    /// useful parse error in that (theoretically impossible) case.
    fn mint_x_str(&self) -> &str {
        self.mint_x.as_deref()
            .or_else(|| self.token_x.as_ref().map(|t| t.address.as_str()))
            .unwrap_or("")
    }
    fn mint_y_str(&self) -> &str {
        self.mint_y.as_deref()
            .or_else(|| self.token_y.as_ref().map(|t| t.address.as_str()))
            .unwrap_or("")
    }
    fn token_x_decimals(&self) -> u8 {
        self.legacy_token_x_decimals
            .or_else(|| self.token_x.as_ref().map(|t| t.decimals))
            .unwrap_or(0)
    }
    fn token_y_decimals(&self) -> u8 {
        self.legacy_token_y_decimals
            .or_else(|| self.token_y.as_ref().map(|t| t.decimals))
            .unwrap_or(0)
    }
    fn bin_step_resolved(&self) -> u32 {
        self.legacy_bin_step
            .or_else(|| self.pool_config.as_ref().map(|c| c.bin_step))
            .unwrap_or(0)
    }
    /// Active bin id. Prefer the explicit field; fall back to deriving it
    /// from current_price + bin_step (the new datapi response doesn't
    /// expose active_id but does give current_price).
    fn active_id_resolved(&self) -> Option<i32> {
        if self.active_id.is_some() { return self.active_id; }
        let bin_step = self.bin_step_resolved();
        let price = self.current_price?;
        if bin_step == 0 || price <= 0.0 { return None; }
        let factor = 1.0 + (bin_step as f64) / 10_000.0;
        let id = price.ln() / factor.ln();
        if !id.is_finite() { return None; }
        Some(id.round() as i32)
    }
    /// Combined reward mints list, regardless of API shape. Filters out
    /// the System-program zero address (placeholder for "no reward").
    fn reward_mints_resolved(&self) -> Vec<String> {
        if let Some(v) = &self.legacy_reward_mints {
            return v.clone();
        }
        const ZERO: &str = "11111111111111111111111111111111";
        let mut out = Vec::new();
        if let Some(m) = &self.reward_mint_x {
            if m != ZERO { out.push(m.clone()); }
        }
        if let Some(m) = &self.reward_mint_y {
            if m != ZERO { out.push(m.clone()); }
        }
        out
    }
}

#[derive(Debug, Deserialize)]
struct DlmmPositionData {
    /// Pool (lb_pair) address.
    #[serde(alias = "lbPair", alias = "lb_pair", alias = "pool")]
    lb_pair: String,
    #[serde(alias = "lowerBinId", alias = "lower_bin_id")]
    lower_bin_id: i32,
    #[serde(alias = "upperBinId", alias = "upper_bin_id")]
    upper_bin_id: i32,
}

// ─────────────────────────────────────────────────────────────────────────────
// Action Params
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraSwapParams {
    /// Input token mint (required).
    pub input_mint: String,
    /// Output token mint (required).
    pub output_mint: String,
    /// Human-readable amount of input token (e.g. "1.5").
    pub amount: String,
    /// Slippage in basis points (default: 50 = 0.5%). Max: 5000.
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    /// DLMM pool (lb_pair) address (required for direct DLMM routing).
    #[serde(default)]
    pub pool: Option<String>,
    /// Priority fee: "auto" | "low" | "medium" | "high" | exact lamports.
    #[serde(default)]
    #[allow(dead_code)]
    pub priority_fee: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraAddLiquidityParams {
    /// DLMM pool (lb_pair) address.
    pub pool: String,
    /// Token X amount (human-readable). May be "0" for single-sided.
    #[allow(dead_code)]
    pub amount_x: String,
    /// Token Y amount (human-readable). May be "0" for single-sided.
    #[allow(dead_code)]
    pub amount_y: String,
    /// Lower bin ID. Required unless min_price is provided.
    #[serde(default)]
    pub min_bin_id: Option<i32>,
    /// Upper bin ID. Required unless max_price is provided.
    #[serde(default)]
    pub max_bin_id: Option<i32>,
    /// Lower price bound in token Y per token X (alternative to min_bin_id).
    #[serde(default)]
    pub min_price: Option<f64>,
    /// Upper price bound (alternative to max_bin_id).
    #[serde(default)]
    pub max_price: Option<f64>,
    /// Max active bin drift tolerance in bins (default: 3).
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    /// Liquidity distribution: "uniform" (default) | "spot" (active-bin-heavy).
    #[serde(default)]
    pub strategy: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraRemoveLiquidityParams {
    /// Position address.
    pub position: String,
    /// Specific bin IDs to remove from. If omitted: removes from ALL position bins.
    #[serde(default)]
    pub bin_ids: Option<Vec<i32>>,
    /// Fraction of liquidity to remove per bin in basis points (default: 10000 = 100%).
    #[serde(default)]
    pub bps_to_remove: Option<u16>,
    /// Min output slippage in basis points (default: 100 = 1%).
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraCreatePoolParams {
    /// Token X mint address (canonical ordering: numerically smaller pubkey).
    pub token_x_mint: String,
    /// Token Y mint address.
    pub token_y_mint: String,
    /// Bin step in basis points (e.g. 25 = 0.25% price spacing per bin).
    pub bin_step: u32,
    /// Initial active price in token Y per token X.
    pub initial_price: f64,
    /// Optional: seed token X liquidity.
    #[serde(default)]
    #[allow(dead_code)]
    pub amount_x: Option<String>,
    /// Optional: seed token Y liquidity.
    #[serde(default)]
    #[allow(dead_code)]
    pub amount_y: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraOpenPositionParams {
    /// DLMM pool (lb_pair) address.
    pub pool: String,
    /// Token X deposit amount (human-readable).
    pub amount_x: String,
    /// Token Y deposit amount (human-readable).
    pub amount_y: String,
    /// Lower bound as bin ID. Provide bin IDs OR prices, not both.
    #[serde(default)]
    pub min_bin_id: Option<i32>,
    /// Upper bound as bin ID.
    #[serde(default)]
    pub max_bin_id: Option<i32>,
    /// Lower price bound in token Y per token X.
    #[serde(default)]
    pub min_price: Option<f64>,
    /// Upper price bound.
    #[serde(default)]
    pub max_price: Option<f64>,
    /// Liquidity distribution: "uniform" (default) | "spot".
    #[serde(default)]
    pub strategy: Option<String>,
    /// Max active bin drift in bins (default: 3).
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraClosePositionParams {
    /// Position address.
    pub position: String,
    /// Min output slippage in basis points (default: 100 = 1%).
    #[serde(default)]
    #[allow(dead_code)]
    pub slippage_bps: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraAddToPositionParams {
    /// Existing position address.
    pub position: String,
    /// Additional token X (human-readable).
    pub amount_x: String,
    /// Additional token Y (human-readable).
    pub amount_y: String,
    /// Max active bin drift (default: 3).
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    /// Distribution: "uniform" | "spot" (default: "uniform").
    #[serde(default)]
    pub strategy: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraClaimFeesParams {
    /// Position address.
    pub position: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraClaimRewardsParams {
    /// Position address.
    pub position: String,
    /// Specific reward index to claim (0 or 1). Omit = claim all.
    #[serde(default)]
    pub reward_index: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraStakeParams {
    /// Farm program address.
    pub farm: String,
    /// LP token amount to stake (human-readable).
    pub amount: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraUnstakeParams {
    /// Farm program address.
    pub farm: String,
    /// LP token amount to unstake (human-readable).
    pub amount: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraHarvestParams {
    /// Farm program address.
    pub farm: String,
}

// ─────────────────────────────────────────────────────────────────────────────
// Borsh Instruction Args (exact SDK IDL field ordering)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(BorshSerialize)]
struct SwapIxArgs {
    amount_in: u64,
    min_amount_out: u64,
}

/// Single bin liquidity distribution entry by weight (SDK IDL: BinLiquidityDistributionByWeight).
#[derive(BorshSerialize, Clone)]
struct BinLiqDistByWeight {
    bin_id: i32,
    weight: u16,
}

/// add_liquidity_by_weight args (SDK IDL: LiquidityParameterByWeight).
#[derive(BorshSerialize)]
struct LiqByWeightArgs {
    amount_x: u64,
    amount_y: u64,
    active_id: i32,
    max_active_bin_slippage: i32,
    /// Single distribution field — NOT separate x/y lists.
    bin_liquidity_dist: Vec<BinLiqDistByWeight>,
}

/// remove_liquidity args.
#[derive(BorshSerialize, Clone)]
struct BinLiqReduction {
    bin_id: i32,
    bps_to_remove: u16,
}

#[derive(BorshSerialize)]
struct RemoveLiqArgs {
    bin_liquidity_removal: Vec<BinLiqReduction>,
    amount_x_min: u64,
    amount_y_min: u64,
}

/// initialize_position_pda args.
#[derive(BorshSerialize)]
struct InitPosPdaArgs {
    lower_bin_id: i32,
    width: i32,
}

/// initialize_lb_pair args (no base_fee_bps — use preset_parameter for fee config).
#[derive(BorshSerialize)]
struct InitLbPairArgs {
    active_id: i32,
    bin_step: u16,
}

/// claim_fee2 args.
#[derive(BorshSerialize)]
struct ClaimFee2Args {
    remaining_accounts_info: RemainingAccountsInfo,
}

/// claim_reward2 args.
#[derive(BorshSerialize)]
struct ClaimReward2Args {
    reward_index: u64,
    remaining_accounts_info: RemainingAccountsInfo,
}

/// Token-2022 hook account info passed to v2 instructions.
/// Empty slices = no extra Token-2022 hook accounts required.
#[derive(BorshSerialize)]
struct RemainingAccountsInfo {
    slices: Vec<RemainingAccountsSlice>,
}

#[derive(BorshSerialize)]
struct RemainingAccountsSlice {
    accounts_type: u8,
    length: u8,
}

/// Farm deposit args.
#[derive(BorshSerialize)]
struct FarmDepositArgs {
    amount: u64,
}

/// Farm withdraw args.
#[derive(BorshSerialize)]
struct FarmWithdrawArgs {
    amount: u64,
}

// ─────────────────────────────────────────────────────────────────────────────
// Validators
// ─────────────────────────────────────────────────────────────────────────────

pub fn validate_meteora_swap_params(p: &MeteoraSwapParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.input_mint)
        .map_err(|_| AppError::InvalidParams(format!("inputMint '{}' is not a valid address", p.input_mint)))?;
    Pubkey::from_str(&p.output_mint)
        .map_err(|_| AppError::InvalidParams(format!("outputMint '{}' is not a valid address", p.output_mint)))?;
    if p.input_mint == p.output_mint {
        return Err(AppError::InvalidParams("inputMint and outputMint must differ".into()));
    }
    let amount: f64 = p.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    if let Some(s) = p.slippage_bps {
        if s > 5000 {
            return Err(AppError::InvalidParams("slippageBps must be ≤ 5000 (50%)".into()));
        }
    }
    if let Some(ref pool) = p.pool {
        Pubkey::from_str(pool)
            .map_err(|_| AppError::InvalidParams(format!("pool '{}' is not a valid address", pool)))?;
    }
    Ok(())
}

pub fn validate_meteora_add_liquidity_params(p: &MeteoraAddLiquidityParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.pool)
        .map_err(|_| AppError::InvalidParams(format!("pool '{}' is not a valid address", p.pool)))?;
    let ax: f64 = p.amount_x.parse()
        .map_err(|_| AppError::InvalidParams("amountX must be a number".into()))?;
    let ay: f64 = p.amount_y.parse()
        .map_err(|_| AppError::InvalidParams("amountY must be a number".into()))?;
    if ax < 0.0 || ay < 0.0 {
        return Err(AppError::InvalidParams("Amounts must be non-negative".into()));
    }
    if ax == 0.0 && ay == 0.0 {
        return Err(AppError::InvalidParams("At least one amount must be positive".into()));
    }
    let has_bins = p.min_bin_id.is_some() && p.max_bin_id.is_some();
    let has_prices = p.min_price.is_some() && p.max_price.is_some();
    if !has_bins && !has_prices {
        return Err(AppError::InvalidParams(
            "Provide either (minBinId + maxBinId) or (minPrice + maxPrice)".into(),
        ));
    }
    if let (Some(mn), Some(mx)) = (p.min_bin_id, p.max_bin_id) {
        if mn >= mx {
            return Err(AppError::InvalidParams("minBinId must be less than maxBinId".into()));
        }
        if mx - mn > MAX_BIN_PER_ARRAY {
            return Err(AppError::InvalidParams(
                format!("Bin range cannot exceed {} bins per position", MAX_BIN_PER_ARRAY),
            ));
        }
    }
    if let (Some(mn), Some(mx)) = (p.min_price, p.max_price) {
        if mn <= 0.0 || mx <= 0.0 {
            return Err(AppError::InvalidParams("Prices must be positive".into()));
        }
        if mn >= mx {
            return Err(AppError::InvalidParams("minPrice must be less than maxPrice".into()));
        }
    }
    if let Some(s) = p.slippage_bps {
        if s > 5000 {
            return Err(AppError::InvalidParams("slippageBps must be ≤ 5000".into()));
        }
    }
    Ok(())
}

pub fn validate_meteora_remove_liquidity_params(p: &MeteoraRemoveLiquidityParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.position)
        .map_err(|_| AppError::InvalidParams(format!("position '{}' is not a valid address", p.position)))?;
    if let Some(bps) = p.bps_to_remove {
        if bps == 0 || bps > 10_000 {
            return Err(AppError::InvalidParams("bpsToRemove must be between 1 and 10000".into()));
        }
    }
    Ok(())
}

pub fn validate_meteora_create_pool_params(p: &MeteoraCreatePoolParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.token_x_mint)
        .map_err(|_| AppError::InvalidParams(format!("tokenXMint '{}' is not valid", p.token_x_mint)))?;
    Pubkey::from_str(&p.token_y_mint)
        .map_err(|_| AppError::InvalidParams(format!("tokenYMint '{}' is not valid", p.token_y_mint)))?;
    if p.token_x_mint == p.token_y_mint {
        return Err(AppError::InvalidParams("tokenXMint and tokenYMint must differ".into()));
    }
    if p.bin_step == 0 {
        return Err(AppError::InvalidParams("binStep must be positive".into()));
    }
    if p.bin_step > 10_000 {
        return Err(AppError::InvalidParams("binStep must be ≤ 10000".into()));
    }
    if p.initial_price <= 0.0 {
        return Err(AppError::InvalidParams("initialPrice must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_open_position_params(p: &MeteoraOpenPositionParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.pool)
        .map_err(|_| AppError::InvalidParams(format!("pool '{}' is not a valid address", p.pool)))?;
    let ax: f64 = p.amount_x.parse()
        .map_err(|_| AppError::InvalidParams("amountX must be a number".into()))?;
    let ay: f64 = p.amount_y.parse()
        .map_err(|_| AppError::InvalidParams("amountY must be a number".into()))?;
    if ax < 0.0 || ay < 0.0 {
        return Err(AppError::InvalidParams("Amounts must be non-negative".into()));
    }
    if ax == 0.0 && ay == 0.0 {
        return Err(AppError::InvalidParams("At least one amount must be positive".into()));
    }
    let has_bins = p.min_bin_id.is_some() && p.max_bin_id.is_some();
    let has_prices = p.min_price.is_some() && p.max_price.is_some();
    if !has_bins && !has_prices {
        return Err(AppError::InvalidParams(
            "Provide either (minBinId + maxBinId) or (minPrice + maxPrice)".into(),
        ));
    }
    if let (Some(mn), Some(mx)) = (p.min_bin_id, p.max_bin_id) {
        if mn >= mx {
            return Err(AppError::InvalidParams("minBinId must be less than maxBinId".into()));
        }
        if mx - mn > MAX_BIN_PER_ARRAY {
            return Err(AppError::InvalidParams(
                format!("Position width cannot exceed {} bins", MAX_BIN_PER_ARRAY),
            ));
        }
    }
    if let (Some(mn), Some(mx)) = (p.min_price, p.max_price) {
        if mn <= 0.0 || mx <= 0.0 {
            return Err(AppError::InvalidParams("Prices must be positive".into()));
        }
        if mn >= mx {
            return Err(AppError::InvalidParams("minPrice must be less than maxPrice".into()));
        }
    }
    Ok(())
}

pub fn validate_meteora_close_position_params(p: &MeteoraClosePositionParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.position)
        .map_err(|_| AppError::InvalidParams(format!("position '{}' is not a valid address", p.position)))?;
    Ok(())
}

pub fn validate_meteora_add_to_position_params(p: &MeteoraAddToPositionParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.position)
        .map_err(|_| AppError::InvalidParams(format!("position '{}' is not a valid address", p.position)))?;
    let ax: f64 = p.amount_x.parse()
        .map_err(|_| AppError::InvalidParams("amountX must be a number".into()))?;
    let ay: f64 = p.amount_y.parse()
        .map_err(|_| AppError::InvalidParams("amountY must be a number".into()))?;
    if ax < 0.0 || ay < 0.0 {
        return Err(AppError::InvalidParams("Amounts must be non-negative".into()));
    }
    if ax == 0.0 && ay == 0.0 {
        return Err(AppError::InvalidParams("At least one amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_claim_fees_params(p: &MeteoraClaimFeesParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.position)
        .map_err(|_| AppError::InvalidParams(format!("position '{}' is not a valid address", p.position)))?;
    Ok(())
}

pub fn validate_meteora_claim_rewards_params(p: &MeteoraClaimRewardsParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.position)
        .map_err(|_| AppError::InvalidParams(format!("position '{}' is not a valid address", p.position)))?;
    if let Some(idx) = p.reward_index {
        if idx > 1 {
            return Err(AppError::InvalidParams("rewardIndex must be 0 or 1".into()));
        }
    }
    Ok(())
}

pub fn validate_meteora_stake_params(p: &MeteoraStakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.farm)
        .map_err(|_| AppError::InvalidParams(format!("farm '{}' is not a valid address", p.farm)))?;
    let amount: f64 = p.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_unstake_params(p: &MeteoraUnstakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.farm)
        .map_err(|_| AppError::InvalidParams(format!("farm '{}' is not a valid address", p.farm)))?;
    let amount: f64 = p.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_harvest_params(p: &MeteoraHarvestParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.farm)
        .map_err(|_| AppError::InvalidParams(format!("farm '{}' is not a valid address", p.farm)))?;
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// PDA Helpers (seeds from SDK v0.11.0)
// ─────────────────────────────────────────────────────────────────────────────

fn dlmm_program() -> Pubkey {
    Pubkey::from_str(METEORA_DLMM_PROGRAM_ID).expect("valid DLMM_PROGRAM_ID")
}

fn farm_program() -> Pubkey {
    Pubkey::from_str(METEORA_FARM_PROGRAM_ID).expect("valid FARM_PROGRAM_ID")
}

fn token_program() -> Pubkey {
    Pubkey::from_str(TOKEN_PROGRAM_ID).expect("valid TOKEN_PROGRAM_ID")
}

fn memo_program() -> Pubkey {
    Pubkey::from_str(MEMO_PROGRAM_ID).expect("valid MEMO_PROGRAM_ID")
}

/// Anchor event authority PDA: [b"__event_authority"]
fn dlmm_event_authority() -> Pubkey {
    Pubkey::find_program_address(&[b"__event_authority"], &dlmm_program()).0
}

/// lb_pair PDA: [token_x_mint, token_y_mint, bin_step_u16_le]
fn lb_pair_pda(token_x: &Pubkey, token_y: &Pubkey, bin_step: u16) -> Pubkey {
    Pubkey::find_program_address(
        &[token_x.as_ref(), token_y.as_ref(), &bin_step.to_le_bytes()],
        &dlmm_program(),
    ).0
}

/// Reserve vault PDA: [token_mint, lb_pair]  ← mint FIRST, then pool
fn reserve_pda(token_mint: &Pubkey, lb_pair: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[token_mint.as_ref(), lb_pair.as_ref()],
        &dlmm_program(),
    ).0
}

/// Oracle PDA: [b"oracle", lb_pair]
fn oracle_pda(lb_pair: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[b"oracle", lb_pair.as_ref()],
        &dlmm_program(),
    ).0
}

/// Bin array PDA: [b"bin_array", lb_pair, index_i64_le]
fn bin_array_pda(lb_pair: &Pubkey, index: i64) -> Pubkey {
    Pubkey::find_program_address(
        &[b"bin_array", lb_pair.as_ref(), &index.to_le_bytes()],
        &dlmm_program(),
    ).0
}

/// Position PDA (initializePositionPda): [b"position", lb_pair, owner, lower_bin_id_i32_le, width_i32_le]
fn position_pda(lb_pair: &Pubkey, owner: &Pubkey, lower_bin_id: i32, width: i32) -> Pubkey {
    Pubkey::find_program_address(
        &[
            b"position",
            lb_pair.as_ref(),
            owner.as_ref(),
            &lower_bin_id.to_le_bytes(),
            &width.to_le_bytes(),
        ],
        &dlmm_program(),
    ).0
}

/// Reward vault PDA: [lb_pair, reward_index_u64_le]
fn reward_vault_pda(lb_pair: &Pubkey, reward_index: u64) -> Pubkey {
    Pubkey::find_program_address(
        &[lb_pair.as_ref(), &reward_index.to_le_bytes()],
        &dlmm_program(),
    ).0
}

/// Preset parameter PDA: [b"preset_parameter", bin_step_u16_le]
fn preset_parameter_pda(bin_step: u16) -> Pubkey {
    Pubkey::find_program_address(
        &[b"preset_parameter", &bin_step.to_le_bytes()],
        &dlmm_program(),
    ).0
}

/// Farm user staking state PDA: [b"user_staking", farm, user]
fn farm_user_pda(farm: &Pubkey, user: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[b"user_staking", farm.as_ref(), user.as_ref()],
        &farm_program(),
    ).0
}

/// Farm reward vault PDA: [b"reward_vault", farm]
fn farm_reward_vault_pda(farm: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[b"reward_vault", farm.as_ref()],
        &farm_program(),
    ).0
}

/// Convert a bin_id to the index of its containing bin-array account.
/// Uses div_euclid for correct floor division of negative bin IDs.
fn bin_id_to_array_index(bin_id: i32) -> i64 {
    bin_id.div_euclid(MAX_BIN_PER_ARRAY) as i64
}

/// Convert a price (token Y per token X) to a DLMM bin ID.
/// Formula: bin_id = floor(log_{1+step/10000}(price))
fn price_to_bin_id(price: f64, bin_step_bps: u32) -> i32 {
    if price <= 0.0 || bin_step_bps == 0 {
        return 0;
    }
    let base = 1.0 + (bin_step_bps as f64) / 10_000.0;
    price.log(base).floor() as i32
}

/// Anchor discriminator: sha256("global:<name>")[..8]
fn disc(name: &str) -> [u8; 8] {
    let mut h = Sha256::new();
    h.update(format!("global:{name}"));
    h.finalize()[..8].try_into().expect("8 bytes")
}

/// Build instruction data: discriminator ++ borsh(args)
fn ix_data<T: BorshSerialize>(discriminator: [u8; 8], args: &T) -> Vec<u8> {
    let mut data = discriminator.to_vec();
    data.extend(borsh::to_vec(args).unwrap_or_default());
    data
}

/// Pre-flight: ensure every bin-array PDA covering the chosen position
/// range exists on-chain, and return `initialize_bin_array` instructions
/// for the ones that don't.
///
/// Why this exists
/// ---------------
/// `add_liquidity_by_weight` (and friends) borrow a writable handle to
/// `bin_array_lower` / `bin_array_upper`. If a bin array has never been
/// touched by an LP, its PDA holds no on-chain account, and Anchor fails
/// with error 3007 (`AccountDidNotDeserialize`) when it tries to load it.
/// On Meteora DLMM the funder calls `initialize_bin_array(index)` to
/// rent-fund and zero-init the array; afterwards add_liquidity works.
///
/// We only emit init ixs for arrays that are *actually* missing — calling
/// `initialize_bin_array` on an already-existing array fails with
/// `BinArrayAlreadyInitialized`, so a defensive "always init" approach
/// would break the second LP into the same range.

/// Wrapped SOL mint — required for any DLMM pool whose `token_y` is "SOL".
const WSOL_MINT_STR: &str = "So11111111111111111111111111111111111111112";

/// On-chain `LbPair` field offsets we care about. The account is laid out as
/// (8-byte discriminator) + (32 StaticParameters) + (32 VariableParameters) +
/// (1 bump_seed) + (2 bin_step_seed) + (1 pair_type) + (4 active_id) +
/// (2 bin_step) + (1 status) + (1 require_base_factor_seed) +
/// (2 base_factor_seed) + (1 activation_type) + (1 creator_pool_on_off_control) +
/// then 4 pubkeys: token_x_mint, token_y_mint, reserve_x, reserve_y.
///
/// Reserve PDAs are NOT stable across pools — Meteora derives them with
/// program-internal logic that the IDL does not expose, so we read them
/// straight from the LbPair account instead of trying to recompute them.
struct LbPairFields {
    reserve_x: Pubkey,
    reserve_y: Pubkey,
}

async fn fetch_reserves(rpc_url: &str, lb_pair: &Pubkey) -> Result<LbPairFields, AppError> {
    let rpc = AsyncRpc::new_with_commitment(rpc_url.to_string(), CommitmentConfig::confirmed());
    let acc = rpc.get_account(lb_pair).await
        .map_err(|e| AppError::ProtocolError(format!("Fetch LbPair {lb_pair}: {e}")))?;
    let data = acc.data;
    if data.len() < 216 {
        return Err(AppError::ProtocolError(format!(
            "LbPair {lb_pair} data too short ({} bytes)", data.len()
        )));
    }
    let read_pubkey = |off: usize| -> Pubkey {
        let mut buf = [0u8; 32];
        buf.copy_from_slice(&data[off..off + 32]);
        Pubkey::new_from_array(buf)
    };
    Ok(LbPairFields {
        reserve_x: read_pubkey(152),
        reserve_y: read_pubkey(184),
    })
}

/// Build the prelude ixs an LP needs before `add_liquidity_by_weight` runs:
///   1. Idempotently create the user's token-X ATA.
///   2. Idempotently create the user's token-Y ATA.
///   3. If either mint is wSOL, transfer the deposit lamports into the wSOL
///      ATA and call `sync_native` so the SPL balance reflects the wrap.
///
/// Without these the pool reserve transfers fail with Anchor 3012
/// (`AccountNotInitialized`) — the user's ATA simply does not exist on
/// chain, and native SOL cannot be moved through an SPL transfer.
fn build_lp_token_setup_ixs(
    user: &Pubkey,
    mint_x: &Pubkey,
    mint_y: &Pubkey,
    amount_x: u64,
    amount_y: u64,
) -> Vec<Instruction> {
    let wsol = Pubkey::from_str(WSOL_MINT_STR).expect("valid wSOL mint");
    let token_prog = token_program();
    let mut ixs: Vec<Instruction> = Vec::new();

    ixs.push(create_associated_token_account_idempotent(
        user, user, mint_x, &token_prog,
    ));
    ixs.push(create_associated_token_account_idempotent(
        user, user, mint_y, &token_prog,
    ));

    if *mint_x == wsol && amount_x > 0 {
        let ata = get_associated_token_address(user, &wsol);
        ixs.push(solana_sdk::system_instruction::transfer(user, &ata, amount_x));
        ixs.push(spl_token::instruction::sync_native(&token_prog, &ata).expect("valid"));
    }
    if *mint_y == wsol && amount_y > 0 {
        let ata = get_associated_token_address(user, &wsol);
        ixs.push(solana_sdk::system_instruction::transfer(user, &ata, amount_y));
        ixs.push(spl_token::instruction::sync_native(&token_prog, &ata).expect("valid"));
    }
    ixs
}

/// Tail ix to append after `add_liquidity_by_weight`: closes the user's wSOL
/// ATA, returning any unused wrapped SOL (and the rent) to the wallet as
/// native SOL. Without this the wallet sees a "+wSOL" delta because Meteora
/// rarely consumes the full wrapped amount — strategy weights and slippage
/// padding leave a remainder in the ATA.
fn build_wsol_unwrap_ixs(user: &Pubkey, mint_x: &Pubkey, mint_y: &Pubkey) -> Vec<Instruction> {
    let wsol = Pubkey::from_str(WSOL_MINT_STR).expect("valid wSOL mint");
    if *mint_x != wsol && *mint_y != wsol {
        return Vec::new();
    }
    let ata = get_associated_token_address(user, &wsol);
    vec![
        spl_token::instruction::close_account(&token_program(), &ata, user, user, &[]).expect("valid"),
    ]
}

async fn ensure_bin_arrays_initialized(
    rpc: &AsyncRpc,
    lb_pair: &Pubkey,
    indices: &[i64],
    funder: &Pubkey,
) -> Result<Vec<Instruction>, AppError> {
    if indices.is_empty() { return Ok(Vec::new()); }

    // Deduplicate (lower / upper often share an array for narrow ranges).
    let mut unique: Vec<i64> = indices.to_vec();
    unique.sort();
    unique.dedup();

    // Map each index → its PDA, then ask the RPC which ones exist.
    let pdas: Vec<Pubkey> = unique.iter()
        .map(|i| bin_array_pda(lb_pair, *i))
        .collect();
    let accounts = rpc.get_multiple_accounts(&pdas).await
        .map_err(|e| AppError::ProtocolError(format!("RPC get_multiple_accounts: {e}")))?;

    #[derive(BorshSerialize)]
    struct InitBinArrayArgs { index: i64 }

    let mut ixs: Vec<Instruction> = Vec::new();
    for (i, account_opt) in accounts.into_iter().enumerate() {
        if account_opt.is_some() { continue; }
        let index = unique[i];
        let bin_array = pdas[i];
        ixs.push(Instruction {
            program_id: dlmm_program(),
            accounts: vec![
                AccountMeta::new_readonly(*lb_pair, false),
                AccountMeta::new(bin_array, false),
                AccountMeta::new(*funder, true),
                AccountMeta::new_readonly(system_program::id(), false),
            ],
            data: ix_data(disc("initialize_bin_array"), &InitBinArrayArgs { index }),
        });
    }
    Ok(ixs)
}

fn empty_remaining_accounts() -> RemainingAccountsInfo {
    RemainingAccountsInfo { slices: vec![] }
}

// ─────────────────────────────────────────────────────────────────────────────
// Liquidity Distribution Helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Build bin liquidity distribution weights.
/// strategy: "uniform" → equal weight per bin; "spot" → bell-curve centered on active_id.
fn build_bin_distribution(
    lower_bin_id: i32,
    upper_bin_id: i32,
    active_id: i32,
    strategy: Option<&str>,
) -> Vec<BinLiqDistByWeight> {
    let width = (upper_bin_id - lower_bin_id) as usize;
    if width == 0 {
        return vec![];
    }

    match strategy.unwrap_or("uniform") {
        "spot" => {
            // Concentrate most liquidity around the active bin.
            (0..width)
                .map(|i| {
                    let bin_id = lower_bin_id + i as i32;
                    let distance = (bin_id - active_id).unsigned_abs() as u16;
                    let weight = 100u16.saturating_sub(distance.saturating_mul(10)).max(1);
                    BinLiqDistByWeight { bin_id, weight }
                })
                .collect()
        }
        _ /* "uniform" */ => {
            // Equal weight across all bins.
            (0..width)
                .map(|i| BinLiqDistByWeight {
                    bin_id: lower_bin_id + i as i32,
                    weight: 1,
                })
                .collect()
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// RPC Transaction Builder
// ─────────────────────────────────────────────────────────────────────────────

/// Fetch a confirmed blockhash and serialize a v0 transaction to base64.
/// This replaces the broken `serialize_vtx` that used `Hash::default()`.
async fn build_vtx_b64(rpc_url: &str, user: &Pubkey, ixs: &[Instruction]) -> Result<String, AppError> {
    let rpc = AsyncRpc::new_with_commitment(
        rpc_url.to_string(),
        CommitmentConfig::confirmed(),
    );
    let blockhash = rpc
        .get_latest_blockhash()
        .await
        .map_err(|e| AppError::ProtocolError(format!("RPC get_latest_blockhash: {e}")))?;
    let v0_msg = v0::Message::try_compile(user, ixs, &[], blockhash)
        .map_err(|e| AppError::Internal(format!("Compile v0 message: {e}")))?;
    let num_sigs = v0_msg.header.num_required_signatures as usize;
    let vtx = VersionedTransaction {
        signatures: vec![Signature::default(); num_sigs],
        message: VersionedMessage::V0(v0_msg),
    };

    // Pre-flight simulate so a build that the wallet would reject surfaces
    // the real failing account / log line back to the chat error message
    // instead of the wallet's opaque "program error 3012". sig_verify=false
    // and replace_recent_blockhash skip the unsigned-tx checks.
    if let Ok(sim) = rpc.simulate_transaction_with_config(
        &vtx,
        solana_client::rpc_config::RpcSimulateTransactionConfig {
            sig_verify: false,
            replace_recent_blockhash: true,
            commitment: Some(CommitmentConfig::confirmed()),
            encoding: None,
            accounts: None,
            min_context_slot: None,
            inner_instructions: false,
        },
    ).await {
        if let Some(err) = sim.value.err {
            let logs = sim.value.logs.unwrap_or_default();
            let tail: Vec<String> = logs.iter().rev().take(8).rev().cloned().collect();
            return Err(AppError::ProtocolError(format!(
                "Simulation failed: {err:?}. Logs (last 8): {}",
                tail.join(" | ")
            )));
        }
    }

    bincode::serialize(&vtx)
        .map(|bytes| base64::engine::general_purpose::STANDARD.encode(bytes))
        .map_err(|e| AppError::Internal(format!("Serialize vtx: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// DLMM API Fetch Helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Fetch pool pair info. Returns Err if the pool is not found.
async fn fetch_pair(http: &reqwest::Client, pool: &str) -> Result<DlmmPairInfo, AppError> {
    // New API: pool detail moved from /pair/{x} to /pools/{x}.
    let url = format!("{DLMM_API}/pools/{pool}");
    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("DLMM pair fetch: {e}")))?;
    if !resp.status().is_success() {
        return Err(AppError::ProtocolError(format!("Pool '{pool}' not found on Meteora DLMM")));
    }
    resp.json::<DlmmPairInfo>()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Parse DLMM pair info: {e}")))
}

/// Fetch position data by position id. The legacy `/position/{id}` endpoint
/// was removed when Meteora migrated to the datapi host; the new API only
/// exposes positions per-user via `/portfolio/open?user=...`. Callers that
/// only know the position id (typical for tx construction flows) need the
/// position's owner wallet first. Until the dependent flows are reworked to
/// fetch the user list and pick by id, return a clear error so the action
/// fails with a useful message instead of a 404.
async fn fetch_pos(_http: &reqwest::Client, position: &str) -> Result<DlmmPositionData, AppError> {
    Err(AppError::ProtocolError(format!(
        "Meteora DLMM position lookup by id is temporarily unsupported \
         after the datapi migration; pass the position via the user's \
         portfolio (position '{position}')."
    )))
}
// ─────────────────────────────────────────────────────────────────────────────
// Build: Swap
// ─────────────────────────────────────────────────────────────────────────────

/// Build a DLMM swap transaction.
///
/// Instruction: `swap`
/// Fixed accounts (positions 0-14): lb_pair, bitmap_ext, reserve_x, reserve_y,
///   user_token_in, user_token_out, token_x_mint, token_y_mint, oracle (writable),
///   host_fee_in, user, token_x_prog, token_y_prog, event_authority, program
/// Remaining accounts (appended): 3 bin arrays around active bin (each writable).
pub async fn build_meteora_swap(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraSwapParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_swap_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let input_mint = Pubkey::from_str(&params.input_mint)
        .map_err(|e| AppError::InvalidParams(format!("Invalid inputMint: {e}")))?;
    let output_mint = Pubkey::from_str(&params.output_mint)
        .map_err(|e| AppError::InvalidParams(format!("Invalid outputMint: {e}")))?;

    let pool_str = params.pool.as_deref()
        .ok_or_else(|| AppError::InvalidParams("pool is required for DLMM swap".into()))?;
    let lb_pair = Pubkey::from_str(pool_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid pool: {e}")))?;

    // Fetch pool to determine token order and decimals.
    let pair = fetch_pair(http, pool_str).await?;
    let active_id = pair.active_id_resolved().unwrap_or(0);
    let mint_x = Pubkey::from_str(pair.mint_x_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintX from API: {e}")))?;
    let mint_y = Pubkey::from_str(pair.mint_y_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintY from API: {e}")))?;

    let x_to_y = input_mint == mint_x;
    let input_decimals = if x_to_y { pair.token_x_decimals() } else { pair.token_y_decimals() };

    let slippage = params.slippage_bps.unwrap_or(50);
    let amount_float: f64 = params.amount.parse()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?;
    let amount_in = (amount_float * 10_f64.powi(input_decimals as i32)) as u64;
    // min_amount_out: set to 0 — slippage enforced by on-chain slippage param
    let min_amount_out = 0u64;

    let args = SwapIxArgs { amount_in, min_amount_out };

    // 3 consecutive bin arrays centered on the active bin.
    let active_arr = bin_id_to_array_index(active_id);
    let ba0 = bin_array_pda(&lb_pair, active_arr - 1);
    let ba1 = bin_array_pda(&lb_pair, active_arr);
    let ba2 = bin_array_pda(&lb_pair, active_arr + 1);

    let _lb_fields = fetch_reserves(rpc_url, &lb_pair).await?;
    let reserve_x = _lb_fields.reserve_x;
    let reserve_y = _lb_fields.reserve_y;
    let user_token_in  = get_associated_token_address(&user, &input_mint);
    let user_token_out = get_associated_token_address(&user, &output_mint);
    let oracle = oracle_pda(&lb_pair);
    let event_authority = dlmm_event_authority();

    let mut ixs: Vec<Instruction> = vec![];

    // Create output ATA if it doesn't exist yet (idempotent).
    ixs.push(create_associated_token_account_idempotent(
        &user,
        &user,
        &output_mint,
        &token_program(),
    ));

    // swap instruction: 15 fixed accounts + 3 remaining bin arrays.
    ixs.push(Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(lb_pair, false),
            // bitmap_ext: optional — Anchor convention for absent is to
            // pass the program ID itself; system_program would cause 3007.
            AccountMeta::new_readonly(dlmm_program(), false),
            AccountMeta::new(reserve_x, false),
            AccountMeta::new(reserve_y, false),
            AccountMeta::new(user_token_in, false),
            AccountMeta::new(user_token_out, false),
            AccountMeta::new_readonly(mint_x, false),
            AccountMeta::new_readonly(mint_y, false),
            AccountMeta::new(oracle, false),             // oracle MUST be writable
            // host_fee_in: optional — pass system_program
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(event_authority, false),
            AccountMeta::new_readonly(dlmm_program(), false),
            // Remaining: bin arrays around active bin (writable).
            AccountMeta::new(ba0, false),
            AccountMeta::new(ba1, false),
            AccountMeta::new(ba2, false),
        ],
        data: ix_data(disc("swap"), &args),
    });

    let mut warnings = vec![];
    if slippage > 300 {
        warnings.push(format!("High slippage: {:.1}%", slippage as f64 / 100.0));
    }

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;
    let pool_display = &pool_str[..8.min(pool_str.len())];

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_swap".into(),
            description: format!(
                "Swap {} {} → {} on Meteora DLMM (pool {}…)",
                params.amount,
                &params.input_mint[..8.min(params.input_mint.len())],
                &params.output_mint[..8.min(params.output_mint.len())],
                pool_display,
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "inputMint":   params.input_mint,
                "outputMint":  params.output_mint,
                "amount":      params.amount,
                "slippageBps": slippage,
                "pool":        pool_str,
                "amountIn":    amount_in,
            }),
            warnings,
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Open Position
// ─────────────────────────────────────────────────────────────────────────────

/// Open a new DLMM position and add liquidity.
///
/// Instructions:
///   1. `initialize_position_pda` — create the position account (PDA-based, single signer)
///   2. `add_liquidity_by_weight`  — deposit tokens into position bins
pub async fn build_meteora_open_position(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraOpenPositionParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_open_position_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let lb_pair = Pubkey::from_str(&params.pool)
        .map_err(|e| AppError::InvalidParams(format!("Invalid pool: {e}")))?;

    let pair = fetch_pair(http, &params.pool).await?;
    let active_id = pair.active_id_resolved().unwrap_or(0);
    let bin_step = pair.bin_step_resolved();
    let mint_x = Pubkey::from_str(pair.mint_x_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintX: {e}")))?;
    let mint_y = Pubkey::from_str(pair.mint_y_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintY: {e}")))?;
    let x_dec = pair.token_x_decimals();
    let y_dec = pair.token_y_decimals();

    // Resolve bin range from either direct IDs or prices.
    let (lower_bin_id, upper_bin_id) = resolve_bin_range(params.min_bin_id, params.max_bin_id,
        params.min_price, params.max_price, bin_step)?;
    let width = upper_bin_id - lower_bin_id;

    let amount_x = parse_to_base_units(&params.amount_x, x_dec)?;
    let amount_y = parse_to_base_units(&params.amount_y, y_dec)?;

    let slippage_bins = params.slippage_bps.map(|s| (s as i32).max(1)).unwrap_or(3);
    let bin_dist = build_bin_distribution(
        lower_bin_id, upper_bin_id, active_id, params.strategy.as_deref(),
    );

    let position = position_pda(&lb_pair, &user, lower_bin_id, width);
    let lower_arr = bin_id_to_array_index(lower_bin_id);
    // Anchor cannot mutably borrow the same account twice. When the position
    // fits in a single bin array (`lower_bin_id` and `upper_bin_id - 1` resolve
    // to the same index) the SDK convention is to pass `lower_arr + 1` as the
    // upper bin array — the on-chain program tolerates an unused upper as long
    // as it is initialized. `ensure_bin_arrays_initialized` will rent-fund any
    // missing array, so this is safe.
    let upper_arr_raw = bin_id_to_array_index(upper_bin_id.saturating_sub(1));
    let upper_arr = if upper_arr_raw == lower_arr { lower_arr + 1 } else { upper_arr_raw };
    let ba_lower = bin_array_pda(&lb_pair, lower_arr);
    let ba_upper = bin_array_pda(&lb_pair, upper_arr);
    let _lb_fields = fetch_reserves(rpc_url, &lb_pair).await?;
    let reserve_x = _lb_fields.reserve_x;
    let reserve_y = _lb_fields.reserve_y;
    let user_ata_x = get_associated_token_address(&user, &mint_x);
    let user_ata_y = get_associated_token_address(&user, &mint_y);
    let event_auth = dlmm_event_authority();

    // Pre-flight: ensure both bin arrays exist on-chain. First-time LPs
    // into a fresh range otherwise hit Anchor 3007 when add_liquidity
    // tries to deserialize an uninitialized bin_array PDA.
    let rpc = AsyncRpc::new_with_commitment(rpc_url.to_string(), CommitmentConfig::confirmed());
    let init_bin_ixs = ensure_bin_arrays_initialized(
        &rpc, &lb_pair, &[lower_arr, upper_arr], &user,
    ).await?;

    let mut ixs: Vec<Instruction> = vec![];

    // ATA + wSOL wrap prelude (see build_meteora_add_liquidity for rationale).
    ixs.extend(build_lp_token_setup_ixs(
        &user, &mint_x, &mint_y, amount_x, amount_y,
    ));

    ixs.extend(init_bin_ixs);

    // 1. initialize_position_pda
    // payer = base = owner = user (all same pubkey; base + payer must sign)
    ixs.push(Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(user, true),               // payer (writable, signer)
            AccountMeta::new_readonly(user, true),      // base (signer, determines PDA)
            AccountMeta::new(position, false),           // position PDA (writable)
            AccountMeta::new(lb_pair, false),            // lb_pair (writable)
            AccountMeta::new_readonly(user, false),     // owner (readonly)
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new_readonly(sysvar::rent::id(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(dlmm_program(), false),
        ],
        data: ix_data(disc("initialize_position_pda"), &InitPosPdaArgs { lower_bin_id, width }),
    });

    // 2. add_liquidity_by_weight
    ixs.push(Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(position, false),
            AccountMeta::new(lb_pair, false),
            // bitmap_ext is optional in the on-chain program. The Anchor
            // convention for "absent optional account" is to pass the program
            // ID itself, NOT system_program::id(). Passing system_program causes
            // Anchor to try deserialising an unrelated account → error 3007
            // (AccountDidNotDeserialize).
            AccountMeta::new_readonly(dlmm_program(), false), // bitmap_ext (absent placeholder)
            AccountMeta::new(user_ata_x, false),
            AccountMeta::new(user_ata_y, false),
            AccountMeta::new(reserve_x, false),
            AccountMeta::new(reserve_y, false),
            AccountMeta::new_readonly(mint_x, false),
            AccountMeta::new_readonly(mint_y, false),
            AccountMeta::new(ba_lower, false),
            AccountMeta::new(ba_upper, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(dlmm_program(), false),
        ],
        data: ix_data(disc("add_liquidity_by_weight"), &LiqByWeightArgs {
            amount_x,
            amount_y,
            active_id,
            max_active_bin_slippage: slippage_bins,
            bin_liquidity_dist: bin_dist,
        }),
    });

    // Close wSOL ATA so any unused wrapped SOL refunds to the wallet as
    // native SOL (Meteora consumes only what bin weights demand, leaving
    // a remainder).
    ixs.extend(build_wsol_unwrap_ixs(&user, &mint_x, &mint_y));

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_open_position".into(),
            description: format!(
                "Open Meteora DLMM position bins [{lower_bin_id}..{upper_bin_id}] ({width} bins)"
            ),
            estimated_fee: "~0.005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "pool":        params.pool,
                "position":    position.to_string(),
                "lowerBinId":  lower_bin_id,
                "upperBinId":  upper_bin_id,
                "width":       width,
                "amountX":     params.amount_x,
                "amountY":     params.amount_y,
                "activeId":    active_id,
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Add Liquidity
// ─────────────────────────────────────────────────────────────────────────────

/// Add liquidity to an existing or new DLMM position.
///
/// If no position exists for the given bin range, `initialize_position_pda` is
/// prepended automatically.
pub async fn build_meteora_add_liquidity(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraAddLiquidityParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_add_liquidity_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let lb_pair = Pubkey::from_str(&params.pool)
        .map_err(|e| AppError::InvalidParams(format!("Invalid pool: {e}")))?;

    let pair = fetch_pair(http, &params.pool).await?;
    let active_id = pair.active_id_resolved().unwrap_or(0);
    let bin_step = pair.bin_step_resolved();
    let mint_x = Pubkey::from_str(pair.mint_x_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintX: {e}")))?;
    let mint_y = Pubkey::from_str(pair.mint_y_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintY: {e}")))?;
    let x_dec = pair.token_x_decimals();
    let y_dec = pair.token_y_decimals();

    let (lower_bin_id, upper_bin_id) = resolve_bin_range(
        params.min_bin_id, params.max_bin_id,
        params.min_price, params.max_price, bin_step,
    )?;
    let width = upper_bin_id - lower_bin_id;
    let amount_x = parse_to_base_units(&params.amount_x, x_dec)?;
    let amount_y = parse_to_base_units(&params.amount_y, y_dec)?;
    let slippage_bins = params.slippage_bps.map(|s| (s as i32).max(1)).unwrap_or(3);
    let bin_dist = build_bin_distribution(
        lower_bin_id, upper_bin_id, active_id, params.strategy.as_deref(),
    );

    let position = position_pda(&lb_pair, &user, lower_bin_id, width);
    let lower_arr = bin_id_to_array_index(lower_bin_id);
    // Anchor cannot mutably borrow the same account twice. When the position
    // fits in a single bin array (`lower_bin_id` and `upper_bin_id - 1` resolve
    // to the same index) the SDK convention is to pass `lower_arr + 1` as the
    // upper bin array — the on-chain program tolerates an unused upper as long
    // as it is initialized. `ensure_bin_arrays_initialized` will rent-fund any
    // missing array, so this is safe.
    let upper_arr_raw = bin_id_to_array_index(upper_bin_id.saturating_sub(1));
    let upper_arr = if upper_arr_raw == lower_arr { lower_arr + 1 } else { upper_arr_raw };
    let ba_lower = bin_array_pda(&lb_pair, lower_arr);
    let ba_upper = bin_array_pda(&lb_pair, upper_arr);
    let _lb_fields = fetch_reserves(rpc_url, &lb_pair).await?;
    let reserve_x = _lb_fields.reserve_x;
    let reserve_y = _lb_fields.reserve_y;
    let user_ata_x = get_associated_token_address(&user, &mint_x);
    let user_ata_y = get_associated_token_address(&user, &mint_y);
    let event_auth = dlmm_event_authority();

    // Pre-flight: position account + bin array existence checks. Both have
    // to live on-chain before add_liquidity_by_weight can read them; a
    // missing position triggers initialize_position_pda, a missing bin
    // array triggers initialize_bin_array. Without these prepended ixs,
    // first-time LPs into a fresh range get Anchor error 3007.
    let rpc = AsyncRpc::new_with_commitment(rpc_url.to_string(), CommitmentConfig::confirmed());
    let position_exists = rpc.get_account(&position).await.is_ok();

    let mut ixs: Vec<Instruction> = vec![];

    // ATA + wSOL wrap prelude — without this, deposits of native SOL or
    // first-time deposits of an SPL the user has never held trip Anchor
    // 3012 (AccountNotInitialized) on the reserve transfer.
    ixs.extend(build_lp_token_setup_ixs(
        &user, &mint_x, &mint_y, amount_x, amount_y,
    ));

    // Bin array init ixs — emit only for arrays not yet on-chain.
    let init_bin_ixs = ensure_bin_arrays_initialized(
        &rpc, &lb_pair, &[lower_arr, upper_arr], &user,
    ).await?;
    ixs.extend(init_bin_ixs);

    if !position_exists {
        ixs.push(Instruction {
            program_id: dlmm_program(),
            accounts: vec![
                AccountMeta::new(user, true),
                AccountMeta::new_readonly(user, true),
                AccountMeta::new(position, false),
                AccountMeta::new(lb_pair, false),
                AccountMeta::new_readonly(user, false),
                AccountMeta::new_readonly(system_program::id(), false),
                AccountMeta::new_readonly(sysvar::rent::id(), false),
                AccountMeta::new_readonly(event_auth, false),
                AccountMeta::new_readonly(dlmm_program(), false),
            ],
            data: ix_data(disc("initialize_position_pda"), &InitPosPdaArgs { lower_bin_id, width }),
        });
    }

    ixs.push(Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(position, false),
            AccountMeta::new(lb_pair, false),
            // bitmap_ext is optional. Anchor convention for "absent" is the
            // program ID itself, NOT system_program::id() (which would deserialize
            // into BinArrayBitmapExtension and trip error 3007).
            AccountMeta::new_readonly(dlmm_program(), false),
            AccountMeta::new(user_ata_x, false),
            AccountMeta::new(user_ata_y, false),
            AccountMeta::new(reserve_x, false),
            AccountMeta::new(reserve_y, false),
            AccountMeta::new_readonly(mint_x, false),
            AccountMeta::new_readonly(mint_y, false),
            AccountMeta::new(ba_lower, false),
            AccountMeta::new(ba_upper, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(dlmm_program(), false),
        ],
        data: ix_data(disc("add_liquidity_by_weight"), &LiqByWeightArgs {
            amount_x,
            amount_y,
            active_id,
            max_active_bin_slippage: slippage_bins,
            bin_liquidity_dist: bin_dist,
        }),
    });

    // Close wSOL ATA so any unused wrapped SOL refunds to the wallet as
    // native SOL (Meteora consumes only what bin weights demand, leaving
    // a remainder).
    ixs.extend(build_wsol_unwrap_ixs(&user, &mint_x, &mint_y));

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_add_liquidity".into(),
            description: format!(
                "Add liquidity to Meteora DLMM bins [{lower_bin_id}..{upper_bin_id}]"
            ),
            estimated_fee: "~0.002 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "pool":       params.pool,
                "position":   position.to_string(),
                "lowerBinId": lower_bin_id,
                "upperBinId": upper_bin_id,
                "amountX":    params.amount_x,
                "amountY":    params.amount_y,
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Remove Liquidity
// ─────────────────────────────────────────────────────────────────────────────

/// Remove liquidity from a DLMM position.
///
/// Instruction: `remove_liquidity`
pub async fn build_meteora_remove_liquidity(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraRemoveLiquidityParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_remove_liquidity_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let position = Pubkey::from_str(&params.position)
        .map_err(|e| AppError::InvalidParams(format!("Invalid position: {e}")))?;

    let pos_data = fetch_pos(http, &params.position).await?;
    let lb_pair = Pubkey::from_str(&pos_data.lb_pair)
        .map_err(|e| AppError::ProtocolError(format!("Invalid lb_pair from API: {e}")))?;

    let pair = fetch_pair(http, &pos_data.lb_pair).await?;
    let mint_x = Pubkey::from_str(pair.mint_x_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintX: {e}")))?;
    let mint_y = Pubkey::from_str(pair.mint_y_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintY: {e}")))?;

    let bps = params.bps_to_remove.unwrap_or(10_000);
    let lower_bin_id = pos_data.lower_bin_id;
    let upper_bin_id = pos_data.upper_bin_id;

    // Which bins to remove from.
    let bin_removals: Vec<BinLiqReduction> = if let Some(ref ids) = params.bin_ids {
        ids.iter()
            .map(|&bin_id| BinLiqReduction { bin_id, bps_to_remove: bps })
            .collect()
    } else {
        (lower_bin_id..upper_bin_id)
            .map(|bin_id| BinLiqReduction { bin_id, bps_to_remove: bps })
            .collect()
    };

    let slippage = params.slippage_bps.unwrap_or(100);
    let amount_x_min = 0u64; // no pre-estimate without price oracle
    let amount_y_min = 0u64;

    let lower_arr = bin_id_to_array_index(lower_bin_id);
    // Anchor cannot mutably borrow the same account twice. When the position
    // fits in a single bin array (`lower_bin_id` and `upper_bin_id - 1` resolve
    // to the same index) the SDK convention is to pass `lower_arr + 1` as the
    // upper bin array — the on-chain program tolerates an unused upper as long
    // as it is initialized. `ensure_bin_arrays_initialized` will rent-fund any
    // missing array, so this is safe.
    let upper_arr_raw = bin_id_to_array_index(upper_bin_id.saturating_sub(1));
    let upper_arr = if upper_arr_raw == lower_arr { lower_arr + 1 } else { upper_arr_raw };
    let ba_lower = bin_array_pda(&lb_pair, lower_arr);
    let ba_upper = bin_array_pda(&lb_pair, upper_arr);
    let _lb_fields = fetch_reserves(rpc_url, &lb_pair).await?;
    let reserve_x = _lb_fields.reserve_x;
    let reserve_y = _lb_fields.reserve_y;
    let user_ata_x = get_associated_token_address(&user, &mint_x);
    let user_ata_y = get_associated_token_address(&user, &mint_y);
    let event_auth = dlmm_event_authority();

    let ixs = vec![Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(position, false),
            AccountMeta::new(lb_pair, false),
            // bitmap_ext is optional in the on-chain program. The Anchor
            // convention for "absent optional account" is to pass the program
            // ID itself, NOT system_program::id(). Passing system_program causes
            // Anchor to try deserialising an unrelated account → error 3007
            // (AccountDidNotDeserialize).
            AccountMeta::new_readonly(dlmm_program(), false), // bitmap_ext (absent placeholder)
            AccountMeta::new(user_ata_x, false),
            AccountMeta::new(user_ata_y, false),
            AccountMeta::new(reserve_x, false),
            AccountMeta::new(reserve_y, false),
            AccountMeta::new_readonly(mint_x, false),
            AccountMeta::new_readonly(mint_y, false),
            AccountMeta::new(ba_lower, false),
            AccountMeta::new(ba_upper, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(dlmm_program(), false),
        ],
        data: ix_data(disc("remove_liquidity"), &RemoveLiqArgs {
            bin_liquidity_removal: bin_removals.clone(),
            amount_x_min,
            amount_y_min,
        }),
    }];

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_remove_liquidity".into(),
            description: format!(
                "Remove {}% liquidity from Meteora position {}…",
                bps / 100,
                &params.position[..8.min(params.position.len())],
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "position":   params.position,
                "bpsToRemove": bps,
                "slippageBps": slippage,
                "binCount":   bin_removals.len(),
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Close Position
// ─────────────────────────────────────────────────────────────────────────────

/// Close a DLMM position: remove all liquidity, claim fees, then close.
///
/// Returns two sequential transactions:
///   TX1: `remove_liquidity` (all bins) + `claim_fee2`
///   TX2: `close_position2`
pub async fn build_meteora_close_position(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraClosePositionParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_close_position_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let position = Pubkey::from_str(&params.position)
        .map_err(|e| AppError::InvalidParams(format!("Invalid position: {e}")))?;

    let pos_data = fetch_pos(http, &params.position).await?;
    let lb_pair = Pubkey::from_str(&pos_data.lb_pair)
        .map_err(|e| AppError::ProtocolError(format!("Invalid lb_pair from API: {e}")))?;

    let pair = fetch_pair(http, &pos_data.lb_pair).await?;
    let mint_x = Pubkey::from_str(pair.mint_x_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintX: {e}")))?;
    let mint_y = Pubkey::from_str(pair.mint_y_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintY: {e}")))?;

    let lower_bin_id = pos_data.lower_bin_id;
    let upper_bin_id = pos_data.upper_bin_id;

    let bin_removals: Vec<BinLiqReduction> = (lower_bin_id..upper_bin_id)
        .map(|bin_id| BinLiqReduction { bin_id, bps_to_remove: 10_000 })
        .collect();

    let lower_arr = bin_id_to_array_index(lower_bin_id);
    // Anchor cannot mutably borrow the same account twice. When the position
    // fits in a single bin array (`lower_bin_id` and `upper_bin_id - 1` resolve
    // to the same index) the SDK convention is to pass `lower_arr + 1` as the
    // upper bin array — the on-chain program tolerates an unused upper as long
    // as it is initialized. `ensure_bin_arrays_initialized` will rent-fund any
    // missing array, so this is safe.
    let upper_arr_raw = bin_id_to_array_index(upper_bin_id.saturating_sub(1));
    let upper_arr = if upper_arr_raw == lower_arr { lower_arr + 1 } else { upper_arr_raw };
    let ba_lower = bin_array_pda(&lb_pair, lower_arr);
    let ba_upper = bin_array_pda(&lb_pair, upper_arr);
    let _lb_fields = fetch_reserves(rpc_url, &lb_pair).await?;
    let reserve_x = _lb_fields.reserve_x;
    let reserve_y = _lb_fields.reserve_y;
    let user_ata_x = get_associated_token_address(&user, &mint_x);
    let user_ata_y = get_associated_token_address(&user, &mint_y);
    let event_auth = dlmm_event_authority();

    // TX1: remove_liquidity + claim_fee2
    let ixs_tx1 = vec![
        Instruction {
            program_id: dlmm_program(),
            accounts: vec![
                AccountMeta::new(position, false),
                AccountMeta::new(lb_pair, false),
                // bitmap_ext (optional, absent → program ID per Anchor convention)
                AccountMeta::new_readonly(dlmm_program(), false),
                AccountMeta::new(user_ata_x, false),
                AccountMeta::new(user_ata_y, false),
                AccountMeta::new(reserve_x, false),
                AccountMeta::new(reserve_y, false),
                AccountMeta::new_readonly(mint_x, false),
                AccountMeta::new_readonly(mint_y, false),
                AccountMeta::new(ba_lower, false),
                AccountMeta::new(ba_upper, false),
                AccountMeta::new_readonly(user, true),
                AccountMeta::new_readonly(token_program(), false),
                AccountMeta::new_readonly(token_program(), false),
                AccountMeta::new_readonly(event_auth, false),
                AccountMeta::new_readonly(dlmm_program(), false),
            ],
            data: ix_data(disc("remove_liquidity"), &RemoveLiqArgs {
                bin_liquidity_removal: bin_removals,
                amount_x_min: 0,
                amount_y_min: 0,
            }),
        },
        // claim_fee2: 14 accounts
        Instruction {
            program_id: dlmm_program(),
            accounts: vec![
                AccountMeta::new(lb_pair, false),
                AccountMeta::new(position, false),
                AccountMeta::new_readonly(user, true),
                AccountMeta::new(reserve_x, false),
                AccountMeta::new(reserve_y, false),
                AccountMeta::new(user_ata_x, false),
                AccountMeta::new(user_ata_y, false),
                AccountMeta::new_readonly(mint_x, false),
                AccountMeta::new_readonly(mint_y, false),
                AccountMeta::new_readonly(token_program(), false),
                AccountMeta::new_readonly(token_program(), false),
                AccountMeta::new_readonly(memo_program(), false),
                AccountMeta::new_readonly(event_auth, false),
                AccountMeta::new_readonly(dlmm_program(), false),
            ],
            data: ix_data(disc("claim_fee2"), &ClaimFee2Args {
                remaining_accounts_info: empty_remaining_accounts(),
            }),
        },
    ];

    // TX2: close_position2 (5 accounts only)
    let ixs_tx2 = vec![Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(position, false),
            AccountMeta::new_readonly(user, true),      // sender
            AccountMeta::new(user, false),              // rent_receiver
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(dlmm_program(), false),
        ],
        data: disc("close_position2").to_vec(),
    }];

    let tx1 = build_vtx_b64(rpc_url, &user, &ixs_tx1).await?;
    let tx2 = build_vtx_b64(rpc_url, &user, &ixs_tx2).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_close_position".into(),
            description: format!(
                "Close Meteora DLMM position {}… (remove liquidity + claim fees + close)",
                &params.position[..8.min(params.position.len())],
            ),
            estimated_fee: "~0.001 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "position": params.position,
                "pool":     pos_data.lb_pair,
            }),
            warnings: vec![
                "Two sequential transactions required — sign both.".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(tx1.clone()),
        additional_signers_required: 0,
        execution_steps: Some(serde_json::json!({
            "type": "sequential",
            "transactions": [
                { "label": "Remove liquidity + claim fees",  "transaction": tx1 },
                { "label": "Close position (recover rent)",  "transaction": tx2 },
            ]
        })),
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Add to Position
// ─────────────────────────────────────────────────────────────────────────────

/// Add liquidity to an existing DLMM position.
///
/// Instruction: `add_liquidity_by_weight` (no initialize needed — position already exists)
pub async fn build_meteora_add_to_position(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraAddToPositionParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_add_to_position_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let position = Pubkey::from_str(&params.position)
        .map_err(|e| AppError::InvalidParams(format!("Invalid position: {e}")))?;

    let pos_data = fetch_pos(http, &params.position).await?;
    let lb_pair = Pubkey::from_str(&pos_data.lb_pair)
        .map_err(|e| AppError::ProtocolError(format!("Invalid lb_pair: {e}")))?;

    let pair = fetch_pair(http, &pos_data.lb_pair).await?;
    let active_id = pair.active_id_resolved().unwrap_or(0);
    let mint_x = Pubkey::from_str(pair.mint_x_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintX: {e}")))?;
    let mint_y = Pubkey::from_str(pair.mint_y_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintY: {e}")))?;
    let x_dec = pair.token_x_decimals();
    let y_dec = pair.token_y_decimals();

    let lower_bin_id = pos_data.lower_bin_id;
    let upper_bin_id = pos_data.upper_bin_id;
    let amount_x = parse_to_base_units(&params.amount_x, x_dec)?;
    let amount_y = parse_to_base_units(&params.amount_y, y_dec)?;
    let slippage_bins = params.slippage_bps.map(|s| (s as i32).max(1)).unwrap_or(3);
    let bin_dist = build_bin_distribution(
        lower_bin_id, upper_bin_id, active_id, params.strategy.as_deref(),
    );

    let lower_arr = bin_id_to_array_index(lower_bin_id);
    // Anchor cannot mutably borrow the same account twice. When the position
    // fits in a single bin array (`lower_bin_id` and `upper_bin_id - 1` resolve
    // to the same index) the SDK convention is to pass `lower_arr + 1` as the
    // upper bin array — the on-chain program tolerates an unused upper as long
    // as it is initialized. `ensure_bin_arrays_initialized` will rent-fund any
    // missing array, so this is safe.
    let upper_arr_raw = bin_id_to_array_index(upper_bin_id.saturating_sub(1));
    let upper_arr = if upper_arr_raw == lower_arr { lower_arr + 1 } else { upper_arr_raw };
    let ba_lower = bin_array_pda(&lb_pair, lower_arr);
    let ba_upper = bin_array_pda(&lb_pair, upper_arr);
    let _lb_fields = fetch_reserves(rpc_url, &lb_pair).await?;
    let reserve_x = _lb_fields.reserve_x;
    let reserve_y = _lb_fields.reserve_y;
    let user_ata_x = get_associated_token_address(&user, &mint_x);
    let user_ata_y = get_associated_token_address(&user, &mint_y);
    let event_auth = dlmm_event_authority();

    // ATA + wSOL wrap prelude (see build_meteora_add_liquidity for rationale).
    let mut ixs: Vec<Instruction> = build_lp_token_setup_ixs(
        &user, &mint_x, &mint_y, amount_x, amount_y,
    );
    ixs.push(Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(position, false),
            AccountMeta::new(lb_pair, false),
            // bitmap_ext (optional, absent → program ID per Anchor convention)
            AccountMeta::new_readonly(dlmm_program(), false),
            AccountMeta::new(user_ata_x, false),
            AccountMeta::new(user_ata_y, false),
            AccountMeta::new(reserve_x, false),
            AccountMeta::new(reserve_y, false),
            AccountMeta::new_readonly(mint_x, false),
            AccountMeta::new_readonly(mint_y, false),
            AccountMeta::new(ba_lower, false),
            AccountMeta::new(ba_upper, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(dlmm_program(), false),
        ],
        data: ix_data(disc("add_liquidity_by_weight"), &LiqByWeightArgs {
            amount_x,
            amount_y,
            active_id,
            max_active_bin_slippage: slippage_bins,
            bin_liquidity_dist: bin_dist,
        }),
    });

    // Close wSOL ATA so any unused wrapped SOL refunds to the wallet as
    // native SOL (Meteora consumes only what bin weights demand, leaving
    // a remainder).
    ixs.extend(build_wsol_unwrap_ixs(&user, &mint_x, &mint_y));

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_add_to_position".into(),
            description: format!(
                "Add liquidity to existing Meteora position {}…",
                &params.position[..8.min(params.position.len())],
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "position": params.position,
                "amountX":  params.amount_x,
                "amountY":  params.amount_y,
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Claim Fees
// ─────────────────────────────────────────────────────────────────────────────

/// Claim trading fees from a DLMM position.
///
/// Instruction: `claim_fee2` (v2 — supports Token-2022; no extra accounts for SPL).
/// Accounts (14): lb_pair, position, sender, reserve_x, reserve_y,
///   user_ata_x, user_ata_y, mint_x, mint_y, token_prog_x, token_prog_y,
///   memo_program, event_authority, program
pub async fn build_meteora_claim_fees(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraClaimFeesParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_claim_fees_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let position = Pubkey::from_str(&params.position)
        .map_err(|e| AppError::InvalidParams(format!("Invalid position: {e}")))?;

    let pos_data = fetch_pos(http, &params.position).await?;
    let lb_pair = Pubkey::from_str(&pos_data.lb_pair)
        .map_err(|e| AppError::ProtocolError(format!("Invalid lb_pair: {e}")))?;

    let pair = fetch_pair(http, &pos_data.lb_pair).await?;
    let mint_x = Pubkey::from_str(pair.mint_x_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintX: {e}")))?;
    let mint_y = Pubkey::from_str(pair.mint_y_str())
        .map_err(|e| AppError::ProtocolError(format!("Invalid mintY: {e}")))?;

    let _lb_fields = fetch_reserves(rpc_url, &lb_pair).await?;
    let reserve_x = _lb_fields.reserve_x;
    let reserve_y = _lb_fields.reserve_y;
    let user_ata_x = get_associated_token_address(&user, &mint_x);
    let user_ata_y = get_associated_token_address(&user, &mint_y);
    let event_auth = dlmm_event_authority();

    let mut ixs: Vec<Instruction> = vec![];

    // Ensure output ATAs exist.
    ixs.push(create_associated_token_account_idempotent(&user, &user, &mint_x, &token_program()));
    ixs.push(create_associated_token_account_idempotent(&user, &user, &mint_y, &token_program()));

    ixs.push(Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(lb_pair, false),
            AccountMeta::new(position, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new(reserve_x, false),
            AccountMeta::new(reserve_y, false),
            AccountMeta::new(user_ata_x, false),
            AccountMeta::new(user_ata_y, false),
            AccountMeta::new_readonly(mint_x, false),
            AccountMeta::new_readonly(mint_y, false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(memo_program(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(dlmm_program(), false),
        ],
        data: ix_data(disc("claim_fee2"), &ClaimFee2Args {
            remaining_accounts_info: empty_remaining_accounts(),
        }),
    });

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_claim_fees".into(),
            description: format!(
                "Claim trading fees from Meteora position {}…",
                &params.position[..8.min(params.position.len())],
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "position": params.position }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Claim Rewards
// ─────────────────────────────────────────────────────────────────────────────

/// Claim farming rewards from a DLMM position.
///
/// Instruction: `claim_reward2` (v2) for each reward index.
/// If reward_index is omitted, claims all available reward indices (0 and 1).
pub async fn build_meteora_claim_rewards(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraClaimRewardsParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_claim_rewards_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let position = Pubkey::from_str(&params.position)
        .map_err(|e| AppError::InvalidParams(format!("Invalid position: {e}")))?;

    let pos_data = fetch_pos(http, &params.position).await?;
    let lb_pair = Pubkey::from_str(&pos_data.lb_pair)
        .map_err(|e| AppError::ProtocolError(format!("Invalid lb_pair: {e}")))?;

    let pair = fetch_pair(http, &pos_data.lb_pair).await?;
    let event_auth = dlmm_event_authority();

    // Determine which reward indices to claim.
    let indices: Vec<u64> = match params.reward_index {
        Some(idx) => vec![idx as u64],
        None => {
            // Claim all configured reward slots (0 and 1).
            let mut v = vec![0u64];
            if pair.reward_mints_resolved().len() > 1 {
                v.push(1);
            }
            v
        }
    };

    let mut ixs: Vec<Instruction> = vec![];

    for &reward_index in &indices {
        let reward_vault = reward_vault_pda(&lb_pair, reward_index);

        // Reward mint: derive from pool's reward_mints or use the vault ATA owner lookup.
        // If the API doesn't provide reward mints, default to a best-effort vault derivation.
        let reward_mints_vec = pair.reward_mints_resolved();
        let reward_mint_str = reward_mints_vec.get(reward_index as usize)
            .map(|s| s.as_str())
            .unwrap_or("");

        if reward_mint_str.is_empty() {
            // No reward at this index — skip.
            continue;
        }

        let reward_mint = Pubkey::from_str(reward_mint_str)
            .map_err(|_| AppError::ProtocolError(format!("Invalid reward_mint at index {reward_index}")))?;

        let user_reward_ata = get_associated_token_address(&user, &reward_mint);

        ixs.push(create_associated_token_account_idempotent(
            &user, &user, &reward_mint, &token_program(),
        ));

        ixs.push(Instruction {
            program_id: dlmm_program(),
            accounts: vec![
                AccountMeta::new(lb_pair, false),
                AccountMeta::new(position, false),
                AccountMeta::new_readonly(user, true),
                AccountMeta::new(reward_vault, false),
                AccountMeta::new_readonly(reward_mint, false),
                AccountMeta::new(user_reward_ata, false),
                AccountMeta::new_readonly(token_program(), false),
                AccountMeta::new_readonly(memo_program(), false),
                AccountMeta::new_readonly(event_auth, false),
                AccountMeta::new_readonly(dlmm_program(), false),
            ],
            data: ix_data(disc("claim_reward2"), &ClaimReward2Args {
                reward_index,
                remaining_accounts_info: empty_remaining_accounts(),
            }),
        });
    }

    if ixs.is_empty() {
        return Err(AppError::ProtocolError(
            "No active reward emissions found on this position's pool".into(),
        ));
    }

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_claim_rewards".into(),
            description: format!(
                "Claim rewards from Meteora position {}…",
                &params.position[..8.min(params.position.len())],
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "position":    params.position,
                "rewardIndex": params.reward_index,
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Create Pool
// ─────────────────────────────────────────────────────────────────────────────

/// Create a new DLMM pool (lb_pair).
///
/// Instruction: `initialize_lb_pair`
/// Note: token mints must be sorted (token_x numerically < token_y) per SDK convention.
pub async fn build_meteora_create_pool(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraCreatePoolParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_create_pool_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let mut mint_x = Pubkey::from_str(&params.token_x_mint)
        .map_err(|e| AppError::InvalidParams(format!("Invalid tokenXMint: {e}")))?;
    let mut mint_y = Pubkey::from_str(&params.token_y_mint)
        .map_err(|e| AppError::InvalidParams(format!("Invalid tokenYMint: {e}")))?;

    // Canonical ordering: mint_x bytes < mint_y bytes (per Meteora SDK convention).
    if mint_x.as_ref() > mint_y.as_ref() {
        std::mem::swap(&mut mint_x, &mut mint_y);
    }

    let bin_step = params.bin_step as u16;

    // Compute active_id from initial_price.
    let bin_step_ratio = 1.0 + params.bin_step as f64 / 10_000.0;
    let active_id = if params.initial_price > 0.0 && bin_step_ratio > 1.0 {
        (params.initial_price.ln() / bin_step_ratio.ln()) as i32
    } else {
        0
    };

    let lb_pair = lb_pair_pda(&mint_x, &mint_y, bin_step);
    let _lb_fields = fetch_reserves(rpc_url, &lb_pair).await?;
    let reserve_x = _lb_fields.reserve_x;
    let reserve_y = _lb_fields.reserve_y;
    let oracle = oracle_pda(&lb_pair);
    let preset = preset_parameter_pda(bin_step);
    let event_auth = dlmm_event_authority();

    let ixs = vec![Instruction {
        program_id: dlmm_program(),
        accounts: vec![
            AccountMeta::new(lb_pair, false),
            // bitmap_ext is optional in the on-chain program. The Anchor
            // convention for "absent optional account" is to pass the program
            // ID itself, NOT system_program::id(). Passing system_program causes
            // Anchor to try deserialising an unrelated account → error 3007
            // (AccountDidNotDeserialize).
            AccountMeta::new_readonly(dlmm_program(), false), // bitmap_ext (absent placeholder)
            AccountMeta::new_readonly(mint_x, false),
            AccountMeta::new_readonly(mint_y, false),
            AccountMeta::new(reserve_x, false),
            AccountMeta::new(reserve_y, false),
            AccountMeta::new(oracle, false),
            AccountMeta::new_readonly(preset, false),
            AccountMeta::new(user, true),              // funder
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new_readonly(sysvar::rent::id(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(dlmm_program(), false),
        ],
        data: ix_data(disc("initialize_lb_pair"), &InitLbPairArgs { active_id, bin_step }),
    }];

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_create_pool".into(),
            description: format!(
                "Create Meteora DLMM pool bin_step={} at price {:.6}",
                params.bin_step, params.initial_price,
            ),
            estimated_fee: "~0.05 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "tokenXMint":    mint_x.to_string(),
                "tokenYMint":    mint_y.to_string(),
                "binStep":       params.bin_step,
                "initialPrice":  params.initial_price,
                "activeId":      active_id,
                "lbPair":        lb_pair.to_string(),
            }),
            warnings: vec![
                "Ensure a preset_parameter account exists for this bin step on-chain.".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build: Stake / Unstake / Harvest (Meteora Farm Program)
// ─────────────────────────────────────────────────────────────────────────────

/// Stake LP tokens in a Meteora farm.
///
/// Instruction: `deposit` on the Meteora Farm program.
pub async fn build_meteora_stake(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_stake_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let farm = Pubkey::from_str(&params.farm)
        .map_err(|e| AppError::InvalidParams(format!("Invalid farm: {e}")))?;

    let amount_float: f64 = params.amount.parse()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?;
    // LP tokens typically have 6 decimals on Meteora.
    let amount = (amount_float * 1_000_000.0) as u64;

    let user_staking = farm_user_pda(&farm, &user);
    let reward_vault = farm_reward_vault_pda(&farm);
    let event_auth = Pubkey::find_program_address(&[b"__event_authority"], &farm_program()).0;

    let ixs = vec![Instruction {
        program_id: farm_program(),
        accounts: vec![
            AccountMeta::new(farm, false),
            AccountMeta::new(user_staking, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new(reward_vault, false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(farm_program(), false),
        ],
        data: ix_data(
            {
                let mut h = Sha256::new();
                h.update("global:deposit");
                h.finalize()[..8].try_into().unwrap()
            },
            &FarmDepositArgs { amount },
        ),
    }];

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_stake".into(),
            description: format!("Stake {} LP tokens in Meteora farm {}…", params.amount,
                &params.farm[..8.min(params.farm.len())]),
            estimated_fee: "~0.002 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "farm": params.farm, "amount": params.amount }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Unstake LP tokens from a Meteora farm.
///
/// Instruction: `withdraw` on the Meteora Farm program.
pub async fn build_meteora_unstake(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraUnstakeParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_unstake_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let farm = Pubkey::from_str(&params.farm)
        .map_err(|e| AppError::InvalidParams(format!("Invalid farm: {e}")))?;

    let amount_float: f64 = params.amount.parse()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?;
    let amount = (amount_float * 1_000_000.0) as u64;

    let user_staking = farm_user_pda(&farm, &user);
    let reward_vault = farm_reward_vault_pda(&farm);
    let event_auth = Pubkey::find_program_address(&[b"__event_authority"], &farm_program()).0;

    let ixs = vec![Instruction {
        program_id: farm_program(),
        accounts: vec![
            AccountMeta::new(farm, false),
            AccountMeta::new(user_staking, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new(reward_vault, false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(farm_program(), false),
        ],
        data: ix_data(
            {
                let mut h = Sha256::new();
                h.update("global:withdraw");
                h.finalize()[..8].try_into().unwrap()
            },
            &FarmWithdrawArgs { amount },
        ),
    }];

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_unstake".into(),
            description: format!("Unstake {} LP tokens from Meteora farm {}…", params.amount,
                &params.farm[..8.min(params.farm.len())]),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "farm": params.farm, "amount": params.amount }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Harvest accumulated rewards from a Meteora farm.
///
/// Instruction: `claim_reward` on the Meteora Farm program.
pub async fn build_meteora_harvest(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraHarvestParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_harvest_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let farm = Pubkey::from_str(&params.farm)
        .map_err(|e| AppError::InvalidParams(format!("Invalid farm: {e}")))?;

    let user_staking = farm_user_pda(&farm, &user);
    let reward_vault = farm_reward_vault_pda(&farm);
    let event_auth = Pubkey::find_program_address(&[b"__event_authority"], &farm_program()).0;

    let ixs = vec![Instruction {
        program_id: farm_program(),
        accounts: vec![
            AccountMeta::new(farm, false),
            AccountMeta::new(user_staking, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new(reward_vault, false),
            AccountMeta::new_readonly(token_program(), false),
            AccountMeta::new_readonly(event_auth, false),
            AccountMeta::new_readonly(farm_program(), false),
        ],
        data: {
            let mut h = Sha256::new();
            h.update("global:claim_reward");
            h.finalize()[..8].to_vec()
        },
    }];

    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_harvest".into(),
            description: format!("Harvest rewards from Meteora farm {}…",
                &params.farm[..8.min(params.farm.len())]),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "farm": params.farm }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Utility Helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Convert a human-readable amount string to base units (e.g. "1.5" with decimals=6 → 1_500_000).
fn parse_to_base_units(amount_str: &str, decimals: u8) -> Result<u64, AppError> {
    let f: f64 = amount_str.parse()
        .map_err(|_| AppError::InvalidParams(format!("Cannot parse amount '{amount_str}'")))?;
    if f < 0.0 {
        return Err(AppError::InvalidParams(format!("Amount '{amount_str}' must be non-negative")));
    }
    Ok((f * 10_f64.powi(decimals as i32)) as u64)
}

/// Resolve a bin range from either direct bin IDs or price bounds.
fn resolve_bin_range(
    min_bin_id: Option<i32>,
    max_bin_id: Option<i32>,
    min_price: Option<f64>,
    max_price: Option<f64>,
    bin_step: u32,
) -> Result<(i32, i32), AppError> {
    if let (Some(lower), Some(upper)) = (min_bin_id, max_bin_id) {
        if lower >= upper {
            return Err(AppError::InvalidParams("minBinId must be less than maxBinId".into()));
        }
        return Ok((lower, upper));
    }
    if let (Some(min_p), Some(max_p)) = (min_price, max_price) {
        if min_p <= 0.0 || max_p <= 0.0 || min_p >= max_p {
            return Err(AppError::InvalidParams("minPrice must be positive and less than maxPrice".into()));
        }
        let lower = price_to_bin_id(min_p, bin_step);
        let upper = price_to_bin_id(max_p, bin_step) + 1; // +1 to make upper exclusive
        if lower >= upper {
            return Err(AppError::InvalidParams("Price range maps to empty bin range".into()));
        }
        return Ok((lower, upper));
    }
    Err(AppError::InvalidParams(
        "Provide either (minBinId + maxBinId) or (minPrice + maxPrice)".into(),
    ))
}

// ─────────────────────────────────────────────────────────────────────────────
// Generic HTTP Helpers (GET / POST returning serde_json::Value)
// ─────────────────────────────────────────────────────────────────────────────

async fn meteora_get(http: &reqwest::Client, url: &str) -> Result<serde_json::Value, AppError> {
    let resp = http
        .get(url)
        .header("Accept", "application/json")
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Meteora GET {url}: {e}")))?;
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Meteora GET parse {url}: {e}")))
}

async fn meteora_post(
    http: &reqwest::Client,
    url: &str,
    body: &serde_json::Value,
) -> Result<serde_json::Value, AppError> {
    let resp = http
        .post(url)
        .json(body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Meteora POST {url}: {e}")))?;
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Meteora POST parse {url}: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// New Param Structs — GET Query Actions
// ─────────────────────────────────────────────────────────────────────────────

// ── DLMM ─────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDlmmGetPairsParams {
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
    #[serde(default)]
    pub query: Option<String>,
    /// Format: "field:asc" or "field:desc", e.g. "volume_24h:desc"
    #[serde(default)]
    pub sort_by: Option<String>,
    #[serde(default)]
    pub filter_by: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDlmmGetPairParams {
    pub address: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDlmmGetUserPositionsParams {
    #[serde(default)]
    pub wallet: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDlmmGetActiveBinParams {
    pub address: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDlmmGetPoolGroupsParams {
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    pub sort_by: Option<String>,
    #[serde(default)]
    pub filter_by: Option<String>,
    /// Time window for volume metric, e.g. "volume_24h" or "volume_7d"
    #[serde(default)]
    pub volume_tw: Option<String>,
    /// Time window for fee/TVL ratio, e.g. "fee_tvl_ratio_24h"
    #[serde(default)]
    pub fee_tvl_ratio_tw: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDlmmGetPoolGroupParams {
    /// Token pair identifier returned by get_pool_groups, e.g. "MINTA-MINTB"
    pub lexical_order_mints: String,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
    #[serde(default)]
    pub sort_by: Option<String>,
    #[serde(default)]
    pub filter_by: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDlmmGetPoolOhlcvParams {
    pub address: String,
    /// Candle interval: "5m", "30m", "1h", "2h", "4h", "12h", "24h"
    #[serde(default)]
    pub timeframe: Option<String>,
    #[serde(default)]
    pub start_time: Option<i64>,
    #[serde(default)]
    pub end_time: Option<i64>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDlmmGetPoolVolumeHistoryParams {
    pub address: String,
    /// Time bucket: "5m", "30m", "1h", "2h", "4h", "12h", "24h"
    #[serde(default)]
    pub timeframe: Option<String>,
    #[serde(default)]
    pub start_time: Option<i64>,
    #[serde(default)]
    pub end_time: Option<i64>,
}

// ── DAMM v2 ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2GetPoolsParams {
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    pub sort_by: Option<String>,
    #[serde(default)]
    pub filter_by: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2GetPoolGroupsParams {
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    pub sort_by: Option<String>,
    #[serde(default)]
    pub filter_by: Option<String>,
    #[serde(default)]
    pub volume_tw: Option<String>,
    #[serde(default)]
    pub fee_tvl_ratio_tw: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2GetPoolGroupParams {
    pub lexical_order_mints: String,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    pub sort_by: Option<String>,
    #[serde(default)]
    pub filter_by: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2GetPoolParams {
    pub address: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2GetPoolOhlcvParams {
    pub address: String,
    /// Candle interval: "5m", "30m", "1h", "2h", "4h", "12h", "24h"
    #[serde(default)]
    pub timeframe: Option<String>,
    #[serde(default)]
    pub start_time: Option<i64>,
    #[serde(default)]
    pub end_time: Option<i64>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2GetPoolVolumeHistoryParams {
    pub address: String,
    /// Candle interval: "5m", "30m", "1h", "2h", "4h", "12h", "24h"
    #[serde(default)]
    pub timeframe: Option<String>,
    #[serde(default)]
    pub start_time: Option<i64>,
    #[serde(default)]
    pub end_time: Option<i64>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2GetProtocolMetricsParams {}

// ── DAMM v1 ──────────────────────────────────────────────────────────────────

/// GET /pools — filter & list DAMM v1 pools
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1GetPoolsParams {
    /// One or more pool addresses (comma-separated)
    #[serde(default)]
    pub address: Option<String>,
    /// Include pools with unknown tokens
    #[serde(default)]
    pub unknown: Option<bool>,
    /// Filter by pool type: "dynamic", "multitoken", "lst", "farms"
    #[serde(default)]
    pub pool_type: Option<String>,
    /// Only monitored pools
    #[serde(default)]
    pub is_monitoring: Option<bool>,
    /// Hide pools with TVL below this value (USD)
    #[serde(default)]
    pub hide_low_tvl: Option<f64>,
    /// Hide pools with very low APR
    #[serde(default)]
    pub hide_low_apr: Option<bool>,
    /// Filter by launchpad identifier (comma-separated)
    #[serde(default)]
    pub launchpad: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1GetPoolConfigsParams {}

/// GET /pools/search — paginated, filterable, sortable pool search
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1SearchPoolsParams {
    /// Page number (0-based, required)
    pub page: u32,
    /// Page size (required)
    pub size: u32,
    /// Search/filter string (token name, symbol, or address)
    #[serde(default)]
    pub filter: Option<String>,
    /// Sort metric: "tvl", "volume", "fee_tvl_ratio", "l_m"
    #[serde(default)]
    pub sort_key: Option<String>,
    /// Sort direction: "asc" or "desc"
    #[serde(default)]
    pub order_by: Option<String>,
    /// Pool addresses to force to the top (comma-separated)
    #[serde(default)]
    pub pools_to_top: Option<String>,
    #[serde(default)]
    pub unknown: Option<bool>,
    /// "dynamic", "multitoken", "lst", "farms"
    #[serde(default)]
    pub pool_type: Option<String>,
    #[serde(default)]
    pub is_monitoring: Option<bool>,
    #[serde(default)]
    pub hide_low_tvl: Option<f64>,
    #[serde(default)]
    pub hide_low_apr: Option<bool>,
    /// Allowlist of token mints to include (comma-separated)
    #[serde(default)]
    pub include_token_mints: Option<String>,
    /// Allowlist of token pair strings "mintA-mintB" (comma-separated)
    #[serde(default)]
    pub include_pool_token_pairs: Option<String>,
    #[serde(default)]
    pub launchpad: Option<String>,
}

/// GET /farms — list all DAMM v1 pools that have active farms (no params)
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1GetFarmsParams {}

/// GET /pools-metrics — protocol-level aggregate metrics (no params)
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1GetPoolsMetricsParams {}

/// GET /alpha-vault — list alpha vaults with optional filters
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1GetAlphaVaultsParams {
    /// Filter by vault address (comma-separated)
    #[serde(default)]
    pub vault_address: Option<String>,
    /// Filter by pool address (comma-separated)
    #[serde(default)]
    pub pool_address: Option<String>,
    /// Filter by base mint (comma-separated)
    #[serde(default)]
    pub base_mint: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1GetAlphaVaultConfigsParams {}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1GetPoolsByVaultLpParams {
    pub a_vault_lp: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1GetFeeConfigParams {
    pub config_address: String,
}

// ── Stake2Earn ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2EGetAnalyticsParams {}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2EGetAllVaultsParams {}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2EFilterVaultsParams {
    /// Comma-separated pool addresses to filter by (max 100)
    #[serde(default)]
    pub pool_address: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2EGetVaultParams {
    pub vault_address: String,
}

// ── Dynamic Vault ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraVaultGetInfoParams {}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraVaultGetAddressesParams {}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraVaultGetStateParams {
    pub token_mint: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraVaultGetApyParams {
    pub token_mint: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraVaultGetApyHistoryParams {
    pub token_mint: String,
    pub start_timestamp: i64,
    pub end_timestamp: i64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraVaultGetVirtualPriceParams {
    pub token_mint: String,
    pub strategy: String,
}

// ─────────────────────────────────────────────────────────────────────────────
// New Param Structs — TX Building Actions
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1SwapParams {
    pub input_mint: String,
    pub output_mint: String,
    pub amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    /// Optional: specific DAMM v1 pool address (informational; Jupiter routes automatically).
    #[serde(default)]
    #[allow(dead_code)]
    pub pool: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1DepositParams {
    pub pool: String,
    pub token_a_amount: String,
    pub token_b_amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV1WithdrawParams {
    pub pool: String,
    pub lp_amount: String,
    #[serde(default)]
    pub min_a_amount: Option<String>,
    #[serde(default)]
    pub min_b_amount: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2SwapParams {
    pub input_mint: String,
    pub output_mint: String,
    pub amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    /// Optional: specific DAMM v2 pool address (informational; Jupiter routes automatically).
    #[serde(default)]
    #[allow(dead_code)]
    pub pool: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2AddLiquidityParams {
    pub pool: String,
    pub max_amount_a: String,
    pub max_amount_b: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraDammV2RemoveLiquidityParams {
    pub pool: String,
    pub lp_amount: String,
    pub position_nft: String,
    #[serde(default)]
    pub min_amount_a: Option<String>,
    #[serde(default)]
    pub min_amount_b: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraVaultDepositParams {
    pub token_mint: String,
    pub amount: String,
    #[serde(default)]
    pub affiliate_id: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraVaultWithdrawParams {
    pub token_mint: String,
    pub unmint_amount: String,
    #[serde(default)]
    pub affiliate_id: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2EStakeParams {
    pub vault: String,
    pub amount: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2EUnstakeParams {
    pub vault: String,
    pub amount: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2EClaimFeeParams {
    pub vault: String,
    #[serde(default)]
    pub max_amount: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2ECancelUnstakeParams {
    pub vault: String,
    pub escrow: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeteoraS2EWithdrawParams {
    pub vault: String,
    pub escrow: String,
}

// ─────────────────────────────────────────────────────────────────────────────
// Validators — GET Query Actions (minimal, mostly check required string params)
// ─────────────────────────────────────────────────────────────────────────────

pub fn validate_meteora_dlmm_get_pairs_params(_p: &MeteoraDlmmGetPairsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dlmm_get_pair_params(p: &MeteoraDlmmGetPairParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.address)
        .map_err(|_| AppError::InvalidParams(format!("address '{}' is not a valid pubkey", p.address)))?;
    Ok(())
}

pub fn validate_meteora_dlmm_get_user_positions_params(p: &MeteoraDlmmGetUserPositionsParams) -> Result<(), AppError> {
    if let Some(ref w) = p.wallet {
        Pubkey::from_str(w)
            .map_err(|_| AppError::InvalidParams(format!("wallet '{}' is not a valid pubkey", w)))?;
    }
    Ok(())
}

pub fn validate_meteora_dlmm_get_active_bin_params(p: &MeteoraDlmmGetActiveBinParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.address)
        .map_err(|_| AppError::InvalidParams(format!("address '{}' is not a valid pubkey", p.address)))?;
    Ok(())
}

pub fn validate_meteora_dlmm_get_pool_groups_params(_p: &MeteoraDlmmGetPoolGroupsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dlmm_get_pool_group_params(p: &MeteoraDlmmGetPoolGroupParams) -> Result<(), AppError> {
    if p.lexical_order_mints.is_empty() {
        return Err(AppError::InvalidParams("lexicalOrderMints is required".into()));
    }
    Ok(())
}

pub fn validate_meteora_dlmm_get_pool_ohlcv_params(p: &MeteoraDlmmGetPoolOhlcvParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.address)
        .map_err(|_| AppError::InvalidParams(format!("address '{}' is not a valid pubkey", p.address)))?;
    if let Some(ref tf) = p.timeframe {
        let valid = ["5m", "30m", "1h", "2h", "4h", "12h", "24h"];
        if !valid.contains(&tf.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "timeframe '{}' is invalid; valid values: {:?}", tf, valid
            )));
        }
    }
    Ok(())
}

/// GET /stats/protocol_metrics — no parameters
#[derive(Debug, Clone, Deserialize)]
pub struct MeteoraDlmmGetProtocolStatsParams {}

pub fn validate_meteora_dlmm_get_protocol_stats_params(_p: &MeteoraDlmmGetProtocolStatsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dlmm_get_pool_volume_history_params(p: &MeteoraDlmmGetPoolVolumeHistoryParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.address)
        .map_err(|_| AppError::InvalidParams(format!("address '{}' is not a valid pubkey", p.address)))?;
    if let Some(ref tf) = p.timeframe {
        let valid = ["5m", "30m", "1h", "2h", "4h", "12h", "24h"];
        if !valid.contains(&tf.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "timeframe '{}' is invalid; valid values: {:?}", tf, valid
            )));
        }
    }
    Ok(())
}

pub fn validate_meteora_dammv2_get_pools_params(_p: &MeteoraDammV2GetPoolsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dammv2_get_pool_groups_params(_p: &MeteoraDammV2GetPoolGroupsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dammv2_get_pool_group_params(p: &MeteoraDammV2GetPoolGroupParams) -> Result<(), AppError> {
    if p.lexical_order_mints.is_empty() {
        return Err(AppError::InvalidParams("lexicalOrderMints is required".into()));
    }
    Ok(())
}

pub fn validate_meteora_dammv2_get_pool_params(p: &MeteoraDammV2GetPoolParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.address)
        .map_err(|_| AppError::InvalidParams(format!("address '{}' is not a valid pubkey", p.address)))?;
    Ok(())
}

pub fn validate_meteora_dammv2_get_pool_ohlcv_params(p: &MeteoraDammV2GetPoolOhlcvParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.address)
        .map_err(|_| AppError::InvalidParams(format!("address '{}' is not a valid pubkey", p.address)))?;
    if let Some(ref tf) = p.timeframe {
        let valid = ["5m", "30m", "1h", "2h", "4h", "12h", "24h"];
        if !valid.contains(&tf.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "timeframe '{}' is invalid; valid values: {:?}", tf, valid
            )));
        }
    }
    Ok(())
}

pub fn validate_meteora_dammv2_get_pool_volume_history_params(p: &MeteoraDammV2GetPoolVolumeHistoryParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.address)
        .map_err(|_| AppError::InvalidParams(format!("address '{}' is not a valid pubkey", p.address)))?;
    if let Some(ref tf) = p.timeframe {
        let valid = ["5m", "30m", "1h", "2h", "4h", "12h", "24h"];
        if !valid.contains(&tf.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "timeframe '{}' is invalid; valid values: {:?}", tf, valid
            )));
        }
    }
    Ok(())
}

pub fn validate_meteora_dammv2_get_protocol_metrics_params(_p: &MeteoraDammV2GetProtocolMetricsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dammv1_get_pools_params(p: &MeteoraDammV1GetPoolsParams) -> Result<(), AppError> {
    if let Some(ref pt) = p.pool_type {
        let valid = ["dynamic", "multitoken", "lst", "farms"];
        if !valid.contains(&pt.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "poolType '{}' is invalid; valid values: {:?}", pt, valid
            )));
        }
    }
    Ok(())
}

pub fn validate_meteora_dammv1_get_pool_configs_params(_p: &MeteoraDammV1GetPoolConfigsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dammv1_search_pools_params(p: &MeteoraDammV1SearchPoolsParams) -> Result<(), AppError> {
    if let Some(ref sk) = p.sort_key {
        let valid = ["tvl", "volume", "fee_tvl_ratio", "l_m"];
        if !valid.contains(&sk.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "sortKey '{}' is invalid; valid values: {:?}", sk, valid
            )));
        }
    }
    if let Some(ref ob) = p.order_by {
        let valid = ["asc", "desc"];
        if !valid.contains(&ob.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "orderBy '{}' is invalid; valid values: asc, desc", ob
            )));
        }
    }
    if let Some(ref pt) = p.pool_type {
        let valid = ["dynamic", "multitoken", "lst", "farms"];
        if !valid.contains(&pt.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "poolType '{}' is invalid; valid values: {:?}", pt, valid
            )));
        }
    }
    Ok(())
}

pub fn validate_meteora_dammv1_get_farms_params(_p: &MeteoraDammV1GetFarmsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dammv1_get_pools_metrics_params(_p: &MeteoraDammV1GetPoolsMetricsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dammv1_get_alpha_vaults_params(_p: &MeteoraDammV1GetAlphaVaultsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dammv1_get_alpha_vault_configs_params(_p: &MeteoraDammV1GetAlphaVaultConfigsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_dammv1_get_pools_by_vault_lp_params(p: &MeteoraDammV1GetPoolsByVaultLpParams) -> Result<(), AppError> {
    if p.a_vault_lp.is_empty() {
        return Err(AppError::InvalidParams("aVaultLp is required".into()));
    }
    Ok(())
}

pub fn validate_meteora_dammv1_get_fee_config_params(p: &MeteoraDammV1GetFeeConfigParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.config_address)
        .map_err(|_| AppError::InvalidParams(format!("configAddress '{}' is not a valid pubkey", p.config_address)))?;
    Ok(())
}

pub fn validate_meteora_s2e_get_analytics_params(_p: &MeteoraS2EGetAnalyticsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_s2e_get_all_vaults_params(_p: &MeteoraS2EGetAllVaultsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_s2e_filter_vaults_params(p: &MeteoraS2EFilterVaultsParams) -> Result<(), AppError> {
    if let Some(ref addrs) = p.pool_address {
        let count = addrs.split(',').filter(|s| !s.trim().is_empty()).count();
        if count > 100 {
            return Err(AppError::InvalidParams(
                format!("pool_address: maximum 100 addresses allowed, got {count}")
            ));
        }
    }
    Ok(())
}

pub fn validate_meteora_s2e_get_vault_params(p: &MeteoraS2EGetVaultParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.vault_address)
        .map_err(|_| AppError::InvalidParams(format!("vaultAddress '{}' is not a valid pubkey", p.vault_address)))?;
    Ok(())
}

pub fn validate_meteora_vault_get_info_params(_p: &MeteoraVaultGetInfoParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_vault_get_addresses_params(_p: &MeteoraVaultGetAddressesParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_meteora_vault_get_state_params(p: &MeteoraVaultGetStateParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.token_mint)
        .map_err(|_| AppError::InvalidParams(format!("tokenMint '{}' is not a valid pubkey", p.token_mint)))?;
    Ok(())
}

pub fn validate_meteora_vault_get_apy_params(p: &MeteoraVaultGetApyParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.token_mint)
        .map_err(|_| AppError::InvalidParams(format!("tokenMint '{}' is not a valid pubkey", p.token_mint)))?;
    Ok(())
}

pub fn validate_meteora_vault_get_apy_history_params(p: &MeteoraVaultGetApyHistoryParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.token_mint)
        .map_err(|_| AppError::InvalidParams(format!("tokenMint '{}' is not a valid pubkey", p.token_mint)))?;
    if p.start_timestamp <= 0 {
        return Err(AppError::InvalidParams("startTimestamp must be a positive Unix timestamp (seconds)".into()));
    }
    if p.start_timestamp >= p.end_timestamp {
        return Err(AppError::InvalidParams(
            format!("startTimestamp ({}) must be less than endTimestamp ({})", p.start_timestamp, p.end_timestamp)
        ));
    }
    Ok(())
}

pub fn validate_meteora_vault_get_virtual_price_params(p: &MeteoraVaultGetVirtualPriceParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.token_mint)
        .map_err(|_| AppError::InvalidParams(format!("tokenMint '{}' is not a valid pubkey", p.token_mint)))?;
    Pubkey::from_str(&p.strategy)
        .map_err(|_| AppError::InvalidParams(format!("strategy '{}' is not a valid pubkey", p.strategy)))?;
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Validators — TX Building Actions
// ─────────────────────────────────────────────────────────────────────────────

pub fn validate_meteora_dammv1_swap_params(p: &MeteoraDammV1SwapParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.input_mint)
        .map_err(|_| AppError::InvalidParams(format!("inputMint '{}' is not valid", p.input_mint)))?;
    Pubkey::from_str(&p.output_mint)
        .map_err(|_| AppError::InvalidParams(format!("outputMint '{}' is not valid", p.output_mint)))?;
    if p.input_mint == p.output_mint {
        return Err(AppError::InvalidParams("inputMint and outputMint must differ".into()));
    }
    let amt: f64 = p.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a positive number".into()))?;
    if amt <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    if let Some(s) = p.slippage_bps {
        if s > 5000 { return Err(AppError::InvalidParams("slippageBps must be ≤ 5000".into())); }
    }
    Ok(())
}

pub fn validate_meteora_dammv1_deposit_params(p: &MeteoraDammV1DepositParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.pool)
        .map_err(|_| AppError::InvalidParams(format!("pool '{}' is not valid", p.pool)))?;
    let a: f64 = p.token_a_amount.parse()
        .map_err(|_| AppError::InvalidParams("tokenAAmount must be a number".into()))?;
    let b: f64 = p.token_b_amount.parse()
        .map_err(|_| AppError::InvalidParams("tokenBAmount must be a number".into()))?;
    if a < 0.0 || b < 0.0 {
        return Err(AppError::InvalidParams("Amounts must be non-negative".into()));
    }
    if a == 0.0 && b == 0.0 {
        return Err(AppError::InvalidParams("At least one amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_dammv1_withdraw_params(p: &MeteoraDammV1WithdrawParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.pool)
        .map_err(|_| AppError::InvalidParams(format!("pool '{}' is not valid", p.pool)))?;
    let lp: f64 = p.lp_amount.parse()
        .map_err(|_| AppError::InvalidParams("lpAmount must be a number".into()))?;
    if lp <= 0.0 {
        return Err(AppError::InvalidParams("lpAmount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_dammv2_swap_params(p: &MeteoraDammV2SwapParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.input_mint)
        .map_err(|_| AppError::InvalidParams(format!("inputMint '{}' is not valid", p.input_mint)))?;
    Pubkey::from_str(&p.output_mint)
        .map_err(|_| AppError::InvalidParams(format!("outputMint '{}' is not valid", p.output_mint)))?;
    if p.input_mint == p.output_mint {
        return Err(AppError::InvalidParams("inputMint and outputMint must differ".into()));
    }
    let amt: f64 = p.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a positive number".into()))?;
    if amt <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    if let Some(s) = p.slippage_bps {
        if s > 5000 { return Err(AppError::InvalidParams("slippageBps must be ≤ 5000".into())); }
    }
    Ok(())
}

pub fn validate_meteora_dammv2_add_liquidity_params(p: &MeteoraDammV2AddLiquidityParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.pool)
        .map_err(|_| AppError::InvalidParams(format!("pool '{}' is not valid", p.pool)))?;
    let a: f64 = p.max_amount_a.parse()
        .map_err(|_| AppError::InvalidParams("maxAmountA must be a number".into()))?;
    let b: f64 = p.max_amount_b.parse()
        .map_err(|_| AppError::InvalidParams("maxAmountB must be a number".into()))?;
    if a < 0.0 || b < 0.0 {
        return Err(AppError::InvalidParams("Amounts must be non-negative".into()));
    }
    if a == 0.0 && b == 0.0 {
        return Err(AppError::InvalidParams("At least one amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_dammv2_remove_liquidity_params(p: &MeteoraDammV2RemoveLiquidityParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.pool)
        .map_err(|_| AppError::InvalidParams(format!("pool '{}' is not valid", p.pool)))?;
    Pubkey::from_str(&p.position_nft)
        .map_err(|_| AppError::InvalidParams(format!("positionNft '{}' is not valid", p.position_nft)))?;
    let lp: f64 = p.lp_amount.parse()
        .map_err(|_| AppError::InvalidParams("lpAmount must be a number".into()))?;
    if lp <= 0.0 {
        return Err(AppError::InvalidParams("lpAmount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_vault_deposit_params(p: &MeteoraVaultDepositParams) -> Result<(), AppError> {
    if p.token_mint.is_empty() {
        return Err(AppError::InvalidParams("tokenMint is required".into()));
    }
    let amt: f64 = p.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amt <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_vault_withdraw_params(p: &MeteoraVaultWithdrawParams) -> Result<(), AppError> {
    if p.token_mint.is_empty() {
        return Err(AppError::InvalidParams("tokenMint is required".into()));
    }
    let amt: f64 = p.unmint_amount.parse()
        .map_err(|_| AppError::InvalidParams("unmintAmount must be a number".into()))?;
    if amt <= 0.0 {
        return Err(AppError::InvalidParams("unmintAmount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_s2e_stake_params(p: &MeteoraS2EStakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.vault)
        .map_err(|_| AppError::InvalidParams(format!("vault '{}' is not valid", p.vault)))?;
    let amt: f64 = p.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amt <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_s2e_unstake_params(p: &MeteoraS2EUnstakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.vault)
        .map_err(|_| AppError::InvalidParams(format!("vault '{}' is not valid", p.vault)))?;
    let amt: f64 = p.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amt <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_meteora_s2e_claim_fee_params(p: &MeteoraS2EClaimFeeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.vault)
        .map_err(|_| AppError::InvalidParams(format!("vault '{}' is not valid", p.vault)))?;
    Ok(())
}

pub fn validate_meteora_s2e_cancel_unstake_params(p: &MeteoraS2ECancelUnstakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.vault)
        .map_err(|_| AppError::InvalidParams(format!("vault '{}' is not valid", p.vault)))?;
    Pubkey::from_str(&p.escrow)
        .map_err(|_| AppError::InvalidParams(format!("escrow '{}' is not valid", p.escrow)))?;
    Ok(())
}

pub fn validate_meteora_s2e_withdraw_params(p: &MeteoraS2EWithdrawParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.vault)
        .map_err(|_| AppError::InvalidParams(format!("vault '{}' is not valid", p.vault)))?;
    Pubkey::from_str(&p.escrow)
        .map_err(|_| AppError::InvalidParams(format!("escrow '{}' is not valid", p.escrow)))?;
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Build Functions — GET Query Actions
// ─────────────────────────────────────────────────────────────────────────────

// ── DLMM ─────────────────────────────────────────────────────────────────────

pub async fn build_meteora_dlmm_get_pairs(
    http: &reqwest::Client,
    params: &MeteoraDlmmGetPairsParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DLMM_API}/pools?");
    if let Some(n) = params.page           { url.push_str(&format!("page={n}&")); }
    if let Some(n) = params.page_size      { url.push_str(&format!("page_size={}&", n.min(1000))); }
    if let Some(ref q) = params.query      { url.push_str(&format!("query={q}&")); }
    if let Some(ref s) = params.sort_by    { url.push_str(&format!("sort_by={s}&")); }
    if let Some(ref f) = params.filter_by  { url.push_str(&format!("filter_by={f}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_pairs".into(),
            description: "Meteora DLMM pair list".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dlmm_get_pair(
    http: &reqwest::Client,
    params: &MeteoraDlmmGetPairParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{DLMM_API}/pools/{}", params.address);
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_pair".into(),
            description: format!("Meteora DLMM pair: {}…", &params.address[..8.min(params.address.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dlmm_get_user_positions(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &MeteoraDlmmGetUserPositionsParams,
) -> Result<BuildResponse, AppError> {
    let wallet = params.wallet.as_deref().unwrap_or(user_pubkey_str);
    // New API: /portfolio/open?user=... returns the user's open DLMM positions.
    // Legacy /position/user/{wallet} was removed.
    let url = format!("{DLMM_API}/portfolio/open?user={wallet}");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_user_positions".into(),
            description: format!("Meteora DLMM positions for {}…", &wallet[..8.min(wallet.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dlmm_get_active_bin(
    http: &reqwest::Client,
    params: &MeteoraDlmmGetActiveBinParams,
) -> Result<BuildResponse, AppError> {
    // New API removed the dedicated /lb_pair/{x}/active_bin endpoint. The pool
    // detail at /pools/{x} carries `current_price` and `pool_config.bin_step`,
    // from which the active bin id derives:
    //   bin_id = round( log(current_price) / log(1 + bin_step / 10000) )
    // We surface the full pool detail and let the caller (LLM or downstream
    // logic) pull the relevant fields. This keeps the query name stable for
    // existing prompts.
    let url = format!("{DLMM_API}/pools/{}", params.address);
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_active_bin".into(),
            description: format!("Meteora DLMM active bin for {}…", &params.address[..8.min(params.address.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dlmm_get_pool_groups(
    http: &reqwest::Client,
    params: &MeteoraDlmmGetPoolGroupsParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DLMM_API}/pools/groups?");
    if let Some(n) = params.page               { url.push_str(&format!("page={n}&")); }
    if let Some(n) = params.page_size          { url.push_str(&format!("page_size={}&", n.min(100))); }
    if let Some(ref q) = params.query          { url.push_str(&format!("query={q}&")); }
    if let Some(ref s) = params.sort_by        { url.push_str(&format!("sort_by={s}&")); }
    if let Some(ref f) = params.filter_by      { url.push_str(&format!("filter_by={f}&")); }
    if let Some(ref v) = params.volume_tw      { url.push_str(&format!("volume_tw={v}&")); }
    if let Some(ref r) = params.fee_tvl_ratio_tw { url.push_str(&format!("fee_tvl_ratio_tw={r}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_pool_groups".into(),
            description: "Meteora DLMM pool groups".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dlmm_get_pool_group(
    http: &reqwest::Client,
    params: &MeteoraDlmmGetPoolGroupParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DLMM_API}/pools/groups/{}?", params.lexical_order_mints);
    if let Some(n) = params.page          { url.push_str(&format!("page={n}&")); }
    if let Some(n) = params.page_size     { url.push_str(&format!("page_size={}&", n.min(100))); }
    if let Some(ref s) = params.sort_by   { url.push_str(&format!("sort_by={s}&")); }
    if let Some(ref f) = params.filter_by { url.push_str(&format!("filter_by={f}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_pool_group".into(),
            description: format!("Meteora DLMM pool group: {}", params.lexical_order_mints),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dlmm_get_pool_ohlcv(
    http: &reqwest::Client,
    params: &MeteoraDlmmGetPoolOhlcvParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DLMM_API}/pools/{}/ohlcv?", params.address);
    if let Some(ref tf) = params.timeframe { url.push_str(&format!("timeframe={tf}&")); }
    if let Some(t) = params.start_time    { url.push_str(&format!("start_time={t}&")); }
    if let Some(t) = params.end_time      { url.push_str(&format!("end_time={t}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_pool_ohlcv".into(),
            description: format!("Meteora DLMM OHLCV for {}…", &params.address[..8.min(params.address.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dlmm_get_pool_volume_history(
    http: &reqwest::Client,
    params: &MeteoraDlmmGetPoolVolumeHistoryParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DLMM_API}/pools/{}/volume/history?", params.address);
    if let Some(ref tf) = params.timeframe { url.push_str(&format!("timeframe={tf}&")); }
    if let Some(t) = params.start_time    { url.push_str(&format!("start_time={t}&")); }
    if let Some(t) = params.end_time      { url.push_str(&format!("end_time={t}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_pool_volume_history".into(),
            description: format!("Meteora DLMM volume history for {}…", &params.address[..8.min(params.address.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dlmm_get_protocol_stats(
    http: &reqwest::Client,
    _params: &MeteoraDlmmGetProtocolStatsParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{DLMM_STATS_API}/stats/protocol_metrics");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dlmm_get_protocol_stats".into(),
            description: "Meteora DLMM protocol-level stats".into(),
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
        data: Some(data),
    })
}

// ── DAMM v2 ──────────────────────────────────────────────────────────────────

pub async fn build_meteora_dammv2_get_pools(
    http: &reqwest::Client,
    params: &MeteoraDammV2GetPoolsParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DAMM_V2_API}/pools?");
    if let Some(n) = params.page           { url.push_str(&format!("page={n}&")); }
    if let Some(n) = params.page_size      { url.push_str(&format!("page_size={}&", n.min(1000))); }
    if let Some(ref q) = params.query      { url.push_str(&format!("query={q}&")); }
    if let Some(ref s) = params.sort_by    { url.push_str(&format!("sort_by={s}&")); }
    if let Some(ref f) = params.filter_by  { url.push_str(&format!("filter_by={f}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_get_pools".into(),
            description: "Meteora DAMM v2 pool list".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv2_get_pool_groups(
    http: &reqwest::Client,
    params: &MeteoraDammV2GetPoolGroupsParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DAMM_V2_API}/pools/groups?");
    if let Some(n) = params.page                { url.push_str(&format!("page={n}&")); }
    if let Some(n) = params.page_size           { url.push_str(&format!("page_size={n}&")); }
    if let Some(ref q) = params.query           { url.push_str(&format!("query={q}&")); }
    if let Some(ref s) = params.sort_by         { url.push_str(&format!("sort_by={s}&")); }
    if let Some(ref f) = params.filter_by       { url.push_str(&format!("filter_by={f}&")); }
    if let Some(ref v) = params.volume_tw       { url.push_str(&format!("volume_tw={v}&")); }
    if let Some(ref r) = params.fee_tvl_ratio_tw { url.push_str(&format!("fee_tvl_ratio_tw={r}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_get_pool_groups".into(),
            description: "Meteora DAMM v2 pool groups".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv2_get_pool_group(
    http: &reqwest::Client,
    params: &MeteoraDammV2GetPoolGroupParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DAMM_V2_API}/pools/groups/{}?", params.lexical_order_mints);
    if let Some(n) = params.page           { url.push_str(&format!("page={n}&")); }
    if let Some(n) = params.page_size      { url.push_str(&format!("page_size={n}&")); }
    if let Some(ref q) = params.query      { url.push_str(&format!("query={q}&")); }
    if let Some(ref s) = params.sort_by    { url.push_str(&format!("sort_by={s}&")); }
    if let Some(ref f) = params.filter_by  { url.push_str(&format!("filter_by={f}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_get_pool_group".into(),
            description: format!("Meteora DAMM v2 pool group: {}", params.lexical_order_mints),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv2_get_pool(
    http: &reqwest::Client,
    params: &MeteoraDammV2GetPoolParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{DAMM_V2_API}/pools/{}", params.address);
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_get_pool".into(),
            description: format!("Meteora DAMM v2 pool: {}…", &params.address[..8.min(params.address.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv2_get_pool_ohlcv(
    http: &reqwest::Client,
    params: &MeteoraDammV2GetPoolOhlcvParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv2_get_pool_ohlcv_params(params)?;
    let mut url = format!("{DAMM_V2_API}/pools/{}/ohlcv?", params.address);
    if let Some(ref tf) = params.timeframe  { url.push_str(&format!("timeframe={tf}&")); }
    if let Some(t) = params.start_time     { url.push_str(&format!("start_time={t}&")); }
    if let Some(t) = params.end_time       { url.push_str(&format!("end_time={t}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_get_pool_ohlcv".into(),
            description: format!("Meteora DAMM v2 OHLCV for {}…", &params.address[..8.min(params.address.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv2_get_pool_volume_history(
    http: &reqwest::Client,
    params: &MeteoraDammV2GetPoolVolumeHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv2_get_pool_volume_history_params(params)?;
    let mut url = format!("{DAMM_V2_API}/pools/{}/volume/history?", params.address);
    if let Some(ref tf) = params.timeframe  { url.push_str(&format!("timeframe={tf}&")); }
    if let Some(t) = params.start_time     { url.push_str(&format!("start_time={t}&")); }
    if let Some(t) = params.end_time       { url.push_str(&format!("end_time={t}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_get_pool_volume_history".into(),
            description: format!("Meteora DAMM v2 volume history for {}…", &params.address[..8.min(params.address.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv2_get_protocol_metrics(
    http: &reqwest::Client,
    _params: &MeteoraDammV2GetProtocolMetricsParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{DAMM_V2_API}/stats/protocol_metrics");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_get_protocol_metrics".into(),
            description: "Meteora DAMM v2 protocol metrics".into(),
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
        data: Some(data),
    })
}

// ── DAMM v1 ──────────────────────────────────────────────────────────────────

pub async fn build_meteora_dammv1_get_pools(
    http: &reqwest::Client,
    params: &MeteoraDammV1GetPoolsParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv1_get_pools_params(params)?;
    let mut url = format!("{DAMM_V1_API}/pools?");
    if let Some(ref a) = params.address          { url.push_str(&format!("address={a}&")); }
    if let Some(b) = params.unknown              { url.push_str(&format!("unknown={b}&")); }
    if let Some(ref pt) = params.pool_type       { url.push_str(&format!("pool_type={pt}&")); }
    if let Some(b) = params.is_monitoring        { url.push_str(&format!("is_monitoring={b}&")); }
    if let Some(v) = params.hide_low_tvl         { url.push_str(&format!("hide_low_tvl={v}&")); }
    if let Some(b) = params.hide_low_apr         { url.push_str(&format!("hide_low_apr={b}&")); }
    if let Some(ref l) = params.launchpad        { url.push_str(&format!("launchpad={l}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_get_pools".into(),
            description: "Meteora DAMM v1 pool list".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv1_get_pool_configs(
    http: &reqwest::Client,
    _params: &MeteoraDammV1GetPoolConfigsParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{DAMM_V1_API}/pool-configs");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_get_pool_configs".into(),
            description: "Meteora DAMM v1 pool configs".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv1_search_pools(
    http: &reqwest::Client,
    params: &MeteoraDammV1SearchPoolsParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv1_search_pools_params(params)?;
    let mut url = format!("{DAMM_V1_API}/pools/search?page={}&size={}&", params.page, params.size);
    if let Some(ref f) = params.filter              { url.push_str(&format!("filter={f}&")); }
    if let Some(ref sk) = params.sort_key           { url.push_str(&format!("sort_key={sk}&")); }
    if let Some(ref ob) = params.order_by           { url.push_str(&format!("order_by={ob}&")); }
    if let Some(ref pt) = params.pools_to_top       { url.push_str(&format!("pools_to_top={pt}&")); }
    if let Some(b) = params.unknown                 { url.push_str(&format!("unknown={b}&")); }
    if let Some(ref pt) = params.pool_type          { url.push_str(&format!("pool_type={pt}&")); }
    if let Some(b) = params.is_monitoring           { url.push_str(&format!("is_monitoring={b}&")); }
    if let Some(v) = params.hide_low_tvl            { url.push_str(&format!("hide_low_tvl={v}&")); }
    if let Some(b) = params.hide_low_apr            { url.push_str(&format!("hide_low_apr={b}&")); }
    if let Some(ref m) = params.include_token_mints { url.push_str(&format!("include_token_mints={m}&")); }
    if let Some(ref p) = params.include_pool_token_pairs { url.push_str(&format!("include_pool_token_pairs={p}&")); }
    if let Some(ref l) = params.launchpad           { url.push_str(&format!("launchpad={l}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_search_pools".into(),
            description: format!("Meteora DAMM v1 pool search: \"{}\"",
                params.filter.as_deref().unwrap_or("(all)")),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv1_get_farms(
    http: &reqwest::Client,
    params: &MeteoraDammV1GetFarmsParams,
) -> Result<BuildResponse, AppError> {
    let _ = params;
    let url = format!("{DAMM_V1_API}/farms");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_get_farms".into(),
            description: "Meteora DAMM v1 farms".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv1_get_pools_metrics(
    http: &reqwest::Client,
    params: &MeteoraDammV1GetPoolsMetricsParams,
) -> Result<BuildResponse, AppError> {
    let _ = params;
    let url = format!("{DAMM_V1_API}/pools-metrics");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_get_pools_metrics".into(),
            description: "Meteora DAMM v1 pools metrics".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv1_get_alpha_vaults(
    http: &reqwest::Client,
    params: &MeteoraDammV1GetAlphaVaultsParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{DAMM_V1_API}/alpha-vault?");
    if let Some(ref v) = params.vault_address { url.push_str(&format!("vault_address={v}&")); }
    if let Some(ref v) = params.pool_address { url.push_str(&format!("pool_address={v}&")); }
    if let Some(ref v) = params.base_mint { url.push_str(&format!("base_mint={v}&")); }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_get_alpha_vaults".into(),
            description: "Meteora DAMM v1 alpha vaults".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv1_get_alpha_vault_configs(
    http: &reqwest::Client,
    _params: &MeteoraDammV1GetAlphaVaultConfigsParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{DAMM_V1_API}/alpha-vault-configs");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_get_alpha_vault_configs".into(),
            description: "Meteora DAMM v1 alpha vault configs".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv1_get_pools_by_vault_lp(
    http: &reqwest::Client,
    params: &MeteoraDammV1GetPoolsByVaultLpParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{DAMM_V1_API}/get_pools_by_a_vault_lp");
    let body = serde_json::json!({ "a_vault_lp": params.a_vault_lp });
    let data = meteora_post(http, &url, &body).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_get_pools_by_vault_lp".into(),
            description: format!("Meteora DAMM v1 pools for vault LP: {}…", &params.a_vault_lp[..8.min(params.a_vault_lp.len())]),
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
        data: Some(data),
    })
}

pub async fn build_meteora_dammv1_get_fee_config(
    http: &reqwest::Client,
    params: &MeteoraDammV1GetFeeConfigParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{DAMM_V1_API}/fee-config/{}", params.config_address);
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_get_fee_config".into(),
            description: format!("Meteora DAMM v1 fee config: {}…", &params.config_address[..8.min(params.config_address.len())]),
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
        data: Some(data),
    })
}

// ── Stake2Earn ────────────────────────────────────────────────────────────────

pub async fn build_meteora_s2e_get_analytics(
    http: &reqwest::Client,
    _params: &MeteoraS2EGetAnalyticsParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{STAKE2EARN_API}/analytics/all");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_get_analytics".into(),
            description: "Meteora Stake2Earn analytics".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_s2e_get_all_vaults(
    http: &reqwest::Client,
    _params: &MeteoraS2EGetAllVaultsParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{STAKE2EARN_API}/vault/all");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_get_all_vaults".into(),
            description: "Meteora Stake2Earn vault list".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_s2e_filter_vaults(
    http: &reqwest::Client,
    params: &MeteoraS2EFilterVaultsParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{STAKE2EARN_API}/filter_vaults?");
    if let Some(ref addrs) = params.pool_address {
        for addr in addrs.split(',').map(str::trim).filter(|s| !s.is_empty()) {
            url.push_str(&format!("pool_address={addr}&"));
        }
    }
    let data = meteora_get(http, url.trim_end_matches(['&', '?'])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_filter_vaults".into(),
            description: "Meteora Stake2Earn vault filter".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_s2e_get_vault(
    http: &reqwest::Client,
    params: &MeteoraS2EGetVaultParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{STAKE2EARN_API}/get_one_vault/{}", params.vault_address);
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_get_vault".into(),
            description: format!("Meteora Stake2Earn vault: {}…", &params.vault_address[..8.min(params.vault_address.len())]),
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
        data: Some(data),
    })
}

// ── Dynamic Vault ─────────────────────────────────────────────────────────────

pub async fn build_meteora_vault_get_info(
    http: &reqwest::Client,
    _params: &MeteoraVaultGetInfoParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{VAULT_API}/vault_info");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_vault_get_info".into(),
            description: "Meteora Dynamic Vault info".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_vault_get_addresses(
    http: &reqwest::Client,
    _params: &MeteoraVaultGetAddressesParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{VAULT_API}/vault_addresses");
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_vault_get_addresses".into(),
            description: "Meteora Dynamic Vault addresses".into(),
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
        data: Some(data),
    })
}

pub async fn build_meteora_vault_get_state(
    http: &reqwest::Client,
    params: &MeteoraVaultGetStateParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{VAULT_API}/vault_state/{}", params.token_mint);
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_vault_get_state".into(),
            description: format!("Meteora Dynamic Vault state for {}", params.token_mint),
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
        data: Some(data),
    })
}

pub async fn build_meteora_vault_get_apy(
    http: &reqwest::Client,
    params: &MeteoraVaultGetApyParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{VAULT_API}/apy_state/{}", params.token_mint);
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_vault_get_apy".into(),
            description: format!("Meteora Dynamic Vault APY for {}", params.token_mint),
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
        data: Some(data),
    })
}

pub async fn build_meteora_vault_get_apy_history(
    http: &reqwest::Client,
    params: &MeteoraVaultGetApyHistoryParams,
) -> Result<BuildResponse, AppError> {
    let url = format!(
        "{VAULT_API}/apy_filter/{}/{}/{}",
        params.token_mint, params.start_timestamp, params.end_timestamp
    );
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_vault_get_apy_history".into(),
            description: format!("Meteora Dynamic Vault APY history for {}", params.token_mint),
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
        data: Some(data),
    })
}

pub async fn build_meteora_vault_get_virtual_price(
    http: &reqwest::Client,
    params: &MeteoraVaultGetVirtualPriceParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{VAULT_API}/virtual_price/{}/{}", params.token_mint, params.strategy);
    let data = meteora_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_vault_get_virtual_price".into(),
            description: format!("Meteora Dynamic Vault virtual price for {}", params.token_mint),
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
        data: Some(data),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Build Functions — TX Building Actions
// ─────────────────────────────────────────────────────────────────────────────

// ── DAMM v1 Swap (Jupiter-routed) ─────────────────────────────────────────────

pub async fn build_meteora_dammv1_swap(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraDammV1SwapParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv1_swap_params(params)?;
    let slippage = params.slippage_bps.unwrap_or(50);
    // Jupiter quote — no AMM restriction; Jupiter routes through DAMM v1 automatically.
    // Host: lite-api.jup.ag (the legacy quote-api.jup.ag/v6 hostname was retired
    // and now NXDOMAINs).
    let quote_url = format!(
        "https://lite-api.jup.ag/swap/v1/quote?inputMint={}&outputMint={}&amount={}&slippageBps={}&onlyDirectRoutes=false",
        params.input_mint, params.output_mint, params.amount, slippage
    );
    let quote: serde_json::Value = http.get(&quote_url)
        .send().await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter quote: {e}")))?
        .json().await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter quote parse: {e}")))?;

    let swap_body = serde_json::json!({
        "quoteResponse": quote,
        "userPublicKey": user_pubkey_str,
        "wrapAndUnwrapSol": true,
        "dynamicComputeUnitLimit": true,
        "prioritizationFeeLamports": "auto"
    });
    let swap_resp: serde_json::Value = http
        .post("https://quote-api.jup.ag/v6/swap")
        .json(&swap_body)
        .send().await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter swap: {e}")))?
        .json().await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter swap parse: {e}")))?;

    let tx_b64 = swap_resp["swapTransaction"]
        .as_str()
        .ok_or_else(|| AppError::ProtocolError("Jupiter swap: missing swapTransaction".into()))?
        .to_string();

    let _ = rpc_url; // Jupiter returns a ready-to-sign transaction
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_swap".into(),
            description: format!(
                "Swap {} {} → {} via Meteora DAMM v1 (Jupiter-routed)",
                params.amount,
                &params.input_mint[..8.min(params.input_mint.len())],
                &params.output_mint[..8.min(params.output_mint.len())],
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "inputMint": params.input_mint,
                "outputMint": params.output_mint,
                "amount": params.amount,
                "slippageBps": slippage,
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: Some(quote),
        is_cross_chain: false,
        data: None,
    })
}

// ── DAMM v1 Deposit ───────────────────────────────────────────────────────────

pub async fn build_meteora_dammv1_deposit(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraDammV1DepositParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv1_deposit_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let pool_pk = Pubkey::from_str(&params.pool)
        .map_err(|e| AppError::InvalidParams(format!("Invalid pool: {e}")))?;
    let program = Pubkey::from_str(METEORA_DAMM_V1_PROGRAM_ID).unwrap();

    // Fetch pool data from REST API to get token mints + vault addresses
    let pool_info_url = format!("{DAMM_V1_API}/pools/{}", params.pool);
    let pool_info = meteora_get(http, &pool_info_url).await?;

    let mint_a_str = pool_info["pool_token_mints"][0].as_str()
        .or_else(|| pool_info["tokenA"]["mint"].as_str())
        .or_else(|| pool_info["mint_a"].as_str())
        .ok_or_else(|| AppError::ProtocolError("DAMM v1 pool API response missing token A mint".into()))?
        .to_string();
    let mint_b_str = pool_info["pool_token_mints"][1].as_str()
        .or_else(|| pool_info["tokenB"]["mint"].as_str())
        .or_else(|| pool_info["mint_b"].as_str())
        .ok_or_else(|| AppError::ProtocolError("DAMM v1 pool API response missing token B mint".into()))?
        .to_string();

    // Parse amounts with 6 decimals (default) — exact decimals are on-chain
    let amount_a = parse_to_base_units(&params.token_a_amount, 6)?;
    let amount_b = parse_to_base_units(&params.token_b_amount, 6)?;
    let slippage = params.slippage_bps.unwrap_or(100);
    let min_lp: u64 = 0; // accept any LP amount

    // Anchor discriminator: sha256("global:add_balance_liquidity")[:8]
    let disc = {
        let mut h = Sha256::new();
        h.update("global:add_balance_liquidity");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&amount_a.to_le_bytes());
    data.extend_from_slice(&amount_b.to_le_bytes());
    data.extend_from_slice(&min_lp.to_le_bytes());

    // Minimal account set — pool, user token accounts, user, token program
    // Full IDL account ordering may require additional vault/oracle accounts.
    let mint_a = Pubkey::from_str(&mint_a_str)
        .map_err(|e| AppError::ProtocolError(format!("mintA: {e}")))?;
    let mint_b = Pubkey::from_str(&mint_b_str)
        .map_err(|e| AppError::ProtocolError(format!("mintB: {e}")))?;
    let user_a = get_associated_token_address(&user, &mint_a);
    let user_b = get_associated_token_address(&user, &mint_b);
    let accounts = vec![
        AccountMeta::new(pool_pk, false),
        AccountMeta::new(user_a, false),
        AccountMeta::new(user_b, false),
        AccountMeta::new_readonly(user, true),
        AccountMeta::new_readonly(token_program(), false),
    ];

    let ixs = vec![Instruction { program_id: program, accounts, data }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_deposit".into(),
            description: format!(
                "Deposit {} / {} into Meteora DAMM v1 pool {}…",
                params.token_a_amount, params.token_b_amount,
                &params.pool[..8.min(params.pool.len())]
            ),
            estimated_fee: "~0.0004 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "pool": params.pool,
                "tokenAAmount": params.token_a_amount,
                "tokenBAmount": params.token_b_amount,
                "slippageBps": slippage,
            }),
            warnings: vec![
                "DAMM v1 deposit: exact account ordering follows the on-chain IDL. \
                 Verify with Meteora SDK before signing.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── DAMM v1 Withdraw ──────────────────────────────────────────────────────────

pub async fn build_meteora_dammv1_withdraw(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraDammV1WithdrawParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv1_withdraw_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let pool_pk = Pubkey::from_str(&params.pool)
        .map_err(|e| AppError::InvalidParams(format!("Invalid pool: {e}")))?;
    let program = Pubkey::from_str(METEORA_DAMM_V1_PROGRAM_ID).unwrap();

    let pool_info_url = format!("{DAMM_V1_API}/pools/{}", params.pool);
    let pool_info = meteora_get(http, &pool_info_url).await?;

    let mint_a_str = pool_info["pool_token_mints"][0].as_str()
        .or_else(|| pool_info["mint_a"].as_str())
        .ok_or_else(|| AppError::ProtocolError("DAMM v1 pool API response missing token A mint".into()))?
        .to_string();
    let mint_b_str = pool_info["pool_token_mints"][1].as_str()
        .or_else(|| pool_info["mint_b"].as_str())
        .ok_or_else(|| AppError::ProtocolError("DAMM v1 pool API response missing token B mint".into()))?
        .to_string();

    let lp_amount = parse_to_base_units(&params.lp_amount, 6)?;
    let min_a: u64 = params.min_a_amount.as_deref()
        .map(|s| parse_to_base_units(s, 6)).transpose()?.unwrap_or(0);
    let min_b: u64 = params.min_b_amount.as_deref()
        .map(|s| parse_to_base_units(s, 6)).transpose()?.unwrap_or(0);

    let disc = {
        let mut h = Sha256::new();
        h.update("global:remove_balance_liquidity");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&lp_amount.to_le_bytes());
    data.extend_from_slice(&min_a.to_le_bytes());
    data.extend_from_slice(&min_b.to_le_bytes());

    let mint_a = Pubkey::from_str(&mint_a_str)
        .map_err(|e| AppError::ProtocolError(format!("mintA: {e}")))?;
    let mint_b = Pubkey::from_str(&mint_b_str)
        .map_err(|e| AppError::ProtocolError(format!("mintB: {e}")))?;
    let user_a = get_associated_token_address(&user, &mint_a);
    let user_b = get_associated_token_address(&user, &mint_b);
    let accounts = vec![
        AccountMeta::new(pool_pk, false),
        AccountMeta::new(user_a, false),
        AccountMeta::new(user_b, false),
        AccountMeta::new_readonly(user, true),
        AccountMeta::new_readonly(token_program(), false),
    ];

    let ixs = vec![Instruction { program_id: program, accounts, data }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv1_withdraw".into(),
            description: format!(
                "Withdraw {} LP from Meteora DAMM v1 pool {}…",
                params.lp_amount,
                &params.pool[..8.min(params.pool.len())]
            ),
            estimated_fee: "~0.0004 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "pool": params.pool,
                "lpAmount": params.lp_amount,
                "minAAmount": params.min_a_amount,
                "minBAmount": params.min_b_amount,
            }),
            warnings: vec![
                "DAMM v1 withdraw: exact account ordering follows the on-chain IDL. \
                 Verify with Meteora SDK before signing.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── DAMM v2 Swap (Jupiter-routed) ─────────────────────────────────────────────

pub async fn build_meteora_dammv2_swap(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraDammV2SwapParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv2_swap_params(params)?;
    let slippage = params.slippage_bps.unwrap_or(50);
    let quote_url = format!(
        "https://quote-api.jup.ag/v6/quote?inputMint={}&outputMint={}&amount={}&slippageBps={}&onlyDirectRoutes=false",
        params.input_mint, params.output_mint, params.amount, slippage
    );
    let quote: serde_json::Value = http.get(&quote_url)
        .send().await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter quote: {e}")))?
        .json().await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter quote parse: {e}")))?;

    let swap_body = serde_json::json!({
        "quoteResponse": quote,
        "userPublicKey": user_pubkey_str,
        "wrapAndUnwrapSol": true,
        "dynamicComputeUnitLimit": true,
        "prioritizationFeeLamports": "auto"
    });
    let swap_resp: serde_json::Value = http
        .post("https://quote-api.jup.ag/v6/swap")
        .json(&swap_body)
        .send().await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter swap: {e}")))?
        .json().await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter swap parse: {e}")))?;

    let tx_b64 = swap_resp["swapTransaction"]
        .as_str()
        .ok_or_else(|| AppError::ProtocolError("Jupiter swap: missing swapTransaction".into()))?
        .to_string();

    let _ = rpc_url;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_swap".into(),
            description: format!(
                "Swap {} {} → {} via Meteora DAMM v2 (Jupiter-routed)",
                params.amount,
                &params.input_mint[..8.min(params.input_mint.len())],
                &params.output_mint[..8.min(params.output_mint.len())],
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "inputMint": params.input_mint,
                "outputMint": params.output_mint,
                "amount": params.amount,
                "slippageBps": slippage,
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: Some(quote),
        is_cross_chain: false,
        data: None,
    })
}

// ── DAMM v2 Add Liquidity ─────────────────────────────────────────────────────

pub async fn build_meteora_dammv2_add_liquidity(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraDammV2AddLiquidityParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv2_add_liquidity_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let pool_pk = Pubkey::from_str(&params.pool)
        .map_err(|e| AppError::InvalidParams(format!("Invalid pool: {e}")))?;
    let program = Pubkey::from_str(METEORA_DAMM_V1_PROGRAM_ID).unwrap(); // DAMM v2 shares program

    let pool_info_url = format!("{DAMM_V2_API}/pools/{}", params.pool);
    let pool_info = meteora_get(http, &pool_info_url).await?;

    let mint_a_str = pool_info["mintA"].as_str()
        .or_else(|| pool_info["token_a_mint"].as_str())
        .ok_or_else(|| AppError::ProtocolError("DAMM v2 pool API response missing token A mint".into()))?
        .to_string();
    let mint_b_str = pool_info["mintB"].as_str()
        .or_else(|| pool_info["token_b_mint"].as_str())
        .ok_or_else(|| AppError::ProtocolError("DAMM v2 pool API response missing token B mint".into()))?
        .to_string();

    let amount_a = parse_to_base_units(&params.max_amount_a, 6)?;
    let amount_b = parse_to_base_units(&params.max_amount_b, 6)?;
    let min_lp: u64 = 0;

    let disc = {
        let mut h = Sha256::new();
        h.update("global:add_liquidity");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&amount_a.to_le_bytes());
    data.extend_from_slice(&amount_b.to_le_bytes());
    data.extend_from_slice(&min_lp.to_le_bytes());

    let mint_a = Pubkey::from_str(&mint_a_str)
        .map_err(|e| AppError::ProtocolError(format!("mintA: {e}")))?;
    let mint_b = Pubkey::from_str(&mint_b_str)
        .map_err(|e| AppError::ProtocolError(format!("mintB: {e}")))?;
    let user_a = get_associated_token_address(&user, &mint_a);
    let user_b = get_associated_token_address(&user, &mint_b);
    let accounts = vec![
        AccountMeta::new(pool_pk, false),
        AccountMeta::new(user_a, false),
        AccountMeta::new(user_b, false),
        AccountMeta::new_readonly(user, true),
        AccountMeta::new_readonly(token_program(), false),
    ];

    let ixs = vec![Instruction { program_id: program, accounts, data }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_add_liquidity".into(),
            description: format!(
                "Add liquidity to Meteora DAMM v2 pool {}…",
                &params.pool[..8.min(params.pool.len())]
            ),
            estimated_fee: "~0.0004 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "pool": params.pool,
                "maxAmountA": params.max_amount_a,
                "maxAmountB": params.max_amount_b,
                "slippageBps": params.slippage_bps.unwrap_or(100),
            }),
            warnings: vec![
                "DAMM v2 add_liquidity: exact account ordering follows the on-chain IDL. \
                 Verify with Meteora SDK before signing.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── DAMM v2 Remove Liquidity ──────────────────────────────────────────────────

pub async fn build_meteora_dammv2_remove_liquidity(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraDammV2RemoveLiquidityParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_dammv2_remove_liquidity_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let pool_pk = Pubkey::from_str(&params.pool)
        .map_err(|e| AppError::InvalidParams(format!("Invalid pool: {e}")))?;
    let position_nft = Pubkey::from_str(&params.position_nft)
        .map_err(|e| AppError::InvalidParams(format!("Invalid positionNft: {e}")))?;
    let program = Pubkey::from_str(METEORA_DAMM_V1_PROGRAM_ID).unwrap();

    let pool_info_url = format!("{DAMM_V2_API}/pools/{}", params.pool);
    let pool_info = meteora_get(http, &pool_info_url).await?;

    let mint_a_str = pool_info["mintA"].as_str()
        .or_else(|| pool_info["token_a_mint"].as_str())
        .ok_or_else(|| AppError::ProtocolError("DAMM v2 pool API response missing token A mint".into()))?
        .to_string();
    let mint_b_str = pool_info["mintB"].as_str()
        .or_else(|| pool_info["token_b_mint"].as_str())
        .ok_or_else(|| AppError::ProtocolError("DAMM v2 pool API response missing token B mint".into()))?
        .to_string();

    let lp_amount = parse_to_base_units(&params.lp_amount, 6)?;
    let min_a: u64 = params.min_amount_a.as_deref()
        .map(|s| parse_to_base_units(s, 6)).transpose()?.unwrap_or(0);
    let min_b: u64 = params.min_amount_b.as_deref()
        .map(|s| parse_to_base_units(s, 6)).transpose()?.unwrap_or(0);

    let disc = {
        let mut h = Sha256::new();
        h.update("global:remove_liquidity");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&lp_amount.to_le_bytes());
    data.extend_from_slice(&min_a.to_le_bytes());
    data.extend_from_slice(&min_b.to_le_bytes());

    let mint_a = Pubkey::from_str(&mint_a_str)
        .map_err(|e| AppError::ProtocolError(format!("mintA: {e}")))?;
    let mint_b = Pubkey::from_str(&mint_b_str)
        .map_err(|e| AppError::ProtocolError(format!("mintB: {e}")))?;
    let accounts = vec![
        AccountMeta::new(pool_pk, false),
        AccountMeta::new_readonly(position_nft, false),
        AccountMeta::new_readonly(user, true),
        AccountMeta::new(get_associated_token_address(&user, &mint_a), false),
        AccountMeta::new(get_associated_token_address(&user, &mint_b), false),
        AccountMeta::new_readonly(token_program(), false),
    ];

    let ixs = vec![Instruction { program_id: program, accounts, data }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_dammv2_remove_liquidity".into(),
            description: format!(
                "Remove {} LP from Meteora DAMM v2 pool {}…",
                params.lp_amount,
                &params.pool[..8.min(params.pool.len())]
            ),
            estimated_fee: "~0.0004 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "pool": params.pool,
                "lpAmount": params.lp_amount,
                "positionNft": params.position_nft,
                "minAmountA": params.min_amount_a,
                "minAmountB": params.min_amount_b,
            }),
            warnings: vec![
                "DAMM v2 remove_liquidity: exact account ordering follows the on-chain IDL. \
                 Verify with Meteora SDK before signing.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── Dynamic Vault Deposit ─────────────────────────────────────────────────────

pub async fn build_meteora_vault_deposit(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraVaultDepositParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_vault_deposit_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let program = Pubkey::from_str(METEORA_VAULT_PROGRAM_ID).unwrap();

    // Fetch vault state to get vault address
    let state_url = format!("{VAULT_API}/vault_state/{}", params.token_mint);
    let vault_state = meteora_get(http, &state_url).await?;
    let vault_addr_str = vault_state["vault"].as_str()
        .or_else(|| vault_state["vault_address"].as_str())
        .ok_or_else(|| AppError::ProtocolError("Dynamic Vault API response missing vault address".into()))?
        .to_string();

    let amount = parse_to_base_units(&params.amount, 6)?;
    let min_lp: u64 = 0;

    let disc = {
        let mut h = Sha256::new();
        h.update("global:deposit");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&amount.to_le_bytes());
    data.extend_from_slice(&min_lp.to_le_bytes());

    let mut accounts = vec![AccountMeta::new_readonly(user, true)];
    if !vault_addr_str.is_empty() {
        let vault_pk = Pubkey::from_str(&vault_addr_str)
            .map_err(|e| AppError::ProtocolError(format!("vaultAddress: {e}")))?;
        accounts.insert(0, AccountMeta::new(vault_pk, false));
    }
    if let Ok(mint_pk) = Pubkey::from_str(&params.token_mint) {
        accounts.push(AccountMeta::new(get_associated_token_address(&user, &mint_pk), false));
    }
    accounts.push(AccountMeta::new_readonly(token_program(), false));
    accounts.push(AccountMeta::new_readonly(system_program::id(), false));

    let ixs = vec![Instruction { program_id: program, accounts, data }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_vault_deposit".into(),
            description: format!("Deposit {} into Meteora Dynamic Vault ({})", params.amount, params.token_mint),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "tokenMint": params.token_mint,
                "amount": params.amount,
                "affiliateId": params.affiliate_id,
            }),
            warnings: vec![
                "Vault deposit: exact account ordering follows the on-chain IDL. \
                 Verify with Meteora Vault SDK before signing.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── Dynamic Vault Withdraw ────────────────────────────────────────────────────

pub async fn build_meteora_vault_withdraw(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraVaultWithdrawParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_vault_withdraw_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let program = Pubkey::from_str(METEORA_VAULT_PROGRAM_ID).unwrap();

    let state_url = format!("{VAULT_API}/vault_state/{}", params.token_mint);
    let vault_state = meteora_get(http, &state_url).await?;
    let vault_addr_str = vault_state["vault"].as_str()
        .or_else(|| vault_state["vault_address"].as_str())
        .ok_or_else(|| AppError::ProtocolError("Dynamic Vault API response missing vault address".into()))?
        .to_string();

    let unmint_amount = parse_to_base_units(&params.unmint_amount, 6)?;
    let min_out: u64 = 0;

    let disc = {
        let mut h = Sha256::new();
        h.update("global:withdraw");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&unmint_amount.to_le_bytes());
    data.extend_from_slice(&min_out.to_le_bytes());

    let mut accounts = vec![AccountMeta::new_readonly(user, true)];
    if !vault_addr_str.is_empty() {
        let vault_pk = Pubkey::from_str(&vault_addr_str)
            .map_err(|e| AppError::ProtocolError(format!("vaultAddress: {e}")))?;
        accounts.insert(0, AccountMeta::new(vault_pk, false));
    }
    if let Ok(mint_pk) = Pubkey::from_str(&params.token_mint) {
        accounts.push(AccountMeta::new(get_associated_token_address(&user, &mint_pk), false));
    }
    accounts.push(AccountMeta::new_readonly(token_program(), false));

    let ixs = vec![Instruction { program_id: program, accounts, data }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_vault_withdraw".into(),
            description: format!("Withdraw {} LP from Meteora Dynamic Vault ({})", params.unmint_amount, params.token_mint),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "tokenMint": params.token_mint,
                "unmintAmount": params.unmint_amount,
                "affiliateId": params.affiliate_id,
            }),
            warnings: vec![
                "Vault withdraw: exact account ordering follows the on-chain IDL. \
                 Verify with Meteora Vault SDK before signing.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── Stake2Earn Stake ──────────────────────────────────────────────────────────

pub async fn build_meteora_s2e_stake(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraS2EStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_s2e_stake_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let vault_pk = Pubkey::from_str(&params.vault)
        .map_err(|e| AppError::InvalidParams(format!("Invalid vault: {e}")))?;
    let program = Pubkey::from_str(METEORA_S2E_PROGRAM_ID).unwrap();

    let amount = parse_to_base_units(&params.amount, 6)?;

    let disc = {
        let mut h = Sha256::new();
        h.update("global:stake");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&amount.to_le_bytes());

    let ixs = vec![Instruction {
        program_id: program,
        accounts: vec![
            AccountMeta::new(vault_pk, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
        ],
        data,
    }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_stake".into(),
            description: format!(
                "Stake {} into Meteora Stake2Earn vault {}…",
                params.amount, &params.vault[..8.min(params.vault.len())]
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "vault": params.vault, "amount": params.amount }),
            warnings: vec![
                "S2E stake: exact account ordering follows the on-chain IDL. \
                 Verify with Meteora m3m3 SDK before signing.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── Stake2Earn Unstake ────────────────────────────────────────────────────────

pub async fn build_meteora_s2e_unstake(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraS2EUnstakeParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_s2e_unstake_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let vault_pk = Pubkey::from_str(&params.vault)
        .map_err(|e| AppError::InvalidParams(format!("Invalid vault: {e}")))?;
    let program = Pubkey::from_str(METEORA_S2E_PROGRAM_ID).unwrap();

    let amount = parse_to_base_units(&params.amount, 6)?;

    let disc = {
        let mut h = Sha256::new();
        h.update("global:unstake");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&amount.to_le_bytes());

    let ixs = vec![Instruction {
        program_id: program,
        accounts: vec![
            AccountMeta::new(vault_pk, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
        ],
        data,
    }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_unstake".into(),
            description: format!(
                "Unstake {} from Meteora Stake2Earn vault {}…",
                params.amount, &params.vault[..8.min(params.vault.len())]
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "vault": params.vault, "amount": params.amount }),
            warnings: vec![
                "S2E unstake: initiates unbonding. Exact account ordering follows the on-chain IDL.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── Stake2Earn Claim Fee ──────────────────────────────────────────────────────

pub async fn build_meteora_s2e_claim_fee(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraS2EClaimFeeParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_s2e_claim_fee_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let vault_pk = Pubkey::from_str(&params.vault)
        .map_err(|e| AppError::InvalidParams(format!("Invalid vault: {e}")))?;
    let program = Pubkey::from_str(METEORA_S2E_PROGRAM_ID).unwrap();

    let max_amount: u64 = params.max_amount.as_deref()
        .map(|s| parse_to_base_units(s, 6)).transpose()?.unwrap_or(u64::MAX);

    let disc = {
        let mut h = Sha256::new();
        h.update("global:claim_fee");
        h.finalize()[..8].to_vec()
    };
    let mut data = disc;
    data.extend_from_slice(&max_amount.to_le_bytes());

    let ixs = vec![Instruction {
        program_id: program,
        accounts: vec![
            AccountMeta::new(vault_pk, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
        ],
        data,
    }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_claim_fee".into(),
            description: format!(
                "Claim fees from Meteora Stake2Earn vault {}…",
                &params.vault[..8.min(params.vault.len())]
            ),
            estimated_fee: "~0.0003 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "vault": params.vault, "maxAmount": params.max_amount }),
            warnings: vec![
                "S2E claim_fee: exact account ordering follows the on-chain IDL.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── Stake2Earn Cancel Unstake ─────────────────────────────────────────────────

pub async fn build_meteora_s2e_cancel_unstake(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraS2ECancelUnstakeParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_s2e_cancel_unstake_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let vault_pk = Pubkey::from_str(&params.vault)
        .map_err(|e| AppError::InvalidParams(format!("Invalid vault: {e}")))?;
    let escrow_pk = Pubkey::from_str(&params.escrow)
        .map_err(|e| AppError::InvalidParams(format!("Invalid escrow: {e}")))?;
    let program = Pubkey::from_str(METEORA_S2E_PROGRAM_ID).unwrap();

    let disc = {
        let mut h = Sha256::new();
        h.update("global:cancel_unstake");
        h.finalize()[..8].to_vec()
    };

    let ixs = vec![Instruction {
        program_id: program,
        accounts: vec![
            AccountMeta::new(vault_pk, false),
            AccountMeta::new(escrow_pk, false),
            AccountMeta::new_readonly(user, true),
        ],
        data: disc,
    }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_cancel_unstake".into(),
            description: format!(
                "Cancel unstake escrow {} on Stake2Earn vault {}…",
                &params.escrow[..8.min(params.escrow.len())],
                &params.vault[..8.min(params.vault.len())]
            ),
            estimated_fee: "~0.0002 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "vault": params.vault, "escrow": params.escrow }),
            warnings: vec![
                "S2E cancel_unstake: exact account ordering follows the on-chain IDL.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── Stake2Earn Withdraw (after unstake) ───────────────────────────────────────

pub async fn build_meteora_s2e_withdraw(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey_str: &str,
    params: &MeteoraS2EWithdrawParams,
) -> Result<BuildResponse, AppError> {
    validate_meteora_s2e_withdraw_params(params)?;

    let user = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let vault_pk = Pubkey::from_str(&params.vault)
        .map_err(|e| AppError::InvalidParams(format!("Invalid vault: {e}")))?;
    let escrow_pk = Pubkey::from_str(&params.escrow)
        .map_err(|e| AppError::InvalidParams(format!("Invalid escrow: {e}")))?;
    let program = Pubkey::from_str(METEORA_S2E_PROGRAM_ID).unwrap();

    let disc = {
        let mut h = Sha256::new();
        h.update("global:withdraw");
        h.finalize()[..8].to_vec()
    };

    let ixs = vec![Instruction {
        program_id: program,
        accounts: vec![
            AccountMeta::new(vault_pk, false),
            AccountMeta::new(escrow_pk, false),
            AccountMeta::new_readonly(user, true),
            AccountMeta::new_readonly(token_program(), false),
        ],
        data: disc,
    }];
    let tx = build_vtx_b64(rpc_url, &user, &ixs).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "meteora_s2e_withdraw".into(),
            description: format!(
                "Withdraw from Stake2Earn escrow {} on vault {}…",
                &params.escrow[..8.min(params.escrow.len())],
                &params.vault[..8.min(params.vault.len())]
            ),
            estimated_fee: "~0.0002 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "vault": params.vault, "escrow": params.escrow }),
            warnings: vec![
                "S2E withdraw: exact account ordering follows the on-chain IDL.".into()
            ],
            requires_approval: true,
        },
        transaction: Some(tx),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const VALID_PAIR: &str = "HcjZvfeSNJbNkfLD4eEcRBr96AD3w1Tm3fSXURqLSfm1";
    const VALID_WALLET: &str = "HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa";

    // ── DLMM constants ────────────────────────────────────────────────────────

    #[test]
    fn test_dlmm_api_constant() {
        assert_eq!(DLMM_API, "https://dlmm.datapi.meteora.ag");
    }

    #[test]
    fn test_dlmm_stats_api_constant() {
        assert_eq!(DLMM_STATS_API, "https://dlmm.datapi.meteora.ag");
    }

    #[test]
    fn test_protocol_stats_url_format() {
        let url = format!("{DLMM_STATS_API}/stats/protocol_metrics");
        assert_eq!(url, "https://dlmm.datapi.meteora.ag/stats/protocol_metrics");
    }

    // ── validate_meteora_dlmm_get_pairs_params ────────────────────────────────

    #[test]
    fn test_validate_get_pairs_empty_params_ok() {
        let p = MeteoraDlmmGetPairsParams {
            page: None,
            page_size: None,
            query: None,
            sort_by: None,
            filter_by: None,
        };
        assert!(validate_meteora_dlmm_get_pairs_params(&p).is_ok());
    }

    #[test]
    fn test_validate_get_pairs_with_all_fields_ok() {
        let p = MeteoraDlmmGetPairsParams {
            page: Some(2),
            page_size: Some(50),
            query: Some("SOL-USDC".into()),
            sort_by: Some("volume_24h:desc".into()),
            filter_by: Some("tvl:gt:10000".into()),
        };
        assert!(validate_meteora_dlmm_get_pairs_params(&p).is_ok());
    }

    // ── JSON deserialization (camelCase → snake_case) ─────────────────────────

    #[test]
    fn test_get_pairs_params_deserialize_camel_case() {
        let json = r#"{"page":1,"pageSize":20,"query":"BONK","sortBy":"fee_tvl_ratio:desc"}"#;
        let p: MeteoraDlmmGetPairsParams = serde_json::from_str(json).unwrap();
        assert_eq!(p.page, Some(1));
        assert_eq!(p.page_size, Some(20));
        assert_eq!(p.query.as_deref(), Some("BONK"));
        assert_eq!(p.sort_by.as_deref(), Some("fee_tvl_ratio:desc"));
    }

    #[test]
    fn test_get_pairs_params_deserialize_empty_object() {
        let p: MeteoraDlmmGetPairsParams = serde_json::from_str("{}").unwrap();
        assert!(p.page.is_none());
        assert!(p.query.is_none());
    }

    // ── validate_meteora_dlmm_get_pair_params ─────────────────────────────────

    #[test]
    fn test_validate_get_pair_valid_address_ok() {
        let p = MeteoraDlmmGetPairParams { address: VALID_PAIR.into() };
        assert!(validate_meteora_dlmm_get_pair_params(&p).is_ok());
    }

    #[test]
    fn test_validate_get_pair_invalid_address_err() {
        let p = MeteoraDlmmGetPairParams { address: "not-a-pubkey".into() };
        assert!(validate_meteora_dlmm_get_pair_params(&p).is_err());
    }

    #[test]
    fn test_validate_get_pair_empty_address_err() {
        let p = MeteoraDlmmGetPairParams { address: "".into() };
        assert!(validate_meteora_dlmm_get_pair_params(&p).is_err());
    }

    #[test]
    fn test_get_pair_params_deserialize_camel_case() {
        let json = r#"{"address":"HcjZvfeSNJbNkfLD4eEcRBr96AD3w1Tm3fSXURqLSfm1"}"#;
        let p: MeteoraDlmmGetPairParams = serde_json::from_str(json).unwrap();
        assert_eq!(p.address, VALID_PAIR);
    }

    // ── validate_meteora_dlmm_get_active_bin_params ───────────────────────────

    #[test]
    fn test_validate_get_active_bin_valid_ok() {
        let p = MeteoraDlmmGetActiveBinParams { address: VALID_PAIR.into() };
        assert!(validate_meteora_dlmm_get_active_bin_params(&p).is_ok());
    }

    #[test]
    fn test_validate_get_active_bin_invalid_err() {
        let p = MeteoraDlmmGetActiveBinParams { address: "bad".into() };
        assert!(validate_meteora_dlmm_get_active_bin_params(&p).is_err());
    }

    // ── validate_meteora_dlmm_get_pool_ohlcv_params ───────────────────────────

    #[test]
    fn test_validate_ohlcv_valid_timeframes_ok() {
        for tf in ["5m", "30m", "1h", "2h", "4h", "12h", "24h"] {
            let p = MeteoraDlmmGetPoolOhlcvParams {
                address: VALID_PAIR.into(),
                timeframe: Some(tf.into()),
                start_time: None,
                end_time: None,
            };
            assert!(validate_meteora_dlmm_get_pool_ohlcv_params(&p).is_ok(), "timeframe {tf} should be valid");
        }
    }

    #[test]
    fn test_validate_ohlcv_invalid_timeframe_err() {
        let p = MeteoraDlmmGetPoolOhlcvParams {
            address: VALID_PAIR.into(),
            timeframe: Some("3d".into()),
            start_time: None,
            end_time: None,
        };
        assert!(validate_meteora_dlmm_get_pool_ohlcv_params(&p).is_err());
    }

    #[test]
    fn test_validate_ohlcv_no_timeframe_ok() {
        let p = MeteoraDlmmGetPoolOhlcvParams {
            address: VALID_PAIR.into(),
            timeframe: None,
            start_time: None,
            end_time: None,
        };
        assert!(validate_meteora_dlmm_get_pool_ohlcv_params(&p).is_ok());
    }

    #[test]
    fn test_validate_ohlcv_invalid_address_err() {
        let p = MeteoraDlmmGetPoolOhlcvParams {
            address: "short".into(),
            timeframe: Some("1h".into()),
            start_time: None,
            end_time: None,
        };
        assert!(validate_meteora_dlmm_get_pool_ohlcv_params(&p).is_err());
    }

    #[test]
    fn test_ohlcv_params_deserialize_camel_case() {
        let json = r#"{"address":"HcjZvfeSNJbNkfLD4eEcRBr96AD3w1Tm3fSXURqLSfm1","timeframe":"1h","startTime":1700000000,"endTime":1700086400}"#;
        let p: MeteoraDlmmGetPoolOhlcvParams = serde_json::from_str(json).unwrap();
        assert_eq!(p.timeframe.as_deref(), Some("1h"));
        assert_eq!(p.start_time, Some(1700000000));
        assert_eq!(p.end_time, Some(1700086400));
    }

    // ── validate_meteora_dlmm_get_pool_groups_params ──────────────────────────

    #[test]
    fn test_validate_get_pool_groups_ok() {
        let p = MeteoraDlmmGetPoolGroupsParams {
            page: Some(1),
            page_size: Some(10),
            query: Some("SOL".into()),
            sort_by: None,
            filter_by: None,
            volume_tw: Some("volume_24h".into()),
            fee_tvl_ratio_tw: None,
        };
        assert!(validate_meteora_dlmm_get_pool_groups_params(&p).is_ok());
    }

    #[test]
    fn test_validate_get_pool_groups_empty_ok() {
        let p = MeteoraDlmmGetPoolGroupsParams {
            page: None,
            page_size: None,
            query: None,
            sort_by: None,
            filter_by: None,
            volume_tw: None,
            fee_tvl_ratio_tw: None,
        };
        assert!(validate_meteora_dlmm_get_pool_groups_params(&p).is_ok());
    }

    // ── validate_meteora_dlmm_get_pool_group_params ───────────────────────────

    #[test]
    fn test_validate_get_pool_group_valid_ok() {
        let p = MeteoraDlmmGetPoolGroupParams {
            lexical_order_mints: "MINTA-MINTB".into(),
            page: None,
            page_size: None,
            sort_by: None,
            filter_by: None,
        };
        assert!(validate_meteora_dlmm_get_pool_group_params(&p).is_ok());
    }

    #[test]
    fn test_validate_get_pool_group_empty_lexical_err() {
        let p = MeteoraDlmmGetPoolGroupParams {
            lexical_order_mints: "".into(),
            page: None,
            page_size: None,
            sort_by: None,
            filter_by: None,
        };
        assert!(validate_meteora_dlmm_get_pool_group_params(&p).is_err());
    }

    #[test]
    fn test_get_pool_group_params_deserialize_camel_case() {
        let json = r#"{"lexicalOrderMints":"SOL-USDC","page":1}"#;
        let p: MeteoraDlmmGetPoolGroupParams = serde_json::from_str(json).unwrap();
        assert_eq!(p.lexical_order_mints, "SOL-USDC");
        assert_eq!(p.page, Some(1));
    }

    // ── validate_meteora_dlmm_get_user_positions_params ───────────────────────

    #[test]
    fn test_validate_user_positions_valid_wallet_ok() {
        let p = MeteoraDlmmGetUserPositionsParams { wallet: Some(VALID_WALLET.into()) };
        assert!(validate_meteora_dlmm_get_user_positions_params(&p).is_ok());
    }

    #[test]
    fn test_validate_user_positions_no_wallet_ok() {
        let p = MeteoraDlmmGetUserPositionsParams { wallet: None };
        assert!(validate_meteora_dlmm_get_user_positions_params(&p).is_ok());
    }

    #[test]
    fn test_validate_user_positions_invalid_wallet_err() {
        let p = MeteoraDlmmGetUserPositionsParams { wallet: Some("invalid!wallet".into()) };
        assert!(validate_meteora_dlmm_get_user_positions_params(&p).is_err());
    }

    // ── validate_meteora_dlmm_get_protocol_stats_params ──────────────────────

    #[test]
    fn test_validate_protocol_stats_always_ok() {
        let p = MeteoraDlmmGetProtocolStatsParams {};
        assert!(validate_meteora_dlmm_get_protocol_stats_params(&p).is_ok());
    }

    #[test]
    fn test_protocol_stats_params_deserialize_empty_object() {
        let p: MeteoraDlmmGetProtocolStatsParams = serde_json::from_str("{}").unwrap();
        let _ = p;
    }

    #[test]
    fn test_protocol_stats_params_deserialize_ignores_extra_fields() {
        // LLM may send extra fields; struct should still deserialize
        let p: MeteoraDlmmGetProtocolStatsParams =
            serde_json::from_str(r#"{"unknown":"field","extra":123}"#).unwrap();
        let _ = p;
    }

    // ── validate_meteora_dlmm_get_pool_volume_history_params ──────────────────

    #[test]
    fn test_validate_volume_history_valid_ok() {
        let p = MeteoraDlmmGetPoolVolumeHistoryParams {
            address: VALID_PAIR.into(),
            timeframe: Some("1h".into()),
            start_time: None,
            end_time: None,
        };
        assert!(validate_meteora_dlmm_get_pool_volume_history_params(&p).is_ok());
    }

    #[test]
    fn test_validate_volume_history_invalid_timeframe_err() {
        let p = MeteoraDlmmGetPoolVolumeHistoryParams {
            address: VALID_PAIR.into(),
            timeframe: Some("7d".into()),
            start_time: None,
            end_time: None,
        };
        assert!(validate_meteora_dlmm_get_pool_volume_history_params(&p).is_err());
    }

    #[test]
    fn test_validate_volume_history_invalid_address_err() {
        let p = MeteoraDlmmGetPoolVolumeHistoryParams {
            address: "not-a-pubkey".into(),
            timeframe: None,
            start_time: None,
            end_time: None,
        };
        assert!(validate_meteora_dlmm_get_pool_volume_history_params(&p).is_err());
    }

    // ── Integration tests (require network) ───────────────────────────────────
    // Run with: cargo test -- --ignored

    #[tokio::test]
    #[ignore]
    async fn integration_build_protocol_stats_returns_data() {
        let http = reqwest::Client::new();
        let p = MeteoraDlmmGetProtocolStatsParams {};
        let result = build_meteora_dlmm_get_protocol_stats(&http, &p).await;
        assert!(result.is_ok(), "build_meteora_dlmm_get_protocol_stats failed: {:?}", result.err());
        let resp = result.unwrap();
        assert_eq!(resp.preview.action_type, "meteora_dlmm_get_protocol_stats");
        assert!(resp.transaction.is_none());
        assert!(resp.data.is_some(), "data field should be populated with API response");
    }

    #[tokio::test]
    #[ignore]
    async fn integration_build_protocol_stats_data_is_json() {
        let http = reqwest::Client::new();
        let p = MeteoraDlmmGetProtocolStatsParams {};
        let resp = build_meteora_dlmm_get_protocol_stats(&http, &p).await.unwrap();
        let data = resp.data.unwrap();
        assert!(data.is_object() || data.is_array(), "expected JSON object or array");
    }

    #[tokio::test]
    #[ignore]
    async fn integration_build_get_pairs_default_params() {
        let http = reqwest::Client::new();
        let p = MeteoraDlmmGetPairsParams {
            page: None, page_size: None, query: None, sort_by: None, filter_by: None,
        };
        let result = build_meteora_dlmm_get_pairs(&http, &p).await;
        assert!(result.is_ok(), "{:?}", result.err());
        assert!(result.unwrap().data.is_some());
    }

    #[tokio::test]
    #[ignore]
    async fn integration_build_get_pair_sol_usdc() {
        let http = reqwest::Client::new();
        let p = MeteoraDlmmGetPairParams { address: VALID_PAIR.into() };
        let result = build_meteora_dlmm_get_pair(&http, &p).await;
        assert!(result.is_ok(), "{:?}", result.err());
    }

    #[tokio::test]
    #[ignore]
    async fn integration_build_get_active_bin_sol_usdc() {
        let http = reqwest::Client::new();
        let p = MeteoraDlmmGetActiveBinParams { address: VALID_PAIR.into() };
        let result = build_meteora_dlmm_get_active_bin(&http, &p).await;
        assert!(result.is_ok(), "{:?}", result.err());
    }
}

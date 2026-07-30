//! Orca Whirlpools — Solana CLMM DEX integration.
//!
//! # Transaction building
//! Uses the `orca_whirlpools` Rust SDK v7 (solana v3 sub-crates) to build all
//! position management transactions. A thin v2/v3 type bridge converts the SDK's
//! instructions and keypairs back to the solana-sdk v2 types used by this service.
//!
//! # Swaps
//! Routed through Jupiter (`quote-api.jup.ag/v6`) with `dexes=Whirlpool` so
//! all swap liquidity is sourced from Orca Whirlpool pools.
//!
//! # Pool / token data (read-only GET)
//! Uses the Orca v2 public REST API: `https://api.orca.so/v2/solana`
//! Free, no API key. Returns pools with tickSpacing, TVL, volume, etc.

use base64::Engine;
use serde::{Deserialize, Serialize};
use std::str::FromStr;
use std::time::Duration;
use uuid::Uuid;

use solana_rpc_client::nonblocking::rpc_client::RpcClient as AsyncRpc;
use solana_sdk::{
    commitment_config::CommitmentConfig,
    hash::Hash as SolanaHash,
    instruction::{AccountMeta, Instruction},
    pubkey::Pubkey,
    signer::{keypair::Keypair, Signer},
    transaction::{Transaction, VersionedTransaction},
};

// Orca Whirlpools SDK v7 — uses solana v3 sub-crates internally.
use orca_solana_ix::{AccountMeta as SdkAccountMeta, Instruction as SdkInstruction};
use orca_solana_keypair::Keypair as SdkKeypair;
use orca_solana_pubkey::Pubkey as SdkPubkey;
use orca_solana_rpc::nonblocking::rpc_client::RpcClient as SdkRpcClient;
use orca_whirlpools::{
    close_position_instructions, create_concentrated_liquidity_pool_instructions,
    decrease_liquidity_instructions, fetch_positions_for_owner, fetch_positions_in_whirlpool,
    harvest_position_instructions, increase_liquidity_instructions,
    open_full_range_position_instructions, open_position_instructions,
    open_position_instructions_with_tick_bounds, DecreaseLiquidityParam, IncreaseLiquidityParam,
    PositionOrBundle,
};

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::tokens::{get_token_info, resolve_token_address};

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

/// Orca Whirlpools on-chain program address (used for PDA derivation in queries).
pub const ORCA_WHIRLPOOL_PROGRAM_ID: &str = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCQ";

/// Orca v2 public REST API — pool data, token metadata, protocol stats.
const ORCA_V2_API: &str = "https://api.orca.so/v2/solana";

/// Jupiter endpoints — paid (`api.jup.ag/swap/v1`) used when API key is present,
/// public (`quote-api.jup.ag/v6`) used otherwise (lower rate limits).
const JUP_PAID_QUOTE: &str = "https://api.jup.ag/swap/v1/quote";
const JUP_PAID_SWAP: &str = "https://api.jup.ag/swap/v1/swap";
const JUP_PUB_QUOTE: &str = "https://quote-api.jup.ag/v6/quote";
const JUP_PUB_SWAP: &str = "https://quote-api.jup.ag/v6/swap";

/// Whirlpool tick index bounds (from orca_whirlpools_core).
const MIN_TICK_INDEX: i32 = -443636;
const MAX_TICK_INDEX: i32 = 443636;

// ──────────────────────────────────────────────────────────────────────────────
// Parameter types
// ──────────────────────────────────────────────────────────────────────────────

/// Swap via Orca Whirlpools (routed through Jupiter).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaSwapParams {
    pub input_mint: String,
    pub output_mint: String,
    pub amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    /// "in" = exact input (default), "out" = exact output
    #[serde(default)]
    pub swap_mode: Option<String>,
    /// Priority fee: "auto" (default), "low", "medium", "high", or exact lamports as a string.
    #[serde(default)]
    pub priority_fee: Option<String>,
    /// Optional: specific whirlpool to use. Informational only (Jupiter picks routing).
    #[serde(default)]
    pub whirlpool: Option<String>,
}

/// Open a full-range liquidity position (AMM-like).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaAddLiquidityParams {
    pub whirlpool: String,
    pub amount_a: String,
    pub amount_b: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Deprecated — use `orca_close_position` or `orca_decrease_position`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaRemoveLiquidityParams {
    pub whirlpool: String,
    pub liquidity: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Open a concentrated liquidity position on Orca Whirlpool.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaOpenPositionParams {
    #[serde(default)]
    pub whirlpool: Option<String>,
    #[serde(default)]
    pub input_mint: Option<String>,
    #[serde(default)]
    pub input_amount: Option<String>,
    #[serde(default)]
    pub tick_lower: Option<i32>,
    #[serde(default)]
    pub tick_upper: Option<i32>,
    #[serde(default)]
    pub token_a: Option<String>,
    #[serde(default)]
    pub token_b: Option<String>,
    #[serde(default)]
    pub amount_a: Option<String>,
    #[serde(default)]
    pub min_price: Option<f64>,
    #[serde(default)]
    pub max_price: Option<f64>,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Close an existing Orca Whirlpool position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaClosePositionParams {
    pub position: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Add more liquidity to an existing Orca Whirlpool position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaIncreasePositionParams {
    pub position: String,
    pub input_mint: String,
    pub input_amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Partially withdraw from an existing Orca Whirlpool position.
///
/// Specify **exactly one** of the three withdrawal modes:
/// - `liquidity` — raw SDK liquidity units (u128 string, e.g. `"500000000"`)
/// - `input_mint` + `input_amount` — withdraw the equivalent of N tokens of a given mint
///   (SDK picks `DecreaseLiquidityParam::TokenA` or `::TokenB` based on which side matches)
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaDecreasePositionParams {
    pub position: String,
    /// Raw liquidity units to remove (u128 string). Mutually exclusive with inputMint/inputAmount.
    #[serde(default)]
    pub liquidity: Option<String>,
    /// Token mint (symbol or address) to withdraw.  Mutually exclusive with `liquidity`.
    #[serde(default)]
    pub input_mint: Option<String>,
    /// Amount of `input_mint` to withdraw, in display units (e.g. `"1.5"`).
    #[serde(default)]
    pub input_amount: Option<String>,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Collect trading fees from a Whirlpool position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaCollectFeesParams {
    pub position: String,
}

/// Collect reward tokens from a Whirlpool position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaCollectRewardsParams {
    pub position: String,
    #[serde(default)]
    pub reward_index: Option<u8>,
}

/// Create a new Orca Whirlpool (concentrated or splash).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaCreatePoolParams {
    /// Token A symbol or mint address
    pub token_a: String,
    /// Token B symbol or mint address
    pub token_b: String,
    /// Initial price (tokenB per tokenA, in display units — e.g. 190 for 1 SOL = 190 USDC)
    pub initial_price: f64,
    /// Tick spacing: 1, 2, 4, 8, 16, 32, 64, 128, 256, or 32896 (splash pool).
    /// Defaults to 128 (standard concentrated pool).
    #[serde(default)]
    pub tick_spacing: Option<u16>,
}

// ── GET query param types ─────────────────────────────────────────────────────

/// List Orca Whirlpools pools with rich filtering options.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetPoolsParams {
    /// Sort by: "volume" | "tvl" | "fees" | "rewards" | "yieldovertvl" | "lockedliquiditypercent"
    #[serde(default)]
    pub sort_by: Option<String>,
    /// "asc" or "desc"
    #[serde(default)]
    pub sort_direction: Option<String>,
    #[serde(default)]
    pub size: Option<u32>,
    /// Pagination: cursor for next page (from previous response)
    #[serde(default)]
    pub next: Option<String>,
    /// Pagination: cursor for previous page (from previous response)
    #[serde(default)]
    pub previous: Option<String>,
    #[serde(default)]
    pub has_rewards: Option<bool>,
    #[serde(default)]
    pub has_warning: Option<bool>,
    #[serde(default)]
    pub has_adaptive_fee: Option<bool>,
    #[serde(default)]
    pub is_wavebreak: Option<bool>,
    #[serde(default)]
    pub min_tvl: Option<f64>,
    #[serde(default)]
    pub min_volume: Option<f64>,
    #[serde(default)]
    pub min_locked_liquidity_percent: Option<f64>,
    /// Filter by token mint or symbol
    #[serde(default)]
    pub token: Option<String>,
    /// Filter pools that contain BOTH of these tokens (comma-separated)
    #[serde(default)]
    pub tokens_both_of: Option<String>,
    /// Specific pool addresses (comma-separated)
    #[serde(default)]
    pub addresses: Option<String>,
    /// Stats time periods, e.g. "1d,7d,30d"
    #[serde(default)]
    pub stats: Option<String>,
    #[serde(default)]
    pub include_blocked: Option<bool>,
}

/// Search Orca pools by query string.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaSearchPoolsParams {
    pub q: String,
    #[serde(default)]
    pub size: Option<u32>,
    #[serde(default)]
    pub next: Option<String>,
    #[serde(default)]
    pub sort_by: Option<String>,
    #[serde(default)]
    pub sort_direction: Option<String>,
    #[serde(default)]
    pub min_tvl: Option<f64>,
    #[serde(default)]
    pub min_volume: Option<f64>,
    #[serde(default)]
    pub stats: Option<String>,
    /// Filter by tokens the user holds
    #[serde(default)]
    pub user_tokens: Option<String>,
    #[serde(default)]
    pub has_rewards: Option<bool>,
    #[serde(default)]
    pub verified_only: Option<bool>,
    #[serde(default)]
    pub has_locked_liquidity: Option<bool>,
}

/// Get a specific Orca pool by address.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetPoolParams {
    pub address: String,
    /// Stats time periods, e.g. "1d,7d,30d"
    #[serde(default)]
    pub stats: Option<String>,
}

/// Get locked liquidity info for a pool.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetLockedLiquidityParams {
    pub address: String,
}

/// Get Orca protocol-wide statistics.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetProtocolStatsParams {}

/// Get ORCA token info, price and supply.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetOrcaTokenParams {}

/// Get ORCA token circulating supply.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetCirculatingSupplyParams {}

/// Get ORCA token total supply.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetTotalSupplyParams {}

/// List tokens indexed by Orca.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetTokensParams {
    #[serde(default)]
    pub size: Option<u32>,
    /// Pagination token
    #[serde(default)]
    pub next: Option<String>,
    #[serde(default)]
    pub previous: Option<String>,
    /// Sort by: "address" | "mint_id" | "volume_24h"
    #[serde(default)]
    pub sort_by: Option<String>,
    /// "asc" or "desc"
    #[serde(default)]
    pub sort_direction: Option<String>,
    /// Comma-separated mint addresses to retrieve
    #[serde(default)]
    pub tokens: Option<String>,
}

/// Search Orca tokens by query string.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaSearchTokensParams {
    pub q: String,
}

/// Get a specific token by mint address.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetTokenParams {
    /// Token symbol or mint address
    pub mint_address: String,
}

/// List all Orca Whirlpool positions owned by a wallet (on-chain RPC query).
///
/// Finds every SPL token account with amount=1 and decimals=0 (NFTs), derives
/// the Whirlpool position PDA for each candidate mint, and fetches position data.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetUserPositionsParams {
    /// Wallet address to query. Defaults to the authenticated user's wallet.
    pub wallet: Option<String>,
}

/// List all open positions inside a specific Orca Whirlpool pool (on-chain RPC query).
///
/// Uses `getProgramAccounts` with a memcmp filter on the whirlpool field (offset 8)
/// to enumerate every Position account for the given pool.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OrcaGetPoolPositionsParams {
    /// Whirlpool pool address.
    pub whirlpool: String,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_orca_swap_params(p: &OrcaSwapParams) -> Result<(), AppError> {
    if p.input_mint.is_empty() {
        return Err(AppError::InvalidParams("inputMint is required".into()));
    }
    if p.output_mint.is_empty() {
        return Err(AppError::InvalidParams("outputMint is required".into()));
    }
    let resolved_in = resolve_token_address(&p.input_mint);
    let resolved_out = resolve_token_address(&p.output_mint);
    if resolved_in == resolved_out {
        return Err(AppError::InvalidParams(
            "inputMint and outputMint cannot be the same token".into(),
        ));
    }
    let amount: f64 = p
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    if let Some(slippage) = p.slippage_bps {
        if slippage > 5000 {
            return Err(AppError::InvalidParams(
                "slippageBps must be between 0 and 5000 (50%)".into(),
            ));
        }
    }
    if let Some(ref mode) = p.swap_mode {
        match mode.to_lowercase().as_str() {
            "in" | "exactin" | "out" | "exactout" => {}
            other => {
                return Err(AppError::InvalidParams(format!(
                    "Invalid swapMode '{other}'. Accepted: 'in' / 'ExactIn' or 'out' / 'ExactOut'"
                )))
            }
        }
    }
    Ok(())
}

pub fn validate_orca_add_liquidity_params(p: &OrcaAddLiquidityParams) -> Result<(), AppError> {
    if p.whirlpool.is_empty() {
        return Err(AppError::InvalidParams("whirlpool is required".into()));
    }
    // Validate whirlpool is a valid base58 pubkey.
    Pubkey::from_str(&p.whirlpool).map_err(|_| {
        AppError::InvalidParams(format!(
            "whirlpool '{}' is not a valid address",
            p.whirlpool
        ))
    })?;

    let a: f64 = p
        .amount_a
        .parse()
        .map_err(|_| AppError::InvalidParams("amountA must be a positive number".into()))?;
    if a <= 0.0 {
        return Err(AppError::InvalidParams("amountA must be positive".into()));
    }
    let b: f64 = p
        .amount_b
        .parse()
        .map_err(|_| AppError::InvalidParams("amountB must be a non-negative number".into()))?;
    if b < 0.0 {
        return Err(AppError::InvalidParams(
            "amountB must be non-negative".into(),
        ));
    }
    if let Some(slippage) = p.slippage_bps {
        if slippage > 5000 {
            return Err(AppError::InvalidParams(
                "slippageBps must be between 0 and 5000 (50%)".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_remove_liquidity_params(
    _p: &OrcaRemoveLiquidityParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_orca_open_position_params(p: &OrcaOpenPositionParams) -> Result<(), AppError> {
    // Must specify the pool via address or token pair.
    if p.whirlpool.is_none() && p.token_a.is_none() {
        return Err(AppError::InvalidParams(
            "Provide either (whirlpool + inputMint + inputAmount + minPrice + maxPrice) \
             or (tokenA + tokenB + amountA + minPrice + maxPrice) \
             or (whirlpool + tickLower + tickUpper + inputMint + inputAmount)"
                .into(),
        ));
    }
    // Validate whirlpool address when provided.
    if let Some(ref wp) = p.whirlpool {
        Pubkey::from_str(wp).map_err(|_| {
            AppError::InvalidParams(format!("whirlpool '{wp}' is not a valid address"))
        })?;
    }
    // If using the token-pair lookup path, both tokens must be specified.
    if p.token_a.is_some() && p.token_b.is_none() {
        return Err(AppError::InvalidParams(
            "tokenB is required when tokenA is provided".into(),
        ));
    }
    if p.token_b.is_some() && p.token_a.is_none() {
        return Err(AppError::InvalidParams(
            "tokenA is required when tokenB is provided".into(),
        ));
    }
    // Range: accept either tick indices OR prices — but exactly one pair must be complete.
    let has_ticks = p.tick_lower.is_some() && p.tick_upper.is_some();
    let has_prices = p.min_price.is_some() && p.max_price.is_some();
    if !has_ticks && !has_prices {
        return Err(AppError::InvalidParams(
            "Provide a price range: either (minPrice + maxPrice) or (tickLower + tickUpper)".into(),
        ));
    }
    if let (Some(mn), Some(mx)) = (p.min_price, p.max_price) {
        if mn >= mx {
            return Err(AppError::InvalidParams(
                "minPrice must be less than maxPrice".into(),
            ));
        }
        if mn <= 0.0 {
            return Err(AppError::InvalidParams("minPrice must be positive".into()));
        }
    }
    if let (Some(tl), Some(tu)) = (p.tick_lower, p.tick_upper) {
        if tl >= tu {
            return Err(AppError::InvalidParams(
                "tickLower must be less than tickUpper".into(),
            ));
        }
        if tl < MIN_TICK_INDEX || tu > MAX_TICK_INDEX {
            return Err(AppError::InvalidParams(format!(
                "Ticks must be in range [{MIN_TICK_INDEX}, {MAX_TICK_INDEX}]"
            )));
        }
    }
    // At least one of inputAmount / amountA must be a positive number.
    let amount_str = p
        .input_amount
        .as_deref()
        .or(p.amount_a.as_deref())
        .unwrap_or("");
    if amount_str.is_empty() {
        return Err(AppError::InvalidParams(
            "Provide an input amount: inputAmount (with inputMint) or amountA".into(),
        ));
    }
    let amount: f64 = amount_str.parse().map_err(|_| {
        AppError::InvalidParams("inputAmount / amountA must be a positive number".into())
    })?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams(
            "inputAmount / amountA must be greater than 0".into(),
        ));
    }

    if let Some(slippage) = p.slippage_bps {
        if slippage > 5000 {
            return Err(AppError::InvalidParams(
                "slippageBps must be between 0 and 5000 (50%)".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_close_position_params(p: &OrcaClosePositionParams) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    Pubkey::from_str(&p.position).map_err(|_| {
        AppError::InvalidParams(format!("position '{}' is not a valid address", p.position))
    })?;
    if let Some(slippage) = p.slippage_bps {
        if slippage > 5000 {
            return Err(AppError::InvalidParams(
                "slippageBps must be between 0 and 5000 (50%)".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_increase_position_params(
    p: &OrcaIncreasePositionParams,
) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    Pubkey::from_str(&p.position).map_err(|_| {
        AppError::InvalidParams(format!("position '{}' is not a valid address", p.position))
    })?;
    if p.input_mint.is_empty() {
        return Err(AppError::InvalidParams("inputMint is required".into()));
    }
    let amt: f64 = p
        .input_amount
        .parse()
        .map_err(|_| AppError::InvalidParams("inputAmount must be a positive number".into()))?;
    if amt <= 0.0 {
        return Err(AppError::InvalidParams(
            "inputAmount must be positive".into(),
        ));
    }
    if let Some(slippage) = p.slippage_bps {
        if slippage > 5000 {
            return Err(AppError::InvalidParams(
                "slippageBps must be between 0 and 5000 (50%)".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_decrease_position_params(
    p: &OrcaDecreasePositionParams,
) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    Pubkey::from_str(&p.position).map_err(|_| {
        AppError::InvalidParams(format!("position '{}' is not a valid address", p.position))
    })?;

    // Exactly one withdrawal mode must be specified.
    let has_liquidity = p
        .liquidity
        .as_deref()
        .map(|s| !s.is_empty())
        .unwrap_or(false);
    let has_token = p
        .input_mint
        .as_deref()
        .map(|s| !s.is_empty())
        .unwrap_or(false);

    if !has_liquidity && !has_token {
        return Err(AppError::InvalidParams(
            "Specify a withdrawal amount: either `liquidity` (raw u128) \
             or `inputMint` + `inputAmount` (token amount)"
                .into(),
        ));
    }
    if has_liquidity && has_token {
        return Err(AppError::InvalidParams(
            "`liquidity` and `inputMint`/`inputAmount` are mutually exclusive — specify only one"
                .into(),
        ));
    }

    if has_liquidity {
        let liq: u128 = p.liquidity.as_deref().unwrap().parse().map_err(|_| {
            AppError::InvalidParams(
                "liquidity must be a valid positive integer (u128, e.g. '500000000')".into(),
            )
        })?;
        if liq == 0 {
            return Err(AppError::InvalidParams(
                "liquidity must be greater than 0".into(),
            ));
        }
    }

    if has_token {
        let amount_str = p.input_amount.as_deref().unwrap_or("0");
        let amount: f64 = amount_str
            .parse()
            .map_err(|_| AppError::InvalidParams("inputAmount must be a positive number".into()))?;
        if amount <= 0.0 {
            return Err(AppError::InvalidParams(
                "inputAmount must be greater than 0".into(),
            ));
        }
    }

    if let Some(slippage) = p.slippage_bps {
        if slippage > 5000 {
            return Err(AppError::InvalidParams(
                "slippageBps must be between 0 and 5000 (50%)".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_collect_fees_params(p: &OrcaCollectFeesParams) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    Pubkey::from_str(&p.position).map_err(|_| {
        AppError::InvalidParams(format!("position '{}' is not a valid address", p.position))
    })?;
    Ok(())
}

pub fn validate_orca_collect_rewards_params(p: &OrcaCollectRewardsParams) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    Pubkey::from_str(&p.position).map_err(|_| {
        AppError::InvalidParams(format!("position '{}' is not a valid address", p.position))
    })?;
    if let Some(idx) = p.reward_index {
        if idx > 2 {
            return Err(AppError::InvalidParams(
                "rewardIndex must be 0, 1, or 2 (Whirlpools supports at most 3 rewards per pool)"
                    .into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_create_pool_params(p: &OrcaCreatePoolParams) -> Result<(), AppError> {
    if p.token_a.is_empty() {
        return Err(AppError::InvalidParams("tokenA is required".into()));
    }
    if p.token_b.is_empty() {
        return Err(AppError::InvalidParams("tokenB is required".into()));
    }
    if p.initial_price <= 0.0 {
        return Err(AppError::InvalidParams(
            "initialPrice must be positive".into(),
        ));
    }
    if let Some(ts) = p.tick_spacing {
        let valid: &[u16] = &[1, 2, 4, 8, 16, 32, 64, 128, 256, 32896];
        if !valid.contains(&ts) {
            return Err(AppError::InvalidParams(
                "tickSpacing must be one of: 1, 2, 4, 8, 16, 32, 64, 128, 256, 32896 (splash)"
                    .into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_get_pools_params(p: &OrcaGetPoolsParams) -> Result<(), AppError> {
    if let Some(ref s) = p.sort_by {
        let valid = [
            "volume",
            "tvl",
            "fees",
            "rewards",
            "yieldovertvl",
            "lockedliquiditypercent",
        ];
        if !valid.contains(&s.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "sortBy must be one of: {}",
                valid.join(", ")
            )));
        }
    }
    if let Some(ref d) = p.sort_direction {
        if d != "asc" && d != "desc" {
            return Err(AppError::InvalidParams(
                "sortDirection must be 'asc' or 'desc'".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_search_pools_params(p: &OrcaSearchPoolsParams) -> Result<(), AppError> {
    if p.q.is_empty() {
        return Err(AppError::InvalidParams(
            "q (search query) is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_orca_get_pool_params(p: &OrcaGetPoolParams) -> Result<(), AppError> {
    if p.address.is_empty() {
        return Err(AppError::InvalidParams("address is required".into()));
    }
    Ok(())
}

pub fn validate_orca_get_locked_liquidity_params(
    p: &OrcaGetLockedLiquidityParams,
) -> Result<(), AppError> {
    if p.address.is_empty() {
        return Err(AppError::InvalidParams("address is required".into()));
    }
    Ok(())
}

pub fn validate_orca_get_protocol_stats_params(
    _p: &OrcaGetProtocolStatsParams,
) -> Result<(), AppError> {
    Ok(())
}
pub fn validate_orca_get_orca_token_params(_p: &OrcaGetOrcaTokenParams) -> Result<(), AppError> {
    Ok(())
}
pub fn validate_orca_get_circulating_supply_params(
    _p: &OrcaGetCirculatingSupplyParams,
) -> Result<(), AppError> {
    Ok(())
}
pub fn validate_orca_get_total_supply_params(
    _p: &OrcaGetTotalSupplyParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_orca_get_tokens_params(p: &OrcaGetTokensParams) -> Result<(), AppError> {
    if let Some(ref s) = p.sort_by {
        let valid = ["address", "mint_id", "volume_24h"];
        if !valid.contains(&s.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "sort_by must be one of: {}",
                valid.join(", ")
            )));
        }
    }
    if let Some(ref d) = p.sort_direction {
        if d != "asc" && d != "desc" {
            return Err(AppError::InvalidParams(
                "sort_direction must be 'asc' or 'desc'".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_orca_search_tokens_params(p: &OrcaSearchTokensParams) -> Result<(), AppError> {
    if p.q.is_empty() {
        return Err(AppError::InvalidParams(
            "q (search query) is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_orca_get_token_params(p: &OrcaGetTokenParams) -> Result<(), AppError> {
    if p.mint_address.is_empty() {
        return Err(AppError::InvalidParams("mintAddress is required".into()));
    }
    Ok(())
}

pub fn validate_orca_get_user_positions_params(
    p: &OrcaGetUserPositionsParams,
) -> Result<(), AppError> {
    if let Some(ref w) = p.wallet {
        if !w.is_empty() {
            Pubkey::from_str(w).map_err(|_| {
                AppError::InvalidParams(format!("wallet '{w}' is not a valid address"))
            })?;
        }
    }
    Ok(())
}

pub fn validate_orca_get_pool_positions_params(
    p: &OrcaGetPoolPositionsParams,
) -> Result<(), AppError> {
    if p.whirlpool.is_empty() {
        return Err(AppError::InvalidParams("whirlpool is required".into()));
    }
    Pubkey::from_str(&p.whirlpool).map_err(|_| {
        AppError::InvalidParams(format!(
            "whirlpool '{}' is not a valid address",
            p.whirlpool
        ))
    })?;
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// On-chain data structs
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug)]
struct WhirlpoolOnchain {
    tick_spacing: i32,
    sqrt_price: u128, // Q64.64
    #[allow(dead_code)]
    tick_current_index: i32,
    token_mint_a: Pubkey,
    token_mint_b: Pubkey,
    token_vault_a: Pubkey,
    token_vault_b: Pubkey,
    reward_vaults: [Option<Pubkey>; 3],
    reward_mints: [Option<Pubkey>; 3],
}

#[derive(Debug)]
struct PositionOnchain {
    whirlpool: Pubkey,
    position_mint: Pubkey,
    liquidity: u128,
    tick_lower_index: i32,
    tick_upper_index: i32,
}

// ──────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ──────────────────────────────────────────────────────────────────────────────

fn parse_pubkey_bytes(data: &[u8], offset: usize) -> Result<Pubkey, AppError> {
    if data.len() < offset + 32 {
        return Err(AppError::ProtocolError("Account data too short".into()));
    }
    Pubkey::try_from(&data[offset..offset + 32])
        .map_err(|_| AppError::ProtocolError("Invalid pubkey bytes".into()))
}

/// Parse Whirlpool account data (raw bytes, including 8-byte discriminator).
fn parse_whirlpool(data: &[u8]) -> Result<WhirlpoolOnchain, AppError> {
    if data.len() < 269 + 3 * 128 {
        return Err(AppError::ProtocolError(
            "Whirlpool account data too short".into(),
        ));
    }
    // All offsets are absolute (including the 8-byte discriminator prefix).
    let tick_spacing = u16::from_le_bytes([data[41], data[42]]) as i32;
    let sqrt_price = u128::from_le_bytes(
        data[65..81]
            .try_into()
            .map_err(|_| AppError::ProtocolError("Whirlpool: invalid sqrt_price slice".into()))?,
    );
    let tick_current_index = i32::from_le_bytes(data[81..85].try_into().map_err(|_| {
        AppError::ProtocolError("Whirlpool: invalid tick_current_index slice".into())
    })?);
    let token_mint_a = parse_pubkey_bytes(data, 101)?;
    let token_vault_a = parse_pubkey_bytes(data, 133)?;
    let token_mint_b = parse_pubkey_bytes(data, 181)?;
    let token_vault_b = parse_pubkey_bytes(data, 213)?;

    // reward_infos[i] starts at offset 269 + i * 128
    let mut reward_mints: [Option<Pubkey>; 3] = [None, None, None];
    let mut reward_vaults: [Option<Pubkey>; 3] = [None, None, None];
    let zero = Pubkey::default();
    for i in 0..3 {
        let base = 269 + i * 128;
        let mint = parse_pubkey_bytes(data, base)?;
        let vault = parse_pubkey_bytes(data, base + 32)?;
        if mint != zero {
            reward_mints[i] = Some(mint);
            reward_vaults[i] = Some(vault);
        }
    }

    Ok(WhirlpoolOnchain {
        tick_spacing,
        sqrt_price,
        tick_current_index,
        token_mint_a,
        token_mint_b,
        token_vault_a,
        token_vault_b,
        reward_vaults,
        reward_mints,
    })
}

/// Parse Position account data (raw bytes, including 8-byte discriminator).
fn parse_position(data: &[u8]) -> Result<PositionOnchain, AppError> {
    // Full Whirlpool Position account is 216 bytes (8 disc + 32 whirlpool + 32 mint +
    // 16 liquidity + 4 lower + 4 upper + ... = 216 total).
    if data.len() < 216 {
        return Err(AppError::ProtocolError(format!(
            "Position account data too short: {} bytes (expected >= 216)",
            data.len()
        )));
    }
    let whirlpool = parse_pubkey_bytes(data, 8)?;
    let position_mint = parse_pubkey_bytes(data, 40)?;
    let liquidity = u128::from_le_bytes(
        data[72..88]
            .try_into()
            .map_err(|_| AppError::ProtocolError("Position: invalid liquidity slice".into()))?,
    );
    let tick_lower_index =
        i32::from_le_bytes(data[88..92].try_into().map_err(|_| {
            AppError::ProtocolError("Position: invalid tick_lower_index slice".into())
        })?);
    let tick_upper_index =
        i32::from_le_bytes(data[92..96].try_into().map_err(|_| {
            AppError::ProtocolError("Position: invalid tick_upper_index slice".into())
        })?);
    Ok(PositionOnchain {
        whirlpool,
        position_mint,
        liquidity,
        tick_lower_index,
        tick_upper_index,
    })
}

/// Create an async RPC client for the given URL.
fn make_rpc(url: &str) -> AsyncRpc {
    AsyncRpc::new_with_timeout_and_commitment(
        url.to_string(),
        Duration::from_secs(30),
        CommitmentConfig::confirmed(),
    )
}

/// Parse a base58 pubkey string.
fn pk(s: &str) -> Result<Pubkey, AppError> {
    Pubkey::from_str(s).map_err(|e| AppError::InvalidParams(format!("Invalid pubkey '{s}': {e}")))
}

/// Derive the Position PDA for a given position mint.
fn position_pda(position_mint: &Pubkey) -> Pubkey {
    let program = Pubkey::from_str(ORCA_WHIRLPOOL_PROGRAM_ID).unwrap();
    Pubkey::find_program_address(&[b"position", position_mint.as_ref()], &program).0
}

/// Convert a human-readable amount string to integer base units.
fn to_base_units(amount: &str, decimals: u8) -> Result<u64, AppError> {
    let f: f64 = amount
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid amount: {amount}")))?;
    if f < 0.0 {
        return Err(AppError::InvalidParams(format!(
            "Amount must be non-negative, got: {amount}"
        )));
    }
    Ok((f * 10_f64.powi(decimals as i32)).round() as u64)
}

fn token_decimals(symbol_or_mint: &str) -> u8 {
    get_token_info(symbol_or_mint)
        .map(|t| t.decimals)
        .unwrap_or(9)
}

fn token_symbol(symbol_or_mint: &str) -> String {
    get_token_info(symbol_or_mint)
        .map(|t| t.symbol.to_string())
        .unwrap_or_else(|| format!("{}…", &symbol_or_mint[..symbol_or_mint.len().min(8)]))
}

fn short_id(id: &str) -> String {
    format!("{}…", &id[..id.len().min(8)])
}

/// Convert tick index to f64 sqrt_price (not Q64.64).
fn tick_to_sqrt_price_f64(tick: i32) -> f64 {
    1.0001_f64.powf(tick as f64 / 2.0)
}

/// Fetch + parse Whirlpool account from chain.
async fn fetch_whirlpool(rpc: &AsyncRpc, address: &Pubkey) -> Result<WhirlpoolOnchain, AppError> {
    let account = rpc
        .get_account(address)
        .await
        .map_err(|e| AppError::ProtocolError(format!("Fetch whirlpool: {e}")))?;
    parse_whirlpool(&account.data)
}

/// Fetch + parse Position account from chain.
async fn fetch_position(rpc: &AsyncRpc, address: &Pubkey) -> Result<PositionOnchain, AppError> {
    let account = rpc
        .get_account(address)
        .await
        .map_err(|e| AppError::ProtocolError(format!("Fetch position: {e}")))?;
    parse_position(&account.data)
}

/// Resolve a position — accepts EITHER the position PDA address OR the position NFT mint.
///
/// Users typically see positions in their wallet by the NFT mint address.
/// The position PDA is `PDA(program, ["position", mint])`.
/// Both forms are accepted: we try the address as-is first, then try deriving the PDA.
async fn resolve_position(
    rpc: &AsyncRpc,
    addr_str: &str,
) -> Result<(Pubkey, PositionOnchain), AppError> {
    let addr = pk(addr_str)?;

    // Try interpreting as position PDA directly
    if let Ok(pos) = fetch_position(rpc, &addr).await {
        return Ok((addr, pos));
    }

    // Try interpreting as position NFT mint → derive PDA
    let derived = position_pda(&addr);
    let pos = fetch_position(rpc, &derived).await.map_err(|_| {
        AppError::InvalidParams(format!(
            "Position '{addr_str}' not found. \
             Provide either the position account address (PDA) or the position NFT mint address."
        ))
    })?;
    Ok((derived, pos))
}

/// Build, partially sign, and base64-encode a transaction.
///
/// Serialises as `VersionedTransaction` (Legacy message wrapped in v0 envelope) so that
/// the `/actions/simulate` endpoint (which deserialises as `VersionedTransaction`) and
/// modern wallet adapters both handle it correctly.
///
/// `extra_signers` are server-side keypairs (e.g., position mint keypair for openPosition).
/// The user's fee-payer signature slot is left blank for the frontend to fill.
async fn build_tx_b64(
    instructions: &[Instruction],
    payer: &Pubkey,
    extra_signers: &[Keypair],
    rpc: &AsyncRpc,
) -> Result<String, AppError> {
    let blockhash: SolanaHash = rpc
        .get_latest_blockhash()
        .await
        .map_err(|e| AppError::ProtocolError(format!("get_latest_blockhash: {e}")))?;

    let mut tx = Transaction::new_with_payer(instructions, Some(payer));
    if !extra_signers.is_empty() {
        let refs: Vec<&dyn Signer> = extra_signers.iter().map(|k| k as &dyn Signer).collect();
        tx.partial_sign(&refs, blockhash);
    } else {
        // Set the recent blockhash even when no partial signers are present.
        tx.message.recent_blockhash = blockhash;
    }

    // Wrap in VersionedTransaction so the simulate endpoint and modern wallets can
    // deserialise it without special-casing the legacy format.
    let versioned = VersionedTransaction::from(tx);
    let bytes = bincode::serialize(&versioned)
        .map_err(|e| AppError::Internal(format!("TX serialize: {e}")))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(bytes))
}

/// Look up the highest-TVL Orca Whirlpool for a token pair using the v2 REST API.
/// Returns `(pool_address, tick_spacing)`.
async fn lookup_orca_whirlpool(
    http: &reqwest::Client,
    mint_a: &str,
    mint_b: &str,
) -> Result<(String, i32), AppError> {
    let raw = http
        .get(format!("{ORCA_V2_API}/pools"))
        .query(&[
            ("tokensBothOf[]", mint_a),
            ("tokensBothOf[]", mint_b),
            ("sortBy", "tvl"),
            ("size", "1"),
        ])
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Orca pool lookup: {e}")))?;

    if !raw.status().is_success() {
        let status = raw.status().as_u16();
        let body = raw.text().await.unwrap_or_default();
        let preview = &body[..body.len().min(200)];
        return Err(AppError::ProtocolError(format!(
            "Orca API returned {status} for {mint_a}/{mint_b}: {preview}"
        )));
    }

    let resp: serde_json::Value = raw
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Orca pool lookup parse: {e}")))?;

    let pool = resp
        .get("data")
        .and_then(|d| d.as_array())
        .and_then(|a| a.first())
        .ok_or_else(|| {
            AppError::ProtocolError(format!("No Orca whirlpool found for {mint_a}/{mint_b}"))
        })?;

    let address = pool
        .get("address")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::ProtocolError("Orca pool response missing address".into()))?
        .to_string();

    let tick_spacing = pool
        .get("tickSpacing")
        .and_then(|v| v.as_i64())
        .unwrap_or(64) as i32;

    Ok((address, tick_spacing))
}

// ──────────────────────────────────────────────────────────────────────────────
// SDK v3 bridge helpers
// ──────────────────────────────────────────────────────────────────────────────

/// Create an SDK-compatible async RPC client (solana-rpc-client v3).
fn make_sdk_rpc(url: &str) -> SdkRpcClient {
    SdkRpcClient::new(url.to_string())
}

/// Convert our v2 `Pubkey` to the SDK's v3 `Pubkey`.
fn to_sdk_pk(pk: &Pubkey) -> SdkPubkey {
    SdkPubkey::new_from_array(pk.to_bytes())
}

/// Convert an SDK v3 `Pubkey` to our v2 `Pubkey`.
fn from_sdk_pk(pk: SdkPubkey) -> Pubkey {
    Pubkey::new_from_array(pk.to_bytes())
}

/// Convert an SDK v3 `Instruction` to our v2 `Instruction`.
fn from_sdk_ix(ix: SdkInstruction) -> Instruction {
    Instruction {
        program_id: from_sdk_pk(ix.program_id),
        accounts: ix
            .accounts
            .into_iter()
            .map(|am: SdkAccountMeta| AccountMeta {
                pubkey: from_sdk_pk(am.pubkey),
                is_signer: am.is_signer,
                is_writable: am.is_writable,
            })
            .collect(),
        data: ix.data,
    }
}

/// Convert an SDK v3 `Keypair` to our v2 `Keypair`.
fn from_sdk_kp(kp: SdkKeypair) -> Keypair {
    Keypair::from_bytes(&kp.to_bytes()).expect("keypair bytes are always valid")
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_swap  (Jupiter routing through Whirlpool DEX)
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_swap(
    http: &reqwest::Client,
    _rpc_url: &str,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &OrcaSwapParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_swap_params(params)?;
    pk(user_pubkey)?; // validate payer before hitting Jupiter

    let input_mint = resolve_token_address(&params.input_mint);
    let output_mint = resolve_token_address(&params.output_mint);
    let slippage_bps = params.slippage_bps.unwrap_or(50);
    let swap_mode = match params
        .swap_mode
        .as_deref()
        .unwrap_or("in")
        .to_lowercase()
        .as_str()
    {
        "out" | "exactout" => "ExactOut",
        _ => "ExactIn",
    };
    // For ExactIn  `amount` is the input  quantity — use input_mint  decimals.
    // For ExactOut `amount` is the output quantity — use output_mint decimals.
    let amount_decimals = if swap_mode == "ExactOut" {
        token_decimals(&params.output_mint)
    } else {
        token_decimals(&params.input_mint)
    };
    let amount_base = to_base_units(&params.amount, amount_decimals)?;

    // Validate mints are well-formed pubkeys before embedding in URL
    pk(&input_mint)?;
    pk(&output_mint)?;

    // Use paid endpoint (higher rate limits) when API key is present, fall back to public.
    let quote_url = if jupiter_api_key.is_some() {
        JUP_PAID_QUOTE
    } else {
        JUP_PUB_QUOTE
    };
    let swap_url = if jupiter_api_key.is_some() {
        JUP_PAID_SWAP
    } else {
        JUP_PUB_SWAP
    };

    // Quote via Jupiter with Orca Whirlpool DEX filter
    let amount_str = amount_base.to_string();
    let slippage_str = slippage_bps.to_string();
    let mut quote_req = http.get(quote_url).query(&[
        ("inputMint", input_mint.as_str()),
        ("outputMint", output_mint.as_str()),
        ("amount", amount_str.as_str()),
        ("slippageBps", slippage_str.as_str()),
        ("swapMode", swap_mode),
        ("dexes", "Whirlpool"),
    ]);
    if let Some(key) = jupiter_api_key {
        quote_req = quote_req.header("Authorization", format!("Bearer {key}"));
    }
    let quote_resp = quote_req
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter quote request: {e}")))?;
    if !quote_resp.status().is_success() {
        let body = quote_resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Jupiter Whirlpool quote: {body}"
        )));
    }
    let quote: serde_json::Value = quote_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter quote parse: {e}")))?;

    // Wire priority_fee to Jupiter's prioritizationFeeLamports
    let prioritization_fee = match params.priority_fee.as_deref() {
        Some("low") => serde_json::json!(1_000u64),
        Some("medium") => serde_json::json!(10_000u64),
        Some("high") => serde_json::json!(100_000u64),
        Some("auto") | None => serde_json::json!("auto"),
        Some(exact) => exact
            .parse::<u64>()
            .map(|n| serde_json::json!(n))
            .unwrap_or(serde_json::json!("auto")),
    };

    // Build swap TX via Jupiter
    let swap_body = serde_json::json!({
        "quoteResponse": quote,
        "userPublicKey": user_pubkey,
        "wrapAndUnwrapSol": true,
        "dynamicComputeUnitLimit": true,
        "prioritizationFeeLamports": prioritization_fee,
    });
    let mut swap_req = http.post(swap_url).json(&swap_body);
    if let Some(key) = jupiter_api_key {
        swap_req = swap_req.header("Authorization", format!("Bearer {key}"));
    }
    let swap_resp = swap_req
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter swap request: {e}")))?;
    if !swap_resp.status().is_success() {
        let body = swap_resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Jupiter swap build: {body}"
        )));
    }
    let swap_data: serde_json::Value = swap_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Jupiter swap parse: {e}")))?;
    let tx_b64 = swap_data["swapTransaction"]
        .as_str()
        .ok_or_else(|| {
            AppError::ProtocolError("Missing swapTransaction in Jupiter response".into())
        })?
        .to_string();

    let out_decimals = token_decimals(&params.output_mint);
    let out_amount = quote["outAmount"]
        .as_str()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(0);
    let estimated_out = out_amount as f64 / 10_f64.powi(out_decimals as i32);
    let price_impact: f64 = quote["priceImpactPct"]
        .as_str()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0.0);

    let in_sym = token_symbol(&params.input_mint);
    let out_sym = token_symbol(&params.output_mint);
    let mut warnings = vec![];
    if price_impact > 1.0 {
        warnings.push(format!("Price impact: {price_impact:.2}%"));
    }
    if price_impact > 5.0 {
        warnings.push("Very high price impact! Consider a smaller trade size.".into());
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_swap".to_string(),
            description: format!(
                "Swap {} {} → ~{:.6} {} via Orca Whirlpools",
                params.amount, in_sym, estimated_out, out_sym
            ),
            estimated_fee: "~0.005 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings,
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

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_add_liquidity (opens a full-range position + adds liquidity)
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_add_liquidity(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaAddLiquidityParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_add_liquidity_params(params)?;

    let rpc = make_rpc(rpc_url);
    let sdk_rpc = make_sdk_rpc(rpc_url);
    let pool_pk = pk(&params.whirlpool)?;
    let user_pk = pk(user_pubkey)?;

    // Fetch pool to determine token decimals for amount conversion.
    let pool = fetch_whirlpool(&rpc, &pool_pk).await?;
    let decimals_a = get_token_info(&pool.token_mint_a.to_string())
        .map(|t| t.decimals)
        .unwrap_or(9);
    let decimals_b = get_token_info(&pool.token_mint_b.to_string())
        .map(|t| t.decimals)
        .unwrap_or(9);

    let token_max_a = to_base_units(&params.amount_a, decimals_a)?;
    let token_max_b = to_base_units(&params.amount_b, decimals_b)?;
    let slippage_bps = params.slippage_bps.unwrap_or(100);

    let result = open_full_range_position_instructions(
        &sdk_rpc,
        to_sdk_pk(&pool_pk),
        IncreaseLiquidityParam {
            token_max_a,
            token_max_b,
        },
        Some(slippage_bps as u16),
        Some(to_sdk_pk(&user_pk)),
    )
    .await
    .map_err(|e| AppError::ProtocolError(format!("open_full_range_position: {e}")))?;

    let position_mint_pk = from_sdk_pk(result.position_mint);
    let init_cost = result.initialization_cost;
    let ixs: Vec<Instruction> = result.instructions.into_iter().map(from_sdk_ix).collect();
    let signers: Vec<Keypair> = result
        .additional_signers
        .into_iter()
        .map(from_sdk_kp)
        .collect();

    let tx_b64 = build_tx_b64(&ixs, &user_pk, &signers, &rpc).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_add_liquidity".to_string(),
            description: format!(
                "Open full-range position on Whirlpool {} — NFT: {}",
                short_id(&params.whirlpool),
                short_id(&position_mint_pk.to_string())
            ),
            estimated_fee: format!("~{:.4} SOL (init + tx)", init_cost as f64 / 1e9),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                format!("Position NFT: {position_mint_pk}"),
                "Impermanent loss risk — prices may diverge from entry".into(),
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

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_remove_liquidity (deprecated)
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_remove_liquidity(
    _http: &reqwest::Client,
    _rpc_url: &str,
    _user_pubkey: &str,
    _params: &OrcaRemoveLiquidityParams,
) -> Result<BuildResponse, AppError> {
    Err(AppError::InvalidParams(
        "orca_remove_liquidity is not supported in the Whirlpools CLMM model. \
         Use orca_close_position to withdraw all liquidity, or \
         orca_decrease_position to partially withdraw."
            .into(),
    ))
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_open_position (concentrated liquidity, custom price range)
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_open_position(
    http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaOpenPositionParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_open_position_params(params)?;

    let rpc = make_rpc(rpc_url);
    let sdk_rpc = make_sdk_rpc(rpc_url);
    let user_pk = pk(user_pubkey)?;

    // Resolve pool address (from param or look up via token pair).
    let pool_addr = if let Some(ref wp) = params.whirlpool {
        wp.clone()
    } else {
        let ta = params.token_a.as_deref().unwrap_or("");
        let tb = params.token_b.as_deref().unwrap_or("");
        let (addr, _) =
            lookup_orca_whirlpool(http, &resolve_token_address(ta), &resolve_token_address(tb))
                .await?;
        addr
    };

    let pool_pk = pk(&pool_addr)?;

    // Fetch pool to determine which mint is token A (for input routing).
    let pool = fetch_whirlpool(&rpc, &pool_pk).await?;

    let input_sym = params
        .input_mint
        .as_deref()
        .or(params.token_a.as_deref())
        .unwrap_or("");
    let amount_str = params
        .input_amount
        .as_deref()
        .or(params.amount_a.as_deref())
        .unwrap_or("0");
    let in_decimals = token_decimals(input_sym);
    let amount_base = to_base_units(amount_str, in_decimals)?;
    let slippage_bps = params.slippage_bps.unwrap_or(100);

    // Route input to the correct side; cap the other side at u64::MAX.
    let input_is_a =
        !input_sym.is_empty() && resolve_token_address(input_sym) == pool.token_mint_a.to_string();
    let (token_max_a, token_max_b) = if input_is_a {
        (amount_base, u64::MAX)
    } else {
        (u64::MAX, amount_base)
    };
    let liquidity_param = IncreaseLiquidityParam {
        token_max_a,
        token_max_b,
    };

    let result = if let (Some(tl), Some(tu)) = (params.tick_lower, params.tick_upper) {
        // Direct tick bounds — SDK validates alignment to tick_spacing.
        open_position_instructions_with_tick_bounds(
            &sdk_rpc,
            to_sdk_pk(&pool_pk),
            tl,
            tu,
            liquidity_param,
            Some(slippage_bps as u16),
            Some(to_sdk_pk(&user_pk)),
        )
        .await
        .map_err(|e| AppError::ProtocolError(format!("open_position_with_tick_bounds: {e}")))?
    } else {
        // Price-range path — SDK converts prices to aligned tick indices.
        let min_price = params
            .min_price
            .ok_or_else(|| AppError::InvalidParams("minPrice is required".into()))?;
        let max_price = params
            .max_price
            .ok_or_else(|| AppError::InvalidParams("maxPrice is required".into()))?;
        open_position_instructions(
            &sdk_rpc,
            to_sdk_pk(&pool_pk),
            min_price,
            max_price,
            liquidity_param,
            Some(slippage_bps as u16),
            Some(to_sdk_pk(&user_pk)),
        )
        .await
        .map_err(|e| AppError::ProtocolError(format!("open_position: {e}")))?
    };

    let position_mint_pk = from_sdk_pk(result.position_mint);
    let init_cost = result.initialization_cost;
    let ixs: Vec<Instruction> = result.instructions.into_iter().map(from_sdk_ix).collect();
    let signers: Vec<Keypair> = result
        .additional_signers
        .into_iter()
        .map(from_sdk_kp)
        .collect();

    let tx_b64 = build_tx_b64(&ixs, &user_pk, &signers, &rpc).await?;
    let in_sym = token_symbol(input_sym);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_open_position".to_string(),
            description: format!(
                "Open Whirlpool position: {amount_str} {in_sym} in pool {} — NFT: {}",
                short_id(&pool_addr),
                short_id(&position_mint_pk.to_string())
            ),
            estimated_fee: format!("~{:.4} SOL (init + tx)", init_cost as f64 / 1e9),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                format!("Position NFT: {position_mint_pk}"),
                "Concentrated liquidity earns fees only while price stays within your range".into(),
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

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_close_position
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_close_position(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaClosePositionParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_close_position_params(params)?;

    let rpc = make_rpc(rpc_url);
    let sdk_rpc = make_sdk_rpc(rpc_url);
    let user_pk = pk(user_pubkey)?;

    // Resolve position: accept PDA or NFT mint address.
    let (_, pos) = resolve_position(&rpc, &params.position).await?;
    let slippage_bps = params.slippage_bps.unwrap_or(100);

    let result = close_position_instructions(
        &sdk_rpc,
        to_sdk_pk(&pos.position_mint),
        Some(slippage_bps as u16),
        Some(to_sdk_pk(&user_pk)),
    )
    .await
    .map_err(|e| AppError::ProtocolError(format!("close_position: {e}")))?;

    let ixs: Vec<Instruction> = result.instructions.into_iter().map(from_sdk_ix).collect();
    let signers: Vec<Keypair> = result
        .additional_signers
        .into_iter()
        .map(from_sdk_kp)
        .collect();

    let tx_b64 = build_tx_b64(&ixs, &user_pk, &signers, &rpc).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_close_position".to_string(),
            description: format!(
                "Close Whirlpool position {} — withdraw liquidity, collect fees/rewards, burn NFT",
                short_id(&params.position)
            ),
            estimated_fee: "~0.005 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
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

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_increase_position
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_increase_position(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaIncreasePositionParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_increase_position_params(params)?;

    let rpc = make_rpc(rpc_url);
    let sdk_rpc = make_sdk_rpc(rpc_url);
    let user_pk = pk(user_pubkey)?;

    // Resolve position to obtain the NFT mint address and pool.
    let (_, pos) = resolve_position(&rpc, &params.position).await?;
    let pool = fetch_whirlpool(&rpc, &pos.whirlpool).await?;

    let in_decimals = token_decimals(&params.input_mint);
    let amount_base = to_base_units(&params.input_amount, in_decimals)?;
    let slippage_bps = params.slippage_bps.unwrap_or(100);

    // Route input to the correct side (A or B); cap the other side at u64::MAX.
    let input_is_a = resolve_token_address(&params.input_mint) == pool.token_mint_a.to_string();
    let (token_max_a, token_max_b) = if input_is_a {
        (amount_base, u64::MAX)
    } else {
        (u64::MAX, amount_base)
    };

    let result = increase_liquidity_instructions(
        &sdk_rpc,
        to_sdk_pk(&pos.position_mint),
        IncreaseLiquidityParam {
            token_max_a,
            token_max_b,
        },
        Some(slippage_bps as u16),
        Some(to_sdk_pk(&user_pk)),
    )
    .await
    .map_err(|e| AppError::ProtocolError(format!("increase_liquidity: {e}")))?;

    let ixs: Vec<Instruction> = result.instructions.into_iter().map(from_sdk_ix).collect();
    let signers: Vec<Keypair> = result
        .additional_signers
        .into_iter()
        .map(from_sdk_kp)
        .collect();

    let tx_b64 = build_tx_b64(&ixs, &user_pk, &signers, &rpc).await?;
    let sym = token_symbol(&params.input_mint);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_increase_position".to_string(),
            description: format!(
                "Add {} {} to Whirlpool position {}",
                params.input_amount,
                sym,
                short_id(&params.position)
            ),
            estimated_fee: "~0.005 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
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

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_decrease_position
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_decrease_position(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaDecreasePositionParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_decrease_position_params(params)?;

    let rpc = make_rpc(rpc_url);
    let sdk_rpc = make_sdk_rpc(rpc_url);
    let user_pk = pk(user_pubkey)?;

    // Resolve position to obtain the NFT mint address.
    let (_, pos) = resolve_position(&rpc, &params.position).await?;
    let slippage_bps = params.slippage_bps.unwrap_or(100);

    // Build the SDK withdrawal param based on which mode was specified (validated above).
    let sdk_param: DecreaseLiquidityParam;
    let description: String;

    if let Some(ref liq_str) = params.liquidity {
        // Liquidity-units mode.
        let liquidity: u128 = liq_str.parse().map_err(|_| {
            AppError::InvalidParams("liquidity must be a valid u128 integer".into())
        })?;
        if liquidity > pos.liquidity {
            return Err(AppError::InvalidParams(format!(
                "Requested liquidity {liquidity} exceeds position liquidity {} — \
                 use orca_close_position to withdraw everything",
                pos.liquidity
            )));
        }
        sdk_param = DecreaseLiquidityParam::Liquidity(liquidity);
        description = format!(
            "Remove {} liquidity units from Whirlpool position {}",
            liq_str,
            short_id(&params.position)
        );
    } else {
        // Token-amount mode — resolve which side of the pool the input mint is on.
        let input_sym = params.input_mint.as_deref().unwrap_or("");
        let amount_str = params.input_amount.as_deref().unwrap_or("0");
        let in_decimals = token_decimals(input_sym);
        let amount_base = to_base_units(amount_str, in_decimals)?;

        let pool = fetch_whirlpool(&rpc, &pos.whirlpool).await?;
        let input_is_a = !input_sym.is_empty()
            && resolve_token_address(input_sym) == pool.token_mint_a.to_string();

        sdk_param = if input_is_a {
            DecreaseLiquidityParam::TokenA(amount_base)
        } else {
            DecreaseLiquidityParam::TokenB(amount_base)
        };
        let in_sym = token_symbol(input_sym);
        description = format!(
            "Withdraw {amount_str} {in_sym} from Whirlpool position {}",
            short_id(&params.position)
        );
    }

    let result = decrease_liquidity_instructions(
        &sdk_rpc,
        to_sdk_pk(&pos.position_mint),
        sdk_param,
        Some(slippage_bps as u16),
        Some(to_sdk_pk(&user_pk)),
    )
    .await
    .map_err(|e| AppError::ProtocolError(format!("decrease_liquidity: {e}")))?;

    let ixs: Vec<Instruction> = result.instructions.into_iter().map(from_sdk_ix).collect();
    let signers: Vec<Keypair> = result
        .additional_signers
        .into_iter()
        .map(from_sdk_kp)
        .collect();

    let tx_b64 = build_tx_b64(&ixs, &user_pk, &signers, &rpc).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_decrease_position".to_string(),
            description,
            estimated_fee: "~0.003 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
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

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_collect_fees
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_collect_fees(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaCollectFeesParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_collect_fees_params(params)?;

    let rpc = make_rpc(rpc_url);
    let sdk_rpc = make_sdk_rpc(rpc_url);
    let user_pk = pk(user_pubkey)?;

    // Resolve position to obtain the NFT mint address.
    let (_, pos) = resolve_position(&rpc, &params.position).await?;

    let result = harvest_position_instructions(
        &sdk_rpc,
        to_sdk_pk(&pos.position_mint),
        Some(to_sdk_pk(&user_pk)),
    )
    .await
    .map_err(|e| AppError::ProtocolError(format!("harvest_position (fees): {e}")))?;

    let fee_a = result.fees_quote.fee_owed_a;
    let fee_b = result.fees_quote.fee_owed_b;
    let has_rewards = result
        .rewards_quote
        .rewards
        .iter()
        .any(|r| r.rewards_owed > 0);
    let ixs: Vec<Instruction> = result.instructions.into_iter().map(from_sdk_ix).collect();
    let signers: Vec<Keypair> = result
        .additional_signers
        .into_iter()
        .map(from_sdk_kp)
        .collect();

    let tx_b64 = build_tx_b64(&ixs, &user_pk, &signers, &rpc).await?;

    let mut warnings: Vec<String> = vec![];
    if has_rewards {
        warnings.push(
            "This transaction also collects all accrued rewards — \
             the Orca SDK harvests fees and rewards together in one instruction."
                .into(),
        );
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_collect_fees".to_string(),
            description: format!(
                "Collect fees from Whirlpool position {} (fee_a={fee_a}, fee_b={fee_b})",
                short_id(&params.position),
            ),
            estimated_fee: "~0.002 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings,
            requires_approval: false,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_collect_rewards
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_collect_rewards(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaCollectRewardsParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_collect_rewards_params(params)?;

    let rpc = make_rpc(rpc_url);
    let sdk_rpc = make_sdk_rpc(rpc_url);
    let user_pk = pk(user_pubkey)?;

    // Resolve position to obtain the NFT mint address.
    let (_, pos) = resolve_position(&rpc, &params.position).await?;

    let result = harvest_position_instructions(
        &sdk_rpc,
        to_sdk_pk(&pos.position_mint),
        Some(to_sdk_pk(&user_pk)),
    )
    .await
    .map_err(|e| AppError::ProtocolError(format!("harvest_position (rewards): {e}")))?;

    // Validate that rewards exist (optionally scoped to a single index).
    let target_index = params.reward_index;
    let active_count = if let Some(idx) = target_index {
        let owed = result
            .rewards_quote
            .rewards
            .get(idx as usize)
            .map(|r| r.rewards_owed)
            .unwrap_or(0);
        if owed == 0 {
            return Err(AppError::ProtocolError(format!(
                "Reward index {idx} has no active emission on this pool"
            )));
        }
        1
    } else {
        let count = result
            .rewards_quote
            .rewards
            .iter()
            .filter(|r| r.rewards_owed > 0)
            .count();
        if count == 0 {
            return Err(AppError::ProtocolError(
                "No active reward emissions found on this pool".into(),
            ));
        }
        count
    };

    let ixs: Vec<Instruction> = result.instructions.into_iter().map(from_sdk_ix).collect();
    let signers: Vec<Keypair> = result
        .additional_signers
        .into_iter()
        .map(from_sdk_kp)
        .collect();

    let tx_b64 = build_tx_b64(&ixs, &user_pk, &signers, &rpc).await?;

    // If the caller scoped to a specific reward index, warn that the SDK harvests everything.
    let mut warnings: Vec<String> = vec![];
    if let Some(idx) = params.reward_index {
        warnings.push(format!(
            "rewardIndex {idx} was validated, but the transaction collects ALL active rewards \
             (and any accrued fees) in one instruction — the Orca SDK does not support \
             per-index selective harvesting."
        ));
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_collect_rewards".to_string(),
            description: format!(
                "Collect {active_count} reward(s) from Whirlpool position {}",
                short_id(&params.position)
            ),
            estimated_fee: "~0.002 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings,
            requires_approval: false,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// REST API helper
// ──────────────────────────────────────────────────────────────────────────────

async fn orca_get(http: &reqwest::Client, url: &str) -> Result<serde_json::Value, AppError> {
    let resp = http
        .get(url)
        .header("Accept", "application/json")
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Orca GET error: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        let preview = &body[..body.len().min(300)];
        return Err(AppError::ProtocolError(format!(
            "Orca API {status}: {preview}"
        )));
    }
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Orca GET parse: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_create_pool
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_create_pool(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaCreatePoolParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_create_pool_params(params)?;

    let rpc = make_rpc(rpc_url);
    let sdk_rpc = make_sdk_rpc(rpc_url);
    let user_pk = pk(user_pubkey)?;

    let mint_a_str = resolve_token_address(&params.token_a);
    let mint_b_str = resolve_token_address(&params.token_b);
    let raw_a = pk(&mint_a_str)?;
    let raw_b = pk(&mint_b_str)?;

    // SDK requires token_a <= token_b in canonical byte order.
    let (mint_a, mint_b, display_price) = if raw_a.as_ref() <= raw_b.as_ref() {
        (raw_a, raw_b, params.initial_price)
    } else {
        (raw_b, raw_a, 1.0 / params.initial_price)
    };

    let tick_spacing = params.tick_spacing.unwrap_or(128);
    let sym_a = token_symbol(&params.token_a);
    let sym_b = token_symbol(&params.token_b);

    let result = create_concentrated_liquidity_pool_instructions(
        &sdk_rpc,
        to_sdk_pk(&mint_a),
        to_sdk_pk(&mint_b),
        tick_spacing,
        Some(display_price),
        Some(to_sdk_pk(&user_pk)),
    )
    .await
    .map_err(|e| AppError::ProtocolError(format!("create_concentrated_pool: {e}")))?;

    let pool_addr = from_sdk_pk(result.pool_address);
    let init_cost = result.initialization_cost;
    let ixs: Vec<Instruction> = result.instructions.into_iter().map(from_sdk_ix).collect();
    let signers: Vec<Keypair> = result
        .additional_signers
        .into_iter()
        .map(from_sdk_kp)
        .collect();

    let tx_b64 = build_tx_b64(&ixs, &user_pk, &signers, &rpc).await?;

    let pool_type_str = if tick_spacing == 32896 {
        "splash"
    } else {
        "concentrated"
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_create_pool".to_string(),
            description: format!(
                "Create Orca {pool_type_str} pool {sym_a}/{sym_b} \
                 (tickSpacing={tick_spacing}, initialPrice={:.6})",
                params.initial_price
            ),
            estimated_fee: format!("~{:.4} SOL (init + tx)", init_cost as f64 / 1e9),
            estimated_refund: None,
            params: serde_json::json!({
                "whirlpool": pool_addr.to_string(),
                "tokenA": mint_a.to_string(),
                "tokenB": mint_b.to_string(),
                "tickSpacing": tick_spacing,
                "initialPrice": display_price,
            }),
            warnings: vec![
                format!("Pool address: {pool_addr}"),
                "After pool creation, tick arrays must be initialized before opening positions"
                    .to_string(),
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

// ──────────────────────────────────────────────────────────────────────────────
// Build — GET query actions (REST API: https://api.orca.so/v2/solana)
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_orca_get_pools(
    http: &reqwest::Client,
    params: &OrcaGetPoolsParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{ORCA_V2_API}/pools?");
    if let Some(ref s) = params.sort_by {
        url.push_str(&format!("sortBy={s}&"));
    }
    if let Some(ref d) = params.sort_direction {
        url.push_str(&format!("sortDirection={d}&"));
    }
    if let Some(n) = params.size {
        url.push_str(&format!("size={n}&"));
    }
    if let Some(ref n) = params.next {
        url.push_str(&format!("next={n}&"));
    }
    if let Some(ref p) = params.previous {
        url.push_str(&format!("previous={p}&"));
    }
    if let Some(r) = params.has_rewards {
        url.push_str(&format!("hasRewards={r}&"));
    }
    if let Some(w) = params.has_warning {
        url.push_str(&format!("hasWarning={w}&"));
    }
    if let Some(a) = params.has_adaptive_fee {
        url.push_str(&format!("hasAdaptiveFee={a}&"));
    }
    if let Some(w) = params.is_wavebreak {
        url.push_str(&format!("isWavebreak={w}&"));
    }
    if let Some(v) = params.min_tvl {
        url.push_str(&format!("minTvl={v}&"));
    }
    if let Some(v) = params.min_volume {
        url.push_str(&format!("minVolume={v}&"));
    }
    if let Some(v) = params.min_locked_liquidity_percent {
        url.push_str(&format!("minLockedLiquidityPercent={v}&"));
    }
    if let Some(ref t) = params.token {
        let resolved = resolve_token_address(t);
        url.push_str(&format!("token={resolved}&"));
    }
    if let Some(ref t) = params.tokens_both_of {
        // Orca API expects repeated tokensBothOf[]=mint1&tokensBothOf[]=mint2
        for mint in t.split(',').map(|s| s.trim()).filter(|s| !s.is_empty()) {
            url.push_str(&format!("tokensBothOf[]={mint}&"));
        }
    }
    if let Some(ref a) = params.addresses {
        url.push_str(&format!("addresses={a}&"));
    }
    if let Some(ref s) = params.stats {
        url.push_str(&format!("stats={s}&"));
    }
    if let Some(b) = params.include_blocked {
        url.push_str(&format!("includeBlocked={b}&"));
    }

    let data = orca_get(http, url.trim_end_matches('&')).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_pools".to_string(),
            description: "Orca Whirlpools pool list".to_string(),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_search_pools(
    http: &reqwest::Client,
    params: &OrcaSearchPoolsParams,
) -> Result<BuildResponse, AppError> {
    let q_enc: String = params
        .q
        .chars()
        .flat_map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '~') {
                vec![c]
            } else if c == ' ' {
                vec!['+']
            } else {
                format!("%{:02X}", c as u32).chars().collect()
            }
        })
        .collect();
    let mut url = format!("{ORCA_V2_API}/pools/search?q={q_enc}");
    if let Some(n) = params.size {
        url.push_str(&format!("&size={n}"));
    }
    if let Some(ref n) = params.next {
        url.push_str(&format!("&next={n}"));
    }
    if let Some(ref s) = params.sort_by {
        url.push_str(&format!("&sortBy={s}"));
    }
    if let Some(ref d) = params.sort_direction {
        url.push_str(&format!("&sortDirection={d}"));
    }
    if let Some(v) = params.min_tvl {
        url.push_str(&format!("&minTvl={v}"));
    }
    if let Some(v) = params.min_volume {
        url.push_str(&format!("&minVolume={v}"));
    }
    if let Some(ref s) = params.stats {
        url.push_str(&format!("&stats={s}"));
    }
    if let Some(ref t) = params.user_tokens {
        url.push_str(&format!("&userTokens={t}"));
    }
    if let Some(r) = params.has_rewards {
        url.push_str(&format!("&hasRewards={r}"));
    }
    if let Some(v) = params.verified_only {
        url.push_str(&format!("&verifiedOnly={v}"));
    }
    if let Some(l) = params.has_locked_liquidity {
        url.push_str(&format!("&hasLockedLiquidity={l}"));
    }

    let data = orca_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_search_pools".to_string(),
            description: format!("Orca pool search: \"{}\"", params.q),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_get_pool(
    http: &reqwest::Client,
    params: &OrcaGetPoolParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{ORCA_V2_API}/pools/{}", params.address);
    if let Some(ref s) = params.stats {
        url.push_str(&format!("?stats={s}"));
    }
    let data = orca_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_pool".to_string(),
            description: format!("Orca pool details: {}", short_id(&params.address)),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_get_locked_liquidity(
    http: &reqwest::Client,
    params: &OrcaGetLockedLiquidityParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{ORCA_V2_API}/lock/{}", params.address);
    let data = orca_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_locked_liquidity".to_string(),
            description: format!("Orca locked liquidity for: {}", short_id(&params.address)),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_get_protocol_stats(
    http: &reqwest::Client,
    _params: &OrcaGetProtocolStatsParams,
) -> Result<BuildResponse, AppError> {
    let data = orca_get(http, &format!("{ORCA_V2_API}/protocol")).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_protocol_stats".to_string(),
            description: "Orca protocol TVL, volume, fees and revenue".to_string(),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_get_orca_token(
    http: &reqwest::Client,
    _params: &OrcaGetOrcaTokenParams,
) -> Result<BuildResponse, AppError> {
    let data = orca_get(http, &format!("{ORCA_V2_API}/protocol/token")).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_orca_token".to_string(),
            description: "ORCA token price, supply and stats".to_string(),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_get_circulating_supply(
    http: &reqwest::Client,
    _params: &OrcaGetCirculatingSupplyParams,
) -> Result<BuildResponse, AppError> {
    let data = orca_get(
        http,
        &format!("{ORCA_V2_API}/protocol/token/circulating_supply"),
    )
    .await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_circulating_supply".to_string(),
            description: "ORCA token circulating supply".to_string(),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_get_total_supply(
    http: &reqwest::Client,
    _params: &OrcaGetTotalSupplyParams,
) -> Result<BuildResponse, AppError> {
    let data = orca_get(http, &format!("{ORCA_V2_API}/protocol/token/total_supply")).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_total_supply".to_string(),
            description: "ORCA token total supply".to_string(),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_get_tokens(
    http: &reqwest::Client,
    params: &OrcaGetTokensParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{ORCA_V2_API}/tokens?");
    if let Some(n) = params.size {
        url.push_str(&format!("size={n}&"));
    }
    if let Some(ref n) = params.next {
        url.push_str(&format!("next={n}&"));
    }
    if let Some(ref p) = params.previous {
        url.push_str(&format!("previous={p}&"));
    }
    if let Some(ref s) = params.sort_by {
        url.push_str(&format!("sort_by={s}&"));
    }
    if let Some(ref d) = params.sort_direction {
        url.push_str(&format!("sort_direction={d}&"));
    }
    if let Some(ref t) = params.tokens {
        url.push_str(&format!("tokens={t}&"));
    }

    let data = orca_get(http, url.trim_end_matches('&')).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_tokens".to_string(),
            description: "Orca token list".to_string(),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_search_tokens(
    http: &reqwest::Client,
    params: &OrcaSearchTokensParams,
) -> Result<BuildResponse, AppError> {
    let q_enc: String = params
        .q
        .chars()
        .flat_map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '~') {
                vec![c]
            } else if c == ' ' {
                vec!['+']
            } else {
                format!("%{:02X}", c as u32).chars().collect()
            }
        })
        .collect();
    let url = format!("{ORCA_V2_API}/tokens/search?q={q_enc}");
    let data = orca_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_search_tokens".to_string(),
            description: format!("Orca token search: \"{}\"", params.q),
            estimated_fee: "0".to_string(),
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

pub async fn build_orca_get_token(
    http: &reqwest::Client,
    params: &OrcaGetTokenParams,
) -> Result<BuildResponse, AppError> {
    let mint = resolve_token_address(&params.mint_address);
    let url = format!("{ORCA_V2_API}/tokens/{mint}");
    let data = orca_get(http, &url).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_token".to_string(),
            description: format!("Orca token: {}", params.mint_address),
            estimated_fee: "0".to_string(),
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

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_get_user_positions (on-chain via SDK)
// ──────────────────────────────────────────────────────────────────────────────

/// Query all Orca Whirlpool positions owned by a wallet.
///
/// Uses `orca_whirlpools::fetch_positions_for_owner` which queries both the SPL
/// Token program and the Token-2022 program, and handles position bundles
/// (multiple positions under one NFT) in addition to plain positions.
pub async fn build_orca_get_user_positions(
    _http: &reqwest::Client,
    rpc_url: &str,
    user_pubkey: &str,
    params: &OrcaGetUserPositionsParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_get_user_positions_params(params)?;

    let wallet_str = params.wallet.as_deref().unwrap_or(user_pubkey);
    let wallet_pk = pk(wallet_str)?;
    let sdk_rpc = make_sdk_rpc(rpc_url);

    let all = fetch_positions_for_owner(&sdk_rpc, to_sdk_pk(&wallet_pk))
        .await
        .map_err(|e| AppError::ProtocolError(format!("fetch_positions_for_owner: {e}")))?;

    let mut positions: Vec<serde_json::Value> = Vec::new();

    for item in all {
        match item {
            PositionOrBundle::Position(hp) => {
                let price_lower = tick_to_sqrt_price_f64(hp.data.tick_lower_index).powi(2);
                let price_upper = tick_to_sqrt_price_f64(hp.data.tick_upper_index).powi(2);
                positions.push(serde_json::json!({
                    "type": "position",
                    "positionAddress": hp.address.to_string(),
                    "positionMint": hp.data.position_mint.to_string(),
                    "whirlpool": hp.data.whirlpool.to_string(),
                    "liquidity": hp.data.liquidity.to_string(),
                    "tickLowerIndex": hp.data.tick_lower_index,
                    "tickUpperIndex": hp.data.tick_upper_index,
                    "priceLower": price_lower,
                    "priceUpper": price_upper,
                    "feeOwedA": hp.data.fee_owed_a,
                    "feeOwedB": hp.data.fee_owed_b,
                }));
            }
            PositionOrBundle::PositionBundle(bundle) => {
                let bundled: Vec<serde_json::Value> = bundle
                    .positions
                    .iter()
                    .map(|bp| {
                        let price_lower = tick_to_sqrt_price_f64(bp.data.tick_lower_index).powi(2);
                        let price_upper = tick_to_sqrt_price_f64(bp.data.tick_upper_index).powi(2);
                        serde_json::json!({
                            "positionAddress": bp.address.to_string(),
                            "whirlpool": bp.data.whirlpool.to_string(),
                            "liquidity": bp.data.liquidity.to_string(),
                            "tickLowerIndex": bp.data.tick_lower_index,
                            "tickUpperIndex": bp.data.tick_upper_index,
                            "priceLower": price_lower,
                            "priceUpper": price_upper,
                            "feeOwedA": bp.data.fee_owed_a,
                            "feeOwedB": bp.data.fee_owed_b,
                        })
                    })
                    .collect();
                positions.push(serde_json::json!({
                    "type": "bundle",
                    "positionBundleAddress": bundle.address.to_string(),
                    "positionBundleMint": bundle.data.position_bundle_mint.to_string(),
                    "positions": bundled,
                }));
            }
        }
    }

    let total = positions.len();
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_user_positions".to_string(),
            description: format!(
                "Orca positions for {} ({total} found)",
                short_id(wallet_str)
            ),
            estimated_fee: "0".to_string(),
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
        data: Some(serde_json::json!({ "positions": positions, "total": total, "wallet": wallet_str })),
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — orca_get_pool_positions (on-chain via SDK)
// ──────────────────────────────────────────────────────────────────────────────

/// Query all open positions inside a specific Orca Whirlpool pool.
///
/// Uses `orca_whirlpools::fetch_positions_in_whirlpool` which calls
/// `getProgramAccounts` with the whirlpool filter internally and decodes
/// each Position account using the SDK's Borsh deserializer.
pub async fn build_orca_get_pool_positions(
    _http: &reqwest::Client,
    rpc_url: &str,
    params: &OrcaGetPoolPositionsParams,
) -> Result<BuildResponse, AppError> {
    validate_orca_get_pool_positions_params(params)?;

    let whirlpool_pk = pk(&params.whirlpool)?;
    let sdk_rpc = make_sdk_rpc(rpc_url);

    let all = fetch_positions_in_whirlpool(&sdk_rpc, to_sdk_pk(&whirlpool_pk))
        .await
        .map_err(|e| AppError::ProtocolError(format!("fetch_positions_in_whirlpool: {e}")))?;

    let positions: Vec<serde_json::Value> = all
        .iter()
        .map(|decoded| {
            let pos = &decoded.data;
            let price_lower = tick_to_sqrt_price_f64(pos.tick_lower_index).powi(2);
            let price_upper = tick_to_sqrt_price_f64(pos.tick_upper_index).powi(2);
            serde_json::json!({
                "positionAddress": decoded.address.to_string(),
                "positionMint": pos.position_mint.to_string(),
                "whirlpool": pos.whirlpool.to_string(),
                "liquidity": pos.liquidity.to_string(),
                "tickLowerIndex": pos.tick_lower_index,
                "tickUpperIndex": pos.tick_upper_index,
                "priceLower": price_lower,
                "priceUpper": price_upper,
                "feeOwedA": pos.fee_owed_a,
                "feeOwedB": pos.fee_owed_b,
            })
        })
        .collect();

    let total = positions.len();
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "orca_get_pool_positions".to_string(),
            description: format!(
                "Orca pool positions: {} ({total} open)",
                short_id(&params.whirlpool)
            ),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "positions": positions,
                "total": total,
                "whirlpool": params.whirlpool,
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

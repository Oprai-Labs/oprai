use serde::{Deserialize, Serialize};
use solana_sdk::pubkey::Pubkey;
use spl_associated_token_account::get_associated_token_address;
use std::str::FromStr;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;
use crate::solana::tokens::{get_token_info, resolve_token_address, SOL_MINT};

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

const RAYDIUM_HOST: &str = "https://transaction-v1.raydium.io";
const RAYDIUM_API: &str = "https://api-v3.raydium.io";
const RAYDIUM_OWNER_API: &str = "https://owner-v1.raydium.io";

// ──────────────────────────────────────────────────────────────────────────────
// Parameter Types
// ──────────────────────────────────────────────────────────────────────────────

/// Swap via Raydium AMM / CLMM routing.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumSwapParams {
    pub input_mint: String,
    pub output_mint: String,
    pub amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    /// "in" = exact input (default), "out" = exact output
    #[serde(default)]
    pub swap_mode: Option<String>,
}

/// Add liquidity to a Raydium standard AMM pool.
/// Accepts either poolId+amount OR tokenA+tokenB+amountA+amountB (LLM-friendly).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumAddLiquidityParams {
    /// Direct pool ID (optional — resolved from tokenA/tokenB if omitted)
    #[serde(default)]
    pub pool_id: Option<String>,
    /// Single-token deposit amount (used when poolId is provided directly)
    #[serde(default)]
    pub amount: Option<String>,
    /// Token A symbol or mint (e.g. "SOL")
    #[serde(default)]
    pub token_a: Option<String>,
    /// Token B symbol or mint (e.g. "USDC")
    #[serde(default)]
    pub token_b: Option<String>,
    /// Amount of token A to deposit
    #[serde(default)]
    pub amount_a: Option<String>,
    /// Amount of token B to deposit
    #[serde(default)]
    pub amount_b: Option<String>,
    /// Which token to deposit when using poolId mode (symbol or mint address)
    #[serde(default)]
    pub input_mint: Option<String>,
    /// true = input is the base token (default: true)
    #[serde(default)]
    pub base_in: Option<bool>,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Remove liquidity from a Raydium standard AMM pool.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumRemoveLiquidityParams {
    pub pool_id: String,
    /// LP token amount to burn — accepts "lpAmount" or "liquidity" field name
    #[serde(alias = "liquidity")]
    pub lp_amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Create a new Raydium standard AMM pool.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumCreatePoolParams {
    /// First token — accepts "mintA" or "tokenA" field name
    #[serde(alias = "token_a", alias = "tokenA")]
    pub mint_a: String,
    /// Second token — accepts "mintB" or "tokenB" field name
    #[serde(alias = "token_b", alias = "tokenB")]
    pub mint_b: String,
    pub amount_a: String,
    pub amount_b: String,
    #[serde(default)]
    pub start_time: Option<u64>,
}

/// Open a concentrated liquidity (CLMM) position on Raydium.
/// Accepts either poolId+inputMint+inputAmount+tickLower+tickUpper
/// OR tokenA+tokenB+amountA+minPrice+maxPrice (LLM-friendly).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumOpenPositionParams {
    /// CLMM pool ID (resolved from tokenA/tokenB if omitted)
    #[serde(default)]
    pub pool_id: Option<String>,
    /// Input token for the deposit (resolved from tokenA if omitted)
    #[serde(default)]
    pub input_mint: Option<String>,
    /// Input token amount (resolved from amountA if omitted)
    #[serde(default)]
    pub input_amount: Option<String>,
    /// Lower tick boundary (computed from minPrice if omitted)
    #[serde(default)]
    pub tick_lower: Option<i32>,
    /// Upper tick boundary (computed from maxPrice if omitted)
    #[serde(default)]
    pub tick_upper: Option<i32>,
    // LLM-friendly alternative params
    #[serde(default)]
    pub token_a: Option<String>,
    #[serde(default)]
    pub token_b: Option<String>,
    #[serde(default)]
    pub amount_a: Option<String>,
    #[serde(default)]
    pub amount_b: Option<String>,
    /// Human-readable lower price bound (converted to tickLower)
    #[serde(default)]
    pub min_price: Option<f64>,
    /// Human-readable upper price bound (converted to tickUpper)
    #[serde(default)]
    pub max_price: Option<f64>,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Close an existing CLMM position (withdraw all liquidity + fees).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumClosePositionParams {
    /// NFT mint address that represents the position
    pub position_id: String,
}

/// Add more liquidity to an existing CLMM position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumIncreasePositionParams {
    pub position_id: String,
    pub input_mint: String,
    pub input_amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Partially or fully withdraw from an existing CLMM position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumDecreasePositionParams {
    pub position_id: String,
    /// Raw liquidity units to remove (from position info)
    pub liquidity: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_raydium_swap_params(p: &RaydiumSwapParams) -> Result<(), AppError> {
    if p.input_mint.is_empty() {
        return Err(AppError::InvalidParams("inputMint is required".into()));
    }
    if p.output_mint.is_empty() {
        return Err(AppError::InvalidParams("outputMint is required".into()));
    }
    let amount: f64 = p
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_raydium_add_liquidity_params(
    p: &RaydiumAddLiquidityParams,
) -> Result<(), AppError> {
    let has_pool_id = p.pool_id.as_deref().map(|s| !s.is_empty()).unwrap_or(false);
    let has_amount = p.amount.as_deref().map(|s| !s.is_empty()).unwrap_or(false);
    let has_pool_mode = has_pool_id && has_amount;

    let has_token_mode = p.token_a.as_deref().map(|s| !s.is_empty()).unwrap_or(false)
        && p.token_b.as_deref().map(|s| !s.is_empty()).unwrap_or(false)
        && p.amount_a
            .as_deref()
            .map(|s| !s.is_empty())
            .unwrap_or(false)
        && p.amount_b
            .as_deref()
            .map(|s| !s.is_empty())
            .unwrap_or(false);

    if !has_pool_mode && !has_token_mode {
        return Err(AppError::InvalidParams(
            "Provide either (poolId + inputMint + amount) or (tokenA + tokenB + amountA + amountB)"
                .into(),
        ));
    }

    // In pool-mode, inputMint is required to correctly convert the human-readable amount to
    // base units. Without it we'd default to 9 decimals which is wrong for USDC (6), BONK (5), etc.
    if has_pool_mode
        && p.input_mint
            .as_deref()
            .map(|s| s.is_empty())
            .unwrap_or(true)
    {
        return Err(AppError::InvalidParams(
            "inputMint is required when using poolId mode (needed to determine token decimals)"
                .into(),
        ));
    }

    Ok(())
}

pub fn validate_raydium_remove_liquidity_params(
    p: &RaydiumRemoveLiquidityParams,
) -> Result<(), AppError> {
    if p.pool_id.is_empty() {
        return Err(AppError::InvalidParams("poolId is required".into()));
    }
    let lp: f64 = p
        .lp_amount
        .parse()
        .map_err(|_| AppError::InvalidParams("lpAmount must be a positive number".into()))?;
    if lp <= 0.0 {
        return Err(AppError::InvalidParams("lpAmount must be positive".into()));
    }
    Ok(())
}

pub fn validate_raydium_create_pool_params(p: &RaydiumCreatePoolParams) -> Result<(), AppError> {
    if p.mint_a.is_empty() {
        return Err(AppError::InvalidParams("mintA is required".into()));
    }
    if p.mint_b.is_empty() {
        return Err(AppError::InvalidParams("mintB is required".into()));
    }
    let a: f64 = p
        .amount_a
        .parse()
        .map_err(|_| AppError::InvalidParams("amountA must be a positive number".into()))?;
    let b: f64 = p
        .amount_b
        .parse()
        .map_err(|_| AppError::InvalidParams("amountB must be a positive number".into()))?;
    if a <= 0.0 || b <= 0.0 {
        return Err(AppError::InvalidParams(
            "amountA and amountB must be positive".into(),
        ));
    }
    Ok(())
}

pub fn validate_raydium_open_position_params(
    p: &RaydiumOpenPositionParams,
) -> Result<(), AppError> {
    let has_direct = p.pool_id.as_deref().map(|s| !s.is_empty()).unwrap_or(false);
    let has_token_mode = p.token_a.as_deref().map(|s| !s.is_empty()).unwrap_or(false)
        && p.token_b.as_deref().map(|s| !s.is_empty()).unwrap_or(false);
    if !has_direct && !has_token_mode {
        return Err(AppError::InvalidParams(
            "Provide either poolId or (tokenA + tokenB)".into(),
        ));
    }
    if p.min_price.is_none() && p.tick_lower.is_none() {
        return Err(AppError::InvalidParams(
            "Provide either minPrice/maxPrice or tickLower/tickUpper".into(),
        ));
    }
    if let (Some(min), Some(max)) = (p.min_price, p.max_price) {
        if min <= 0.0 || max <= min {
            return Err(AppError::InvalidParams(
                "maxPrice must be greater than minPrice > 0".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_raydium_close_position_params(
    p: &RaydiumClosePositionParams,
) -> Result<(), AppError> {
    if p.position_id.is_empty() {
        return Err(AppError::InvalidParams("positionId is required".into()));
    }
    Ok(())
}

pub fn validate_raydium_increase_position_params(
    p: &RaydiumIncreasePositionParams,
) -> Result<(), AppError> {
    if p.position_id.is_empty() {
        return Err(AppError::InvalidParams("positionId is required".into()));
    }
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
    Ok(())
}

pub fn validate_raydium_decrease_position_params(
    p: &RaydiumDecreasePositionParams,
) -> Result<(), AppError> {
    if p.position_id.is_empty() {
        return Err(AppError::InvalidParams("positionId is required".into()));
    }
    if p.liquidity.is_empty() {
        return Err(AppError::InvalidParams("liquidity is required".into()));
    }
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Internal helpers
// ──────────────────────────────────────────────────────────────────────────────

/// Convert a human-readable amount to integer base units using token decimals.
fn to_base_units(amount: &str, decimals: u8) -> Result<u64, AppError> {
    let f: f64 = amount
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid amount: {amount}")))?;
    Ok((f * 10_f64.powi(decimals as i32)).round() as u64)
}

/// Get decimals for a token symbol or mint address. Falls back to 9 (SOL-like) for unknowns.
fn token_decimals(symbol_or_mint: &str) -> u8 {
    get_token_info(symbol_or_mint)
        .map(|t| t.decimals)
        .unwrap_or(9)
}

/// Get a display symbol (ticker) for a token symbol or mint address.
fn token_symbol(symbol_or_mint: &str) -> String {
    get_token_info(symbol_or_mint)
        .map(|t| t.symbol.to_string())
        .unwrap_or_else(|| {
            let len = symbol_or_mint.len().min(8);
            format!("{}…", &symbol_or_mint[..len])
        })
}

/// Shorten a pool/position ID for display.
fn short_id(id: &str) -> String {
    let len = id.len().min(8);
    format!("{}…", &id[..len])
}

/// Convert a human-readable price to a raw CLMM tick index (NOT yet aligned to tick spacing).
/// tick = floor(log(price) / log(1.0001))
/// Always call `align_tick_lower` / `align_tick_upper` before sending to the Raydium API.
pub fn price_to_tick(price: f64) -> i32 {
    (price.ln() / 1.0001_f64.ln()).floor() as i32
}

/// Shorten a wallet/address for display (first 8 chars + ellipsis).
fn short_addr(addr: &str) -> String {
    let len = addr.len().min(8);
    format!("{}…", &addr[..len])
}

/// Round a tick DOWN to the nearest valid multiple of `tick_spacing` (for tickLower).
/// Uses `rem_euclid` so negative ticks are handled correctly.
fn align_tick_lower(tick: i32, spacing: i32) -> i32 {
    if spacing <= 0 {
        return tick;
    }
    tick - tick.rem_euclid(spacing)
}

/// Round a tick UP to the nearest valid multiple of `tick_spacing` (for tickUpper).
fn align_tick_upper(tick: i32, spacing: i32) -> i32 {
    if spacing <= 0 {
        return tick;
    }
    let rem = tick.rem_euclid(spacing);
    if rem == 0 {
        tick
    } else {
        tick + (spacing - rem)
    }
}

/// Look up the highest-liquidity Raydium pool for a token pair.
/// `pool_type`: "StandardAmm" or "ConcentratedLiquidity"
/// Returns `(pool_id, tick_spacing)`. `tick_spacing` is always `None` for StandardAmm pools.
pub async fn lookup_raydium_pool_id(
    http: &reqwest::Client,
    mint1: &str,
    mint2: &str,
    pool_type: &str,
) -> Result<String, AppError> {
    let (pool_id, _) = lookup_raydium_pool_with_config(http, mint1, mint2, pool_type).await?;
    Ok(pool_id)
}

/// Same as `lookup_raydium_pool_id` but also returns the CLMM tick spacing (for open-position).
pub async fn lookup_raydium_clmm_pool(
    http: &reqwest::Client,
    mint1: &str,
    mint2: &str,
) -> Result<(String, i32), AppError> {
    let (pool_id, tick_spacing) =
        lookup_raydium_pool_with_config(http, mint1, mint2, "ConcentratedLiquidity").await?;
    Ok((pool_id, tick_spacing.unwrap_or(60))) // 60 = 0.25% fee tier default
}

/// Internal: query pools/info/mint and return (pool_id, Option<tick_spacing>).
async fn lookup_raydium_pool_with_config(
    http: &reqwest::Client,
    mint1: &str,
    mint2: &str,
    pool_type: &str,
) -> Result<(String, Option<i32>), AppError> {
    // Use .query() so reqwest percent-encodes all parameters — prevents URL injection.
    let resp = http
        .get("https://api-v3.raydium.io/pools/info/mint")
        .query(&[
            ("mint1", mint1),
            ("mint2", mint2),
            ("poolType", pool_type),
            ("poolSortField", "liquidity"),
            ("sortType", "desc"),
            ("pageSize", "1"),
            ("page", "1"),
        ])
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium pool lookup error: {e}")))?;

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium pool lookup parse: {e}")))?;

    let pool_id = data
        .pointer("/data/data/0/id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            AppError::ProtocolError(format!(
                "No {} pool found for {}/{}",
                pool_type, mint1, mint2
            ))
        })?;

    // CLMM pools include config.tickSpacing in the response.
    let tick_spacing = data
        .pointer("/data/data/0/config/tickSpacing")
        .and_then(|v| v.as_i64())
        .map(|v| v as i32);

    Ok((pool_id, tick_spacing))
}

/// Fetch tick spacing for a known CLMM pool ID via the pools/info/ids endpoint.
/// Used when the caller already has a pool_id but needs tick spacing for price→tick conversion.
async fn get_pool_tick_spacing(http: &reqwest::Client, pool_id: &str) -> Result<i32, AppError> {
    let resp = http
        .get("https://api-v3.raydium.io/pools/info/ids")
        .query(&[("ids", pool_id)])
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium pool info error: {e}")))?;

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium pool info parse: {e}")))?;

    Ok(data
        .pointer("/data/0/config/tickSpacing")
        .and_then(|v| v.as_i64())
        .map(|v| v as i32)
        .unwrap_or(60)) // 60 = 0.25% fee tier; safest default
}

/// Extract the base64 transaction string from a Raydium API response.
///
/// Raydium returns:
/// ```json
/// { "id": "...", "success": true, "data": { "transaction": "base64..." } }
/// ```
/// or an array variant:
/// ```json
/// { "id": "...", "success": true, "data": [ { "transaction": "base64..." } ] }
/// ```
fn extract_transaction(resp: &serde_json::Value) -> Result<String, AppError> {
    let success = resp
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !success {
        let msg = resp
            .get("msg")
            .or_else(|| resp.get("message"))
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown Raydium API error");
        return Err(AppError::ProtocolError(format!("Raydium: {msg}")));
    }

    let data = resp
        .get("data")
        .ok_or_else(|| AppError::ProtocolError("Raydium response missing 'data'".into()))?;

    // Single-transaction response
    if let Some(tx) = data.get("transaction").and_then(|v| v.as_str()) {
        return Ok(tx.to_string());
    }

    // Array response — take the first transaction
    if let Some(arr) = data.as_array() {
        if let Some(first) = arr.first() {
            if let Some(tx) = first.get("transaction").and_then(|v| v.as_str()) {
                return Ok(tx.to_string());
            }
        }
    }

    Err(AppError::ProtocolError(
        "Could not extract transaction from Raydium response".into(),
    ))
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — raydium_swap
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_raydium_swap(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    user_pubkey: &str,
    params: &RaydiumSwapParams,
) -> Result<BuildResponse, AppError> {
    let input_mint = resolve_token_address(&params.input_mint);
    let output_mint = resolve_token_address(&params.output_mint);
    let slippage_bps = params.slippage_bps.unwrap_or(50);
    // Normalize swapMode: accept both 'in'/'out' and 'ExactIn'/'ExactOut'.
    let is_base_in = !matches!(
        params
            .swap_mode
            .as_deref()
            .unwrap_or("in")
            .to_lowercase()
            .as_str(),
        "out" | "exactout"
    );

    // For swap-base-in:  amount = INPUT  quantity → use INPUT token decimals
    // For swap-base-out: amount = OUTPUT quantity → use OUTPUT token decimals
    // Using input decimals for base-out was the root bug: "0.1" USDC with SOL decimals (9)
    // became 100_000_000 USDC base units = 100 USDC, requiring ~1.17 SOL instead of 0.001 SOL.
    let amount_decimals = if is_base_in {
        token_decimals(&params.input_mint)
    } else {
        token_decimals(&params.output_mint)
    };
    let mut amount_base = to_base_units(&params.amount, amount_decimals)?;

    // Determine whether input / output is native SOL so we can set wrapSol / unwrapSol
    // correctly.  Only wrap when input IS native SOL; only unwrap when output IS native SOL.
    // Setting both to `true` unconditionally causes the Raydium TX builder to wrap the
    // output SOL amount from the user's wallet even when the input is a different token —
    // resulting in "insufficient lamports" simulation failures.
    let is_input_sol = input_mint == SOL_MINT;
    let is_output_sol = output_mint == SOL_MINT;

    // When swapping SOL, the wrapSol mechanism temporarily moves the full swap amount into
    // a wSOL ATA, leaving 0 SOL for the transaction fee. Fetch the wallet balance ONCE
    // and reuse it for both the pre-cap and the post-compute validation.
    const SOL_FEE_BUFFER_LAMPORTS: u64 = 20_000_000; // 0.02 SOL reserved for fees + ATA rent
    let sol_balance_lamports: Option<u64> = if is_input_sol {
        let pubkey: Pubkey = user_pubkey
            .parse()
            .map_err(|_| AppError::InvalidParams("Invalid wallet pubkey".into()))?;
        let rpc_clone = rpc.clone();
        let bal = tokio::task::spawn_blocking(move || rpc_clone.client().get_balance(&pubkey))
            .await
            .map_err(|e| AppError::Internal(format!("Balance fetch join error: {e}")))?
            .map_err(|e| AppError::SolanaRpcError(format!("Failed to fetch SOL balance: {e}")))?;
        Some(bal)
    } else {
        None
    };

    if let Some(balance_lamports) = sol_balance_lamports {
        tracing::info!(
            balance_lamports,
            amount_base,
            buffer = SOL_FEE_BUFFER_LAMPORTS,
            "Raydium SOL swap balance check"
        );

        if balance_lamports < SOL_FEE_BUFFER_LAMPORTS {
            return Err(AppError::InvalidParams(format!(
                "Insufficient SOL balance. Wallet has {:.4} SOL, minimum required for fees is {:.4} SOL.",
                balance_lamports as f64 / 1e9,
                SOL_FEE_BUFFER_LAMPORTS as f64 / 1e9,
            )));
        }

        let max_swappable = balance_lamports - SOL_FEE_BUFFER_LAMPORTS;
        if amount_base > max_swappable {
            tracing::warn!(
                requested = amount_base,
                max_swappable,
                balance_lamports,
                "Raydium swap: requested amount exceeds balance − buffer, capping"
            );
            amount_base = max_swappable;
        }
    }

    // Step 1: GET quote from Raydium compute API (query-string params, NOT JSON body)
    let compute_path = if is_base_in {
        "swap-base-in"
    } else {
        "swap-base-out"
    };
    let compute_resp = http
        .get(format!("{RAYDIUM_HOST}/compute/{compute_path}"))
        .query(&[
            ("inputMint", input_mint.as_str()),
            ("outputMint", output_mint.as_str()),
            ("amount", amount_base.to_string().as_str()),
            ("slippageBps", slippage_bps.to_string().as_str()),
            ("txVersion", "V0"),
        ])
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium compute error: {e}")))?;

    let compute_data: serde_json::Value = compute_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium compute parse: {e}")))?;

    tracing::debug!(
        amount_base,
        is_input_sol,
        is_output_sol,
        input_mint = %input_mint,
        output_mint = %output_mint,
        "Raydium compute request"
    );
    tracing::info!(
        "Raydium compute response: {}",
        serde_json::to_string(&compute_data).unwrap_or_default()
    );

    // Validate compute response
    if !compute_data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        let msg = compute_data
            .get("msg")
            .or_else(|| compute_data.get("message"))
            .and_then(|v| v.as_str())
            .unwrap_or("Raydium compute failed");
        return Err(AppError::ProtocolError(format!("Raydium compute: {msg}")));
    }

    // Extract display info from compute response
    let data = compute_data.get("data").unwrap_or(&serde_json::Value::Null);

    // Extract inputAmount returned by Raydium (may differ from amount_base if routing adds fees).
    // For SOL swaps this is the EXACT lamports the router will try to transfer — if it exceeds
    // the user's balance the transaction will fail at simulation.
    let compute_input_amount: u64 = data
        .get("inputAmount")
        .and_then(|v| {
            v.as_str()
                .and_then(|s| s.parse::<u64>().ok())
                .or_else(|| v.as_u64())
        })
        .unwrap_or(amount_base);

    tracing::info!(
        amount_base_sent = amount_base,
        compute_input_amount,
        is_input_sol,
        "Raydium compute inputAmount check"
    );

    // Post-compute SOL balance check: reuse the cached balance fetched above.
    // Rejects clearly instead of letting Phantom show a cryptic simulation failure.
    if let Some(balance_lamports) = sol_balance_lamports {
        let max_allowed = balance_lamports.saturating_sub(SOL_FEE_BUFFER_LAMPORTS);
        if compute_input_amount > max_allowed {
            return Err(AppError::ProtocolError(format!(
                "Insufficient SOL balance. This route requires {:.4} SOL but your wallet only has {:.4} SOL \
                 (keeping {:.4} SOL for fees). Try swapping a smaller amount (max ~{:.4} SOL).",
                compute_input_amount as f64 / 1e9,
                balance_lamports as f64 / 1e9,
                SOL_FEE_BUFFER_LAMPORTS as f64 / 1e9,
                max_allowed as f64 / 1e9,
            )));
        }
    }

    let out_amount_raw = data
        .get("outputAmount")
        .and_then(|v| {
            v.as_str()
                .map(String::from)
                .or_else(|| v.as_u64().map(|n| n.to_string()))
        })
        .unwrap_or_else(|| "0".to_string());

    let price_impact: f64 = data
        .get("priceImpactPct")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);

    let out_decimals = token_decimals(&params.output_mint);
    let out_amount_f: f64 =
        out_amount_raw.parse::<f64>().unwrap_or(0.0) / 10_f64.powi(out_decimals as i32);

    // Step 2: POST transaction — pass the FULL compute response as `swapResponse`.
    //
    // Raydium V3 requires `inputAccount` (and `outputAccount` for token outputs)
    // when the token is NOT native SOL. Skipping it yields `REQ_INPUT_ACCOUT_ERROR`
    // (Raydium's typo'd code for "required input account error"). Both are simply
    // the user's associated token accounts for the respective mints — Raydium
    // creates the output ATA in-tx if it doesn't exist yet.
    let tx_path = if is_base_in {
        "swap-base-in"
    } else {
        "swap-base-out"
    };
    let user_pk = Pubkey::from_str(user_pubkey)
        .map_err(|_| AppError::InvalidParams("Invalid wallet pubkey".into()))?;
    let mut tx_body = serde_json::json!({
        "swapResponse": compute_data,
        "txVersion": "V0",
        "wallet": user_pubkey,
        "wrapSol": is_input_sol,
        "unwrapSol": is_output_sol,
        "computeUnitPriceMicroLamports": "100000"
    });
    if !is_input_sol {
        let mint_pk = Pubkey::from_str(&input_mint)
            .map_err(|_| AppError::InvalidParams(format!("Invalid input mint: {input_mint}")))?;
        let ata = get_associated_token_address(&user_pk, &mint_pk);
        tx_body["inputAccount"] = serde_json::Value::String(ata.to_string());
    }
    if !is_output_sol {
        let mint_pk = Pubkey::from_str(&output_mint)
            .map_err(|_| AppError::InvalidParams(format!("Invalid output mint: {output_mint}")))?;
        let ata = get_associated_token_address(&user_pk, &mint_pk);
        tx_body["outputAccount"] = serde_json::Value::String(ata.to_string());
    }
    let tx_resp = http
        .post(format!("{RAYDIUM_HOST}/transaction/{tx_path}"))
        .json(&tx_body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium TX build error: {e}")))?;

    let tx_data: serde_json::Value = tx_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium TX parse: {e}")))?;

    let tx_b64 = extract_transaction(&tx_data)?;

    let in_sym = token_symbol(&params.input_mint);
    let out_sym = token_symbol(&params.output_mint);

    let mut warnings = vec![];
    if price_impact > 5.0 {
        warnings.push(format!("High price impact: {price_impact:.2}%"));
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_swap".to_string(),
            description: format!(
                "Swap {} {} → {:.6} {} via Raydium ({:.2}% impact)",
                params.amount, in_sym, out_amount_f, out_sym, price_impact
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
        quote: Some(compute_data),
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — raydium_add_liquidity
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_raydium_add_liquidity(
    http: &reqwest::Client,
    user_pubkey: &str,
    params: &RaydiumAddLiquidityParams,
) -> Result<BuildResponse, AppError> {
    validate_raydium_add_liquidity_params(params)?;
    let slippage_bps = params.slippage_bps.unwrap_or(100);

    // Resolve pool_id — either provided directly or looked up from tokenA/tokenB
    let (pool_id, amount_str, input_mint_str, base_in, desc) =
        if let Some(pid) = params.pool_id.as_deref().filter(|s| !s.is_empty()) {
            let amt = params.amount.as_deref().unwrap_or("0");
            let mint = params.input_mint.as_deref().unwrap_or("");
            let bi = params.base_in.unwrap_or(true);
            let sym = if mint.is_empty() {
                "tokens".to_string()
            } else {
                token_symbol(mint)
            };
            (
                pid.to_string(),
                amt.to_string(),
                mint.to_string(),
                bi,
                format!("Add {} {} to Raydium pool {}", amt, sym, short_id(pid)),
            )
        } else {
            // Token-pair mode: look up pool
            let ta = resolve_token_address(params.token_a.as_deref().unwrap_or(""));
            let tb = resolve_token_address(params.token_b.as_deref().unwrap_or(""));
            let aa = params.amount_a.as_deref().unwrap_or("0");
            let sym_a = token_symbol(params.token_a.as_deref().unwrap_or(""));
            let sym_b = token_symbol(params.token_b.as_deref().unwrap_or(""));
            let pid = lookup_raydium_pool_id(http, &ta, &tb, "StandardAmm").await?;
            (
                pid.clone(),
                aa.to_string(),
                ta.clone(),
                true,
                format!(
                    "Add {} {}/{} liquidity to Raydium pool {}",
                    aa,
                    sym_a,
                    sym_b,
                    short_id(&pid)
                ),
            )
        };

    let decimals = if input_mint_str.is_empty() {
        9
    } else {
        token_decimals(&input_mint_str)
    };
    let amount_base = to_base_units(&amount_str, decimals)?;

    let tx_resp = http
        .post(format!("{RAYDIUM_HOST}/transaction/add-liquidity"))
        .json(&serde_json::json!({
            "owner": user_pubkey,
            "poolId": pool_id,
            "amount": amount_base.to_string(),
            "baseIn": base_in,
            "slippageBps": slippage_bps,
            "txVersion": "V0",
            "computeUnitPriceMicroLamports": "100000"
        }))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium add-liquidity error: {e}")))?;

    let tx_data: serde_json::Value = tx_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium add-liquidity parse: {e}")))?;

    let tx_b64 = extract_transaction(&tx_data)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_add_liquidity".to_string(),
            description: desc,
            estimated_fee: "~0.01 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec!["Impermanent loss risk — prices may diverge from entry".into()],
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
// Build — raydium_remove_liquidity
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_raydium_remove_liquidity(
    http: &reqwest::Client,
    user_pubkey: &str,
    params: &RaydiumRemoveLiquidityParams,
) -> Result<BuildResponse, AppError> {
    let slippage_bps = params.slippage_bps.unwrap_or(100);
    // Raydium AMM v2 LP tokens have 9 decimals
    let lp_base = to_base_units(&params.lp_amount, 9)?;

    let tx_resp = http
        .post(format!("{RAYDIUM_HOST}/transaction/remove-liquidity"))
        .json(&serde_json::json!({
            "owner": user_pubkey,
            "poolId": params.pool_id,
            "lpAmount": lp_base.to_string(),
            "slippageBps": slippage_bps,
            "txVersion": "V0",
            "computeUnitPriceMicroLamports": "100000"
        }))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium remove-liquidity error: {e}")))?;

    let tx_data: serde_json::Value = tx_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium remove-liquidity parse: {e}")))?;

    let tx_b64 = extract_transaction(&tx_data)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_remove_liquidity".to_string(),
            description: format!(
                "Remove {} LP tokens from Raydium pool {}",
                params.lp_amount,
                short_id(&params.pool_id)
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
// Build — raydium_create_pool
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_raydium_create_pool(
    http: &reqwest::Client,
    user_pubkey: &str,
    params: &RaydiumCreatePoolParams,
) -> Result<BuildResponse, AppError> {
    let mint_a = resolve_token_address(&params.mint_a);
    let mint_b = resolve_token_address(&params.mint_b);
    let decimals_a = token_decimals(&params.mint_a);
    let decimals_b = token_decimals(&params.mint_b);
    let amount_a_base = to_base_units(&params.amount_a, decimals_a)?;
    let amount_b_base = to_base_units(&params.amount_b, decimals_b)?;
    let start_time = params.start_time.unwrap_or(0);

    let sym_a = token_symbol(&params.mint_a);
    let sym_b = token_symbol(&params.mint_b);

    let tx_resp = http
        .post(format!("{RAYDIUM_HOST}/transaction/create-pool"))
        .json(&serde_json::json!({
            "owner": user_pubkey,
            "mintA": mint_a,
            "mintB": mint_b,
            "mintAmountA": amount_a_base.to_string(),
            "mintAmountB": amount_b_base.to_string(),
            "startTime": start_time,
            "txVersion": "V0",
            "computeUnitPriceMicroLamports": "100000"
        }))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium create-pool error: {e}")))?;

    let tx_data: serde_json::Value = tx_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium create-pool parse: {e}")))?;

    let tx_b64 = extract_transaction(&tx_data)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_create_pool".to_string(),
            description: format!(
                "Create Raydium {}/{} pool — seed {} {} / {} {}",
                sym_a, sym_b, params.amount_a, sym_a, params.amount_b, sym_b
            ),
            estimated_fee: "~0.15 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                "Pool creation requires a rent deposit (~0.15 SOL, non-refundable)".into(),
                "Initial price is determined by the ratio of your seed tokens".into(),
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
// Build — raydium_open_position  (CLMM concentrated liquidity)
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_raydium_open_position(
    http: &reqwest::Client,
    rpc: &crate::solana::connection::SolanaRpc,
    user_pubkey: &str,
    params: &RaydiumOpenPositionParams,
) -> Result<BuildResponse, AppError> {
    validate_raydium_open_position_params(params)?;
    let slippage_bps = params.slippage_bps.unwrap_or(100);

    // Resolve pool_id and tick_spacing simultaneously.
    // For token-pair mode we use the CLMM-specific lookup which returns tick spacing from the
    // pool's config in the same API response (no extra round-trip).
    // For direct pool_id mode we fetch tick spacing separately only when a price range is given
    // (tick alignment is required — Raydium API rejects non-aligned ticks).
    let (pool_id, tick_spacing_opt) =
        if let Some(pid) = params.pool_id.as_deref().filter(|s| !s.is_empty()) {
            let spacing = if params.min_price.is_some() || params.max_price.is_some() {
                // Price→tick conversion requires tick spacing — fetch it.
                Some(get_pool_tick_spacing(http, pid).await.unwrap_or(60))
            } else {
                None // Direct tick values provided; no alignment needed.
            };
            (pid.to_string(), spacing)
        } else {
            let ta = resolve_token_address(params.token_a.as_deref().unwrap_or(""));
            let tb = resolve_token_address(params.token_b.as_deref().unwrap_or(""));
            let (pid, spacing) = lookup_raydium_clmm_pool(http, &ta, &tb).await?;
            (pid, Some(spacing))
        };

    // Resolve input_mint and input_amount
    let input_mint_sym = params
        .input_mint
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(params.token_a.as_deref())
        .unwrap_or("SOL");
    let input_mint = resolve_token_address(input_mint_sym);
    let decimals = token_decimals(input_mint_sym);

    let input_amount_str = params
        .input_amount
        .as_deref()
        .filter(|s| !s.is_empty())
        .or(params.amount_a.as_deref())
        .unwrap_or("0");
    let input_base = to_base_units(input_amount_str, decimals)?;
    let sym = token_symbol(input_mint_sym);

    // Resolve ticks — use direct ticks if provided, else convert from prices.
    // When converting from prices, align to the pool's tick spacing.
    // Raydium's transaction API rejects ticks that are not multiples of the pool's tick spacing.
    let tick_spacing = tick_spacing_opt.unwrap_or(1); // spacing=1 is a no-op alignment
    let tick_lower = if let Some(t) = params.tick_lower {
        t // User-supplied tick — assume already aligned
    } else if let Some(min_p) = params.min_price {
        align_tick_lower(price_to_tick(min_p), tick_spacing)
    } else {
        return Err(AppError::InvalidParams(
            "tickLower or minPrice is required".into(),
        ));
    };
    let tick_upper = if let Some(t) = params.tick_upper {
        t // User-supplied tick — assume already aligned
    } else if let Some(max_p) = params.max_price {
        align_tick_upper(price_to_tick(max_p), tick_spacing)
    } else {
        return Err(AppError::InvalidParams(
            "tickUpper or maxPrice is required".into(),
        ));
    };

    if tick_lower >= tick_upper {
        return Err(AppError::InvalidParams(
            "tickLower must be less than tickUpper (check that minPrice < maxPrice and both are valid)".into(),
        ));
    }

    let price_desc = if params.min_price.is_some() {
        format!(
            "price range ${:.4}–${:.4} (ticks {} → {}, spacing {})",
            params.min_price.unwrap(),
            params.max_price.unwrap_or(0.0),
            tick_lower,
            tick_upper,
            tick_spacing,
        )
    } else {
        format!("ticks {} → {}", tick_lower, tick_upper)
    };

    // SDK-based path: parse pool, compute liquidity, encode `open_position_v2`.
    // See `services/raydium_clmm/builder.rs` for the heavy lifting.
    use std::str::FromStr;
    let pool_pk = solana_sdk::pubkey::Pubkey::from_str(&pool_id)
        .map_err(|e| AppError::InvalidParams(format!("invalid pool_id: {e}")))?;
    let input_mint_pk = solana_sdk::pubkey::Pubkey::from_str(&input_mint)
        .map_err(|e| AppError::InvalidParams(format!("invalid input_mint: {e}")))?;
    let user_pk = solana_sdk::pubkey::Pubkey::from_str(user_pubkey)
        .map_err(|e| AppError::InvalidParams(format!("invalid user_pubkey: {e}")))?;

    let req = crate::services::raydium_clmm::builder::OpenPositionRequest {
        user_pubkey: user_pk,
        pool_id: pool_pk,
        input_mint: input_mint_pk,
        input_amount: input_base,
        tick_lower,
        tick_upper,
        slippage_bps: slippage_bps as u32,
        with_metadata: false,
    };
    let (mut response, position_nft_mint) =
        crate::services::raydium_clmm::builder::build_open_position(rpc, req).await?;

    // Augment description with the human-readable price range + symbol.
    response.preview.description = format!(
        "Open Raydium CLMM position: {} {} in pool {} ({}) — NFT {}",
        input_amount_str,
        sym,
        short_id(&pool_id),
        price_desc,
        short_id(&position_nft_mint.to_string()),
    );
    Ok(response)
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — raydium_close_position
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_raydium_close_position(
    http: &reqwest::Client,
    user_pubkey: &str,
    params: &RaydiumClosePositionParams,
) -> Result<BuildResponse, AppError> {
    let tx_resp = http
        .post(format!("{RAYDIUM_HOST}/transaction/close-position"))
        .json(&serde_json::json!({
            "owner": user_pubkey,
            "positionId": params.position_id,
            "txVersion": "V0",
            "computeUnitPriceMicroLamports": "100000"
        }))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium close-position error: {e}")))?;

    let tx_data: serde_json::Value = tx_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium close-position parse: {e}")))?;

    let tx_b64 = extract_transaction(&tx_data)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_close_position".to_string(),
            description: format!(
                "Close CLMM position {} — withdraw all liquidity and fees",
                short_id(&params.position_id)
            ),
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
// Build — raydium_increase_position
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_raydium_increase_position(
    http: &reqwest::Client,
    user_pubkey: &str,
    params: &RaydiumIncreasePositionParams,
) -> Result<BuildResponse, AppError> {
    let input_mint = resolve_token_address(&params.input_mint);
    let decimals = token_decimals(&params.input_mint);
    let input_base = to_base_units(&params.input_amount, decimals)?;
    let slippage_bps = params.slippage_bps.unwrap_or(100);
    let sym = token_symbol(&params.input_mint);

    let tx_resp = http
        .post(format!("{RAYDIUM_HOST}/transaction/increase-position"))
        .json(&serde_json::json!({
            "owner": user_pubkey,
            "positionId": params.position_id,
            "inputMint": input_mint,
            "inputAmount": input_base.to_string(),
            "slippageBps": slippage_bps,
            "txVersion": "V0",
            "computeUnitPriceMicroLamports": "100000"
        }))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium increase-position error: {e}")))?;

    let tx_data: serde_json::Value = tx_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium increase-position parse: {e}")))?;

    let tx_b64 = extract_transaction(&tx_data)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_increase_position".to_string(),
            description: format!(
                "Add {} {} to CLMM position {}",
                params.input_amount,
                sym,
                short_id(&params.position_id)
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
// Internal GET helper
// ──────────────────────────────────────────────────────────────────────────────

async fn raydium_get(http: &reqwest::Client, url: &str) -> Result<serde_json::Value, AppError> {
    let resp = http
        .get(url)
        .header("Accept", "application/json")
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium GET error: {e}")))?;
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium GET parse: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// Build — raydium_decrease_position
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_raydium_decrease_position(
    http: &reqwest::Client,
    user_pubkey: &str,
    params: &RaydiumDecreasePositionParams,
) -> Result<BuildResponse, AppError> {
    let slippage_bps = params.slippage_bps.unwrap_or(100);

    let tx_resp = http
        .post(format!("{RAYDIUM_HOST}/transaction/decrease-position"))
        .json(&serde_json::json!({
            "owner": user_pubkey,
            "positionId": params.position_id,
            "liquidity": params.liquidity,
            "slippageBps": slippage_bps,
            "txVersion": "V0",
            "computeUnitPriceMicroLamports": "100000"
        }))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium decrease-position error: {e}")))?;

    let tx_data: serde_json::Value = tx_resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Raydium decrease-position parse: {e}")))?;

    let tx_b64 = extract_transaction(&tx_data)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_decrease_position".to_string(),
            description: format!(
                "Remove liquidity {} from CLMM position {}",
                params.liquidity,
                short_id(&params.position_id)
            ),
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
// GET Query Actions
// ──────────────────────────────────────────────────────────────────────────────

// ── raydium_get_pools ─────────────────────────────────────────────────────────

/// List Raydium pools with optional filtering and sorting.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetPoolsParams {
    /// Pool type: "all", "StandardAmm", "ConcentratedLiquidity", "AllFarm", "StandardAmmFarm", "ConcentratedLiquidityFarm"
    #[serde(default)]
    pub pool_type: Option<String>,
    /// Sort field: "default", "liquidity", "volume24h", "volume7d", "fee24h", "fee7d", "apr24h", "apr7d"
    #[serde(default)]
    pub sort_field: Option<String>,
    /// Sort order: "asc" or "desc"
    #[serde(default)]
    pub sort_type: Option<String>,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
    /// Minimum 24h volume in USD. **Defaults to 0 (no filter)** so we
    /// mirror what raydium.io itself shows — users asking "top pools by
    /// TVL" expect to see Raydium's own ranking verbatim, including the
    /// pegged-stable park-pools at the top. Pass a positive value
    /// (e.g. 1000) only when the LLM detects intent like "active pools
    /// only" or "exclude dust".
    #[serde(default)]
    pub min_volume24h: Option<f64>,
    /// Minimum vol24h / TVL ratio. **Defaults to 0 (no filter)** for the
    /// same reason. Useful when the user explicitly wants to drop
    /// "parking pools" — pegged-stable curiosities like OSRUB/USDT that
    /// hold $30M+ TVL but trade <$15K/day (ratio ≈ 0.0003). Real pools
    /// clear 0.005+ easily; 0.001 is a safe threshold.
    #[serde(default)]
    pub min_vol_to_tvl_ratio: Option<f64>,
}

pub fn validate_raydium_get_pools_params(p: &RaydiumGetPoolsParams) -> Result<(), AppError> {
    if let Some(ref t) = p.pool_type {
        let valid = [
            "all",
            "concentrated",
            "standard",
            "allFarm",
            "concentratedFarm",
            "standardFarm",
        ];
        if !valid.contains(&t.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "poolType must be one of: {}",
                valid.join(", ")
            )));
        }
    }
    if let Some(ref s) = p.sort_field {
        // "tvl" is a common synonym for "liquidity" the LLM might emit;
        // accept it here so the upstream alias map gets a chance to run.
        let valid = [
            "default",
            "liquidity",
            "tvl",
            "volume24h",
            "fee24h",
            "apr24h",
            "volume7d",
            "fee7d",
            "apr7d",
            "volume30d",
            "fee30d",
            "apr30d",
        ];
        if !valid.contains(&s.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "sortField must be one of: {}",
                valid.join(", ")
            )));
        }
    }
    if let Some(ref o) = p.sort_type {
        if o != "asc" && o != "desc" {
            return Err(AppError::InvalidParams(
                "sortType must be 'asc' or 'desc'".into(),
            ));
        }
    }
    Ok(())
}

pub async fn build_raydium_get_pools(
    http: &reqwest::Client,
    params: &RaydiumGetPoolsParams,
) -> Result<BuildResponse, AppError> {
    let pool_type = params.pool_type.as_deref().unwrap_or("all");
    // Accept friendly synonyms the LLM might emit ("tvl" → liquidity,
    // "amm" → standard, "clmm" → concentrated). Without these aliases the
    // validator returned a 400 every time the LLM picked "tvl", which
    // surfaced to the user as a useless "teknik bir sorun" error.
    let sort_field = match params.sort_field.as_deref().unwrap_or("liquidity") {
        "tvl" => "liquidity",
        other => other,
    };
    let sort_type = params.sort_type.as_deref().unwrap_or("desc");
    let page = params.page.unwrap_or(1);
    let page_size = params.page_size.unwrap_or(20).min(1000);
    // Default to NO filtering — we want the same list Raydium's own UI
    // shows when the user asks for "top pools by TVL". Filters only kick
    // in when the LLM passes them explicitly (intent like "active pools
    // only" or "exclude parking pools").
    let min_vol = params.min_volume24h.unwrap_or(0.0);
    let min_ratio = params.min_vol_to_tvl_ratio.unwrap_or(0.0);
    let filter_enabled = min_vol > 0.0 || min_ratio > 0.0;

    // Over-fetch when we expect to drop rows so the user-visible page_size
    // ends up matching the requested page_size. Cap at 200 to keep the
    // single Raydium round-trip from blowing past their rate limit.
    let fetch_size = if filter_enabled {
        (page_size * 4).min(200)
    } else {
        page_size
    };

    let url = format!(
        "{RAYDIUM_API}/pools/info/list?poolType={pool_type}&poolSortField={sort_field}&sortType={sort_type}&page={page}&pageSize={fetch_size}"
    );
    let mut data = raydium_get(http, &url).await?;

    // Post-filter: drop "parking pools" with no real trading activity.
    // Raydium's TVL ranking floods the top with pegged stables that hold
    // $30M+ TVL but trade <$15K/day (OSRUB/USDT, ARUB/USDT, USRUB/USDT…).
    // The vol-to-TVL ratio is the more reliable signal: real pools turn
    // over their TVL at least 0.1%/day, parking pools sit at 0.03%.
    if filter_enabled {
        if let Some(arr) = data
            .get_mut("data")
            .and_then(|d| d.get_mut("data"))
            .and_then(|v| v.as_array_mut())
        {
            arr.retain(|p| {
                let vol = p
                    .get("day")
                    .and_then(|d| d.get("volume"))
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0);
                let tvl = p.get("tvl").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let ratio_ok = if min_ratio > 0.0 && tvl > 0.0 {
                    (vol / tvl) >= min_ratio
                } else {
                    true
                };
                vol >= min_vol && ratio_ok
            });
            // Trim back to the user-requested page_size now that the
            // volume filter has dropped the parking entries.
            if arr.len() > page_size as usize {
                arr.truncate(page_size as usize);
            }
        }
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_pools".to_string(),
            description: format!("Raydium pools ({pool_type}, by {sort_field} {sort_type}, minVol=${min_vol:.0}, minRatio={min_ratio:.4})"),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_search_pools ──────────────────────────────────────────────────────

/// Search Raydium pools by token pair.
///
/// The LLM emits this in three shapes:
///   1. `{tokenA: "USDS", tokenB: "USDC"}`        — canonical
///   2. `{query: "USDS-USDC"}` / `{query: "USDS USDC"}` — shorthand
///   3. `{tokenA: "USDS-USDC"}`                   — model crammed both into tokenA
/// All three resolve to the same upstream query — see `normalize_pair` below.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumSearchPoolsParams {
    /// Token A symbol or mint address (optional when `query` is provided)
    #[serde(default)]
    pub token_a: Option<String>,
    /// Token B symbol or mint address (optional — search by single token if omitted)
    #[serde(default)]
    pub token_b: Option<String>,
    /// Shorthand: "TOKENA-TOKENB" or "TOKENA TOKENB" → splits into tokenA + tokenB
    #[serde(default)]
    pub query: Option<String>,
    /// Pool type filter: "all", "concentrated", "standard", "allFarm", "concentratedFarm", "standardFarm"
    #[serde(default)]
    pub pool_type: Option<String>,
    #[serde(default)]
    pub sort_field: Option<String>,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
}

/// Coerce the LLM's various input shapes into a canonical (tokenA, Option<tokenB>) pair.
/// Accepts `query` shorthand ("USDS-USDC" / "USDS USDC") and tokenA-crammed pairs.
fn normalize_pair(
    token_a: &Option<String>,
    token_b: &Option<String>,
    query: &Option<String>,
) -> Option<(String, Option<String>)> {
    let split_pair = |s: &str| -> Option<(String, Option<String>)> {
        let parts: Vec<&str> = s
            .split(|c: char| matches!(c, '-' | '/' | ' ' | '_' | ','))
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .collect();
        match parts.len() {
            1 => Some((parts[0].to_string(), None)),
            n if n >= 2 => Some((parts[0].to_string(), Some(parts[1].to_string()))),
            _ => None,
        }
    };

    if let Some(q) = query.as_deref() {
        if !q.trim().is_empty() {
            return split_pair(q);
        }
    }
    if let Some(a) = token_a.as_deref() {
        if !a.trim().is_empty() {
            // tokenA may itself be "USDS-USDC"
            if token_b.is_none() {
                if let Some((p1, p2)) = split_pair(a) {
                    return Some((p1, p2));
                }
            }
            return Some((a.to_string(), token_b.clone()));
        }
    }
    None
}

pub fn validate_raydium_search_pools_params(p: &RaydiumSearchPoolsParams) -> Result<(), AppError> {
    if normalize_pair(&p.token_a, &p.token_b, &p.query).is_none() {
        return Err(AppError::InvalidParams(
            "tokenA (or query) is required".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_search_pools(
    http: &reqwest::Client,
    params: &RaydiumSearchPoolsParams,
) -> Result<BuildResponse, AppError> {
    let (token_a, token_b) = normalize_pair(&params.token_a, &params.token_b, &params.query)
        .ok_or_else(|| AppError::InvalidParams("tokenA (or query) is required".into()))?;

    let mint1 = resolve_token_address(&token_a);
    let pool_type = params.pool_type.as_deref().unwrap_or("all");
    let sort_field = params.sort_field.as_deref().unwrap_or("liquidity");
    let page = params.page.unwrap_or(1).max(1);
    let page_size = params.page_size.unwrap_or(20).min(1000);

    let mut url = format!(
        "{RAYDIUM_API}/pools/info/mint?mint1={mint1}&poolType={pool_type}&poolSortField={sort_field}&sortType=desc&pageSize={page_size}&page={page}"
    );
    if let Some(ref b) = token_b {
        let mint2 = resolve_token_address(b);
        url.push_str(&format!("&mint2={mint2}"));
    }

    let data = raydium_get(http, &url).await?;
    let sym_a = token_symbol(&token_a);
    let pair_str = match &token_b {
        Some(b) => format!("{}/{}", sym_a, token_symbol(b)),
        None => sym_a,
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_search_pools".to_string(),
            description: format!("Raydium {pool_type} pools for {pair_str}"),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_pool_info ─────────────────────────────────────────────────────

/// Get detailed info for specific Raydium pools by ID.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetPoolInfoParams {
    /// Comma-separated pool IDs
    pub ids: String,
}

pub fn validate_raydium_get_pool_info_params(p: &RaydiumGetPoolInfoParams) -> Result<(), AppError> {
    if p.ids.is_empty() {
        return Err(AppError::InvalidParams(
            "ids is required (comma-separated pool IDs)".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_get_pool_info(
    http: &reqwest::Client,
    params: &RaydiumGetPoolInfoParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{RAYDIUM_API}/pools/info/ids?ids={}", params.ids);
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_pool_info".to_string(),
            description: format!("Raydium pool info for: {}", params.ids),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_user_positions ────────────────────────────────────────────────

/// Get a user's staked (farm) positions on Raydium.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetUserPositionsParams {
    /// Override wallet address (uses authenticated wallet if omitted)
    #[serde(default)]
    pub wallet: Option<String>,
}

pub fn validate_raydium_get_user_positions_params(
    _p: &RaydiumGetUserPositionsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_user_positions(
    http: &reqwest::Client,
    wallet: &str,
    params: &RaydiumGetUserPositionsParams,
) -> Result<BuildResponse, AppError> {
    let owner = params.wallet.as_deref().unwrap_or(wallet);
    let url = format!("{RAYDIUM_OWNER_API}/position/stake/{owner}");
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_user_positions".to_string(),
            description: format!("Raydium farm positions for {}", short_addr(owner)),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_clmm_positions ────────────────────────────────────────────────

/// Get a user's locked CLMM (concentrated liquidity) positions on Raydium.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetClmmPositionsParams {
    /// Override wallet address (uses authenticated wallet if omitted)
    #[serde(default)]
    pub wallet: Option<String>,
}

pub fn validate_raydium_get_clmm_positions_params(
    _p: &RaydiumGetClmmPositionsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_clmm_positions(
    http: &reqwest::Client,
    wallet: &str,
    params: &RaydiumGetClmmPositionsParams,
) -> Result<BuildResponse, AppError> {
    let owner = params.wallet.as_deref().unwrap_or(wallet);
    let url = format!("{RAYDIUM_OWNER_API}/position/clmm-lock/{owner}");
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_clmm_positions".to_string(),
            description: format!("Raydium locked CLMM positions for {}", short_addr(owner)),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_token_info ────────────────────────────────────────────────────

/// Get token metadata from Raydium's mint registry.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetTokenInfoParams {
    /// Comma-separated token symbols or mint addresses
    pub mints: String,
}

pub fn validate_raydium_get_token_info_params(
    p: &RaydiumGetTokenInfoParams,
) -> Result<(), AppError> {
    if p.mints.is_empty() {
        return Err(AppError::InvalidParams(
            "mints is required (comma-separated symbols or addresses)".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_get_token_info(
    http: &reqwest::Client,
    params: &RaydiumGetTokenInfoParams,
) -> Result<BuildResponse, AppError> {
    let resolved: Vec<String> = params
        .mints
        .split(',')
        .map(|s| resolve_token_address(s.trim()))
        .collect();
    let mints_str = resolved.join(",");
    let url = format!("{RAYDIUM_API}/mint/ids?mints={mints_str}");
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_token_info".to_string(),
            description: format!("Raydium token info for: {}", params.mints),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_platform_stats ────────────────────────────────────────────────

/// Get Raydium platform-wide statistics (TVL, 24h volume).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetPlatformStatsParams {}

pub fn validate_raydium_get_platform_stats_params(
    _p: &RaydiumGetPlatformStatsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_platform_stats(
    http: &reqwest::Client,
    _params: &RaydiumGetPlatformStatsParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/info")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_platform_stats".to_string(),
            description: "Raydium platform TVL and 24h volume".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_clmm_configs ──────────────────────────────────────────────────

/// Get available CLMM fee tier configurations on Raydium.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetClmmConfigsParams {}

pub fn validate_raydium_get_clmm_configs_params(
    _p: &RaydiumGetClmmConfigsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_clmm_configs(
    http: &reqwest::Client,
    _params: &RaydiumGetClmmConfigsParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/clmm-config")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_clmm_configs".to_string(),
            description: "Raydium CLMM fee tier configurations".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_swap_quote ────────────────────────────────────────────────────────

/// Get a swap price quote from Raydium without building a transaction.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumSwapQuoteParams {
    pub input_mint: String,
    pub output_mint: String,
    pub amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    /// "in" = exact input (default), "out" = exact output
    #[serde(default)]
    pub swap_mode: Option<String>,
}

pub fn validate_raydium_swap_quote_params(p: &RaydiumSwapQuoteParams) -> Result<(), AppError> {
    if p.input_mint.is_empty() {
        return Err(AppError::InvalidParams("inputMint is required".into()));
    }
    if p.output_mint.is_empty() {
        return Err(AppError::InvalidParams("outputMint is required".into()));
    }
    if p.amount.is_empty() {
        return Err(AppError::InvalidParams("amount is required".into()));
    }
    let amt: f64 = p
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amt <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    if let Some(ref m) = p.swap_mode {
        if m != "in" && m != "out" {
            return Err(AppError::InvalidParams(
                "swapMode must be 'in' or 'out'".into(),
            ));
        }
    }
    Ok(())
}

pub async fn build_raydium_swap_quote(
    http: &reqwest::Client,
    params: &RaydiumSwapQuoteParams,
) -> Result<BuildResponse, AppError> {
    let input_mint = resolve_token_address(&params.input_mint);
    let output_mint = resolve_token_address(&params.output_mint);
    let slippage_bps = params.slippage_bps.unwrap_or(100);
    let mode = params.swap_mode.as_deref().unwrap_or("in");
    let in_decimals = token_decimals(&params.input_mint);
    let amount_base = to_base_units(&params.amount, in_decimals)?;

    let endpoint = if mode == "out" {
        "swap-base-out"
    } else {
        "swap-base-in"
    };
    let url = format!(
        "{RAYDIUM_HOST}/compute/{endpoint}?inputMint={input_mint}&outputMint={output_mint}&amount={amount_base}&slippageBps={slippage_bps}&txVersion=V0"
    );
    let data = raydium_get(http, &url).await?;
    let sym_in = token_symbol(&params.input_mint);
    let sym_out = token_symbol(&params.output_mint);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_swap_quote".to_string(),
            description: format!(
                "Raydium swap quote: {} {} → {}",
                params.amount, sym_in, sym_out
            ),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_pools_by_lp ───────────────────────────────────────────────────

/// Get Raydium pool info by LP mint addresses.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetPoolsByLpParams {
    /// Comma-separated LP mint addresses
    pub lps: String,
}

pub fn validate_raydium_get_pools_by_lp_params(
    p: &RaydiumGetPoolsByLpParams,
) -> Result<(), AppError> {
    if p.lps.is_empty() {
        return Err(AppError::InvalidParams(
            "lps is required (comma-separated LP mint addresses)".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_get_pools_by_lp(
    http: &reqwest::Client,
    params: &RaydiumGetPoolsByLpParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{RAYDIUM_API}/pools/info/lps?lps={}", params.lps);
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_pools_by_lp".to_string(),
            description: format!("Raydium pool info for LP mints: {}", params.lps),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_pools_v2 ──────────────────────────────────────────────────────

/// List Raydium pools using v2 endpoint (cursor-based pagination).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetPoolsV2Params {
    /// Pool type: "Concentrated" or "Standard"
    #[serde(default)]
    pub pool_type: Option<String>,
    /// Filter by mint address
    #[serde(default)]
    pub mint_filter: Option<String>,
    /// Return only pools with active rewards
    #[serde(default)]
    pub has_reward: Option<bool>,
    /// Sort field: "liquidity","volume24h","fee24h","apr24h","volume7d","fee7d","apr7d","volume30d","fee30d","apr30d"
    #[serde(default)]
    pub sort_field: Option<String>,
    /// Sort order: "asc" or "desc"
    #[serde(default)]
    pub sort_type: Option<String>,
    /// Records per page (required, min 1)
    pub size: u32,
    /// Pagination token from previous response
    #[serde(default)]
    pub next_page_id: Option<String>,
    /// Filter pools containing this mint
    #[serde(default)]
    pub mint1: Option<String>,
    /// Filter pools containing this mint
    #[serde(default)]
    pub mint2: Option<String>,
}

pub fn validate_raydium_get_pools_v2_params(p: &RaydiumGetPoolsV2Params) -> Result<(), AppError> {
    if p.size == 0 {
        return Err(AppError::InvalidParams("size must be at least 1".into()));
    }
    if let Some(ref t) = p.pool_type {
        if t != "Concentrated" && t != "Standard" {
            return Err(AppError::InvalidParams(
                "poolType must be 'Concentrated' or 'Standard'".into(),
            ));
        }
    }
    if let Some(ref s) = p.sort_field {
        let valid = [
            "liquidity",
            "volume24h",
            "fee24h",
            "apr24h",
            "volume7d",
            "fee7d",
            "apr7d",
            "volume30d",
            "fee30d",
            "apr30d",
        ];
        if !valid.contains(&s.as_str()) {
            return Err(AppError::InvalidParams(format!(
                "sortField must be one of: {}",
                valid.join(", ")
            )));
        }
    }
    if let Some(ref o) = p.sort_type {
        if o != "asc" && o != "desc" {
            return Err(AppError::InvalidParams(
                "sortType must be 'asc' or 'desc'".into(),
            ));
        }
    }
    Ok(())
}

pub async fn build_raydium_get_pools_v2(
    http: &reqwest::Client,
    params: &RaydiumGetPoolsV2Params,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{RAYDIUM_API}/pools/info/list-v2?size={}", params.size);
    if let Some(ref t) = params.pool_type {
        url.push_str(&format!("&poolType={t}"));
    }
    if let Some(ref m) = params.mint_filter {
        url.push_str(&format!("&mintFilter={m}"));
    }
    if let Some(r) = params.has_reward {
        url.push_str(&format!("&hasReward={r}"));
    }
    if let Some(ref s) = params.sort_field {
        url.push_str(&format!("&sortField={s}"));
    }
    if let Some(ref o) = params.sort_type {
        url.push_str(&format!("&sortType={o}"));
    }
    if let Some(ref n) = params.next_page_id {
        url.push_str(&format!("&nextPageId={n}"));
    }
    if let Some(ref m1) = params.mint1 {
        url.push_str(&format!("&mint1={}", resolve_token_address(m1)));
    }
    if let Some(ref m2) = params.mint2 {
        url.push_str(&format!("&mint2={}", resolve_token_address(m2)));
    }

    let data = raydium_get(http, &url).await?;
    let pool_type_str = params.pool_type.as_deref().unwrap_or("all");

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_pools_v2".to_string(),
            description: format!("Raydium {pool_type_str} pools (v2, cursor pagination)"),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_pool_keys ─────────────────────────────────────────────────────

/// Get Raydium pool account keys (program addresses) for transaction building.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetPoolKeysParams {
    /// Comma-separated pool IDs
    pub ids: String,
}

pub fn validate_raydium_get_pool_keys_params(p: &RaydiumGetPoolKeysParams) -> Result<(), AppError> {
    if p.ids.is_empty() {
        return Err(AppError::InvalidParams(
            "ids is required (comma-separated pool IDs)".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_get_pool_keys(
    http: &reqwest::Client,
    params: &RaydiumGetPoolKeysParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{RAYDIUM_API}/pools/key/ids?ids={}", params.ids);
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_pool_keys".to_string(),
            description: format!("Raydium pool keys for: {}", params.ids),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_pool_liquidity_history ────────────────────────────────────────

/// Get historical liquidity (TVL) data for a Raydium pool.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetPoolLiquidityHistoryParams {
    /// Pool ID
    pub id: String,
}

pub fn validate_raydium_get_pool_liquidity_history_params(
    p: &RaydiumGetPoolLiquidityHistoryParams,
) -> Result<(), AppError> {
    if p.id.is_empty() {
        return Err(AppError::InvalidParams("id (pool ID) is required".into()));
    }
    Ok(())
}

pub async fn build_raydium_get_pool_liquidity_history(
    http: &reqwest::Client,
    params: &RaydiumGetPoolLiquidityHistoryParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{RAYDIUM_API}/pools/line/liquidity?id={}", params.id);
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_pool_liquidity_history".to_string(),
            description: format!("Liquidity history for pool {}", short_id(&params.id)),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_pool_position_history ─────────────────────────────────────────

/// Get historical CLMM price/tick/liquidity data for a Raydium pool.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetPoolPositionHistoryParams {
    /// Pool ID
    pub id: String,
}

pub fn validate_raydium_get_pool_position_history_params(
    p: &RaydiumGetPoolPositionHistoryParams,
) -> Result<(), AppError> {
    if p.id.is_empty() {
        return Err(AppError::InvalidParams("id (pool ID) is required".into()));
    }
    Ok(())
}

pub async fn build_raydium_get_pool_position_history(
    http: &reqwest::Client,
    params: &RaydiumGetPoolPositionHistoryParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{RAYDIUM_API}/pools/line/position?id={}", params.id);
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_pool_position_history".to_string(),
            description: format!("CLMM position history for pool {}", short_id(&params.id)),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_token_list ────────────────────────────────────────────────────

/// Get the full default token list from Raydium.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetTokenListParams {}

pub fn validate_raydium_get_token_list_params(
    _p: &RaydiumGetTokenListParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_token_list(
    http: &reqwest::Client,
    _params: &RaydiumGetTokenListParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/mint/list")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_token_list".to_string(),
            description: "Raydium default token list".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_token_prices ──────────────────────────────────────────────────

/// Get current prices for tokens on Raydium.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetTokenPricesParams {
    /// Comma-separated token symbols or mint addresses (optional — returns all if omitted)
    #[serde(default)]
    pub mints: Option<String>,
}

pub fn validate_raydium_get_token_prices_params(
    _p: &RaydiumGetTokenPricesParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_token_prices(
    http: &reqwest::Client,
    params: &RaydiumGetTokenPricesParams,
) -> Result<BuildResponse, AppError> {
    let mut url = format!("{RAYDIUM_API}/mint/price");
    if let Some(ref mints) = params.mints {
        let resolved: Vec<String> = mints
            .split(',')
            .map(|s| resolve_token_address(s.trim()))
            .collect();
        url.push_str(&format!("?mints={}", resolved.join(",")));
    }

    let data = raydium_get(http, &url).await?;
    let desc = match &params.mints {
        Some(m) => format!("Raydium prices for: {m}"),
        None => "Raydium token prices".to_string(),
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_token_prices".to_string(),
            description: desc,
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_farm_info ─────────────────────────────────────────────────────

/// Get Raydium farm pool info by IDs.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetFarmInfoParams {
    /// Comma-separated farm IDs
    pub ids: String,
}

pub fn validate_raydium_get_farm_info_params(p: &RaydiumGetFarmInfoParams) -> Result<(), AppError> {
    if p.ids.is_empty() {
        return Err(AppError::InvalidParams(
            "ids is required (comma-separated farm IDs)".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_get_farm_info(
    http: &reqwest::Client,
    params: &RaydiumGetFarmInfoParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{RAYDIUM_API}/farms/info/ids?ids={}", params.ids);
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_farm_info".to_string(),
            description: format!("Raydium farm info for: {}", params.ids),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_farm_by_lp ────────────────────────────────────────────────────

/// Search Raydium farms by LP mint address.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetFarmByLpParams {
    /// LP mint address
    pub lp: String,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub page_size: Option<u32>,
}

pub fn validate_raydium_get_farm_by_lp_params(
    p: &RaydiumGetFarmByLpParams,
) -> Result<(), AppError> {
    if p.lp.is_empty() {
        return Err(AppError::InvalidParams(
            "lp (LP mint address) is required".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_get_farm_by_lp(
    http: &reqwest::Client,
    params: &RaydiumGetFarmByLpParams,
) -> Result<BuildResponse, AppError> {
    let page = params.page.unwrap_or(1);
    let page_size = params.page_size.unwrap_or(20).min(100);
    let url = format!(
        "{RAYDIUM_API}/farms/info/lp?lp={}&pageSize={page_size}&page={page}",
        params.lp
    );
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_farm_by_lp".to_string(),
            description: format!("Raydium farms for LP mint: {}", short_id(&params.lp)),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_farm_keys ─────────────────────────────────────────────────────

/// Get Raydium farm account keys by IDs.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetFarmKeysParams {
    /// Comma-separated farm IDs
    pub ids: String,
}

pub fn validate_raydium_get_farm_keys_params(p: &RaydiumGetFarmKeysParams) -> Result<(), AppError> {
    if p.ids.is_empty() {
        return Err(AppError::InvalidParams(
            "ids is required (comma-separated farm IDs)".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_get_farm_keys(
    http: &reqwest::Client,
    params: &RaydiumGetFarmKeysParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{RAYDIUM_API}/farms/key/ids?ids={}", params.ids);
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_farm_keys".to_string(),
            description: format!("Raydium farm keys for: {}", params.ids),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_ido_keys ──────────────────────────────────────────────────────

/// Get Raydium IDO pool keys by IDs.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetIdoKeysParams {
    /// Comma-separated IDO pool IDs
    pub ids: String,
}

pub fn validate_raydium_get_ido_keys_params(p: &RaydiumGetIdoKeysParams) -> Result<(), AppError> {
    if p.ids.is_empty() {
        return Err(AppError::InvalidParams(
            "ids is required (comma-separated IDO pool IDs)".into(),
        ));
    }
    Ok(())
}

pub async fn build_raydium_get_ido_keys(
    http: &reqwest::Client,
    params: &RaydiumGetIdoKeysParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{RAYDIUM_API}/ido/key/ids?ids={}", params.ids);
    let data = raydium_get(http, &url).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_ido_keys".to_string(),
            description: format!("Raydium IDO pool keys for: {}", params.ids),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_main_version ──────────────────────────────────────────────────

/// Get the current Raydium UI version.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetMainVersionParams {}

pub fn validate_raydium_get_main_version_params(
    _p: &RaydiumGetMainVersionParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_main_version(
    http: &reqwest::Client,
    _params: &RaydiumGetMainVersionParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/version")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_main_version".to_string(),
            description: "Raydium current UI version".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_rpcs ──────────────────────────────────────────────────────────

/// Get the RPC endpoints used by Raydium UI.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetRpcsParams {}

pub fn validate_raydium_get_rpcs_params(_p: &RaydiumGetRpcsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_rpcs(
    http: &reqwest::Client,
    _params: &RaydiumGetRpcsParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/rpcs")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_rpcs".to_string(),
            description: "Raydium RPC endpoints".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_chain_time ────────────────────────────────────────────────────

/// Get the Solana chain time offset vs local UTC.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetChainTimeParams {}

pub fn validate_raydium_get_chain_time_params(
    _p: &RaydiumGetChainTimeParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_chain_time(
    http: &reqwest::Client,
    _params: &RaydiumGetChainTimeParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/chain-time")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_chain_time".to_string(),
            description: "Solana chain time offset vs UTC".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_stake_pools ───────────────────────────────────────────────────

/// Get Raydium RAY stake pool information.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetStakePoolsParams {}

pub fn validate_raydium_get_stake_pools_params(
    _p: &RaydiumGetStakePoolsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_stake_pools(
    http: &reqwest::Client,
    _params: &RaydiumGetStakePoolsParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/stake-pools")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_stake_pools".to_string(),
            description: "Raydium RAY stake pools".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_migrate_lp ────────────────────────────────────────────────────

/// Get the list of Raydium AMM pools eligible for LP migration to CLMM.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetMigrateLpParams {}

pub fn validate_raydium_get_migrate_lp_params(
    _p: &RaydiumGetMigrateLpParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_migrate_lp(
    http: &reqwest::Client,
    _params: &RaydiumGetMigrateLpParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/migrate-lp")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_migrate_lp".to_string(),
            description: "Raydium AMM → CLMM migration pool list".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_auto_fee ──────────────────────────────────────────────────────

/// Get Raydium's suggested transaction priority fee tiers.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetAutoFeeParams {}

pub fn validate_raydium_get_auto_fee_params(_p: &RaydiumGetAutoFeeParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_auto_fee(
    http: &reqwest::Client,
    _params: &RaydiumGetAutoFeeParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/auto-fee")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_auto_fee".to_string(),
            description: "Raydium suggested priority fee tiers (low/medium/high)".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

// ── raydium_get_cpmm_configs ──────────────────────────────────────────────────

/// Get available CPMM (Constant Product Market Maker) pool configurations on Raydium.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RaydiumGetCpmmConfigsParams {}

pub fn validate_raydium_get_cpmm_configs_params(
    _p: &RaydiumGetCpmmConfigsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_raydium_get_cpmm_configs(
    http: &reqwest::Client,
    _params: &RaydiumGetCpmmConfigsParams,
) -> Result<BuildResponse, AppError> {
    let data = raydium_get(http, &format!("{RAYDIUM_API}/main/cpmm-config")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "raydium_get_cpmm_configs".to_string(),
            description: "Raydium CPMM fee configurations".to_string(),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
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

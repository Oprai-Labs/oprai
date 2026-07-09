//! Kamino Finance integration.
//!
//! # Architecture
//! Kamino exposes two API families:
//!
//! ## Transaction Builder  (`https://api.kamino.finance/ktx/`)
//! Returns unsigned, base64-encoded Solana transactions ready for wallet signing.
//! - `POST /ktx/klend/{deposit|withdraw|borrow|repay}` — K-Lend operations
//! - `POST /ktx/kvault/{deposit|withdraw}`            — Earn vault operations
//!
//! ## Data API  (`https://api.kamino.finance/`)
//! Read-only GET endpoints for market/vault state.
//! - `/kvaults/vaults`                    — All earn vaults
//! - `/kvaults/vaults/{vault}/metrics`   — Vault APY & metrics
//! - `/kamino-market`                     — All K-Lend markets
//! - `/kamino-market/{market}/reserves/metrics` — Reserve metrics
//! - `/kvaults/users/{wallet}/positions`  — User earn positions
//! - `/kamino-market/{market}/users/{wallet}/obligations` — User borrow positions
//! - `/oracles/prices`                    — Oracle prices
//!
//! ## Features NOT available via REST API (require Kamino SDK)
//! Multiply vaults, Long/Short leveraged positions, and KMNO staking all require
//! the `@kamino-finance/leverage-sdk` or `klend-sdk`. They return a clear error.
//!
//! ## Amount format
//! All transaction builder endpoints accept amounts as **decimal strings**
//! (e.g. `"1.5"` for 1.5 USDC), NOT base-unit integers.

use serde::{Deserialize, Serialize};

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────


/// Kamino REST API base URL (no trailing slash).
const KAMINO_API: &str = "https://api.kamino.finance";

/// Kamino main K-Lend market — used when the caller omits `market`.
const KAMINO_MAIN_MARKET: &str = "7u3HeL2w1613C9uTdnK9GkfFfByNTYT1Y5MiUBHTrfip";

/// Resolve a `market` param: blank/None or a symbolic alias ("main", "default",
/// "primary") → KAMINO_MAIN_MARKET. A base58-looking string (32–44 chars,
/// no spaces) passes through. Anything else also falls back to MAIN — Kamino
/// API would 400 otherwise, and a 400 turn produces a confused user
/// experience while masking the real issue (the model said "main" instead of
/// looking up a pubkey).
fn resolve_kamino_market<'a>(market: Option<&'a str>) -> &'a str {
    match market.map(str::trim) {
        None => KAMINO_MAIN_MARKET,
        Some("") => KAMINO_MAIN_MARKET,
        Some(m) => {
            let lower = m.to_ascii_lowercase();
            if matches!(lower.as_str(), "main" | "default" | "primary" | "kamino_main" | "klend_main") {
                KAMINO_MAIN_MARKET
            } else if m.len() >= 32 && m.len() <= 44 && !m.contains(' ') {
                m
            } else {
                KAMINO_MAIN_MARKET
            }
        }
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// K-LEND Parameter Types
// ──────────────────────────────────────────────────────────────────────────────

/// Deposit tokens into K-Lend (earns interest; all deposits count as collateral).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoDepositParams {
    /// KLend reserve account address for the token to deposit. Required.
    /// Use kamino_market_reserves to look up reserve addresses.
    pub reserve: String,
    /// Amount to deposit (decimal, e.g. "1.5"). Required.
    pub amount: String,
    /// K-Lend market address. Defaults to Kamino main market.
    #[serde(default)]
    pub market: Option<String>,
}

/// Withdraw tokens from K-Lend.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoWithdrawParams {
    /// KLend reserve account address for the token to withdraw. Required.
    pub reserve: String,
    /// Amount to withdraw (decimal). Required.
    pub amount: String,
    /// K-Lend market address. Defaults to Kamino main market.
    #[serde(default)]
    pub market: Option<String>,
}

/// Borrow tokens from K-Lend against deposited collateral.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoBorrowParams {
    /// KLend reserve account address for the token to borrow. Required.
    /// Use kamino_market_reserves to look up reserve addresses.
    pub reserve: String,
    /// Amount to borrow (decimal). Required.
    pub amount: String,
    /// K-Lend market address. Defaults to Kamino main market.
    #[serde(default)]
    pub market: Option<String>,
}

/// Repay a K-Lend borrow.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoRepayParams {
    /// KLend reserve account address for the token to repay. Required.
    pub reserve: String,
    /// Amount to repay (decimal). Required.
    pub amount: String,
    /// K-Lend market address. Defaults to Kamino main market.
    #[serde(default)]
    pub market: Option<String>,
}

/// Add collateral to a K-Lend obligation (alias for deposit — all deposits are collateral).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoAddCollateralParams {
    /// KLend reserve account address. Required.
    pub reserve: String,
    pub amount: String,
    #[serde(default)]
    pub market: Option<String>,
}

/// Withdraw collateral from a K-Lend obligation (alias for withdraw).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoWithdrawCollateralParams {
    /// KLend reserve account address. Required.
    pub reserve: String,
    pub amount: String,
    #[serde(default)]
    pub market: Option<String>,
}

// ──────────────────────────────────────────────────────────────────────────────
// MULTIPLY VAULTS — SDK-only (not available via REST API)
// ──────────────────────────────────────────────────────────────────────────────

/// Open a Multiply vault position.
/// NOTE: Requires @kamino-finance/leverage-sdk. Not available via REST API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMultiplyOpenParams {
    pub strategy: String,
    pub amount: String,
    pub token: String,
    pub leverage: f64,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Add to a Multiply vault position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMultiplyAddParams {
    pub position: String,
    pub amount: String,
    pub token: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Withdraw from a Multiply vault position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMultiplyWithdrawParams {
    pub position: String,
    pub percent: f64,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Close a Multiply vault position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMultiplyCloseParams {
    pub position: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

// ──────────────────────────────────────────────────────────────────────────────
// LONG/SHORT VAULTS — SDK-only (not available via REST API)
// ──────────────────────────────────────────────────────────────────────────────

/// Open a Long leveraged position.
/// NOTE: Requires @kamino-finance/leverage-sdk. Not available via REST API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoLongOpenParams {
    pub collateral_token: String,
    pub collateral_amount: String,
    pub leverage: f64,
    #[serde(default)]
    pub debt_token: Option<String>,
    #[serde(default)]
    pub market: Option<String>,
    #[serde(default)]
    pub size_usd: Option<f64>,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Open a Short leveraged position.
/// NOTE: Requires @kamino-finance/leverage-sdk. Not available via REST API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoShortOpenParams {
    pub collateral_token: String,
    pub collateral_amount: String,
    pub leverage: f64,
    #[serde(default)]
    pub debt_token: Option<String>,
    #[serde(default)]
    pub market: Option<String>,
    #[serde(default)]
    pub size_usd: Option<f64>,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Close a Long/Short leveraged position.
/// NOTE: Requires @kamino-finance/leverage-sdk. Not available via REST API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoPositionCloseParams {
    pub position: String,
    #[serde(default)]
    pub percent: Option<f64>,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

// ──────────────────────────────────────────────────────────────────────────────
// EARN VAULTS (kVaults / Liquidity Vaults)
// ──────────────────────────────────────────────────────────────────────────────

/// Deposit into a Kamino Earn vault (automated CLMM liquidity).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultDepositParams {
    /// Vault address (base58). Also accepted as "vaultName" alias.
    #[serde(alias = "vaultName")]
    pub vault: String,
    /// Amount to deposit (decimal, e.g. "10.0"). Required.
    pub amount: String,
    /// Token symbol/mint — informational only (vault accepts its configured token).
    #[serde(default)]
    pub token: Option<String>,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Withdraw from a Kamino Earn vault by redeeming kToken shares.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultWithdrawParams {
    /// Vault address (base58).
    #[serde(alias = "vaultName")]
    pub vault: String,
    /// Number of kToken shares to redeem (decimal, e.g. "5.0").
    /// Also accepted as "shares" or "amount".
    #[serde(alias = "shares", alias = "amount")]
    pub ktoken_amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

// ──────────────────────────────────────────────────────────────────────────────
// KMNO STAKING — SDK-only (not available via REST API)
// ──────────────────────────────────────────────────────────────────────────────

/// Stake KMNO governance tokens.
/// NOTE: Requires klend-sdk. Not available via REST API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoStakeParams {
    pub amount: String,
}

/// Unstake KMNO governance tokens.
/// NOTE: Requires klend-sdk. Not available via REST API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUnstakeParams {
    pub amount: String,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

fn validate_reserve_address(reserve: &str, field: &str) -> Result<(), AppError> {
    if reserve.is_empty() || reserve.len() < 32 {
        return Err(AppError::InvalidParams(format!("{field} must be a valid KLend reserve account address (base58)")));
    }
    Ok(())
}

pub fn validate_kamino_deposit_params(p: &KaminoDepositParams) -> Result<(), AppError> {
    validate_reserve_address(&p.reserve, "reserve")?;
    validate_positive_amount(&p.amount, "amount")?;
    if let Some(ref m) = p.market {
        if m.len() < 32 { return Err(AppError::InvalidParams(format!("market '{m}' is not a valid Solana address"))); }
    }
    Ok(())
}

pub fn validate_kamino_withdraw_params(p: &KaminoWithdrawParams) -> Result<(), AppError> {
    validate_reserve_address(&p.reserve, "reserve")?;
    validate_positive_amount(&p.amount, "amount")?;
    if let Some(ref m) = p.market {
        if m.len() < 32 { return Err(AppError::InvalidParams(format!("market '{m}' is not a valid Solana address"))); }
    }
    Ok(())
}

pub fn validate_kamino_borrow_params(p: &KaminoBorrowParams) -> Result<(), AppError> {
    validate_reserve_address(&p.reserve, "reserve")?;
    validate_positive_amount(&p.amount, "amount")?;
    if let Some(ref m) = p.market {
        if m.len() < 32 { return Err(AppError::InvalidParams(format!("market '{m}' is not a valid Solana address"))); }
    }
    Ok(())
}

pub fn validate_kamino_repay_params(p: &KaminoRepayParams) -> Result<(), AppError> {
    validate_reserve_address(&p.reserve, "reserve")?;
    validate_positive_amount(&p.amount, "amount")?;
    if let Some(ref m) = p.market {
        if m.len() < 32 { return Err(AppError::InvalidParams(format!("market '{m}' is not a valid Solana address"))); }
    }
    Ok(())
}

pub fn validate_kamino_add_collateral_params(p: &KaminoAddCollateralParams) -> Result<(), AppError> {
    validate_reserve_address(&p.reserve, "reserve")?;
    validate_positive_amount(&p.amount, "amount")?;
    if let Some(ref m) = p.market {
        if m.len() < 32 { return Err(AppError::InvalidParams(format!("market '{m}' is not a valid Solana address"))); }
    }
    Ok(())
}

pub fn validate_kamino_withdraw_collateral_params(p: &KaminoWithdrawCollateralParams) -> Result<(), AppError> {
    validate_reserve_address(&p.reserve, "reserve")?;
    validate_positive_amount(&p.amount, "amount")?;
    if let Some(ref m) = p.market {
        if m.len() < 32 { return Err(AppError::InvalidParams(format!("market '{m}' is not a valid Solana address"))); }
    }
    Ok(())
}

pub fn validate_kamino_multiply_open_params(p: &KaminoMultiplyOpenParams) -> Result<(), AppError> {
    if p.strategy.is_empty() {
        return Err(AppError::InvalidParams("strategy is required".into()));
    }
    validate_positive_amount(&p.amount, "amount")?;
    if p.leverage < 1.0 || p.leverage > 10.0 {
        return Err(AppError::InvalidParams("leverage must be between 1.0 and 10.0".into()));
    }
    Ok(())
}

pub fn validate_kamino_multiply_add_params(p: &KaminoMultiplyAddParams) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    validate_positive_amount(&p.amount, "amount")?;
    Ok(())
}

pub fn validate_kamino_multiply_withdraw_params(p: &KaminoMultiplyWithdrawParams) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    if p.percent <= 0.0 || p.percent > 100.0 {
        return Err(AppError::InvalidParams(
            "percent must be > 0 and ≤ 100 (or 0.0–1.0 for fractional)".into(),
        ));
    }
    Ok(())
}

pub fn validate_kamino_multiply_close_params(p: &KaminoMultiplyCloseParams) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    Ok(())
}

pub fn validate_kamino_long_open_params(p: &KaminoLongOpenParams) -> Result<(), AppError> {
    if p.collateral_token.is_empty() {
        return Err(AppError::InvalidParams("collateralToken is required".into()));
    }
    validate_positive_amount(&p.collateral_amount, "collateralAmount")?;
    if p.leverage < 1.0 || p.leverage > 20.0 {
        return Err(AppError::InvalidParams("leverage must be between 1.0 and 20.0".into()));
    }
    Ok(())
}

pub fn validate_kamino_short_open_params(p: &KaminoShortOpenParams) -> Result<(), AppError> {
    if p.collateral_token.is_empty() {
        return Err(AppError::InvalidParams("collateralToken is required".into()));
    }
    validate_positive_amount(&p.collateral_amount, "collateralAmount")?;
    if p.leverage < 1.0 || p.leverage > 20.0 {
        return Err(AppError::InvalidParams("leverage must be between 1.0 and 20.0".into()));
    }
    Ok(())
}

pub fn validate_kamino_position_close_params(p: &KaminoPositionCloseParams) -> Result<(), AppError> {
    if p.position.is_empty() {
        return Err(AppError::InvalidParams("position is required".into()));
    }
    if let Some(pct) = p.percent {
        if pct <= 0.0 || pct > 100.0 {
            return Err(AppError::InvalidParams(
                "percent must be > 0 and ≤ 100 (or 0.0–1.0 for fractional)".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_kamino_vault_deposit_params(p: &KaminoVaultDepositParams) -> Result<(), AppError> {
    if p.vault.is_empty() {
        return Err(AppError::InvalidParams("vault address is required".into()));
    }
    if p.vault.len() < 32 {
        return Err(AppError::InvalidParams(format!("vault '{}' is not a valid Solana address", p.vault)));
    }
    validate_positive_amount(&p.amount, "amount")?;
    Ok(())
}

pub fn validate_kamino_vault_withdraw_params(p: &KaminoVaultWithdrawParams) -> Result<(), AppError> {
    if p.vault.is_empty() {
        return Err(AppError::InvalidParams("vault address is required".into()));
    }
    if p.vault.len() < 32 {
        return Err(AppError::InvalidParams(format!("vault '{}' is not a valid Solana address", p.vault)));
    }
    validate_positive_amount(&p.ktoken_amount, "ktokenAmount")?;
    Ok(())
}

pub fn validate_kamino_stake_params(p: &KaminoStakeParams) -> Result<(), AppError> {
    validate_positive_amount(&p.amount, "amount")
}

pub fn validate_kamino_unstake_params(p: &KaminoUnstakeParams) -> Result<(), AppError> {
    validate_positive_amount(&p.amount, "amount")
}

// ──────────────────────────────────────────────────────────────────────────────
// Internal Helpers
// ──────────────────────────────────────────────────────────────────────────────

fn validate_positive_amount(amount: &str, field: &str) -> Result<(), AppError> {
    let v: f64 = amount
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("{field} must be a positive number, got: '{amount}'")))?;
    if v <= 0.0 {
        return Err(AppError::InvalidParams(format!("{field} must be positive, got: {amount}")));
    }
    Ok(())
}

fn short_id(id: &str) -> String {
    let len = id.len().min(8);
    format!("{}…", &id[..len])
}

fn sdk_only_error(feature: &str) -> AppError {
    AppError::InvalidParams(format!(
        "{feature} requires the Kamino leverage-sdk or klend-sdk and is not available \
         via the REST API. Use the OpraiOS SDK integration for this feature."
    ))
}


// ──────────────────────────────────────────────────────────────────────────────
// Internal HTTP helpers
// ──────────────────────────────────────────────────────────────────────────────

/// POST to Kamino TX builder. Returns the base64-encoded transaction string.
async fn kamino_post_tx(
    http: &reqwest::Client,
    path: &str,
    body: &serde_json::Value,
) -> Result<String, AppError> {
    let url = format!("{KAMINO_API}{path}");
    let resp = http
        .post(&url)
        .json(body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino POST {path}: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino POST {path} returned {status}: {body_text}"
        )));
    }
    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino POST {path} parse error: {e}")))?;
    extract_transaction(&json, path)
}

/// Extract the base64 transaction string from a Kamino TX builder response.
/// Kamino returns: `{ "transaction": "<base64>" }` or `{ "tx": "<base64>" }`.
fn extract_transaction(json: &serde_json::Value, ctx: &str) -> Result<String, AppError> {
    if let Some(tx) = json.get("transaction").and_then(|v| v.as_str()) {
        return Ok(tx.to_string());
    }
    if let Some(tx) = json.get("tx").and_then(|v| v.as_str()) {
        return Ok(tx.to_string());
    }
    Err(AppError::ProtocolError(format!(
        "Kamino {ctx} response missing 'transaction' field: {json}"
    )))
}

// ──────────────────────────────────────────────────────────────────────────────
// K-LEND Transaction Builders
// ──────────────────────────────────────────────────────────────────────────────

/// Deposit tokens into K-Lend.
pub async fn build_kamino_deposit(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoDepositParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_deposit_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let body = serde_json::json!({
        "wallet": wallet,
        "market": market,
        "reserve": params.reserve,
        "amount": params.amount,
    });
    let tx_b64 = kamino_post_tx(http, "/ktx/klend/deposit", &body).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_deposit".into(),
            description: format!(
                "Deposit {} into Kamino K-Lend reserve {}",
                params.amount,
                short_id(&params.reserve),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
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

/// Withdraw tokens from K-Lend.
pub async fn build_kamino_withdraw(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoWithdrawParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_withdraw_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let body = serde_json::json!({
        "wallet": wallet,
        "market": market,
        "reserve": params.reserve,
        "amount": params.amount,
    });
    let tx_b64 = kamino_post_tx(http, "/ktx/klend/withdraw", &body).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_withdraw".into(),
            description: format!(
                "Withdraw {} from Kamino K-Lend reserve {}",
                params.amount,
                short_id(&params.reserve),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
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

/// Borrow tokens from K-Lend against deposited collateral.
pub async fn build_kamino_borrow(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoBorrowParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_borrow_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let body = serde_json::json!({
        "wallet": wallet,
        "market": market,
        "reserve": params.reserve,
        "amount": params.amount,
    });
    let tx_b64 = kamino_post_tx(http, "/ktx/klend/borrow", &body).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_borrow".into(),
            description: format!(
                "Borrow {} from Kamino K-Lend reserve {}",
                params.amount,
                short_id(&params.reserve),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec!["Ensure you have sufficient collateral before borrowing.".into()],
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

/// Repay a K-Lend borrow.
pub async fn build_kamino_repay(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoRepayParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_repay_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let body = serde_json::json!({
        "wallet": wallet,
        "market": market,
        "reserve": params.reserve,
        "amount": params.amount,
    });
    let tx_b64 = kamino_post_tx(http, "/ktx/klend/repay", &body).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_repay".into(),
            description: format!(
                "Repay {} to Kamino K-Lend reserve {}",
                params.amount,
                short_id(&params.reserve),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
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

/// Add collateral to K-Lend (alias for deposit — all deposits are collateral).
pub async fn build_kamino_add_collateral(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoAddCollateralParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_add_collateral_params(params)?;
    let deposit_params = KaminoDepositParams {
        reserve: params.reserve.clone(),
        amount: params.amount.clone(),
        market: params.market.clone(),
    };
    let mut result = build_kamino_deposit(http, wallet, &deposit_params).await?;
    result.preview.action_type = "kamino_add_collateral".into();
    result.preview.description = format!(
        "Add {} as collateral in Kamino K-Lend (reserve {})",
        params.amount,
        short_id(&params.reserve),
    );
    Ok(result)
}

/// Withdraw collateral from K-Lend (alias for withdraw).
pub async fn build_kamino_withdraw_collateral(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoWithdrawCollateralParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_withdraw_collateral_params(params)?;
    let withdraw_params = KaminoWithdrawParams {
        reserve: params.reserve.clone(),
        amount: params.amount.clone(),
        market: params.market.clone(),
    };
    let mut result = build_kamino_withdraw(http, wallet, &withdraw_params).await?;
    result.preview.action_type = "kamino_withdraw_collateral".into();
    result.preview.description = format!(
        "Withdraw {} collateral from Kamino K-Lend (reserve {})",
        params.amount,
        short_id(&params.reserve),
    );
    Ok(result)
}

// ──────────────────────────────────────────────────────────────────────────────
// K-Vault (Earn) Transaction Builders
// ──────────────────────────────────────────────────────────────────────────────

/// Deposit into a Kamino Earn vault.
pub async fn build_kamino_vault_deposit(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoVaultDepositParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_deposit_params(params)?;
    let body = serde_json::json!({
        "wallet": wallet,
        "kvault": params.vault,
        "amount": params.amount,
    });
    let tx_b64 = kamino_post_tx(http, "/ktx/kvault/deposit", &body).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_deposit".into(),
            description: format!(
                "Deposit {} into Kamino Earn vault {}",
                params.amount,
                short_id(&params.vault),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
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

/// Withdraw kToken shares from a Kamino Earn vault.
pub async fn build_kamino_vault_withdraw(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoVaultWithdrawParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_withdraw_params(params)?;
    let body = serde_json::json!({
        "wallet": wallet,
        "kvault": params.vault,
        "amount": params.ktoken_amount,
    });
    let tx_b64 = kamino_post_tx(http, "/ktx/kvault/withdraw", &body).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_withdraw".into(),
            description: format!(
                "Withdraw {} kTokens from Kamino Earn vault {}",
                params.ktoken_amount,
                short_id(&params.vault),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
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
// SDK-only stubs (Multiply / Long / Short / KMNO Staking)
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_kamino_multiply_open(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoMultiplyOpenParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_multiply_open_params(p)?;
    Err(sdk_only_error("Kamino Multiply vault open"))
}

pub async fn build_kamino_multiply_add(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoMultiplyAddParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_multiply_add_params(p)?;
    Err(sdk_only_error("Kamino Multiply vault deposit"))
}

pub async fn build_kamino_multiply_withdraw(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoMultiplyWithdrawParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_multiply_withdraw_params(p)?;
    Err(sdk_only_error("Kamino Multiply vault withdraw"))
}

pub async fn build_kamino_multiply_close(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoMultiplyCloseParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_multiply_close_params(p)?;
    Err(sdk_only_error("Kamino Multiply vault close"))
}

pub async fn build_kamino_long_open(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoLongOpenParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_long_open_params(p)?;
    Err(sdk_only_error("Kamino Long leveraged position"))
}

pub async fn build_kamino_short_open(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoShortOpenParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_short_open_params(p)?;
    Err(sdk_only_error("Kamino Short leveraged position"))
}

pub async fn build_kamino_position_close(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoPositionCloseParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_position_close_params(p)?;
    Err(sdk_only_error("Kamino leveraged position close"))
}

pub async fn build_kamino_stake(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_stake_params(p)?;
    Err(sdk_only_error("KMNO staking"))
}

pub async fn build_kamino_unstake(
    _http: &reqwest::Client,
    _wallet: &str,
    p: &KaminoUnstakeParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_unstake_params(p)?;
    Err(sdk_only_error("KMNO unstaking"))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET Query Parameter Types
// ──────────────────────────────────────────────────────────────────────────────

/// List all Kamino Earn vaults.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultsParams {
    /// Max number of vaults to return.
    #[serde(default)]
    pub limit: Option<u32>,
}

/// Get the user's Kamino Earn vault positions.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserVaultPositionsParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
}

/// List all K-Lend markets.
/// API: GET /v2/kamino-market?programId=...
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMarketsParams {
    /// KLend program ID (optional, defaults to KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD).
    #[serde(default)]
    pub program_id: Option<String>,
}

/// Get reserve metrics (APY, utilization, liquidity) for a K-Lend market.
/// API: GET /kamino-market/{pubkey}/reserves/metrics?env=...
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMarketReservesParams {
    /// K-Lend market address. Defaults to the main market.
    #[serde(default)]
    pub market: Option<String>,
    /// Solana cluster: "mainnet-beta" (default), "devnet", or "localnet". Optional.
    #[serde(default)]
    pub env: Option<String>,
}

/// Get the user's active K-Lend borrow/lend obligations.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserObligationsParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// K-Lend market address. Defaults to the main market.
    #[serde(default)]
    pub market: Option<String>,
    /// Solana cluster: "mainnet-beta" (default), "devnet", or "localnet". Optional.
    #[serde(default)]
    pub env: Option<String>,
}

/// Fetch Kamino oracle prices for all tracked tokens.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoOraclePricesParams {}

// ── GET Validators ─────────────────────────────────────────────────────────────

pub fn validate_kamino_vaults_params(_p: &KaminoVaultsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_user_vault_positions_params(
    _p: &KaminoUserVaultPositionsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_markets_params(_p: &KaminoMarketsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_market_reserves_params(
    p: &KaminoMarketReservesParams,
) -> Result<(), AppError> {
    if let Some(ref m) = p.market {
        if m.len() < 32 {
            return Err(AppError::InvalidParams(format!(
                "market '{m}' is not a valid Solana address"
            )));
        }
    }
    Ok(())
}

pub fn validate_kamino_user_obligations_params(
    p: &KaminoUserObligationsParams,
) -> Result<(), AppError> {
    if let Some(ref m) = p.market {
        if m.len() < 32 {
            return Err(AppError::InvalidParams(format!(
                "market '{m}' is not a valid Solana address"
            )));
        }
    }
    Ok(())
}

pub fn validate_kamino_oracle_prices_params(_p: &KaminoOraclePricesParams) -> Result<(), AppError> {
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Internal GET helper
// ──────────────────────────────────────────────────────────────────────────────

/// GET request against the Kamino Data API. Returns the parsed JSON body.
async fn kamino_get(http: &reqwest::Client, path: &str) -> Result<serde_json::Value, AppError> {
    let url = format!("{KAMINO_API}{path}");
    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino GET {path}: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino GET {path} returned {status}: {body_text}"
        )));
    }
    resp.json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino GET {path} parse error: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET Query Builders
// ──────────────────────────────────────────────────────────────────────────────

/// List Kamino Earn vaults with APY and TVL.
pub async fn build_kamino_vaults(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vaults_params(params)?;
    let data = kamino_get(http, "/kvaults/vaults").await?;
    let mut vaults = data.as_array().cloned().unwrap_or_default();
    if let Some(limit) = params.limit {
        vaults.truncate(limit as usize);
    }
    let count = vaults.len();
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vaults".into(),
            description: format!("{count} Kamino Earn vaults available"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::Value::Array(vaults),
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

/// Get the user's current positions in Kamino Earn vaults.
pub async fn build_kamino_user_vault_positions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserVaultPositionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_vault_positions_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let data = kamino_get(http, &format!("/kvaults/users/{target}/positions")).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_vault_positions".into(),
            description: format!("{count} active Kamino Earn position(s) for {}", short_id(target)),
            estimated_fee: "0".into(),
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

/// List all K-Lend markets and their metadata.
pub async fn build_kamino_markets(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoMarketsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_markets_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref p) = params.program_id { query.push(("programId", p.clone())); }
    let data = kamino_get_q(http, "/v2/kamino-market", &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_markets".into(),
            description: format!("{count} K-Lend market(s) available"),
            estimated_fee: "0".into(),
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

/// Get reserve APY, utilization, and liquidity metrics for a K-Lend market.
pub async fn build_kamino_market_reserves(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoMarketReservesParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_market_reserves_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env { query.push(("env", e.clone())); }
    let data =
        kamino_get_q(http, &format!("/kamino-market/{market}/reserves/metrics"), &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_market_reserves".into(),
            description: format!("{count} reserves in K-Lend market {}", short_id(market)),
            estimated_fee: "0".into(),
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

/// Get the user's active K-Lend borrow/deposit obligations.
pub async fn build_kamino_user_obligations(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserObligationsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_obligations_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let market = resolve_kamino_market(params.market.as_deref());
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env { query.push(("env", e.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/kamino-market/{market}/users/{target}/obligations"),
        &query,
    )
    .await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    let warnings = if count == 0 {
        vec![]
    } else {
        vec!["Review your health factor to avoid liquidation.".into()]
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_obligations".into(),
            description: format!(
                "{count} active obligation(s) for {} in K-Lend market {}",
                short_id(target),
                short_id(market),
            ),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
            warnings,
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

/// Fetch oracle prices for all tokens tracked by Kamino.
pub async fn build_kamino_oracle_prices(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoOraclePricesParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_oracle_prices_params(params)?;
    let data = kamino_get(http, "/oracles/prices").await?;
    let count = data
        .as_object()
        .map(|m| m.len())
        .or_else(|| data.as_array().map(|a| a.len()))
        .unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_oracle_prices".into(),
            description: format!("Oracle prices for {count} tokens"),
            estimated_fee: "0".into(),
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

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Extended Earn Vault Data — GET Parameter Types
// ──────────────────────────────────────────────────────────────────────────────

/// Get details for a specific Kamino Earn vault.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultDetailParams {
    /// Vault address (base58). Required.
    pub vault: String,
}

/// Get APY and TVL metrics for a specific Kamino Earn vault.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultMetricsParams {
    /// Vault address (base58). Required.
    pub vault: String,
}

/// Get historical metrics for a specific Kamino Earn vault.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultMetricsHistoryParams {
    /// Vault address (base58). Required.
    pub vault: String,
    /// Start of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub start: Option<i64>,
    /// End of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub end: Option<i64>,
}

/// Get allocation volume history for a specific Kamino Earn vault.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultAllocationHistoryParams {
    /// Vault address (base58). Required.
    pub vault: String,
    /// Start of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub start: Option<i64>,
    /// End of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub end: Option<i64>,
}

/// Get reward information for all Kamino Earn vaults.
/// API: GET /kvaults/rewards?source=<Season1|Season2|Season3|Season4>
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultsRewardsParams {
    /// Points source identifier (Season1, Season2, Season3, Season4). Optional.
    #[serde(default)]
    pub source: Option<String>,
}

/// Get an all-time summary of Kamino Earn vault rewards and interest.
/// API: GET /kvaults/summary?type=<default|private-credit>
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultsSummaryParams {
    /// Summary type: "default" (default) or "private-credit". Optional.
    #[serde(default)]
    pub vault_type: Option<String>,
}

/// Get kToken metadata for a Kamino Earn vault's share token.
/// API: GET /kvaults/mints/{mint}/metadata
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultMintMetadataParams {
    /// kToken mint address (base58). Required.
    pub mint: String,
    /// Solana cluster: "mainnet-beta" (default), "devnet", or "localnet". Optional.
    #[serde(default)]
    pub env: Option<String>,
}

/// Get the kToken SVG image for a Kamino Earn vault's share token.
/// API: GET /kvaults/mints/{mint}/metadata/image.svg
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultMintImageParams {
    /// kToken mint address (base58). Required.
    pub mint: String,
    /// Solana cluster: "mainnet-beta" (default), "devnet", or "localnet". Optional.
    #[serde(default)]
    pub env: Option<String>,
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Extended Earn User Data — GET Parameter Types
// ──────────────────────────────────────────────────────────────────────────────

/// Get historical Earn metrics for a user across all vaults.
/// API: GET /kvaults/users/{pubkey}/metrics/history
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserMetricsHistoryParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Start of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub start: Option<i64>,
    /// End of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub end: Option<i64>,
}

/// Get Earn transaction history for a user.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserTransactionsParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Max number of transactions to return. Optional.
    #[serde(default)]
    pub limit: Option<u32>,
    /// Cursor for pagination (transaction signature). Optional.
    #[serde(default)]
    pub cursor: Option<String>,
}

/// Get a user's position in a specific Kamino Earn vault.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserVaultPositionParams {
    /// Vault address (base58). Required.
    pub vault: String,
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
}

/// Get historical metrics for a user's position in a specific Kamino Earn vault.
/// API: GET /kvaults/users/{userPubkey}/vaults/{vaultPubkey}/metrics/history
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserVaultMetricsHistoryParams {
    /// Vault address (base58). Required.
    pub vault: String,
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Start of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub start: Option<i64>,
    /// End of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub end: Option<i64>,
}

/// Get PnL for a user's position in a specific Kamino Earn vault.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserVaultPnlParams {
    /// Vault address (base58). Required.
    pub vault: String,
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
}

/// Get PnL history for a user's position in a specific Kamino Earn vault.
/// API: GET /kvaults/users/{userPubkey}/vaults/{vaultPubkey}/pnl/history
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserVaultPnlHistoryParams {
    /// Vault address (base58). Required.
    pub vault: String,
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Start of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub start: Option<i64>,
    /// End of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub end: Option<i64>,
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Earn Action — Instructions variants (returns instructions array)
// ──────────────────────────────────────────────────────────────────────────────

/// Get deposit instructions (array) for a Kamino Earn vault (unsigned).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultDepositInstructionsParams {
    /// Vault address (base58). Required.
    pub vault: String,
    /// Amount to deposit (decimal). Required.
    pub amount: String,
    /// Slippage tolerance in basis points. Optional.
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

/// Get withdraw instructions (array) for a Kamino Earn vault (unsigned).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoVaultWithdrawInstructionsParams {
    /// Vault address (base58). Required.
    pub vault: String,
    /// kToken shares to redeem (decimal). Required.
    pub ktoken_amount: String,
    /// Slippage tolerance in basis points. Optional.
    #[serde(default)]
    pub slippage_bps: Option<u32>,
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Extended Borrow Market Data — GET Parameter Types
// ──────────────────────────────────────────────────────────────────────────────

/// Get details for a specific K-Lend market.
/// API: GET /v2/kamino-market/{pubkey}?programId=...
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMarketDetailParams {
    /// K-Lend market address. Defaults to the main market.
    #[serde(default)]
    pub market: Option<String>,
    /// KLend program ID (optional, defaults to KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD).
    #[serde(default)]
    pub program_id: Option<String>,
}

/// Get historical metrics for a specific reserve in a K-Lend market.
/// API: GET /kamino-market/{marketPubkey}/reserves/{reservePubkey}/metrics/history
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMarketReserveHistoryParams {
    /// Reserve public key (on-chain reserve account address). Required.
    pub reserve: String,
    /// K-Lend market address. Defaults to the main market.
    #[serde(default)]
    pub market: Option<String>,
    /// Start of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub start: Option<i64>,
    /// End of range (ISO 8601 string or epoch in milliseconds). Optional.
    #[serde(default)]
    pub end: Option<i64>,
    /// Snapshot frequency: "hour" (default) or "day". Optional.
    #[serde(default)]
    pub frequency: Option<String>,
    /// Solana cluster: "mainnet-beta" (default), "devnet", or "localnet". Optional.
    #[serde(default)]
    pub env: Option<String>,
}

/// Get leverage metrics (Multiply/Long/Short positions) for a K-Lend market.
/// API: GET /kamino-market/{pubkey}/leverage/metrics
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMarketLeverageMetricsParams {
    /// K-Lend market address. Defaults to the main market.
    #[serde(default)]
    pub market: Option<String>,
    /// Solana cluster: "mainnet-beta" (default), "devnet", or "localnet". Optional.
    #[serde(default)]
    pub env: Option<String>,
}

/// Get on-chain account data for reserves across K-Lend markets.
/// API: GET /kamino-market/reserves/account-data?markets=...&programId=...
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoMarketReservesAccountParams {
    /// One or more K-Lend market addresses. Required. Defaults to [KAMINO_MAIN_MARKET] if empty.
    #[serde(default)]
    pub markets: Vec<String>,
    /// KLend program ID (optional, defaults to KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD).
    #[serde(default)]
    pub program_id: Option<String>,
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Validators
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_kamino_vault_detail_params(p: &KaminoVaultDetailParams) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_vault_metrics_params(p: &KaminoVaultMetricsParams) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_vault_metrics_history_params(
    p: &KaminoVaultMetricsHistoryParams,
) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_vault_allocation_history_params(
    p: &KaminoVaultAllocationHistoryParams,
) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_vaults_rewards_params(_p: &KaminoVaultsRewardsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_vaults_summary_params(_p: &KaminoVaultsSummaryParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_vault_mint_metadata_params(
    p: &KaminoVaultMintMetadataParams,
) -> Result<(), AppError> {
    if p.mint.is_empty() || p.mint.len() < 32 {
        return Err(AppError::InvalidParams("mint must be a valid Solana address (kToken mint)".into()));
    }
    Ok(())
}

pub fn validate_kamino_vault_mint_image_params(
    p: &KaminoVaultMintImageParams,
) -> Result<(), AppError> {
    if p.mint.is_empty() || p.mint.len() < 32 {
        return Err(AppError::InvalidParams("mint must be a valid Solana address (kToken mint)".into()));
    }
    Ok(())
}

pub fn validate_kamino_user_metrics_history_params(
    _p: &KaminoUserMetricsHistoryParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_user_transactions_params(
    _p: &KaminoUserTransactionsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_user_vault_position_params(
    p: &KaminoUserVaultPositionParams,
) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_user_vault_metrics_history_params(
    p: &KaminoUserVaultMetricsHistoryParams,
) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_user_vault_pnl_params(
    p: &KaminoUserVaultPnlParams,
) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_user_vault_pnl_history_params(
    p: &KaminoUserVaultPnlHistoryParams,
) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_vault_deposit_instructions_params(
    p: &KaminoVaultDepositInstructionsParams,
) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    validate_positive_amount(&p.amount, "amount")
}

pub fn validate_kamino_vault_withdraw_instructions_params(
    p: &KaminoVaultWithdrawInstructionsParams,
) -> Result<(), AppError> {
    if p.vault.is_empty() || p.vault.len() < 32 {
        return Err(AppError::InvalidParams("vault must be a valid Solana address".into()));
    }
    validate_positive_amount(&p.ktoken_amount, "ktokenAmount")
}

pub fn validate_kamino_market_detail_params(_p: &KaminoMarketDetailParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_market_reserve_history_params(
    p: &KaminoMarketReserveHistoryParams,
) -> Result<(), AppError> {
    if p.reserve.is_empty() {
        return Err(AppError::InvalidParams("reserve (mint address) is required".into()));
    }
    Ok(())
}

pub fn validate_kamino_market_leverage_metrics_params(
    _p: &KaminoMarketLeverageMetricsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_market_reserves_account_params(
    _p: &KaminoMarketReservesAccountParams,
) -> Result<(), AppError> {
    // markets defaults to main market if empty — always valid
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Internal helper — GET with query string
// ──────────────────────────────────────────────────────────────────────────────

async fn kamino_get_q(
    http: &reqwest::Client,
    path: &str,
    query: &[(&str, String)],
) -> Result<serde_json::Value, AppError> {
    let base = format!("{KAMINO_API}{path}");
    let mut req = http.get(&base);
    for (k, v) in query {
        req = req.query(&[(k, v)]);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino GET {path}: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino GET {path} returned {status}: {body_text}"
        )));
    }
    resp.json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino GET {path} parse error: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Extended Earn Vault Data — Build Functions
// ──────────────────────────────────────────────────────────────────────────────

/// Get details for a specific Kamino Earn vault.
pub async fn build_kamino_vault_detail(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultDetailParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_detail_params(params)?;
    let data = kamino_get(http, &format!("/kvaults/vaults/{}", params.vault)).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_detail".into(),
            description: format!("Details for Kamino Earn vault {}", short_id(&params.vault)),
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

/// Get APY and TVL metrics for a specific Kamino Earn vault.
pub async fn build_kamino_vault_metrics(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultMetricsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_metrics_params(params)?;
    let data = kamino_get(http, &format!("/kvaults/vaults/{}/metrics", params.vault)).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_metrics".into(),
            description: format!("APY/TVL metrics for Kamino Earn vault {}", short_id(&params.vault)),
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

/// Get historical metrics for a specific Kamino Earn vault.
pub async fn build_kamino_vault_metrics_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultMetricsHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_metrics_history_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(s) = params.start { query.push(("start", s.to_string())); }
    if let Some(e) = params.end   { query.push(("end", e.to_string())); }
    let data = kamino_get_q(
        http,
        &format!("/kvaults/vaults/{}/metrics/history", params.vault),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_metrics_history".into(),
            description: format!("{count} historical metric entries for vault {}", short_id(&params.vault)),
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

/// Get allocation history for a specific Kamino Earn vault.
pub async fn build_kamino_vault_allocation_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultAllocationHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_allocation_history_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(s) = params.start { query.push(("start", s.to_string())); }
    if let Some(e) = params.end   { query.push(("end", e.to_string())); }
    let data = kamino_get_q(
        http,
        &format!("/kvaults/vaults/{}/allocation-volume/history", params.vault),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_allocation_history".into(),
            description: format!("{count} allocation history entries for vault {}", short_id(&params.vault)),
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

/// Get reward information for Kamino Earn vaults.
pub async fn build_kamino_vaults_rewards(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultsRewardsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vaults_rewards_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(http, "/kvaults/rewards", &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vaults_rewards".into(),
            description: "Kamino Earn vault reward details".into(),
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

/// Get a summary of all Kamino Earn vaults.
pub async fn build_kamino_vaults_summary(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultsSummaryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vaults_summary_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref t) = params.vault_type { query.push(("type", t.clone())); }
    let data = kamino_get_q(http, "/kvaults/summary", &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vaults_summary".into(),
            description: "Kamino Earn vaults summary".into(),
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

/// Get token metadata for a Kamino Earn vault's kToken.
pub async fn build_kamino_vault_mint_metadata(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultMintMetadataParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_mint_metadata_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env { query.push(("env", e.clone())); }
    let data = kamino_get_q(http, &format!("/kvaults/mints/{}/metadata", params.mint), &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_mint_metadata".into(),
            description: format!("kToken metadata for mint {}", short_id(&params.mint)),
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

/// Get the token image for a Kamino Earn vault's kToken.
pub async fn build_kamino_vault_mint_image(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultMintImageParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_mint_image_params(params)?;
    // Returns SVG/image data; we return it as a JSON string value
    let mut url = format!("{KAMINO_API}/kvaults/mints/{}/metadata/image.svg", params.mint);
    if let Some(ref e) = params.env {
        url.push_str(&format!("?env={e}"));
    }
    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino kToken image: {e}")))?;
    let content_type = resp
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("image/svg+xml")
        .to_string();
    let body = resp
        .text()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino kToken image read: {e}")))?;
    let data = serde_json::json!({ "url": url, "contentType": content_type, "data": body });
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_mint_image".into(),
            description: format!("kToken SVG image for mint {}", short_id(&params.mint)),
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

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Extended Earn User Data — Build Functions
// ──────────────────────────────────────────────────────────────────────────────

/// Get historical Earn metrics for a user.
pub async fn build_kamino_user_metrics_history(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserMetricsHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_metrics_history_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(s) = params.start { query.push(("start", s.to_string())); }
    if let Some(e) = params.end   { query.push(("end", e.to_string())); }
    let data = kamino_get_q(
        http,
        &format!("/kvaults/users/{target}/metrics/history"),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_metrics_history".into(),
            description: format!("{count} Earn metric history entries for {}", short_id(target)),
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

/// Get Earn transaction history for a user.
pub async fn build_kamino_user_transactions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserTransactionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_transactions_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(l) = params.limit  { query.push(("limit", l.to_string())); }
    if let Some(ref c) = params.cursor { query.push(("cursor", c.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/kvaults/users/{target}/transactions"),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_transactions".into(),
            description: format!("{count} Kamino Earn transaction(s) for {}", short_id(target)),
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

/// Get a user's position in a specific Kamino Earn vault.
pub async fn build_kamino_user_vault_position(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserVaultPositionParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_vault_position_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let data = kamino_get(
        http,
        &format!("/kvaults/users/{target}/positions/{}", params.vault),  // correct: /positions/{vault}
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_vault_position".into(),
            description: format!(
                "Position of {} in Kamino vault {}",
                short_id(target),
                short_id(&params.vault),
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

/// Get historical metrics for a user's position in a Kamino Earn vault.
pub async fn build_kamino_user_vault_metrics_history(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserVaultMetricsHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_vault_metrics_history_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(s) = params.start { query.push(("start", s.to_string())); }
    if let Some(e) = params.end   { query.push(("end", e.to_string())); }
    let data = kamino_get_q(
        http,
        &format!("/kvaults/users/{target}/vaults/{}/metrics/history", params.vault),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_vault_metrics_history".into(),
            description: format!(
                "{count} metric history entries for {} in vault {}",
                short_id(target),
                short_id(&params.vault),
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

/// Get PnL for a user's position in a Kamino Earn vault.
pub async fn build_kamino_user_vault_pnl(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserVaultPnlParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_vault_pnl_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let data = kamino_get(
        http,
        &format!("/kvaults/users/{target}/vaults/{}/pnl", params.vault),
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_vault_pnl".into(),
            description: format!(
                "PnL for {} in Kamino vault {}",
                short_id(target),
                short_id(&params.vault),
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

/// Get PnL history for a user's position in a Kamino Earn vault.
pub async fn build_kamino_user_vault_pnl_history(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserVaultPnlHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_vault_pnl_history_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(s) = params.start { query.push(("start", s.to_string())); }
    if let Some(e) = params.end   { query.push(("end", e.to_string())); }
    let data = kamino_get_q(
        http,
        &format!("/kvaults/users/{target}/vaults/{}/pnl/history", params.vault),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_vault_pnl_history".into(),
            description: format!(
                "{count} PnL history entries for {} in vault {}",
                short_id(target),
                short_id(&params.vault),
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

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Earn Action — Instructions variants
// ──────────────────────────────────────────────────────────────────────────────

/// Get deposit instructions (not a full transaction) for a Kamino Earn vault.
pub async fn build_kamino_vault_deposit_instructions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoVaultDepositInstructionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_deposit_instructions_params(params)?;
    let mut body = serde_json::json!({
        "wallet": wallet,
        "kvault": params.vault,
        "amount": params.amount,
    });
    if let Some(s) = params.slippage_bps {
        body["slippageBps"] = serde_json::Value::Number(s.into());
    }
    let url = format!("{KAMINO_API}/ktx/kvault/deposit/instructions");
    let resp = http
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino vault deposit instructions: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino vault deposit instructions returned {status}: {body_text}"
        )));
    }
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino vault deposit instructions parse: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_deposit_instructions".into(),
            description: format!(
                "Deposit instructions for {} into Kamino Earn vault {}",
                params.amount,
                short_id(&params.vault),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
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
// USD Benchmark Lending Rates
// ──────────────────────────────────────────────────────────────────────────────

/// Query params for fetching USD benchmark lending rates.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoUsdBenchmarkRatesParams {
    /// Start date — ISO 8601 string or epoch milliseconds. Optional (defaults to 1970-01-01).
    #[serde(default)]
    pub start: Option<String>,
    /// End date — ISO 8601 string or epoch milliseconds. Optional.
    #[serde(default)]
    pub end: Option<String>,
    /// Data granularity: "hour" (default) or "day". Optional.
    #[serde(default)]
    pub frequency: Option<String>,
}

pub fn validate_kamino_usd_benchmark_rates_params(
    p: &KaminoUsdBenchmarkRatesParams,
) -> Result<(), AppError> {
    if let Some(ref f) = p.frequency {
        if f != "hour" && f != "day" {
            return Err(AppError::InvalidParams(format!(
                "frequency must be 'hour' or 'day', got '{f}'"
            )));
        }
    }
    Ok(())
}

/// Fetch USD benchmark lending rates aggregated across multiple protocols.
pub async fn build_kamino_usd_benchmark_rates(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoUsdBenchmarkRatesParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_usd_benchmark_rates_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.start {
        query.push(("start", s.clone()));
    }
    if let Some(ref e) = params.end {
        query.push(("end", e.clone()));
    }
    if let Some(ref f) = params.frequency {
        query.push(("frequency", f.clone()));
    }
    let data = kamino_get_q(http, "/usd-benchmark-rates", &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    let freq = params.frequency.as_deref().unwrap_or("hour");
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_usd_benchmark_rates".into(),
            description: format!("USD benchmark lending rates ({count} {freq}ly data points)"),
            estimated_fee: "0".into(),
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

/// Get withdraw instructions (not a full transaction) for a Kamino Earn vault.
pub async fn build_kamino_vault_withdraw_instructions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoVaultWithdrawInstructionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_withdraw_instructions_params(params)?;
    let mut body = serde_json::json!({
        "wallet": wallet,
        "kvault": params.vault,
        "amount": params.ktoken_amount,
    });
    if let Some(s) = params.slippage_bps {
        body["slippageBps"] = serde_json::Value::Number(s.into());
    }
    let url = format!("{KAMINO_API}/ktx/kvault/withdraw/instructions");
    let resp = http
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino vault withdraw instructions: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino vault withdraw instructions returned {status}: {body_text}"
        )));
    }
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino vault withdraw instructions parse: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_withdraw_instructions".into(),
            description: format!(
                "Withdraw instructions for {} kTokens from Kamino Earn vault {}",
                params.ktoken_amount,
                short_id(&params.vault),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
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
// NEW: Extended Borrow Market Data — Build Functions
// ──────────────────────────────────────────────────────────────────────────────

/// Get details for a specific K-Lend market.
pub async fn build_kamino_market_detail(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoMarketDetailParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_market_detail_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref p) = params.program_id { query.push(("programId", p.clone())); }
    let data = kamino_get_q(http, &format!("/v2/kamino-market/{market}"), &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_market_detail".into(),
            description: format!("K-Lend market details for {}", short_id(market)),
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

/// Get historical metrics for a specific reserve in a K-Lend market.
pub async fn build_kamino_market_reserve_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoMarketReserveHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_market_reserve_history_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(s) = params.start       { query.push(("start", s.to_string())); }
    if let Some(e) = params.end         { query.push(("end", e.to_string())); }
    if let Some(ref f) = params.frequency { query.push(("frequency", f.clone())); }
    if let Some(ref e) = params.env     { query.push(("env", e.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/kamino-market/{market}/reserves/{}/metrics/history", params.reserve),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_market_reserve_history".into(),
            description: format!(
                "{count} historical metric entries for reserve {} in market {}",
                short_id(&params.reserve),
                short_id(market),
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

/// Get leverage (Multiply/Long/Short) metrics for a K-Lend market.
pub async fn build_kamino_market_leverage_metrics(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoMarketLeverageMetricsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_market_leverage_metrics_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env { query.push(("env", e.clone())); }
    let data = kamino_get_q(http, &format!("/kamino-market/{market}/leverage/metrics"), &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_market_leverage_metrics".into(),
            description: format!("Leverage metrics for K-Lend market {}", short_id(market)),
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

/// Get on-chain account data for all reserves in a K-Lend market.
pub async fn build_kamino_market_reserves_account(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoMarketReservesAccountParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_market_reserves_account_params(params)?;
    // markets defaults to main market if not specified
    let market_list: Vec<String> = if params.markets.is_empty() {
        vec![KAMINO_MAIN_MARKET.to_string()]
    } else {
        params.markets.clone()
    };
    let mut query: Vec<(&str, String)> = vec![];
    for m in &market_list { query.push(("markets", m.clone())); }
    if let Some(ref p) = params.program_id { query.push(("programId", p.clone())); }
    let data = kamino_get_q(http, "/kamino-market/reserves/account-data", &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_market_reserves_account".into(),
            description: format!("{count} reserve account data entries for {} market(s)", market_list.len()),
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

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Extended Borrow User/Loan Data — GET Parameter Types
// ──────────────────────────────────────────────────────────────────────────────

/// Get K-Lend rewards for a specific user.
/// API: GET /klend/users/{pubkey}/rewards?source=
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoUserRewardsParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Points source identifier (Season1, Season2, Season3, Season4). Optional.
    #[serde(default)]
    pub source: Option<String>,
}

/// Get detail for a specific K-Lend loan (obligation).
/// API: GET /klend/loans/{pubkey}?env=
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoLoanDetailParams {
    /// Loan (obligation) public key. Required.
    pub loan: String,
    /// Solana cluster: "mainnet-beta" (default), "devnet", or "localnet". Optional.
    #[serde(default)]
    pub env: Option<String>,
}

/// Get PnL for a specific K-Lend obligation.
/// API: GET /v2/kamino-market/{marketPubkey}/obligations/{obligationPubkey}/pnl
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoObligationPnlParams {
    /// Obligation public key. Required.
    pub obligation: String,
    /// Market address. Defaults to main market.
    #[serde(default)]
    pub market: Option<String>,
    /// Solana cluster. Optional.
    #[serde(default)]
    pub env: Option<String>,
    /// KLend program ID. Optional.
    #[serde(default)]
    pub program_id: Option<String>,
    /// For xSOL pairs, use stake rate for PnL calculation. Optional (default: false).
    #[serde(default)]
    pub use_stake_rate: Option<bool>,
}

/// Get historical metrics for a specific K-Lend obligation.
/// API: GET /v2/kamino-market/{marketPubkey}/obligations/{obligationPubkey}/metrics/history
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoObligationMetricsHistoryParams {
    /// Obligation public key. Required.
    pub obligation: String,
    /// Market address. Defaults to main market.
    #[serde(default)]
    pub market: Option<String>,
    /// Solana cluster. Optional.
    #[serde(default)]
    pub env: Option<String>,
    /// Start of range (ISO 8601 or epoch in milliseconds). Optional.
    #[serde(default)]
    pub start: Option<i64>,
    /// End of range (ISO 8601 or epoch in milliseconds). Optional.
    #[serde(default)]
    pub end: Option<i64>,
    /// Use stake rate to calculate net SOL value for xSOL pairs. Optional (default: false).
    #[serde(default)]
    pub use_stake_rate_for_obligation: Option<bool>,
}

/// List all active K-Lend reward programs.
/// API: GET /klend/rewards?source=
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct KaminoRewardsListParams {
    /// Points source identifier (Season1, Season2, Season3, Season4). Optional.
    #[serde(default)]
    pub source: Option<String>,
}

/// Get reward APY history for a reserve deposit/borrow pair.
/// API: GET /klend/{depositReservePubkey}/{borrowReservePubkey}/rewards/history
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoRewardsHistoryParams {
    /// Deposit reserve public key. Required.
    pub deposit_reserve: String,
    /// Borrow reserve public key. Required.
    pub borrow_reserve: String,
    /// Start of range (ISO 8601 or epoch in milliseconds). Optional.
    #[serde(default)]
    pub start: Option<i64>,
    /// End of range (ISO 8601 or epoch in milliseconds). Optional.
    #[serde(default)]
    pub end: Option<i64>,
    /// Frequency aggregation: "hour" or "day". Optional.
    #[serde(default)]
    pub frequency: Option<String>,
}

/// Get K-Lend borrow as a list of unsigned instructions (for manual assembly).
/// API: POST /ktx/klend/borrow-instructions
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoBorrowInstructionsParams {
    /// Reserve account address for the token to borrow. Required.
    pub reserve: String,
    /// Amount to borrow (decimal format, not lamports). Required.
    pub amount: String,
    /// Market address. Defaults to main market.
    #[serde(default)]
    pub market: Option<String>,
}

/// Get K-Lend repay as a list of unsigned instructions (for manual assembly).
/// API: POST /ktx/klend/repay-instructions
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoRepayInstructionsParams {
    /// Reserve account address for the token to repay. Required.
    pub reserve: String,
    /// Amount to repay (decimal format, not lamports). Required.
    pub amount: String,
    /// Market address. Defaults to main market.
    #[serde(default)]
    pub market: Option<String>,
}

/// Perform a KSwap (Kamino multi-router swap) and return a signed-ready transaction.
/// API: GET /kswap/swap/
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoKswapParams {
    /// Input token mint address. Required.
    pub token_in: String,
    /// Output token mint address. Required.
    pub token_out: String,
    /// Input amount in smallest unit (lamports). Required.
    pub amount_in: String,
    /// Maximum acceptable slippage in basis points. Required.
    pub max_slippage_bps: u32,
    /// Include setup instructions (create ATAs, etc.). Optional (default: true).
    #[serde(default)]
    pub include_setup_ixs: Option<bool>,
    /// Auto wrap/unwrap native SOL. Optional (default: true).
    #[serde(default)]
    pub wrap_and_unwrap_sol: Option<bool>,
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Extended Borrow — Validators
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_kamino_user_rewards_params(_p: &KaminoUserRewardsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_loan_detail_params(p: &KaminoLoanDetailParams) -> Result<(), AppError> {
    if p.loan.is_empty() || p.loan.len() < 32 {
        return Err(AppError::InvalidParams("loan must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_obligation_pnl_params(p: &KaminoObligationPnlParams) -> Result<(), AppError> {
    if p.obligation.is_empty() || p.obligation.len() < 32 {
        return Err(AppError::InvalidParams("obligation must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_obligation_metrics_history_params(
    p: &KaminoObligationMetricsHistoryParams,
) -> Result<(), AppError> {
    if p.obligation.is_empty() || p.obligation.len() < 32 {
        return Err(AppError::InvalidParams("obligation must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_rewards_list_params(_p: &KaminoRewardsListParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_kamino_rewards_history_params(p: &KaminoRewardsHistoryParams) -> Result<(), AppError> {
    if p.deposit_reserve.is_empty() || p.deposit_reserve.len() < 32 {
        return Err(AppError::InvalidParams("depositReserve must be a valid Solana address".into()));
    }
    if p.borrow_reserve.is_empty() || p.borrow_reserve.len() < 32 {
        return Err(AppError::InvalidParams("borrowReserve must be a valid Solana address".into()));
    }
    Ok(())
}

pub fn validate_kamino_borrow_instructions_params(
    p: &KaminoBorrowInstructionsParams,
) -> Result<(), AppError> {
    if p.reserve.is_empty() || p.reserve.len() < 32 {
        return Err(AppError::InvalidParams("reserve must be a valid Solana address".into()));
    }
    validate_positive_amount(&p.amount, "amount")
}

pub fn validate_kamino_repay_instructions_params(
    p: &KaminoRepayInstructionsParams,
) -> Result<(), AppError> {
    if p.reserve.is_empty() || p.reserve.len() < 32 {
        return Err(AppError::InvalidParams("reserve must be a valid Solana address".into()));
    }
    validate_positive_amount(&p.amount, "amount")
}

pub fn validate_kamino_kswap_params(p: &KaminoKswapParams) -> Result<(), AppError> {
    if p.token_in.is_empty() {
        return Err(AppError::InvalidParams("tokenIn is required".into()));
    }
    if p.token_out.is_empty() {
        return Err(AppError::InvalidParams("tokenOut is required".into()));
    }
    validate_positive_amount(&p.amount_in, "amountIn")
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: Extended Borrow — Build Functions
// ──────────────────────────────────────────────────────────────────────────────

/// Get K-Lend rewards for a specific user.
pub async fn build_kamino_user_rewards(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserRewardsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_rewards_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/klend/users/{target}/rewards"),
        &query,
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_rewards".into(),
            description: format!("K-Lend reward details for {}", short_id(target)),
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

/// Get detail for a specific K-Lend loan (obligation).
pub async fn build_kamino_loan_detail(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoLoanDetailParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_loan_detail_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env { query.push(("env", e.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/klend/loans/{}", params.loan),
        &query,
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_loan_detail".into(),
            description: format!("K-Lend loan detail for {}", short_id(&params.loan)),
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

/// Get PnL for a specific K-Lend obligation.
pub async fn build_kamino_obligation_pnl(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoObligationPnlParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_obligation_pnl_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env        { query.push(("env", e.clone())); }
    if let Some(ref p) = params.program_id { query.push(("programId", p.clone())); }
    if let Some(s) = params.use_stake_rate { query.push(("useStakeRate", s.to_string())); }
    let data = kamino_get_q(
        http,
        &format!("/v2/kamino-market/{market}/obligations/{}/pnl", params.obligation),
        &query,
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_obligation_pnl".into(),
            description: format!(
                "PnL for obligation {} in K-Lend market {}",
                short_id(&params.obligation),
                short_id(market),
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

/// Get historical metrics for a specific K-Lend obligation.
pub async fn build_kamino_obligation_metrics_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoObligationMetricsHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_obligation_metrics_history_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env                         { query.push(("env", e.clone())); }
    if let Some(s) = params.start                           { query.push(("start", s.to_string())); }
    if let Some(e) = params.end                             { query.push(("end", e.to_string())); }
    if let Some(s) = params.use_stake_rate_for_obligation   { query.push(("useStakeRateForObligation", s.to_string())); }
    let data = kamino_get_q(
        http,
        &format!("/v2/kamino-market/{market}/obligations/{}/metrics/history", params.obligation),
        &query,
    ).await?;
    let count = data
        .get("history")
        .and_then(|h| h.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_obligation_metrics_history".into(),
            description: format!(
                "{count} historical metric snapshots for obligation {}",
                short_id(&params.obligation),
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

/// List all active K-Lend reward programs.
pub async fn build_kamino_rewards_list(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoRewardsListParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_rewards_list_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(http, "/klend/rewards", &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_rewards_list".into(),
            description: format!("{count} active K-Lend reward program(s)"),
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

/// Get reward APY history for a K-Lend reserve deposit/borrow pair.
pub async fn build_kamino_rewards_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoRewardsHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_rewards_history_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(s) = params.start          { query.push(("start", s.to_string())); }
    if let Some(e) = params.end            { query.push(("end", e.to_string())); }
    if let Some(ref f) = params.frequency  { query.push(("frequency", f.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/klend/{}/{}/rewards/history", params.deposit_reserve, params.borrow_reserve),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_rewards_history".into(),
            description: format!(
                "{count} reward history entries for deposit {} / borrow {}",
                short_id(&params.deposit_reserve),
                short_id(&params.borrow_reserve),
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

/// Get K-Lend borrow as a list of unsigned instructions.
pub async fn build_kamino_borrow_instructions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoBorrowInstructionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_borrow_instructions_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let body = serde_json::json!({
        "wallet": wallet,
        "market": market,
        "reserve": params.reserve,
        "amount": params.amount,
    });
    let url = format!("{KAMINO_API}/ktx/klend/borrow-instructions");
    let resp = http
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino borrow instructions: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino borrow instructions returned {status}: {body_text}"
        )));
    }
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino borrow instructions parse: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_borrow_instructions".into(),
            description: format!(
                "Borrow {} from K-Lend reserve {} (instructions)",
                params.amount,
                short_id(&params.reserve),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec!["Ensure you have sufficient collateral before borrowing.".into()],
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

/// Get K-Lend repay as a list of unsigned instructions.
pub async fn build_kamino_repay_instructions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoRepayInstructionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_repay_instructions_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let body = serde_json::json!({
        "wallet": wallet,
        "market": market,
        "reserve": params.reserve,
        "amount": params.amount,
    });
    let url = format!("{KAMINO_API}/ktx/klend/repay-instructions");
    let resp = http
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino repay instructions: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino repay instructions returned {status}: {body_text}"
        )));
    }
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino repay instructions parse: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_repay_instructions".into(),
            description: format!(
                "Repay {} to K-Lend reserve {} (instructions)",
                params.amount,
                short_id(&params.reserve),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
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

/// Perform a KSwap (Kamino multi-router swap) and return a signed-ready transaction.
pub async fn build_kamino_kswap(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoKswapParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_kswap_params(params)?;
    let mut query: Vec<(&str, String)> = vec![
        ("tokenIn",        params.token_in.clone()),
        ("tokenOut",       params.token_out.clone()),
        ("amountIn",       params.amount_in.clone()),
        ("maxSlippageBps", params.max_slippage_bps.to_string()),
        ("wallet",         wallet.to_string()),
    ];
    if let Some(v) = params.include_setup_ixs   { query.push(("includeSetupIxs",   v.to_string())); }
    if let Some(v) = params.wrap_and_unwrap_sol  { query.push(("wrapAndUnwrapSol",  v.to_string())); }
    let resp_json = kamino_get_q(http, "/kswap/swap/", &query).await?;
    let tx_b64 = resp_json
        .get("data")
        .and_then(|d| d.get("transaction"))
        .and_then(|t| t.as_str())
        .map(|s| s.to_string());
    let expected_out = resp_json
        .get("data")
        .and_then(|d| d.get("expectedAmountOut"))
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_kswap".into(),
            description: format!(
                "KSwap: {} {} → {} (expected out: {})",
                params.amount_in,
                short_id(&params.token_in),
                short_id(&params.token_out),
                expected_out,
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: true,
        },
        transaction: tx_b64,
        additional_signers_required: 0,
        execution_steps: None,
        quote: Some(resp_json.clone()),
        is_cross_chain: false,
        data: Some(resp_json),
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// NEW: K-Lend deposit/withdraw instructions
// ──────────────────────────────────────────────────────────────────────────────

/// Get deposit instructions (array) for a K-Lend reserve (unsigned, for custom assembly).
/// API: POST /ktx/klend/deposit-instructions
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoDepositInstructionsParams {
    /// KLend reserve account address for the token to deposit. Required.
    pub reserve: String,
    /// Amount to deposit (decimal format, not lamports). Required.
    pub amount: String,
    /// Market address. Defaults to main market.
    #[serde(default)]
    pub market: Option<String>,
}

/// Get withdraw instructions (array) for a K-Lend reserve (unsigned, for custom assembly).
/// API: POST /ktx/klend/withdraw-instructions
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KaminoWithdrawInstructionsParams {
    /// KLend reserve account address for the token to withdraw. Required.
    pub reserve: String,
    /// Amount to withdraw (decimal format, not lamports). Required.
    pub amount: String,
    /// Market address. Defaults to main market.
    #[serde(default)]
    pub market: Option<String>,
}

pub fn validate_kamino_deposit_instructions_params(
    p: &KaminoDepositInstructionsParams,
) -> Result<(), AppError> {
    validate_reserve_address(&p.reserve, "reserve")?;
    validate_positive_amount(&p.amount, "amount")
}

pub fn validate_kamino_withdraw_instructions_params(
    p: &KaminoWithdrawInstructionsParams,
) -> Result<(), AppError> {
    validate_reserve_address(&p.reserve, "reserve")?;
    validate_positive_amount(&p.amount, "amount")
}

/// Get K-Lend deposit as a list of unsigned instructions.
pub async fn build_kamino_deposit_instructions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoDepositInstructionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_deposit_instructions_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let body = serde_json::json!({
        "wallet": wallet,
        "market": market,
        "reserve": params.reserve,
        "amount": params.amount,
    });
    let url = format!("{KAMINO_API}/ktx/klend/deposit-instructions");
    let resp = http
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino deposit instructions: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino deposit instructions returned {status}: {body_text}"
        )));
    }
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino deposit instructions parse: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_deposit_instructions".into(),
            description: format!(
                "Deposit {} into K-Lend reserve {} (instructions)",
                params.amount,
                short_id(&params.reserve),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
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

/// Get K-Lend withdraw as a list of unsigned instructions.
pub async fn build_kamino_withdraw_instructions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoWithdrawInstructionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_withdraw_instructions_params(params)?;
    let market = resolve_kamino_market(params.market.as_deref());
    let body = serde_json::json!({
        "wallet": wallet,
        "market": market,
        "reserve": params.reserve,
        "amount": params.amount,
    });
    let url = format!("{KAMINO_API}/ktx/klend/withdraw-instructions");
    let resp = http
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino withdraw instructions: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino withdraw instructions returned {status}: {body_text}"
        )));
    }
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino withdraw instructions parse: {e}")))?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_withdraw_instructions".into(),
            description: format!(
                "Withdraw {} from K-Lend reserve {} (instructions)",
                params.amount,
                short_id(&params.reserve),
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
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
// Market Metrics History, Reserve APY History, Obligation Interest
// ──────────────────────────────────────────────────────────────────────────────

/// Historical TVL + obligation count snapshots for a K-Lend market.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoMarketMetricsHistoryParams {
    /// K-Lend market public key (base58). Required.
    pub market: String,
    #[serde(default)]
    pub start: Option<String>,
    #[serde(default)]
    pub end: Option<String>,
    #[serde(default)]
    pub env: Option<String>,
}

pub fn validate_kamino_market_metrics_history_params(
    p: &KaminoMarketMetricsHistoryParams,
) -> Result<(), AppError> {
    if p.market.len() < 32 {
        return Err(AppError::InvalidParams(format!(
            "market '{}' is not a valid Solana address", p.market
        )));
    }
    Ok(())
}

pub async fn build_kamino_market_metrics_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoMarketMetricsHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_market_metrics_history_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.start { query.push(("start", s.clone())); }
    if let Some(ref e) = params.end   { query.push(("end", e.clone())); }
    if let Some(ref e) = params.env   { query.push(("env", e.clone())); }
    let data = kamino_get_q(http, &format!("/kamino-market/{}/metrics/history", params.market), &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_market_metrics_history".into(),
            description: format!("{count} metric snapshots for K-Lend market {}", short_id(&params.market)),
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

/// Historical borrow APY + staking APY for a K-Lend reserve.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoReserveBorrowApyHistoryParams {
    pub market: String,
    pub reserve: String,
    #[serde(default)]
    pub start: Option<String>,
    #[serde(default)]
    pub end: Option<String>,
    #[serde(default)]
    pub env: Option<String>,
}

pub fn validate_kamino_reserve_borrow_apy_history_params(
    p: &KaminoReserveBorrowApyHistoryParams,
) -> Result<(), AppError> {
    if p.market.len() < 32 {
        return Err(AppError::InvalidParams(format!("market '{}' is not a valid Solana address", p.market)));
    }
    if p.reserve.len() < 32 {
        return Err(AppError::InvalidParams(format!("reserve '{}' is not a valid Solana address", p.reserve)));
    }
    Ok(())
}

pub async fn build_kamino_reserve_borrow_apy_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoReserveBorrowApyHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_reserve_borrow_apy_history_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.start { query.push(("start", s.clone())); }
    if let Some(ref e) = params.end   { query.push(("end", e.clone())); }
    if let Some(ref e) = params.env   { query.push(("env", e.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/kamino-market/{}/reserves/{}/borrow-and-staking-apys/history", params.market, params.reserve),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_reserve_borrow_apy_history".into(),
            description: format!("{count} borrow+staking APY snapshots for reserve {}", short_id(&params.reserve)),
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

/// Median borrow APY + staking APY history for a K-Lend reserve.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoReserveBorrowApyMedianParams {
    pub market: String,
    pub reserve: String,
    #[serde(default)]
    pub start: Option<String>,
    #[serde(default)]
    pub end: Option<String>,
    #[serde(default)]
    pub env: Option<String>,
}

pub fn validate_kamino_reserve_borrow_apy_median_params(
    p: &KaminoReserveBorrowApyMedianParams,
) -> Result<(), AppError> {
    if p.market.len() < 32 {
        return Err(AppError::InvalidParams(format!("market '{}' is not a valid Solana address", p.market)));
    }
    if p.reserve.len() < 32 {
        return Err(AppError::InvalidParams(format!("reserve '{}' is not a valid Solana address", p.reserve)));
    }
    Ok(())
}

pub async fn build_kamino_reserve_borrow_apy_median(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoReserveBorrowApyMedianParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_reserve_borrow_apy_median_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.start { query.push(("start", s.clone())); }
    if let Some(ref e) = params.end   { query.push(("end", e.clone())); }
    if let Some(ref e) = params.env   { query.push(("env", e.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/kamino-market/{}/reserves/{}/borrow-and-staking-apys/history/median", params.market, params.reserve),
        &query,
    ).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_reserve_borrow_apy_median".into(),
            description: format!("{count} median APY snapshots for reserve {}", short_id(&params.reserve)),
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

/// Interest fees earned for a K-Lend obligation (lender/deposit perspective).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoObligationInterestEarnedParams {
    pub market: String,
    pub obligation: String,
    #[serde(default)]
    pub start: Option<String>,
    #[serde(default)]
    pub end: Option<String>,
    #[serde(default)]
    pub env: Option<String>,
    #[serde(default)]
    pub program_id: Option<String>,
}

pub fn validate_kamino_obligation_interest_earned_params(
    p: &KaminoObligationInterestEarnedParams,
) -> Result<(), AppError> {
    if p.market.len() < 32 {
        return Err(AppError::InvalidParams(format!("market '{}' is not a valid Solana address", p.market)));
    }
    if p.obligation.len() < 32 {
        return Err(AppError::InvalidParams(format!("obligation '{}' is not a valid Solana address", p.obligation)));
    }
    Ok(())
}

pub async fn build_kamino_obligation_interest_earned(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoObligationInterestEarnedParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_obligation_interest_earned_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.start      { query.push(("start", s.clone())); }
    if let Some(ref e) = params.end        { query.push(("end", e.clone())); }
    if let Some(ref e) = params.env        { query.push(("env", e.clone())); }
    if let Some(ref p) = params.program_id { query.push(("programId", p.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/v2/kamino-market/{}/obligations/{}/interest-fees", params.market, params.obligation),
        &query,
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_obligation_interest_earned".into(),
            description: format!("Interest fees earned for obligation {}", short_id(&params.obligation)),
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

/// Interest fees paid for a K-Lend obligation (borrower perspective).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoObligationInterestPaidParams {
    pub market: String,
    pub obligation: String,
    #[serde(default)]
    pub start: Option<String>,
    #[serde(default)]
    pub end: Option<String>,
    #[serde(default)]
    pub env: Option<String>,
    #[serde(default)]
    pub program_id: Option<String>,
}

pub fn validate_kamino_obligation_interest_paid_params(
    p: &KaminoObligationInterestPaidParams,
) -> Result<(), AppError> {
    if p.market.len() < 32 {
        return Err(AppError::InvalidParams(format!("market '{}' is not a valid Solana address", p.market)));
    }
    if p.obligation.len() < 32 {
        return Err(AppError::InvalidParams(format!("obligation '{}' is not a valid Solana address", p.obligation)));
    }
    Ok(())
}

pub async fn build_kamino_obligation_interest_paid(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoObligationInterestPaidParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_obligation_interest_paid_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.start      { query.push(("start", s.clone())); }
    if let Some(ref e) = params.end        { query.push(("end", e.clone())); }
    if let Some(ref e) = params.env        { query.push(("env", e.clone())); }
    if let Some(ref p) = params.program_id { query.push(("programId", p.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/v2/kamino-market/{}/obligations/{}/interest-paid", params.market, params.obligation),
        &query,
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_obligation_interest_paid".into(),
            description: format!("Interest fees paid for obligation {}", short_id(&params.obligation)),
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

// ──────────────────────────────────────────────────────────────────────────────
// K-Lend Transactions, Borrow Orders, Yields, Airdrop, Staking Yields
// ──────────────────────────────────────────────────────────────────────────────

// ── K-Lend: Obligation Transactions ─────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoObligationTransactionsParams {
    pub market: String,
    pub obligation: String,
    #[serde(default)]
    pub env: Option<String>,
    /// Sort order: "asc" (oldest first, default) or "desc" (newest first).
    #[serde(default)]
    pub sort: Option<String>,
    /// Use log prices for transaction values. Optional.
    #[serde(default)]
    pub use_log_prices: Option<bool>,
}

pub fn validate_kamino_obligation_transactions_params(p: &KaminoObligationTransactionsParams) -> Result<(), AppError> {
    if p.market.len() < 32 {
        return Err(AppError::InvalidParams(format!("market '{}' is not a valid Solana address", p.market)));
    }
    if p.obligation.len() < 32 {
        return Err(AppError::InvalidParams(format!("obligation '{}' is not a valid Solana address", p.obligation)));
    }
    Ok(())
}

pub async fn build_kamino_obligation_transactions(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoObligationTransactionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_obligation_transactions_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env  { query.push(("env", e.clone())); }
    if let Some(ref s) = params.sort { query.push(("sort", s.clone())); }
    if let Some(v) = params.use_log_prices { query.push(("useLogPrices", v.to_string())); }
    let data = kamino_get_q(
        http,
        &format!("/v2/kamino-market/{}/obligations/{}/transactions", params.market, params.obligation),
        &query,
    ).await?;
    let count = data.get("transactions").and_then(|t| t.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_obligation_transactions".into(),
            description: format!("{count} transactions for obligation {}", short_id(&params.obligation)),
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

// ── K-Lend: User Transactions Across All Markets ─────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoUserKlendTransactionsAllParams {
    /// Target wallet address. Optional — defaults to authenticated wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    #[serde(default)]
    pub env: Option<String>,
    #[serde(default)]
    pub sort: Option<String>,
}

pub fn validate_kamino_user_klend_transactions_all_params(_p: &KaminoUserKlendTransactionsAllParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_user_klend_transactions_all(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserKlendTransactionsAllParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_klend_transactions_all_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env  { query.push(("env", e.clone())); }
    if let Some(ref s) = params.sort { query.push(("sort", s.clone())); }
    let data = kamino_get_q(http, &format!("/v2/kamino-market/users/{target}/transactions"), &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_klend_transactions_all".into(),
            description: format!("K-Lend transactions across all markets for {}", short_id(target)),
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

// ── K-Lend: User Transactions in a Specific Market ───────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoUserKlendTransactionsParams {
    pub market: String,
    #[serde(default)]
    pub wallet: Option<String>,
    #[serde(default)]
    pub env: Option<String>,
    #[serde(default)]
    pub sort: Option<String>,
}

pub fn validate_kamino_user_klend_transactions_params(p: &KaminoUserKlendTransactionsParams) -> Result<(), AppError> {
    if p.market.len() < 32 {
        return Err(AppError::InvalidParams(format!("market '{}' is not a valid Solana address", p.market)));
    }
    Ok(())
}

pub async fn build_kamino_user_klend_transactions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserKlendTransactionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_klend_transactions_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env  { query.push(("env", e.clone())); }
    if let Some(ref s) = params.sort { query.push(("sort", s.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/v2/kamino-market/{}/users/{}/transactions", params.market, target),
        &query,
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_klend_transactions".into(),
            description: format!("K-Lend transactions in market {} for {}", short_id(&params.market), short_id(target)),
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

// ── K-Lend: Borrow Order Fills ───────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoBorrowOrderFillsParams {
    /// Obligation public key. Required.
    pub obligation: String,
}

pub fn validate_kamino_borrow_order_fills_params(p: &KaminoBorrowOrderFillsParams) -> Result<(), AppError> {
    if p.obligation.len() < 32 {
        return Err(AppError::InvalidParams(format!("obligation '{}' is not a valid Solana address", p.obligation)));
    }
    Ok(())
}

pub async fn build_kamino_borrow_order_fills(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoBorrowOrderFillsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_borrow_order_fills_params(params)?;
    let data = kamino_get(http, &format!("/v2/kamino-market/obligations/{}/borrow-order-fills", params.obligation)).await?;
    let count = data.get("fills").and_then(|f| f.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_borrow_order_fills".into(),
            description: format!("{count} borrow order fills for obligation {}", short_id(&params.obligation)),
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

// ── K-Lend: Obligations with Open Borrow Orders ──────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoOpenBorrowOrdersParams {
    pub market: String,
    #[serde(default)]
    pub env: Option<String>,
    #[serde(default)]
    pub program_id: Option<String>,
}

pub fn validate_kamino_open_borrow_orders_params(p: &KaminoOpenBorrowOrdersParams) -> Result<(), AppError> {
    if p.market.len() < 32 {
        return Err(AppError::InvalidParams(format!("market '{}' is not a valid Solana address", p.market)));
    }
    Ok(())
}

pub async fn build_kamino_open_borrow_orders(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoOpenBorrowOrdersParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_open_borrow_orders_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref e) = params.env        { query.push(("env", e.clone())); }
    if let Some(ref p) = params.program_id { query.push(("programId", p.clone())); }
    let data = kamino_get_q(
        http,
        &format!("/v2/kamino-market/{}/obligations-with-open-borrow-orders", params.market),
        &query,
    ).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_open_borrow_orders".into(),
            description: format!("Open borrow orders in K-Lend market {}", short_id(&params.market)),
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

// ── Yields: History ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoYieldHistoryParams {
    /// Yield source: token mint address, or "farmAddress-rewardMint" for farm yields. Required.
    pub yield_source: String,
    #[serde(default)]
    pub start: Option<String>,
    #[serde(default)]
    pub end: Option<String>,
}

pub fn validate_kamino_yield_history_params(p: &KaminoYieldHistoryParams) -> Result<(), AppError> {
    if p.yield_source.is_empty() {
        return Err(AppError::InvalidParams("yield_source must not be empty".into()));
    }
    Ok(())
}

pub async fn build_kamino_yield_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoYieldHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_yield_history_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.start { query.push(("start", s.clone())); }
    if let Some(ref e) = params.end   { query.push(("end", e.clone())); }
    let data = kamino_get_q(http, &format!("/yields/{}/history", params.yield_source), &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_yield_history".into(),
            description: format!("{count} yield history snapshots for {}", short_id(&params.yield_source)),
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

// ── Yields: Principal Token Yields ───────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoPrincipalTokenYieldsParams {}

pub fn validate_kamino_principal_token_yields_params(_p: &KaminoPrincipalTokenYieldsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_principal_token_yields(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoPrincipalTokenYieldsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_principal_token_yields_params(params)?;
    let data = kamino_get(http, "/yields/principal-tokens").await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_principal_token_yields".into(),
            description: format!("Principal token yields for {count} tokens"),
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
        data: None,
    })
}

// ── Airdrop: User Allocations ─────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoAirdropAllocationsParams {
    /// Target wallet address. Optional — defaults to authenticated wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Points source identifier, e.g. Season1, Season2. Optional.
    #[serde(default)]
    pub source: Option<String>,
}

pub fn validate_kamino_airdrop_allocations_params(_p: &KaminoAirdropAllocationsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_airdrop_allocations(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoAirdropAllocationsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_airdrop_allocations_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(http, &format!("/v2/airdrop/users/{target}/allocations"), &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_airdrop_allocations".into(),
            description: format!("{count} airdrop allocation(s) for {}", short_id(target)),
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

// ── Airdrop: Metrics ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoAirdropMetricsParams {
    /// Points source identifier: Season1, Season2, Season3, Season4. Optional.
    #[serde(default)]
    pub source: Option<String>,
}

pub fn validate_kamino_airdrop_metrics_params(_p: &KaminoAirdropMetricsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_airdrop_metrics(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoAirdropMetricsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_airdrop_metrics_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(http, "/v2/airdrop/metrics", &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_airdrop_metrics".into(),
            description: "Kamino airdrop metrics".into(),
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

// ── Staking Yields ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoStakingYieldsParams {}

pub fn validate_kamino_staking_yields_params(_p: &KaminoStakingYieldsParams) -> Result<(), AppError> { Ok(()) }

pub async fn build_kamino_staking_yields(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoStakingYieldsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_staking_yields_params(params)?;
    let data = kamino_get(http, "/v2/staking-yields").await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_staking_yields".into(),
            description: format!("Staking APY for {count} liquid staking tokens"),
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
        data: None,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoStakingYieldsMedianParams {}

pub fn validate_kamino_staking_yields_median_params(_p: &KaminoStakingYieldsMedianParams) -> Result<(), AppError> { Ok(()) }

pub async fn build_kamino_staking_yields_median(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoStakingYieldsMedianParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_staking_yields_median_params(params)?;
    let data = kamino_get(http, "/v2/staking-yields/median").await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_staking_yields_median".into(),
            description: format!("Median staking APY for {count} liquid staking tokens"),
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
        data: None,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoStakingYieldsMeanParams {}

pub fn validate_kamino_staking_yields_mean_params(_p: &KaminoStakingYieldsMeanParams) -> Result<(), AppError> { Ok(()) }

pub async fn build_kamino_staking_yields_mean(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoStakingYieldsMeanParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_staking_yields_mean_params(params)?;
    let data = kamino_get(http, "/v2/staking-yields/mean").await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_staking_yields_mean".into(),
            description: format!("Mean staking APY for {count} liquid staking tokens"),
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
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// KVault User Season Rewards + Vault Transactions (by vault)
// ──────────────────────────────────────────────────────────────────────────────

/// Season (Earn) reward metrics for a user across all KVaults.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoUserKvaultRewardsParams {
    /// Target wallet. Optional — defaults to authenticated wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Season identifier: Season1, Season2, Season3, Season4. Optional.
    #[serde(default)]
    pub source: Option<String>,
}

pub fn validate_kamino_user_kvault_rewards_params(_p: &KaminoUserKvaultRewardsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_user_kvault_rewards(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserKvaultRewardsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_kvault_rewards_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(http, &format!("/kvaults/users/{target}/rewards"), &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_kvault_rewards".into(),
            description: format!("{count} KVault season reward(s) for {}", short_id(target)),
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

/// Transaction history for a specific KVault (POST with optional filters).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoVaultTransactionsParams {
    /// KVault address (base58). Required.
    pub vault: String,
    /// Filter by instruction name: buy, sell, deposit, withdraw, invest, withdrawAvailable. Optional.
    #[serde(default)]
    pub instruction: Option<String>,
    /// Start date as ISO 8601 or epoch ms. Optional.
    #[serde(default)]
    pub start: Option<String>,
    /// End date as ISO 8601 or epoch ms. Optional.
    #[serde(default)]
    pub end: Option<String>,
    /// Sort direction: "asc" or "desc" (default: desc). Optional.
    #[serde(default)]
    pub direction: Option<String>,
    /// Pagination token from previous response. Optional.
    #[serde(default)]
    pub pagination_token: Option<String>,
}

pub fn validate_kamino_vault_transactions_params(p: &KaminoVaultTransactionsParams) -> Result<(), AppError> {
    if p.vault.len() < 32 {
        return Err(AppError::InvalidParams(format!("vault '{}' is not a valid Solana address", p.vault)));
    }
    Ok(())
}

pub async fn build_kamino_vault_transactions(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoVaultTransactionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_vault_transactions_params(params)?;
    let mut body = serde_json::json!({});
    if let Some(ref ix) = params.instruction  { body["instruction"] = serde_json::Value::String(ix.clone()); }
    if let Some(ref s)  = params.start        { body["start"] = serde_json::Value::String(s.clone()); }
    if let Some(ref e)  = params.end          { body["end"] = serde_json::Value::String(e.clone()); }
    if let Some(ref d)  = params.direction    { body["direction"] = serde_json::Value::String(d.clone()); }
    if let Some(ref pt) = params.pagination_token { body["paginationToken"] = serde_json::Value::String(pt.clone()); }
    let url = format!("{KAMINO_API}/kvaults/vaults/{}/transactions", params.vault);
    let resp = http
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino vault transactions: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::ProtocolError(format!(
            "Kamino vault transactions returned {status}: {body_text}"
        )));
    }
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Kamino vault transactions parse: {e}")))?;
    let count = data.get("result").and_then(|r| r.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_vault_transactions".into(),
            description: format!("{count} transaction(s) for KVault {}", short_id(&params.vault)),
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

// ──────────────────────────────────────────────────────────────────────────────
// Season Rewards, Staking Boosts, Private Credit, Farms
// ──────────────────────────────────────────────────────────────────────────────

// ── User Staking Boosts ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoUserStakingBoostsParams {
    /// Target wallet. Optional — defaults to authenticated wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Season: Season1, Season2, etc. Optional.
    #[serde(default)]
    pub source: Option<String>,
}

pub fn validate_kamino_user_staking_boosts_params(_p: &KaminoUserStakingBoostsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_user_staking_boosts(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserStakingBoostsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_staking_boosts_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(http, &format!("/users/{target}/staking-boosts"), &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_staking_boosts".into(),
            description: format!("{count} staking boost(s) for {}", short_id(target)),
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

// ── Season Rewards: User ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoSeasonRewardsUserParams {
    /// Target wallet. Optional — defaults to authenticated wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Season: Season1, Season2, Season3, Season4. Optional.
    #[serde(default)]
    pub source: Option<String>,
}

pub fn validate_kamino_season_rewards_user_params(_p: &KaminoSeasonRewardsUserParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_season_rewards_user(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoSeasonRewardsUserParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_season_rewards_user_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(http, &format!("/season-rewards/users/{target}"), &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_season_rewards_user".into(),
            description: format!("Season reward total for {}", short_id(target)),
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

// ── Season Rewards: Final Vesting Pool ───────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoSeasonRewardsVestingPoolParams {
    /// Season: Season1, Season2, Season3, Season4. Optional.
    #[serde(default)]
    pub source: Option<String>,
}

pub fn validate_kamino_season_rewards_vesting_pool_params(_p: &KaminoSeasonRewardsVestingPoolParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_season_rewards_vesting_pool(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoSeasonRewardsVestingPoolParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_season_rewards_vesting_pool_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.source { query.push(("source", s.clone())); }
    let data = kamino_get_q(http, "/season-rewards/final-vesting-pool", &query).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_season_rewards_vesting_pool".into(),
            description: "Kamino season final vesting pool allocation".into(),
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

// ── Private Credit: Metrics ───────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoPrivateCreditMetricsParams {}

pub fn validate_kamino_private_credit_metrics_params(_p: &KaminoPrivateCreditMetricsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_private_credit_metrics(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoPrivateCreditMetricsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_private_credit_metrics_params(params)?;
    let data = kamino_get(http, "/kvaults/private-credit/metrics").await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_private_credit_metrics".into(),
            description: "Kamino private credit aggregate metrics".into(),
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

// ── Private Credit: Metrics History ──────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoPrivateCreditMetricsHistoryParams {
    #[serde(default)]
    pub start: Option<String>,
    #[serde(default)]
    pub end: Option<String>,
}

pub fn validate_kamino_private_credit_metrics_history_params(_p: &KaminoPrivateCreditMetricsHistoryParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_private_credit_metrics_history(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoPrivateCreditMetricsHistoryParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_private_credit_metrics_history_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(ref s) = params.start { query.push(("start", s.clone())); }
    if let Some(ref e) = params.end   { query.push(("end", e.clone())); }
    let data = kamino_get_q(http, "/kvaults/private-credit/metrics/history", &query).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_private_credit_metrics_history".into(),
            description: format!("{count} private credit metric snapshots"),
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

// ── Farms: User Transactions ──────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoUserFarmTransactionsParams {
    /// Target wallet. Optional — defaults to authenticated wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Max results per page: 1-1000, default 100. Optional.
    #[serde(default)]
    pub limit: Option<u32>,
    /// Sort: "asc" (default) or "desc". Optional.
    #[serde(default)]
    pub sort: Option<String>,
    /// Pagination token from previous response. Optional.
    #[serde(default)]
    pub pagination_token: Option<String>,
    /// Return all results without pagination. Optional.
    #[serde(default)]
    pub no_pagination: Option<bool>,
}

pub fn validate_kamino_user_farm_transactions_params(_p: &KaminoUserFarmTransactionsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_kamino_user_farm_transactions(
    http: &reqwest::Client,
    wallet: &str,
    params: &KaminoUserFarmTransactionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_user_farm_transactions_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(l)  = params.limit          { query.push(("limit", l.to_string())); }
    if let Some(ref s) = params.sort        { query.push(("sort", s.clone())); }
    if let Some(ref pt) = params.pagination_token { query.push(("paginationToken", pt.clone())); }
    if let Some(np) = params.no_pagination  { query.push(("noPagination", np.to_string())); }
    let data = kamino_get_q(http, &format!("/farms/users/{target}/transactions"), &query).await?;
    let count = data.get("result").and_then(|r| r.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_user_farm_transactions".into(),
            description: format!("{count} farm transaction(s) for {}", short_id(target)),
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

// ── Farms: Farm Transactions ──────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KaminoFarmTransactionsParams {
    /// Farm public key (base58). Required.
    pub farm: String,
    /// Max results per page: 1-1000, default 100. Optional.
    #[serde(default)]
    pub limit: Option<u32>,
    /// Sort: "asc" or "desc" (default: desc). Optional.
    #[serde(default)]
    pub sort: Option<String>,
    /// Pagination token from previous response. Optional.
    #[serde(default)]
    pub pagination_token: Option<String>,
}

pub fn validate_kamino_farm_transactions_params(p: &KaminoFarmTransactionsParams) -> Result<(), AppError> {
    if p.farm.len() < 32 {
        return Err(AppError::InvalidParams(format!("farm '{}' is not a valid Solana address", p.farm)));
    }
    Ok(())
}

pub async fn build_kamino_farm_transactions(
    http: &reqwest::Client,
    _wallet: &str,
    params: &KaminoFarmTransactionsParams,
) -> Result<BuildResponse, AppError> {
    validate_kamino_farm_transactions_params(params)?;
    let mut query: Vec<(&str, String)> = vec![];
    if let Some(l)  = params.limit          { query.push(("limit", l.to_string())); }
    if let Some(ref s) = params.sort        { query.push(("sort", s.clone())); }
    if let Some(ref pt) = params.pagination_token { query.push(("paginationToken", pt.clone())); }
    let data = kamino_get_q(http, &format!("/farms/{}/transactions", params.farm), &query).await?;
    let count = data.get("result").and_then(|r| r.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "kamino_farm_transactions".into(),
            description: format!("{count} transaction(s) for farm {}", short_id(&params.farm)),
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

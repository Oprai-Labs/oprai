//! Solend Protocol Service
//!
//! Decentralized lending protocol on Solana.
//! Features: deposit, withdraw, borrow, repay, collateral management, liquidation, market info.
//!
//! Program: So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo
//! API: https://api.solend.fi/v1/

use reqwest::Client;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

pub const SOLEND_API: &str = "https://api.solend.fi/v1";

// ──────────────────────────────────────────────────────────────────────────────
// Param structs
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendDepositParams {
    /// Token symbol (e.g. "USDC", "SOL") or mint address
    pub token: String,
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendWithdrawParams {
    pub token: String,
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendBorrowParams {
    pub token: String,
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendRepayParams {
    pub token: String,
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendAddCollateralParams {
    pub token: String,
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendWithdrawCollateralParams {
    pub token: String,
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendLiquidateParams {
    /// Borrower's wallet address
    pub borrower_wallet: String,
    pub token_to_repay: String,
    pub collateral_to_claim: String,
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendUserInfoParams {
    pub wallet: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolendMarketParams {}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolendReservesParams {
    /// Optional: market address to filter. If omitted, returns all reserves.
    pub market: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolendStatsParams {}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolendLstRatesParams {}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolendPricesParams {
    /// Comma-separated mint addresses (optional if symbols set)
    pub mints: Option<String>,
    /// Comma-separated token symbols e.g. "SOL,USDC" (optional if mints set)
    pub symbols: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolendReservesHistoryParams {
    /// Comma-separated reserve public keys
    pub ids: String,
    /// Time span: "1w" | "30d" | "90d" | "1y"
    pub span: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SolendDailyStatsParams {
    /// Date in YYYY-MM-DD format
    pub date: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendFlashLoanParams {
    /// Token symbol (e.g. "USDC") or mint address to flash-borrow
    pub token: String,
    /// Amount to borrow (in human units, e.g. "1000")
    pub amount: String,
    /// Optional: lending market address; defaults to main market
    pub market: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendClaimRewardsParams {
    /// Optional: claim only for a specific reward mint; omit to claim all
    pub reward_mint: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendDepositLiquidityParams {
    /// Token symbol or mint address to deposit (mints cTokens)
    pub token: String,
    pub amount: String,
    /// Optional: lending market address; defaults to main market
    pub market: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendDepositObligationCollateralParams {
    /// cToken symbol or mint address to post as collateral
    pub token: String,
    pub amount: String,
    pub market: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendRedeemCollateralParams {
    /// cToken symbol or mint address to redeem for underlying asset
    pub token: String,
    pub amount: String,
    pub market: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SolendExerciseRewardParams {
    /// Amount of reward options to exercise (in token units)
    pub amount: String,
    /// Optional: reward mint address to exercise (omit to exercise all)
    pub reward_mint: Option<String>,
}


// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

/// Returns a clear "not implemented" error for Solend transactional actions.
/// These routes exist in the builder for completeness but are NOT exposed
/// via the frontend whitelist — SDK integration is pending.
fn not_implemented(action: &str) -> AppError {
    AppError::ProtocolError(format!(
        "{action} is not yet supported. Solend transactional SDK integration is pending. Use the Solend app at app.solend.fi."
    ))
}

// ──────────────────────────────────────────────────────────────────────────────
// Validators
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_solend_deposit_params(p: &SolendDepositParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_solend_withdraw_params(p: &SolendWithdrawParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    Ok(())
}

pub fn validate_solend_borrow_params(p: &SolendBorrowParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_solend_repay_params(p: &SolendRepayParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    Ok(())
}

pub fn validate_solend_add_collateral_params(p: &SolendAddCollateralParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_solend_withdraw_collateral_params(p: &SolendWithdrawCollateralParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    Ok(())
}

pub fn validate_solend_liquidate_params(p: &SolendLiquidateParams) -> Result<(), AppError> {
    if p.borrower_wallet.is_empty() {
        return Err(AppError::InvalidParams("borrowerWallet is required".into()));
    }
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_solend_user_info_params(_p: &SolendUserInfoParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_solend_market_params(_p: &SolendMarketParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_solend_reserves_params(_p: &SolendReservesParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_solend_flash_loan_params(p: &SolendFlashLoanParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_solend_claim_rewards_params(_p: &SolendClaimRewardsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_solend_deposit_liquidity_params(p: &SolendDepositLiquidityParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_solend_deposit_obligation_collateral_params(p: &SolendDepositObligationCollateralParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_solend_redeem_collateral_params(p: &SolendRedeemCollateralParams) -> Result<(), AppError> {
    if p.token.is_empty() {
        return Err(AppError::InvalidParams("token is required".into()));
    }
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_solend_exercise_reward_params(p: &SolendExerciseRewardParams) -> Result<(), AppError> {
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Builders
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_solend_deposit(
    _http: &Client,
    _user: &str,
    _p: &SolendDepositParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_deposit"))
}

pub async fn build_solend_withdraw(
    _http: &Client,
    _user: &str,
    _p: &SolendWithdrawParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_withdraw"))
}

pub async fn build_solend_borrow(
    _http: &Client,
    _user: &str,
    _p: &SolendBorrowParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_borrow"))
}

pub async fn build_solend_repay(
    _http: &Client,
    _user: &str,
    _p: &SolendRepayParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_repay"))
}

pub async fn build_solend_add_collateral(
    _http: &Client,
    _user: &str,
    _p: &SolendAddCollateralParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_add_collateral"))
}

pub async fn build_solend_withdraw_collateral(
    _http: &Client,
    _user: &str,
    _p: &SolendWithdrawCollateralParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_withdraw_collateral"))
}

pub async fn build_solend_liquidate(
    _http: &Client,
    _user: &str,
    _p: &SolendLiquidateParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_liquidate"))
}

pub async fn build_solend_user_info(
    http: &Client,
    user: &str,
    p: &SolendUserInfoParams,
) -> Result<BuildResponse, AppError> {
    let wallet = p.wallet.as_deref().unwrap_or(user);
    // /v1/user-overview returns a map of marketAddress → obligation (null if no position)
    let url = format!("{SOLEND_API}/user-overview?wallet={wallet}");
    let resp = http.get(&url).send().await;

    let description = match resp {
        Ok(r) if r.status().is_success() => {
            let body = r.text().await.unwrap_or_default();
            format!("Solend positions for {}:\n{}", wallet, body)
        }
        Ok(r) => {
            let status = r.status();
            format!("Solend positions for {} (API {}): Use app.solend.fi to view your full position details.", wallet, status)
        }
        Err(_) => {
            format!("Solend positions for {}: Use app.solend.fi to view your deposits, borrows, and health factor.", wallet)
        }
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sl_{}", Uuid::new_v4()),
            action_type: "solend_user_info".to_string(),
            description,
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!({ "wallet": wallet }),
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

pub async fn build_solend_market(
    http: &Client,
    _user: &str,
    _p: &SolendMarketParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{SOLEND_API}/markets/configs?scope=all&deployment=production");
    let resp = http.get(&url).send().await;

    let description = match resp {
        Ok(r) if r.status().is_success() => {
            let body = r.text().await.unwrap_or_default();
            format!("Solend market data:\n{}", body)
        }
        _ => "Solend market data: Visit app.solend.fi for reserve rates, TVL, and market configuration.".to_string(),
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sl_{}", Uuid::new_v4()),
            action_type: "solend_market".to_string(),
            description,
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
        data: None,
    })
}

pub async fn build_solend_reserves(
    http: &Client,
    _user: &str,
    p: &SolendReservesParams,
) -> Result<BuildResponse, AppError> {
    // Fetch reserves for a specific market or all markets
    let url = match &p.market {
        Some(market) if !market.is_empty() => {
            format!("{SOLEND_API}/reserves?market={market}")
        }
        _ => format!("{SOLEND_API}/reserves?scope=all&deployment=production"),
    };

    let resp = http.get(&url).send().await;

    let description = match resp {
        Ok(r) if r.status().is_success() => {
            let body = r.text().await.unwrap_or_default();
            format!("Solend reserves:\n{}", body)
        }
        _ => "Solend reserves: Visit app.solend.fi for current supply/borrow APYs, TVL, and reserve details.".to_string(),
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sl_{}", Uuid::new_v4()),
            action_type: "solend_reserves".to_string(),
            description,
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!({ "market": p.market }),
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

pub async fn build_solend_stats(
    http: &Client,
    _user: &str,
    _p: &SolendStatsParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{SOLEND_API}/stats");
    let resp = http.get(&url).send().await;
    let description = match resp {
        Ok(r) if r.status().is_success() => format!("Solend protocol stats:\n{}", r.text().await.unwrap_or_default()),
        _ => "Solend stats unavailable. Visit app.solend.fi for current TVL and protocol stats.".to_string(),
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sl_{}", Uuid::new_v4()),
            action_type: "solend_stats".to_string(),
            description,
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
        data: None,
    })
}

pub async fn build_solend_lst_rates(
    http: &Client,
    _user: &str,
    _p: &SolendLstRatesParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{SOLEND_API}/lst-rates");
    let resp = http.get(&url).send().await;
    let description = match resp {
        Ok(r) if r.status().is_success() => format!("Solend LST rates:\n{}", r.text().await.unwrap_or_default()),
        _ => "Solend LST rates unavailable.".to_string(),
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sl_{}", Uuid::new_v4()),
            action_type: "solend_lst_rates".to_string(),
            description,
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
        data: None,
    })
}

pub async fn build_solend_prices(
    http: &Client,
    _user: &str,
    p: &SolendPricesParams,
) -> Result<BuildResponse, AppError> {
    let mut qs_parts = vec![];
    if let Some(m) = &p.mints { qs_parts.push(format!("mints={m}")); }
    if let Some(s) = &p.symbols { qs_parts.push(format!("symbols={s}")); }
    let qs = if qs_parts.is_empty() { String::new() } else { format!("?{}", qs_parts.join("&")) };
    let url = format!("{SOLEND_API}/prices{qs}");
    let resp = http.get(&url).send().await;
    let description = match resp {
        Ok(r) if r.status().is_success() => format!("Solend prices:\n{}", r.text().await.unwrap_or_default()),
        _ => "Solend prices unavailable.".to_string(),
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sl_{}", Uuid::new_v4()),
            action_type: "solend_prices".to_string(),
            description,
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!(p),
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

pub async fn build_solend_reserves_history(
    http: &Client,
    _user: &str,
    p: &SolendReservesHistoryParams,
) -> Result<BuildResponse, AppError> {
    let span = p.span.as_deref().unwrap_or("1w");
    let url = format!("{SOLEND_API}/reserves/historical-interest-rates?ids={}&span={span}", p.ids);
    let resp = http.get(&url).send().await;
    let description = match resp {
        Ok(r) if r.status().is_success() => format!("Solend reserves APY history:\n{}", r.text().await.unwrap_or_default()),
        _ => "Solend reserves history unavailable.".to_string(),
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sl_{}", Uuid::new_v4()),
            action_type: "solend_reserves_history".to_string(),
            description,
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!(p),
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

pub async fn build_solend_daily_stats(
    http: &Client,
    _user: &str,
    p: &SolendDailyStatsParams,
) -> Result<BuildResponse, AppError> {
    let url = format!("{SOLEND_API}/daily-stats?date={}", p.date);
    let resp = http.get(&url).send().await;
    let description = match resp {
        Ok(r) if r.status().is_success() => format!("Solend daily stats for {}:\n{}", p.date, r.text().await.unwrap_or_default()),
        _ => format!("Solend daily stats for {} unavailable.", p.date),
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sl_{}", Uuid::new_v4()),
            action_type: "solend_daily_stats".to_string(),
            description,
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!({ "date": p.date }),
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

pub async fn build_solend_flash_loan(
    _http: &Client,
    _user: &str,
    _p: &SolendFlashLoanParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_flash_loan"))
}

pub async fn build_solend_claim_rewards(
    _http: &Client,
    _user: &str,
    _p: &SolendClaimRewardsParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_claim_rewards"))
}

pub async fn build_solend_deposit_liquidity(
    _http: &Client,
    _user: &str,
    _p: &SolendDepositLiquidityParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_deposit_liquidity"))
}

pub async fn build_solend_deposit_obligation_collateral(
    _http: &Client,
    _user: &str,
    _p: &SolendDepositObligationCollateralParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_deposit_obligation_collateral"))
}

pub async fn build_solend_redeem_collateral(
    _http: &Client,
    _user: &str,
    _p: &SolendRedeemCollateralParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_redeem_collateral"))
}

pub async fn build_solend_exercise_reward(
    _http: &Client,
    _user: &str,
    _p: &SolendExerciseRewardParams,
) -> Result<BuildResponse, AppError> {
    Err(not_implemented("solend_exercise_reward"))
}

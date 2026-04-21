/// Jupiter Lend - Earn (deposit/withdraw) and Borrow operations.
///
/// API: https://lite-api.jup.ag/lend
///
/// Programs (mainnet):
///   Earn:   jup3YeL8QhtSx1e253b2FDvsMNC87fDrgQZivbrndc9
///   Borrow: jupr81YtYssSyPt8jbnGuiWon5f6x9TcDEFxYe3Bdzi
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

/// Jupiter Lend Earn (deposit/withdraw) parameters.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupiterLendParams {
    /// Operation: "deposit" or "withdraw"
    pub operation: String,
    /// Token symbol (e.g., "USDC", "USDT", "jupSOL")
    pub token: String,
    /// Amount in human-readable format (e.g., "100.50")
    pub amount: String,
}

/// Jupiter Lend Borrow/Repay parameters.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupiterBorrowParams {
    /// Operation: "borrow" or "repay"
    pub operation: String,
    /// Debt token symbol (e.g., "USDC")
    pub token: String,
    /// Collateral token symbol (e.g., "SOL", "jupSOL")
    pub collateral: Option<String>,
    /// Amount in human-readable format
    pub amount: String,
}

pub fn validate_lend_params(params: &JupiterLendParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    if params.operation != "deposit" && params.operation != "withdraw" {
        return Err(AppError::InvalidParams(
            "Operation must be 'deposit' or 'withdraw'".into(),
        ));
    }
    Ok(())
}

pub fn validate_borrow_params(params: &JupiterBorrowParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    if params.operation != "borrow" && params.operation != "repay" {
        return Err(AppError::InvalidParams(
            "Operation must be 'borrow' or 'repay'".into(),
        ));
    }
    Ok(())
}

/// Get lending market data from Jupiter API.
pub async fn get_lend_markets(http: &reqwest::Client) -> Result<LendMarketsResponse, AppError> {
    let response = http
        .get("https://lite-api.jup.ag/lend/markets")
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch lend markets: {}", e)))?;

    if !response.status().is_success() {
        return Err(AppError::Internal(format!(
            "Lend markets API error: {}",
            response.status()
        )));
    }

    let data: LendMarketsResponse = response
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse lend markets: {}", e)))?;

    Ok(data)
}

#[derive(Debug, Deserialize)]
pub struct LendMarketsResponse {
    #[serde(rename = "earn")]
    pub earn: Vec<EarnMarket>,
    #[serde(rename = "borrow")]
    pub borrow: Vec<BorrowMarket>,
}

#[derive(Debug, Deserialize)]
pub struct EarnMarket {
    pub symbol: String,
    #[allow(dead_code)]
    pub mint: String,
    #[allow(dead_code)]
    pub decimals: u8,
    pub supply_apy: f64,
    #[serde(rename = "rewards_apy")]
    #[allow(dead_code)]
    pub rewards_apy: Option<f64>,
}

#[derive(Debug, Deserialize)]
pub struct BorrowMarket {
    pub symbol: String,
    #[allow(dead_code)]
    pub mint: String,
    #[allow(dead_code)]
    pub decimals: u8,
    pub borrow_apy: f64,
    #[serde(rename = "collateral_factor")]
    pub collateral_factor: f64,
}

/// Build Jupiter Lend Earn transaction (deposit/withdraw).
///
/// Note: This creates a transaction that the user signs and submits directly.
/// The actual on-chain transaction building happens in the frontend using @jup-ag/lend.
pub async fn build_lend_transaction(
    http: &reqwest::Client,
    _user_pubkey: &str,
    params: &JupiterLendParams,
) -> Result<BuildResponse, AppError> {
    validate_lend_params(params)?;

    // Fetch market data for APY info
    let markets = get_lend_markets(http).await?;

    // Find the requested asset
    let asset_info = markets
        .earn
        .iter()
        .find(|m| m.symbol.eq_ignore_ascii_case(&params.token));

    let (symbol, apy) = if let Some(asset) = asset_info {
        (asset.symbol.clone(), asset.supply_apy)
    } else {
        return Err(AppError::InvalidParams(format!(
            "Unsupported token: {}. Supported: USDC, USDT, jupSOL, jitoSOL, EURC",
            params.token
        )));
    };

    let amount: f64 = params.amount.parse().unwrap_or(0.0);
    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: if params.operation == "deposit" {
            "lend".to_string()
        } else {
            "withdraw_lend".to_string()
        },
        description: format!(
            "{} {} to Jupiter Earn (APY: {:.2}%)",
            if params.operation == "deposit" { "Deposit" } else { "Withdraw" },
            format!("{:.4} {}", amount, symbol),
            apy
        ),
        estimated_fee: "5000".to_string(), // ~0.005 SOL
        estimated_refund: None,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings: vec![],
        requires_approval: true,
    };

    // For Jupiter Lend, the transaction is built on the frontend
    // This returns a preview - the actual transaction is built client-side
    Ok(BuildResponse {
        preview,
        transaction: None, // Frontend builds the actual transaction
        additional_signers_required: 0,
        execution_steps: Some(serde_json::json!([
            "Build transaction on frontend using @jup-ag/lend",
            "Sign transaction with wallet",
            "Submit to network"
        ])),
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Build Jupiter Borrow/Repay transaction.
pub async fn build_borrow_transaction(
    http: &reqwest::Client,
    _user_pubkey: &str,
    params: &JupiterBorrowParams,
) -> Result<BuildResponse, AppError> {
    validate_borrow_params(params)?;

    // Fetch market data
    let markets = get_lend_markets(http).await?;

    // Find the debt asset
    let debt_info = markets
        .borrow
        .iter()
        .find(|m| m.symbol.eq_ignore_ascii_case(&params.token));

    let (symbol, borrow_apy, collateral_factor) = if let Some(asset) = debt_info {
        (
            asset.symbol.clone(),
            asset.borrow_apy,
            asset.collateral_factor,
        )
    } else {
        return Err(AppError::InvalidParams(format!(
            "Unsupported borrow token: {}",
            params.token
        )));
    };

    let amount: f64 = params.amount.parse().unwrap_or(0.0);
    let collateral = params.collateral.as_deref().unwrap_or("SOL");

    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: if params.operation == "borrow" {
            "borrow".to_string()
        } else {
            "repay".to_string()
        },
        description: format!(
            "{} {} {} (Collateral: {}, LTV: {:.0}%)",
            if params.operation == "borrow" { "Borrow" } else { "Repay" },
            format!("{:.4} {}", amount, symbol),
            if params.operation == "borrow" {
                format!("@ {:.2}% APY", borrow_apy)
            } else {
                "".to_string()
            },
            collateral,
            collateral_factor * 100.0
        ),
        estimated_fee: "5000".to_string(),
        estimated_refund: None,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings: vec![],
        requires_approval: true,
    };

    Ok(BuildResponse {
        preview,
        transaction: None,
        additional_signers_required: 0,
        execution_steps: Some(serde_json::json!([
            "Build transaction on frontend using @jup-ag/lend",
            "Sign transaction with wallet",
            "Submit to network"
        ])),
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// GET Query: Jupiter Lend Markets
// ──────────────────────────────────────────────────────────────────────────────

/// Params for querying available Jupiter Lend markets and current APYs.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupLendMarketsParams {
    /// Filter by side: "earn" or "borrow". Returns both if omitted.
    #[serde(default)]
    pub side: Option<String>,
}

pub fn validate_jup_lend_markets_params(p: &JupLendMarketsParams) -> Result<(), AppError> {
    if let Some(ref s) = p.side {
        if s != "earn" && s != "borrow" {
            return Err(AppError::InvalidParams(
                "side must be 'earn' or 'borrow'".into(),
            ));
        }
    }
    Ok(())
}

/// Query available Jupiter Lend markets with current APYs.
pub async fn build_jup_lend_markets(
    http: &reqwest::Client,
    _wallet: &str,
    params: &JupLendMarketsParams,
) -> Result<BuildResponse, AppError> {
    validate_jup_lend_markets_params(params)?;
    let markets = get_lend_markets(http).await?;

    let earn_entries: Vec<serde_json::Value> = markets
        .earn
        .iter()
        .map(|m| serde_json::json!({ "symbol": m.symbol, "supplyApy": m.supply_apy }))
        .collect();
    let borrow_entries: Vec<serde_json::Value> = markets
        .borrow
        .iter()
        .map(|m| serde_json::json!({
            "symbol": m.symbol,
            "borrowApy": m.borrow_apy,
            "collateralFactor": m.collateral_factor,
        }))
        .collect();

    let description = match params.side.as_deref() {
        Some("earn") => {
            let list: Vec<String> = markets
                .earn
                .iter()
                .map(|m| format!("{} ({:.2}% APY)", m.symbol, m.supply_apy))
                .collect();
            format!("Earn markets: {}", list.join(", "))
        }
        Some("borrow") => {
            let list: Vec<String> = markets
                .borrow
                .iter()
                .map(|m| format!("{} ({:.2}% APY, LTV {:.0}%)", m.symbol, m.borrow_apy, m.collateral_factor * 100.0))
                .collect();
            format!("Borrow markets: {}", list.join(", "))
        }
        _ => {
            let earn: Vec<String> = markets
                .earn
                .iter()
                .map(|m| format!("{} ({:.2}%)", m.symbol, m.supply_apy))
                .collect();
            let borrow: Vec<String> = markets
                .borrow
                .iter()
                .map(|m| format!("{} ({:.2}%)", m.symbol, m.borrow_apy))
                .collect();
            format!("Earn: {} | Borrow: {}", earn.join(", "), borrow.join(", "))
        }
    };

    let data = match params.side.as_deref() {
        Some("earn") => serde_json::json!({ "earn": earn_entries }),
        Some("borrow") => serde_json::json!({ "borrow": borrow_entries }),
        _ => serde_json::json!({ "earn": earn_entries, "borrow": borrow_entries }),
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_lend_markets".into(),
            description,
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

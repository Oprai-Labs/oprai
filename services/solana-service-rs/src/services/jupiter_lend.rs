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
    // Token support is validated against the LIVE earn-token list in
    // build_lend_transaction — never a hardcoded set here (Jupiter adds assets
    // like WSOL/USDG/USDS/JupUSD over time and a static list goes stale).
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

// ── Live Jupiter Lend API shapes ────────────────────────────────────────────
// The old `/lend/markets` endpoint is dead (404). Earn assets live at
// `/lend/v1/earn/tokens`, borrow vaults at `/lend/v1/borrow/vaults`. Rates and
// factors arrive as basis-point strings ("503" = 5.03%, "800" = 80% factor).

fn parse_rate(s: &Option<String>) -> f64 {
    s.as_deref()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(0.0)
}

#[derive(Debug, Deserialize)]
struct RawLendAsset {
    address: String,
    #[serde(default)]
    symbol: String,
    #[serde(rename = "uiSymbol", default)]
    ui_symbol: Option<String>,
    #[serde(default)]
    decimals: u8,
}

impl RawLendAsset {
    /// User-facing symbol: prefer uiSymbol (e.g. WSOL → "SOL") over the raw symbol.
    fn display_symbol(&self) -> String {
        self.ui_symbol
            .as_deref()
            .filter(|s| !s.is_empty())
            .unwrap_or(&self.symbol)
            .to_string()
    }
}

#[derive(Debug, Deserialize)]
struct RawEarnToken {
    asset: RawLendAsset,
    #[serde(rename = "totalRate", default)]
    total_rate: Option<String>,
    #[serde(rename = "rewardsRate", default)]
    rewards_rate: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RawBorrowVault {
    #[serde(rename = "borrowToken")]
    borrow_token: RawLendAsset,
    #[serde(rename = "borrowRate", default)]
    borrow_rate: Option<String>,
    #[serde(rename = "collateralFactor", default)]
    collateral_factor: Option<String>,
}

/// Get lending market data from the LIVE Jupiter Lend API.
///
/// Earn markets are authoritative (drive deposit/withdraw validation); borrow
/// vaults are best-effort — a borrow-endpoint failure yields an empty borrow
/// list rather than failing the whole call, so Earn keeps working.
pub async fn get_lend_markets(http: &reqwest::Client) -> Result<LendMarketsResponse, AppError> {
    let earn_raw: Vec<RawEarnToken> = http
        .get("https://lite-api.jup.ag/lend/v1/earn/tokens")
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch lend earn tokens: {}", e)))?
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse lend earn tokens: {}", e)))?;

    let earn: Vec<EarnMarket> = earn_raw
        .into_iter()
        .map(|t| EarnMarket {
            symbol: t.asset.display_symbol(),
            mint: t.asset.address,
            decimals: t.asset.decimals,
            supply_apy: parse_rate(&t.total_rate) / 100.0,
            rewards_apy: Some(parse_rate(&t.rewards_rate) / 100.0),
        })
        .collect();

    let borrow: Vec<BorrowMarket> = match http
        .get("https://lite-api.jup.ag/lend/v1/borrow/vaults")
        .send()
        .await
    {
        Ok(resp) => resp
            .json::<Vec<RawBorrowVault>>()
            .await
            .map(|vaults| {
                vaults
                    .into_iter()
                    .map(|v| BorrowMarket {
                        symbol: v.borrow_token.display_symbol(),
                        mint: v.borrow_token.address,
                        decimals: v.borrow_token.decimals,
                        borrow_apy: parse_rate(&v.borrow_rate) / 100.0,
                        collateral_factor: parse_rate(&v.collateral_factor) / 1000.0,
                    })
                    .collect()
            })
            .unwrap_or_default(),
        Err(_) => Vec::new(),
    };

    Ok(LendMarketsResponse { earn, borrow })
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
    /// Everything a depositor receives — interest plus incentives.
    pub supply_apy: f64,
    /// The incentive share of it. Kept apart because the two are not the same
    /// promise: interest is paid by borrowers and moves with demand, while
    /// rewards are paid by the protocol and can stop when it decides to stop
    /// them. A headline rate that is mostly incentives can fall below a lower
    /// one that is all interest, and a comparison made on the combined figure
    /// alone cannot see that coming.
    #[serde(rename = "rewards_apy")]
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

    // Find the requested asset in the LIVE earn list. Match on symbol or mint,
    // and treat SOL / WSOL as equivalent (the API lists native SOL as WSOL).
    let want = params.token.trim();
    let want_sol = want.eq_ignore_ascii_case("SOL") || want.eq_ignore_ascii_case("WSOL");
    let asset_info = markets.earn.iter().find(|m| {
        m.symbol.eq_ignore_ascii_case(want)
            || m.mint == want
            || (want_sol
                && (m.symbol.eq_ignore_ascii_case("SOL") || m.symbol.eq_ignore_ascii_case("WSOL")))
    });

    let (symbol, apy) = if let Some(asset) = asset_info {
        (asset.symbol.clone(), asset.supply_apy)
    } else {
        let supported = markets
            .earn
            .iter()
            .map(|m| m.symbol.as_str())
            .collect::<Vec<_>>()
            .join(", ");
        return Err(AppError::InvalidParams(format!(
            "Jupiter Lend doesn't support '{}' yet. Currently supported: {}",
            params.token, supported
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
            if params.operation == "deposit" {
                "Deposit"
            } else {
                "Withdraw"
            },
            format_args!("{amount:.4} {symbol}"),
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
            if params.operation == "borrow" {
                "Borrow"
            } else {
                "Repay"
            },
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
        .map(|m| {
            serde_json::json!({
                "symbol": m.symbol,
                "supplyApy": m.supply_apy,
                "rewardsApy": m.rewards_apy,
                "interestApy": m.rewards_apy.map(|r| m.supply_apy - r),
            })
        })
        .collect();
    let borrow_entries: Vec<serde_json::Value> = markets
        .borrow
        .iter()
        .map(|m| {
            serde_json::json!({
                "symbol": m.symbol,
                "borrowApy": m.borrow_apy,
                "collateralFactor": m.collateral_factor,
            })
        })
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
                .map(|m| {
                    format!(
                        "{} ({:.2}% APY, LTV {:.0}%)",
                        m.symbol,
                        m.borrow_apy,
                        m.collateral_factor * 100.0
                    )
                })
                .collect();
            format!("Borrow markets: {}", list.join(", "))
        }
        _ => {
            let earn: Vec<String> = markets
                .earn
                .iter()
                .map(|m| {
                    // Name the incentive share when it is enough to change the
                    // answer: USDC read 5.00% against Kamino's 4.76% and won,
                    // but 0.70 of it was rewards — on interest alone it loses.
                    match m.rewards_apy {
                        Some(r) if r >= 0.1 => format!(
                            "{} ({:.2}% = {:.2}% interest + {:.2}% rewards)",
                            m.symbol,
                            m.supply_apy,
                            m.supply_apy - r,
                            r
                        ),
                        _ => format!("{} ({:.2}%)", m.symbol, m.supply_apy),
                    }
                })
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

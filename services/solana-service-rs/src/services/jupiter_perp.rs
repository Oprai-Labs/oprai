/// Jupiter Perpetuals - Open/close perpetual positions and JLP liquidity.
///
/// Program: PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu
/// JLP Mint: 27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4
///
/// Architecture: Keeper model
///   Step 1 (user): Submit a PositionRequest transaction on-chain
///   Step 2 (keeper): Jupiter keeper executes the position change
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

const JUPITER_PERP_API: &str = "https://api.jup.ag/perp/v2";

// Collateral token mints
const SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const WETH_MINT: &str = "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs";
const WBTC_MINT: &str = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh";
pub const JLP_MINT: &str = "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4";

/// Supported perpetual markets.
pub const PERP_MARKETS: &[&str] = &["SOL", "wETH", "wBTC"];

/// Normalize market name: "ETH" -> "wETH", "BTC" -> "wBTC", "SOL" -> "SOL".
fn normalize_market(market: &str) -> String {
    match market.to_uppercase().as_str() {
        "ETH" | "WETH" => "wETH".to_string(),
        "BTC" | "WBTC" => "wBTC".to_string(),
        _ => market.to_string(),
    }
}

/// Determine collateral mint and decimals from market/side/override.
/// - Long positions default to the base token (SOL for SOL-PERP, etc.)
/// - Short positions default to USDC
fn get_collateral_mint_and_decimals(market: &str, side: &str, collateral_token: Option<&str>) -> (&'static str, u8) {
    if let Some(token) = collateral_token {
        return match token.to_uppercase().as_str() {
            "USDC" => (USDC_MINT, 6),
            "USDT" => ("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
            "WETH" | "ETH" => (WETH_MINT, 8),
            "WBTC" | "BTC" => (WBTC_MINT, 8),
            _ => (SOL_MINT, 9), // default SOL
        };
    }
    match side {
        "short" => (USDC_MINT, 6),
        _ => match market {
            "wETH" => (WETH_MINT, 8),
            "wBTC" => (WBTC_MINT, 8),
            _ => (SOL_MINT, 9), // SOL for SOL-PERP longs
        },
    }
}

/// Jupiter Perp open/close position parameters.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupiterPerpParams {
    /// Operation: "open" or "close"
    pub operation: String,
    /// Market: "SOL", "wETH", or "wBTC"
    pub market: String,
    /// Position side: "long" or "short"
    pub side: String,
    /// Collateral amount in human-readable format (e.g. "2" for 2 SOL)
    pub collateral_amount: String,
    /// Optional: explicit position size in USD
    pub size_usd: Option<String>,
    /// Leverage multiplier (e.g. "5" for 5x)
    pub leverage: Option<String>,
    /// Override collateral token (e.g. "USDC", "SOL")
    pub collateral_token: Option<String>,
    /// Slippage in bps (default: 200)
    pub slippage_bps: Option<u16>,
}

/// Jupiter JLP liquidity parameters.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupiterPerpLiquidityParams {
    /// Operation: "add" or "remove"
    pub operation: String,
    /// Amount in human-readable format (SOL or JLP)
    pub amount: String,
    /// Token to add (for add) or receive (for remove). Default: SOL.
    pub token: Option<String>,
}


pub fn validate_perp_params(params: &JupiterPerpParams) -> Result<(), AppError> {
    let amount: f64 = params
        .collateral_amount
        .parse()
        .map_err(|_| AppError::InvalidParams("collateralAmount must be a number".into()))?;

    if amount <= 0.0 {
        return Err(AppError::InvalidParams(
            "collateralAmount must be positive. For close, specify the exact collateral amount \
             to remove (e.g. \"2\" for 2 SOL). To close the full position, use your full collateral size.".into(),
        ));
    }

    if params.operation != "open" && params.operation != "close" {
        return Err(AppError::InvalidParams(
            "operation must be 'open' or 'close'".into(),
        ));
    }

    if params.side != "long" && params.side != "short" {
        return Err(AppError::InvalidParams(
            "side must be 'long' or 'short'".into(),
        ));
    }

    let market = normalize_market(&params.market);
    if !PERP_MARKETS.contains(&market.as_str()) {
        return Err(AppError::InvalidParams(format!(
            "Invalid market: {}. Supported: SOL, wETH, wBTC",
            params.market
        )));
    }

    Ok(())
}

pub fn validate_liquidity_params(params: &JupiterPerpLiquidityParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    if params.operation != "add" && params.operation != "remove" {
        return Err(AppError::InvalidParams(
            "operation must be 'add' or 'remove'".into(),
        ));
    }
    Ok(())
}

/// Build Jupiter Perpetuals open/close position transaction.
pub async fn build_perp_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &JupiterPerpParams,
) -> Result<BuildResponse, AppError> {
    validate_perp_params(params)?;

    let normalized_market = normalize_market(&params.market);
    let amount: f64 = params.collateral_amount.parse().unwrap_or(0.0);
    let leverage: f64 = params
        .leverage
        .as_ref()
        .and_then(|l| l.parse().ok())
        .unwrap_or(2.0);
    let slippage_bps = params.slippage_bps.unwrap_or(200);

    let (collateral_mint, decimals) = get_collateral_mint_and_decimals(
        &normalized_market,
        &params.side,
        params.collateral_token.as_deref(),
    );

    let collateral_lamports = (amount * 10f64.powi(decimals as i32)) as u64;

    let size_usd: f64 = params
        .size_usd
        .as_ref()
        .and_then(|s| s.parse().ok())
        .unwrap_or(amount * leverage);

    let (api_path, body, description) = if params.operation == "open" {
        let desc = format!(
            "Open {} {} position on Jupiter Perps ({}x leverage, {} collateral, ~${:.0} size)",
            normalized_market,
            params.side,
            leverage,
            params.collateral_amount,
            size_usd
        );
        let req_body = serde_json::json!({
            "wallet": user_pubkey,
            "market": normalized_market,
            "side": params.side,
            "collateralMint": collateral_mint,
            "collateralAmount": collateral_lamports.to_string(),
            "leverage": leverage,
            "slippageBps": slippage_bps,
        });
        (format!("{}/increase-position", JUPITER_PERP_API), req_body, desc)
    } else {
        let desc = format!(
            "Close {} {} position on Jupiter Perps ({} collateral)",
            normalized_market, params.side, params.collateral_amount
        );
        let req_body = serde_json::json!({
            "wallet": user_pubkey,
            "market": normalized_market,
            "side": params.side,
            "collateralMint": collateral_mint,
            "collateralAmount": collateral_lamports.to_string(),
            "slippageBps": slippage_bps,
        });
        (format!("{}/decrease-position", JUPITER_PERP_API), req_body, desc)
    };

    let mut req = http.post(&api_path).json(&body);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let response = req
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter Perp API error: {}", e)))?;

    let status = response.status();
    let resp_json: serde_json::Value = response
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse Jupiter Perp response: {}", e)))?;

    if !status.is_success() {
        let err_msg = resp_json
            .get("error")
            .and_then(|v| v.as_str())
            .or_else(|| resp_json.get("message").and_then(|v| v.as_str()))
            .unwrap_or("Unknown Jupiter Perp API error");
        return Err(AppError::Internal(format!(
            "Jupiter Perp API error ({}): {}",
            status, err_msg
        )));
    }

    let transaction = resp_json
        .get("transaction")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: format!("perp_{}", params.operation),
        description,
        estimated_fee: "15000".to_string(), // ~0.015 SOL
        estimated_refund: None,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings: vec![
            "Perpetual trading involves liquidation risk.".to_string(),
            "Jupiter keeper execution may have slippage.".to_string(),
        ],
        requires_approval: true,
    };

    Ok(BuildResponse {
        preview,
        transaction,
        additional_signers_required: 0,
        execution_steps: None,
        quote: Some(resp_json),
        is_cross_chain: false,
        data: None,
    })
}

/// Fetch open perpetual positions for a wallet from Jupiter Portfolio API.
pub async fn build_perp_positions(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    wallet: &str,
) -> Result<BuildResponse, AppError> {
    let url = format!("https://api.jup.ag/portfolio/v1/positions/{wallet}?platforms=jupiter-perpetuals");
    let mut req = http.get(&url).header("Accept", "application/json");
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req.send().await
        .map_err(|e| AppError::Internal(format!("Jupiter Portfolio API error: {e}")))?;

    let status = resp.status();
    let data: serde_json::Value = resp.json().await
        .map_err(|e| AppError::Internal(format!("Failed to parse positions response: {e}")))?;

    if !status.is_success() {
        let msg = data.get("message").and_then(|v| v.as_str()).unwrap_or("API error");
        return Err(AppError::Internal(format!("Jupiter Portfolio API {status}: {msg}")));
    }

    let count = data.get("elements").and_then(|v| v.as_array()).map(|e| e.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "perp_positions".to_string(),
            description: format!("{count} open position(s) on Jupiter Perps for {}", &wallet[..8.min(wallet.len())]),
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

/// Build JLP liquidity (add/remove) transaction.
pub async fn build_perp_liquidity_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &JupiterPerpLiquidityParams,
) -> Result<BuildResponse, AppError> {
    validate_liquidity_params(params)?;

    let amount: f64 = params.amount.parse().unwrap_or(0.0);

    // Resolve token mint and decimals.
    // For add-liquidity: `token` = what the user deposits (decimals vary by token).
    // For remove-liquidity: `token` = what the user receives, BUT the input amount
    //   is always JLP (6 decimals). These are treated separately below.
    let token_sym = params.token.as_deref().unwrap_or("SOL");
    let (token_mint, token_decimals): (&str, u8) = match token_sym.to_uppercase().as_str() {
        "USDC" => (USDC_MINT, 6),
        "USDT" => ("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
        "WETH" | "ETH" => (WETH_MINT, 8),
        "WBTC" | "BTC" => (WBTC_MINT, 8),
        "JLP" => (JLP_MINT, 6),
        _ => (SOL_MINT, 9),
    };

    let (api_path, body, description) = if params.operation == "add" {
        // amount = deposit token amount; use that token's decimals
        let amount_lamports = (amount * 10f64.powi(token_decimals as i32)) as u64;
        let desc = format!("Add {} {} to Jupiter JLP pool", amount, token_sym.to_uppercase());
        let req_body = serde_json::json!({
            "wallet": user_pubkey,
            "mint": token_mint,
            "amount": amount_lamports.to_string(),
            "slippageBps": 100,
        });
        (format!("{}/add-liquidity", JUPITER_PERP_API), req_body, desc)
    } else {
        // amount = JLP tokens to burn (JLP has 6 decimals, independent of the receive token)
        const JLP_DECIMALS: u8 = 6;
        let jlp_lamports = (amount * 10f64.powi(JLP_DECIMALS as i32)) as u64;
        let desc = format!(
            "Remove {} JLP from Jupiter pool, receive {}",
            amount,
            token_sym.to_uppercase()
        );
        let req_body = serde_json::json!({
            "wallet": user_pubkey,
            "mint": token_mint,      // token to receive
            "amount": jlp_lamports.to_string(), // JLP amount to burn (6 decimals)
            "slippageBps": 100,
        });
        (format!("{}/remove-liquidity", JUPITER_PERP_API), req_body, desc)
    };

    let mut req = http.post(&api_path).json(&body);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let response = req
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter Perp JLP API error: {}", e)))?;

    let status = response.status();
    let resp_json: serde_json::Value = response
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse JLP response: {}", e)))?;

    if !status.is_success() {
        let err_msg = resp_json
            .get("error")
            .and_then(|v| v.as_str())
            .or_else(|| resp_json.get("message").and_then(|v| v.as_str()))
            .unwrap_or("Unknown JLP API error");
        return Err(AppError::Internal(format!(
            "Jupiter JLP API error ({}): {}",
            status, err_msg
        )));
    }

    let transaction = resp_json
        .get("transaction")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    let action_type = format!("jlp_{}", params.operation);

    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type,
        description,
        estimated_fee: "10000".to_string(),
        estimated_refund: None,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings: vec![],
        requires_approval: true,
    };

    Ok(BuildResponse {
        preview,
        transaction,
        additional_signers_required: 0,
        execution_steps: None,
        quote: Some(resp_json),
        is_cross_chain: false,
        data: None,
    })
}

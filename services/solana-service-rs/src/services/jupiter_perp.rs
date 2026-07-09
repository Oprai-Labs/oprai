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

/// Jupiter Perps hosted API v2 — the endpoint the official `jup` CLI and the
/// jup.ag/perps frontend use to build open/close transactions.
/// Contract verified against jup-ag/cli `src/clients/PerpsClient.ts`:
///   POST /positions/increase  { asset, inputToken, inputTokenAmount, side,
///                               maxSlippageBps, leverage|sizeUsdDelta, walletAddress }
///                             → { positionPubkey, quote, serializedTxBase64, txMetadata }
///   POST /positions/decrease  { positionPubkey, receiveToken, entirePosition|sizeUsdDelta,
///                               maxSlippageBps } → { serializedTxBase64, ... }
///   GET  /positions?walletAddress=…            → { dataList, count }
/// `inputToken`/`receiveToken`/`asset` are SYMBOLS ("SOL"|"BTC"|"ETH"|"USDC"), NOT mints.
const JUPITER_PERPS_API: &str = "https://perps-api.jup.ag/v2";

/// Legacy JLP liquidity base (add/remove-liquidity). NOTE: JLP is a separate
/// surface and is not part of the v2 perps API above.
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

/// Market symbol the perps API expects: "SOL" | "BTC" | "ETH" (not "wETH"/"wBTC").
fn perp_asset_symbol(market: &str) -> &'static str {
    match market.to_uppercase().as_str() {
        "ETH" | "WETH" => "ETH",
        "BTC" | "WBTC" => "BTC",
        _ => "SOL",
    }
}

/// Determine the collateral token SYMBOL + decimals the user deposits.
/// The perps API takes a symbol ("SOL"|"BTC"|"ETH"|"USDC"), not a mint.
/// - Long positions default to the base token (SOL for SOL-PERP, etc.)
/// - Short positions default to USDC
fn collateral_symbol_and_decimals(asset: &str, side: &str, collateral_token: Option<&str>) -> (&'static str, u8) {
    if let Some(token) = collateral_token {
        return match token.to_uppercase().as_str() {
            "USDC" | "USDT" => ("USDC", 6),
            "WETH" | "ETH" => ("ETH", 8),
            "WBTC" | "BTC" => ("BTC", 8),
            _ => ("SOL", 9),
        };
    }
    match side {
        "short" => ("USDC", 6),
        _ => match asset {
            "ETH" => ("ETH", 8),
            "BTC" => ("BTC", 8),
            _ => ("SOL", 9),
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
    if params.operation != "open" && params.operation != "close" {
        return Err(AppError::InvalidParams(
            "operation must be 'open' or 'close'".into(),
        ));
    }

    // Collateral amount is required only when OPENING. Closing defaults to
    // closing the entire position, so an empty/zero collateral is acceptable
    // (a partial close can instead be driven by an explicit USD size).
    if params.operation == "open" {
        let amount: f64 = params
            .collateral_amount
            .parse()
            .map_err(|_| AppError::InvalidParams("collateralAmount must be a number".into()))?;
        if amount <= 0.0 {
            return Err(AppError::InvalidParams(
                "collateralAmount must be positive when opening a position (e.g. \"2\" for 2 SOL).".into(),
            ));
        }
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

/// Attach the identifying + (optional) auth headers every perps-api call needs.
fn perps_headers(req: reqwest::RequestBuilder, jupiter_api_key: Option<&str>) -> reqwest::RequestBuilder {
    let req = req.header("x-client-platform", "oprai");
    match jupiter_api_key {
        Some(key) => req.header("x-api-key", key),
        None => req,
    }
}

/// Surface a clean, human message from a perps-api error body
/// (`{ "code": "...", "message": "..." }`).
fn perp_api_error(status: reqwest::StatusCode, body: &serde_json::Value) -> AppError {
    let msg = body
        .get("message")
        .and_then(|v| v.as_str())
        .or_else(|| body.get("error").and_then(|v| v.as_str()))
        .unwrap_or("Jupiter Perps request failed");
    AppError::Internal(format!("Jupiter Perps API error ({status}): {msg}"))
}

/// Fetch the raw open positions for a wallet from the perps API.
async fn fetch_perp_positions(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    wallet: &str,
) -> Result<Vec<serde_json::Value>, AppError> {
    let url = format!("{JUPITER_PERPS_API}/positions?walletAddress={wallet}");
    let resp = perps_headers(http.get(&url), jupiter_api_key)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter Perps positions request failed: {e}")))?;
    let status = resp.status();
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse Jupiter Perps positions: {e}")))?;
    if !status.is_success() {
        return Err(perp_api_error(status, &data));
    }
    Ok(data
        .get("dataList")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default())
}

/// Submit a user-signed perp transaction to Jupiter's execute endpoint.
///
/// Jupiter perps transactions require keeper signatures (the `perpSnt…`
/// sentinel + a per-request signer) that only Jupiter's backend can add — a
/// direct RPC submit of the wallet-signed tx would be rejected as missing
/// signatures and never land. The correct flow is: build → user signs →
/// POST the signed tx here → Jupiter fills the remaining signatures, submits,
/// and returns the on-chain `txid`.
///
/// `action` is Jupiter's action tag ("increase-position" | "decrease-position").
pub async fn execute_perp_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    action: &str,
    signed_tx_base64: &str,
) -> Result<serde_json::Value, AppError> {
    let url = format!("{JUPITER_PERPS_API}/transaction/execute");
    let body = serde_json::json!({
        "action": action,
        "serializedTxBase64": signed_tx_base64,
    });
    let response = perps_headers(http.post(&url).json(&body), jupiter_api_key)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter Perps execute request failed: {e}")))?;

    let status = response.status();
    let resp_json: serde_json::Value = response
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse Jupiter Perps execute response: {e}")))?;

    if !status.is_success() {
        return Err(perp_api_error(status, &resp_json));
    }

    let txid = resp_json
        .get("txid")
        .and_then(|v| v.as_str())
        .or_else(|| resp_json.get("signature").and_then(|v| v.as_str()));
    match txid {
        Some(id) => Ok(serde_json::json!({ "txid": id, "signature": id })),
        None => Err(AppError::Internal(
            "Jupiter Perps execute returned no transaction id".into(),
        )),
    }
}

/// Build a Jupiter Perpetuals open/close position transaction via the hosted
/// perps API. Returns an unsigned base64 transaction the wallet then signs.
pub async fn build_perp_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &JupiterPerpParams,
) -> Result<BuildResponse, AppError> {
    validate_perp_params(params)?;

    let asset = perp_asset_symbol(&params.market); // "SOL" | "BTC" | "ETH"
    let slippage_bps = params.slippage_bps.unwrap_or(200);

    let (url, body, description) = if params.operation == "open" {
        let amount: f64 = params.collateral_amount.parse().unwrap_or(0.0);
        let leverage: f64 = params
            .leverage
            .as_ref()
            .and_then(|l| l.parse().ok())
            .unwrap_or(2.0);

        // Collateral the user deposits — a symbol + its decimals for raw scaling.
        let (input_token, decimals) =
            collateral_symbol_and_decimals(asset, &params.side, params.collateral_token.as_deref());
        let input_token_amount = (amount * 10f64.powi(decimals as i32)).round() as u64;

        // Sizing: exactly ONE of leverage / sizeUsdDelta (the API rejects both).
        // Prefer an explicit USD size when the caller supplied one.
        let explicit_size_usd: Option<f64> = params
            .size_usd
            .as_ref()
            .and_then(|s| s.parse().ok())
            .filter(|s| *s > 0.0);

        let mut req_body = serde_json::json!({
            "asset": asset,
            "inputToken": input_token,
            "inputTokenAmount": input_token_amount.to_string(),
            "side": params.side,
            "maxSlippageBps": slippage_bps.to_string(),
            "walletAddress": user_pubkey,
        });
        let size_desc = if let Some(size_usd) = explicit_size_usd {
            let micro_usd = (size_usd * 1_000_000.0).round() as u64;
            req_body["sizeUsdDelta"] = serde_json::json!(micro_usd.to_string());
            format!("~${size_usd:.0} size")
        } else {
            req_body["leverage"] = serde_json::json!(leverage.to_string());
            format!("{leverage}x leverage")
        };

        let desc = format!(
            "Open {asset} {} position on Jupiter Perps ({size_desc}, {} {input_token} collateral)",
            params.side, params.collateral_amount
        );
        (format!("{JUPITER_PERPS_API}/positions/increase"), req_body, desc)
    } else {
        // Close: the perps API decreases a position identified by its pubkey.
        // Resolve the pubkey from the wallet's open positions by asset + side.
        let positions = fetch_perp_positions(http, jupiter_api_key, user_pubkey).await?;
        let position = positions.iter().find(|p| {
            let p_asset = p.get("asset").and_then(|v| v.as_str()).unwrap_or("");
            let p_side = p.get("side").and_then(|v| v.as_str()).unwrap_or("");
            p_asset.eq_ignore_ascii_case(asset) && p_side.eq_ignore_ascii_case(&params.side)
        });
        let position = position.ok_or_else(|| {
            AppError::InvalidParams(format!(
                "No open {asset} {} position found to close.",
                params.side
            ))
        })?;
        let position_pubkey = position
            .get("positionPubkey")
            .and_then(|v| v.as_str())
            .ok_or_else(|| AppError::Internal("Position is missing its pubkey".into()))?;

        // Receive collateral back in the deposited token (symbol).
        let (receive_token, _dec) =
            collateral_symbol_and_decimals(asset, &params.side, params.collateral_token.as_deref());

        // Default to a full close; an explicit USD size performs a partial close.
        let partial_size_usd: Option<f64> = params
            .size_usd
            .as_ref()
            .and_then(|s| s.parse().ok())
            .filter(|s| *s > 0.0);

        let mut req_body = serde_json::json!({
            "positionPubkey": position_pubkey,
            "receiveToken": receive_token,
            "maxSlippageBps": slippage_bps.to_string(),
        });
        let close_desc = if let Some(size_usd) = partial_size_usd {
            let micro_usd = (size_usd * 1_000_000.0).round() as u64;
            req_body["sizeUsdDelta"] = serde_json::json!(micro_usd.to_string());
            format!("reduce ~${size_usd:.0}")
        } else {
            req_body["entirePosition"] = serde_json::json!(true);
            "full close".to_string()
        };

        let desc = format!(
            "Close {asset} {} position on Jupiter Perps ({close_desc})",
            params.side
        );
        (format!("{JUPITER_PERPS_API}/positions/decrease"), req_body, desc)
    };

    let response = perps_headers(http.post(&url).json(&body), jupiter_api_key)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter Perps request failed: {e}")))?;

    let status = response.status();
    let resp_json: serde_json::Value = response
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse Jupiter Perps response: {e}")))?;

    if !status.is_success() {
        return Err(perp_api_error(status, &resp_json));
    }

    // The perps API returns the unsigned tx under `serializedTxBase64`.
    let transaction = resp_json
        .get("serializedTxBase64")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    if transaction.is_none() {
        return Err(AppError::Internal(
            "Jupiter Perps did not return a transaction to sign".into(),
        ));
    }

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

/// Fetch open perpetual positions for a wallet from the Jupiter Perps API.
pub async fn build_perp_positions(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    wallet: &str,
) -> Result<BuildResponse, AppError> {
    let positions = fetch_perp_positions(http, jupiter_api_key, wallet).await?;
    let count = positions.len();

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "perp_positions".to_string(),
            description: format!("{count} open position(s) on Jupiter Perps for {}", &wallet[..8.min(wallet.len())]),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!({ "dataList": positions, "count": count }),
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

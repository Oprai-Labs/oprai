use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::tokens::{get_token_info, resolve_token_address};

const JUPITER_TRIGGER_BASE: &str = "https://api.jup.ag/trigger/v1";
const JUPITER_PRICE_API: &str = "https://api.jup.ag/price/v3";
const MIN_ORDER_USD: f64 = 5.0;

/// Fetch the USD price of a token by mint address via Jupiter Price API v2.
/// Returns None if price cannot be determined (non-fatal — skip validation).
async fn fetch_token_price_usd(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    mint: &str,
) -> Option<f64> {
    let url = format!("{JUPITER_PRICE_API}?ids={mint}");
    let mut req = http.get(&url);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req.send().await.ok()?;
    let data: serde_json::Value = resp.json().await.ok()?;
    data["data"][mint]["price"]
        .as_str()
        .and_then(|s| s.parse::<f64>().ok())
        .or_else(|| data["data"][mint]["price"].as_f64())
}

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/// Limit order creation parameters.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LimitOrderParams {
    /// Input token symbol (e.g. "SOL") or mint address.
    pub input_mint: String,
    /// Output token symbol (e.g. "USDC") or mint address.
    pub output_mint: String,
    /// Human-readable input amount (e.g. "1.5").
    pub amount: String,
    /// Target price: how many output tokens per input token.
    /// e.g. if selling SOL for USDC at $200, target_price = "200"
    pub target_price: String,
    /// Optional slippage in basis points (default: 50 = 0.5%).
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub slippage_bps: Option<u32>,
    /// Optional order expiry in seconds from now. None = no expiry.
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub expiry_seconds: Option<u64>,
}

/// Parameters for cancelling a single limit order.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CancelLimitOrderParams {
    /// On-chain order account pubkey (returned by createOrder).
    pub order: String,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_limit_order_params(params: &LimitOrderParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }

    let target_price: f64 = params
        .target_price
        .parse()
        .map_err(|_| AppError::InvalidParams("Target price must be a positive number".into()))?;
    if target_price <= 0.0 {
        return Err(AppError::InvalidParams(
            "Target price must be positive".into(),
        ));
    }

    if let Some(slippage) = params.slippage_bps {
        if slippage > 5000 {
            return Err(AppError::InvalidParams(
                "Slippage must be between 0 and 50%".into(),
            ));
        }
    }

    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Create limit order
// ──────────────────────────────────────────────────────────────────────────────

/// Build a limit order transaction via Jupiter Trigger API.
///
/// Returns a base64-encoded transaction for the frontend to sign and submit.
/// The `makingAmount` / `takingAmount` encoding sets the price target:
/// - makingAmount = input_amount * 10^input_decimals
/// - takingAmount = input_amount * target_price * 10^output_decimals
pub async fn create_limit_order_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &LimitOrderParams,
) -> Result<BuildResponse, AppError> {
    validate_limit_order_params(params)?;

    let input_mint = resolve_token_address(&params.input_mint);
    let output_mint = resolve_token_address(&params.output_mint);

    let input_token = get_token_info(&params.input_mint);
    let output_token = get_token_info(&params.output_mint);
    let input_decimals = input_token.map(|t| t.decimals).unwrap_or(9);
    let output_decimals = output_token.map(|t| t.decimals).unwrap_or(6);
    let input_symbol = input_token
        .map(|t| t.symbol.to_string())
        .unwrap_or_else(|| input_mint[..8.min(input_mint.len())].to_string());
    let output_symbol = output_token
        .map(|t| t.symbol.to_string())
        .unwrap_or_else(|| output_mint[..8.min(output_mint.len())].to_string());

    let amount_float: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?;
    let target_price_float: f64 = params
        .target_price
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid target price".into()))?;

    // makingAmount = how many input tokens to sell (in smallest unit)
    let making_amount = (amount_float * 10_f64.powi(input_decimals as i32)) as u64;
    // takingAmount = how many output tokens we expect at least (in smallest unit)
    let taking_amount =
        (amount_float * target_price_float * 10_f64.powi(output_decimals as i32)) as u64;

    // Pre-validate minimum order size ($5 USD) using current token price.
    if let Some(price_usd) = fetch_token_price_usd(http, jupiter_api_key, &input_mint).await {
        let order_value_usd = amount_float * price_usd;
        if order_value_usd < MIN_ORDER_USD {
            let min_amount = MIN_ORDER_USD / price_usd;
            return Err(AppError::InvalidParams(format!(
                "Order size too small: ${order_value_usd:.2} USD. \
                 Minimum is $5 USD. \
                 At the current {input_symbol} price of ${price_usd:.2}, \
                 you need at least {min_amount:.4} {input_symbol}."
            )));
        }
    }

    let mut order_params = json!({
        "makingAmount": making_amount.to_string(),
        "takingAmount": taking_amount.to_string(),
    });

    if let Some(expiry_secs) = params.expiry_seconds {
        let expiry_at = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs()
            + expiry_secs;
        order_params["expiredAt"] = json!(expiry_at.to_string());
    }

    let body = json!({
        "inputMint": input_mint,
        "outputMint": output_mint,
        "maker": user_pubkey,
        "payer": user_pubkey,
        "params": order_params,
    });

    let mut req = http
        .post(&format!("{JUPITER_TRIGGER_BASE}/createOrder"))
        .header("Content-Type", "application/json")
        .json(&body);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Jupiter Trigger API error: {e}")))?;

    if !resp.status().is_success() {
        let body_text = resp.text().await.unwrap_or_default();
        let msg = serde_json::from_str::<serde_json::Value>(&body_text)
            .ok()
            .and_then(|v| {
                v["error"]
                    .as_str()
                    .or_else(|| v["message"].as_str())
                    .map(|s| s.to_string())
            })
            .unwrap_or(body_text);
        return Err(AppError::InvalidParams(msg));
    }

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Parse error: {e}")))?;

    let tx_base64 = data["transaction"]
        .as_str()
        .ok_or_else(|| AppError::JupiterApiError("Missing transaction in response".into()))?
        .to_string();

    let out_amount_display = taking_amount as f64 / 10_f64.powi(output_decimals as i32);

    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "limit_order".to_string(),
        description: format!(
            "Limit order: sell {} {} → {:.6} {} (target: {} {}/{})",
            params.amount,
            input_symbol,
            out_amount_display,
            output_symbol,
            params.target_price,
            output_symbol,
            input_symbol,
        ),
        estimated_fee: "~0.002 SOL".to_string(),
        estimated_refund: None,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings: vec![],
        requires_approval: true,
    };

    Ok(BuildResponse {
        preview,
        transaction: Some(tx_base64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Cancel limit order(s)
// ──────────────────────────────────────────────────────────────────────────────

/// Cancel a single limit order via Jupiter Trigger API.
pub async fn cancel_limit_order_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &CancelLimitOrderParams,
) -> Result<BuildResponse, AppError> {
    let body = json!({
        "maker": user_pubkey,
        "order": params.order,
        "computeUnitPrice": "auto",
    });

    let mut req = http
        .post(&format!("{JUPITER_TRIGGER_BASE}/cancelOrder"))
        .header("Content-Type", "application/json")
        .json(&body);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Cancel order API error: {e}")))?;

    if !resp.status().is_success() {
        let body_text = resp.text().await.unwrap_or_default();
        let msg = serde_json::from_str::<serde_json::Value>(&body_text)
            .ok()
            .and_then(|v| {
                v["error"]
                    .as_str()
                    .or_else(|| v["message"].as_str())
                    .map(|s| s.to_string())
            })
            .unwrap_or(body_text);
        return Err(AppError::InvalidParams(msg));
    }

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Parse error: {e}")))?;

    let tx_base64 = data["transaction"]
        .as_str()
        .ok_or_else(|| AppError::JupiterApiError("Missing transaction in cancel response".into()))?
        .to_string();

    let short_order = &params.order[..8.min(params.order.len())];
    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "cancel_limit_order".to_string(),
        description: format!("Cancel limit order {}...", short_order),
        estimated_fee: "~0.001 SOL".to_string(),
        estimated_refund: None,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings: vec![],
        requires_approval: true,
    };

    Ok(BuildResponse {
        preview,
        transaction: Some(tx_base64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Cancel all limit orders
// ──────────────────────────────────────────────────────────────────────────────

/// Cancel all open limit orders for the user.
///
/// Fetches open orders first, then cancels each one and returns all transactions.
/// Returns an error if the user has no open orders.
pub async fn cancel_all_limit_orders_transactions(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
) -> Result<Vec<BuildResponse>, AppError> {
    let orders = get_limit_orders(http, jupiter_api_key, user_pubkey, "open").await?;
    let order_list: Vec<serde_json::Value> =
        orders["orders"].as_array().cloned().unwrap_or_default();

    if order_list.is_empty() {
        return Err(AppError::InvalidParams(
            "No open limit orders found to cancel.".into(),
        ));
    }

    let mut results = Vec::new();
    for order in &order_list {
        let order_key = order["orderKey"]
            .as_str()
            .or_else(|| order["publicKey"].as_str())
            .unwrap_or_default()
            .to_string();
        if order_key.is_empty() {
            continue;
        }
        let params = CancelLimitOrderParams { order: order_key };
        match cancel_limit_order_transaction(http, jupiter_api_key, user_pubkey, &params).await {
            Ok(resp) => results.push(resp),
            Err(e) => tracing::warn!("Failed to cancel order {}: {e}", params.order),
        }
    }

    if results.is_empty() {
        return Err(AppError::InvalidParams(
            "Failed to build cancel transactions for all orders.".into(),
        ));
    }

    Ok(results)
}

/// Pack multiple cancel responses into a single BuildResponse.
///
/// First tx goes into `transaction`, the rest into `execution_steps` as
/// `{ remaining_transactions: [{ transaction, description }] }`.
/// The frontend handles the sequence for `cancel_all_limit_orders`.
pub fn pack_cancel_all_response(mut responses: Vec<BuildResponse>) -> BuildResponse {
    let count = responses.len();
    let first = responses.remove(0);
    let remaining: Vec<serde_json::Value> = responses
        .into_iter()
        .map(|r| {
            json!({
                "transaction": r.transaction,
                "description": r.preview.description,
            })
        })
        .collect();

    let execution_steps = if remaining.is_empty() {
        None
    } else {
        Some(json!({ "remaining_transactions": remaining }))
    };

    let preview = ActionPreview {
        id: first.preview.id,
        action_type: "cancel_all_limit_orders".to_string(),
        description: format!("Cancel {} open limit orders", count),
        estimated_fee: format!("~{:.4} SOL", 0.001 * count as f64),
        estimated_refund: None,
        params: json!({}),
        warnings: vec![],
        requires_approval: true,
    };

    BuildResponse {
        preview,
        transaction: first.transaction,
        additional_signers_required: 0,
        execution_steps,
        quote: None,
        is_cross_chain: false,
        data: None,
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// ──────────────────────────────────────────────────────────────────────────────
// GET Query: jup_limit_orders
// ──────────────────────────────────────────────────────────────────────────────

/// Params for querying limit orders.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupLimitOrdersParams {
    /// Filter by status: "open", "completed", or "cancelled". Default: "open".
    #[serde(default = "default_order_status")]
    pub status: String,
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Input token symbol or mint to filter by (optional).
    #[serde(default)]
    pub input_token: Option<String>,
    /// Output token symbol or mint to filter by (optional).
    #[serde(default)]
    pub output_token: Option<String>,
}

fn default_order_status() -> String {
    "open".to_string()
}

pub fn validate_jup_limit_orders_params(p: &JupLimitOrdersParams) -> Result<(), AppError> {
    const VALID: &[&str] = &["open", "completed", "cancelled"];
    if !VALID.contains(&p.status.as_str()) {
        return Err(AppError::InvalidParams(format!(
            "status must be one of: {}",
            VALID.join(", ")
        )));
    }
    Ok(())
}

/// Fetch and return limit orders as a BuildResponse (no transaction).
pub async fn build_jup_limit_orders(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    wallet: &str,
    params: &JupLimitOrdersParams,
) -> Result<BuildResponse, AppError> {
    validate_jup_limit_orders_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut data = get_limit_orders(http, jupiter_api_key, target, &params.status).await?;

    // Optional client-side filtering by input/output token
    if params.input_token.is_some() || params.output_token.is_some() {
        if let Some(orders) = data.get_mut("orders").and_then(|v| v.as_array_mut()) {
            orders.retain(|order| {
                let input_ok = params.input_token.as_ref().map_or(true, |tok| {
                    let resolved = crate::solana::tokens::resolve_token_address(tok.trim());
                    order
                        .get("inputMint")
                        .and_then(|v| v.as_str())
                        .map_or(false, |m| {
                            m == resolved || m.eq_ignore_ascii_case(tok.trim())
                        })
                });
                let output_ok = params.output_token.as_ref().map_or(true, |tok| {
                    let resolved = crate::solana::tokens::resolve_token_address(tok.trim());
                    order
                        .get("outputMint")
                        .and_then(|v| v.as_str())
                        .map_or(false, |m| {
                            m == resolved || m.eq_ignore_ascii_case(tok.trim())
                        })
                });
                input_ok && output_ok
            });
        }
    }

    let count = data
        .get("orders")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);

    let wallet_short = if target.len() > 8 {
        format!("{}...{}", &target[..4], &target[target.len() - 4..])
    } else {
        target.to_string()
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_limit_orders".into(),
            description: format!(
                "{count} {} limit order(s) for {wallet_short}",
                params.status
            ),
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
// Query open limit orders
// ──────────────────────────────────────────────────────────────────────────────

/// Fetch open (or historical) limit orders for a wallet.
///
/// `order_status` is one of: "open", "completed", "cancelled".
pub async fn get_limit_orders(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    order_status: &str,
) -> Result<serde_json::Value, AppError> {
    // Jupiter Trigger API uses "active"/"history", not "open"/"closed"
    let jup_status = if order_status == "open" {
        "active"
    } else {
        order_status
    };
    let url = format!(
        "{JUPITER_TRIGGER_BASE}/getTriggerOrders?user={user_pubkey}&orderStatus={jup_status}"
    );

    let mut req = http.get(&url);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Get limit orders error: {e}")))?;

    if !resp.status().is_success() {
        let status_code = resp.status();
        let body_text = resp.text().await.unwrap_or_default();
        tracing::warn!(
            "Jupiter Trigger API returned {status_code} for wallet {user_pubkey}: {body_text}"
        );
        // Treat non-2xx as empty orders (wallet may have no orders or API key issue)
        return Ok(serde_json::json!({ "orders": [] }));
    }

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Parse error: {e}")))?;

    Ok(data)
}

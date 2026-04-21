use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::tokens::{get_token_info, resolve_token_address};

const JUPITER_RECURRING_BASE: &str = "https://api.jup.ag/recurring/v1";

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/// DCA (Dollar Cost Averaging) order creation parameters.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DcaParams {
    /// Input token symbol or mint address.
    pub input_mint: String,
    /// Output token symbol or mint address.
    pub output_mint: String,
    /// Total input amount to spend across all cycles (human-readable, e.g. "10").
    pub total_amount: String,
    /// Number of DCA cycles (e.g. 10).
    pub number_of_orders: u32,
    /// Interval between cycles in seconds (e.g. 86400 for daily).
    pub interval_seconds: u64,
    /// Optional Unix timestamp to start. None = start immediately.
    #[serde(default)]
    pub start_at: Option<u64>,
    /// Optional minimum output amount per cycle (slippage floor). Human-readable.
    #[serde(default)]
    pub min_out_per_cycle: Option<String>,
    /// Optional maximum output amount per cycle (slippage ceiling). Human-readable.
    #[serde(default)]
    pub max_out_per_cycle: Option<String>,
}

/// Parameters for cancelling an active DCA order.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CancelDcaParams {
    /// On-chain DCA order account pubkey.
    pub order: String,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_dca_params(params: &DcaParams) -> Result<(), AppError> {
    let total: f64 = params
        .total_amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Total amount must be a positive number".into()))?;
    if total <= 0.0 {
        return Err(AppError::InvalidParams("Total amount must be positive".into()));
    }

    if params.number_of_orders == 0 {
        return Err(AppError::InvalidParams(
            "Number of orders must be at least 1".into(),
        ));
    }
    if params.number_of_orders > 1000 {
        return Err(AppError::InvalidParams(
            "Number of orders cannot exceed 1000".into(),
        ));
    }

    if params.interval_seconds == 0 {
        return Err(AppError::InvalidParams(
            "Interval must be at least 1 second".into(),
        ));
    }

    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Create DCA order
// ──────────────────────────────────────────────────────────────────────────────

/// Human-readable interval description.
fn interval_label(secs: u64) -> &'static str {
    match secs {
        60 => "every minute",
        3600 => "hourly",
        86400 => "daily",
        604800 => "weekly",
        2592000 => "monthly",
        _ => "on schedule",
    }
}

/// Build a DCA order transaction via Jupiter Recurring API.
///
/// The `inAmount` param to Jupiter is the *total* amount; `inAmountPerCycle` is
/// derived as `inAmount / numberOfOrders`.
pub async fn create_dca_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &DcaParams,
) -> Result<BuildResponse, AppError> {
    validate_dca_params(params)?;

    let input_mint = resolve_token_address(&params.input_mint);
    let output_mint = resolve_token_address(&params.output_mint);

    let input_token = get_token_info(&params.input_mint);
    let output_token = get_token_info(&params.output_mint);
    let input_decimals = input_token.map(|t| t.decimals).unwrap_or(9) as i32;
    let output_decimals = output_token.map(|t| t.decimals).unwrap_or(6) as i32;
    let input_symbol = input_token
        .map(|t| t.symbol.to_string())
        .unwrap_or_else(|| input_mint[..8.min(input_mint.len())].to_string());
    let output_symbol = output_token
        .map(|t| t.symbol.to_string())
        .unwrap_or_else(|| output_mint[..8.min(output_mint.len())].to_string());

    let total_float: f64 = params.total_amount.parse::<f64>().map_err(|_| AppError::InvalidParams("Invalid total_amount".into()))?;
    let per_cycle_float = total_float / params.number_of_orders as f64;
    // Jupiter Recurring API expects the per-cycle input amount, not the total.
    let in_amount_per_cycle = (per_cycle_float * 10_f64.powi(input_decimals)) as u64;

    // Build optional min/max per-cycle amounts.
    let min_out_per_cycle: Option<u64> = params
        .min_out_per_cycle
        .as_deref()
        .and_then(|v| v.parse::<f64>().ok())
        .map(|v| (v * 10_f64.powi(output_decimals)) as u64);

    let max_out_per_cycle: Option<u64> = params
        .max_out_per_cycle
        .as_deref()
        .and_then(|v| v.parse::<f64>().ok())
        .map(|v| (v * 10_f64.powi(output_decimals)) as u64);

    let mut time_params = json!({
        "inAmountPerCycle": in_amount_per_cycle,
        "numberOfOrders": params.number_of_orders,
        "interval": params.interval_seconds,
    });

    if let Some(start) = params.start_at {
        time_params["startAt"] = json!(start);
    }
    if let Some(min) = min_out_per_cycle {
        time_params["minOutAmount"] = json!(min);
    }
    if let Some(max) = max_out_per_cycle {
        time_params["maxOutAmount"] = json!(max);
    }

    let body = json!({
        "user": user_pubkey,
        "inputMint": input_mint,
        "outputMint": output_mint,
        "params": {
            "time": time_params,
        },
    });

    let mut req = http
        .post(&format!("{JUPITER_RECURRING_BASE}/createOrder"))
        .header("Content-Type", "application/json")
        .json(&body);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Jupiter Recurring API error: {e}")))?;

    if !resp.status().is_success() {
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::JupiterApiError(format!(
            "DCA order creation failed: {body_text}"
        )));
    }

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Parse error: {e}")))?;

    let tx_base64 = data["transaction"]
        .as_str()
        .ok_or_else(|| AppError::JupiterApiError("Missing transaction in DCA response".into()))?
        .to_string();

    let freq_label = interval_label(params.interval_seconds);

    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "dca".to_string(),
        description: format!(
            "DCA: spend {} {} total → buy {} {}, {:.4} {} per cycle × {} cycles",
            params.total_amount,
            input_symbol,
            output_symbol,
            freq_label,
            per_cycle_float,
            input_symbol,
            params.number_of_orders,
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
// Cancel DCA order
// ──────────────────────────────────────────────────────────────────────────────

/// Cancel an active DCA order via Jupiter Recurring API.
pub async fn cancel_dca_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &CancelDcaParams,
) -> Result<BuildResponse, AppError> {
    let body = json!({
        "order": params.order,
        "user": user_pubkey,
        "recurringType": "time",
    });

    let mut req = http
        .post(&format!("{JUPITER_RECURRING_BASE}/cancelOrder"))
        .header("Content-Type", "application/json")
        .json(&body);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Cancel DCA API error: {e}")))?;

    if !resp.status().is_success() {
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::JupiterApiError(format!(
            "Cancel DCA failed: {body_text}"
        )));
    }

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Parse error: {e}")))?;

    let tx_base64 = data["transaction"]
        .as_str()
        .ok_or_else(|| AppError::JupiterApiError("Missing transaction in cancel DCA response".into()))?
        .to_string();

    let short_order = &params.order[..8.min(params.order.len())];
    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "cancel_dca".to_string(),
        description: format!("Cancel DCA order {}...", short_order),
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
// Query DCA orders
// ──────────────────────────────────────────────────────────────────────────────

// ──────────────────────────────────────────────────────────────────────────────
// GET Query: jup_dca_orders
// ──────────────────────────────────────────────────────────────────────────────

/// Params for querying DCA orders.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupDcaOrdersParams {
    /// Filter by status: "open" (active) or "closed" (history). Default: "open".
    #[serde(default = "default_dca_status")]
    pub status: String,
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
}

fn default_dca_status() -> String {
    "open".to_string()
}

pub fn validate_jup_dca_orders_params(p: &JupDcaOrdersParams) -> Result<(), AppError> {
    if p.status != "open" && p.status != "closed" {
        return Err(AppError::InvalidParams(
            "status must be \"open\" or \"closed\"".into(),
        ));
    }
    Ok(())
}

/// Fetch and return DCA orders as a BuildResponse (no transaction).
pub async fn build_jup_dca_orders(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    wallet: &str,
    params: &JupDcaOrdersParams,
) -> Result<BuildResponse, AppError> {
    validate_jup_dca_orders_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let data = get_dca_orders(http, jupiter_api_key, target, &params.status).await?;

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
            action_type: "jup_dca_orders".into(),
            description: format!(
                "{count} {} DCA order(s) for {wallet_short}",
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

/// Fetch active or historical DCA orders for a wallet.
///
/// `recurring_type` is "time" (price-based orders are deprecated).
/// `status` is "open" or "closed".
pub async fn get_dca_orders(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    status: &str,
) -> Result<serde_json::Value, AppError> {
    // Jupiter Recurring API uses "active"/"history", not "open"/"closed"
    let jup_status = if status == "open" { "active" } else { status };
    let url = format!(
        "{JUPITER_RECURRING_BASE}/getRecurringOrders?user={user_pubkey}&orderStatus={jup_status}&recurringType=time&includeFailedTx=false"
    );

    let mut req = http.get(&url);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Get DCA orders error: {e}")))?;

    if !resp.status().is_success() {
        let status_code = resp.status();
        let body_text = resp.text().await.unwrap_or_default();
        tracing::warn!("Jupiter Recurring API returned {status_code} for wallet {user_pubkey}: {body_text}");
        // Treat non-2xx as empty orders (wallet may have no orders or API key issue)
        return Ok(serde_json::json!({ "orders": [] }));
    }

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Parse error: {e}")))?;

    Ok(data)
}

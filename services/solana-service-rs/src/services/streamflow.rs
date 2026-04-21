//! Streamflow Token Streaming Service
//!
//! Transaction building is delegated to the TypeScript solana-service which
//! uses the official `@streamflow/stream` SDK (JS-only).
//!
//! The Rust service proxies all Streamflow build/read requests to the TS service
//! via HTTP. Configure STREAMFLOW_SDK_URL (default: http://localhost:3031).
//!
//! Supported actions: create, cancel, withdraw, transfer, topup, update,
//!                    create_multiple, get_one, list

use serde::{Deserialize, Serialize};
use reqwest::Client;
use uuid::Uuid;
use crate::error::AppError;

// ─────────────────────────────────────────────────────────────────────────────
// Input param types (deserialized from gateway request)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StreamflowCreateParams {
    pub recipient: String,
    pub mint: String,
    pub amount: String,
    pub period: u64,
    pub amount_per_period: String,
    pub start: Option<u64>,
    pub cliff: Option<u64>,
    pub cliff_amount: Option<String>,
    pub name: Option<String>,
    pub can_topup: Option<bool>,
    pub cancelable_by_sender: Option<bool>,
    pub cancelable_by_recipient: Option<bool>,
    pub transferable_by_sender: Option<bool>,
    pub transferable_by_recipient: Option<bool>,
    pub automatic_withdrawal: Option<bool>,
    pub withdrawal_frequency: Option<u64>,
    pub partner: Option<String>,
    pub is_native: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StreamflowRecipient {
    pub recipient: String,
    pub amount: String,
    pub name: Option<String>,
    pub cliff_amount: Option<String>,
    pub amount_per_period: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StreamflowCreateMultipleParams {
    pub recipients: Vec<StreamflowRecipient>,
    pub mint: String,
    pub period: u64,
    pub start: Option<u64>,
    pub cliff: Option<u64>,
    pub can_topup: Option<bool>,
    pub cancelable_by_sender: Option<bool>,
    pub cancelable_by_recipient: Option<bool>,
    pub transferable_by_sender: Option<bool>,
    pub transferable_by_recipient: Option<bool>,
    pub automatic_withdrawal: Option<bool>,
    pub withdrawal_frequency: Option<u64>,
    pub partner: Option<String>,
    pub is_native: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamflowCancelParams {
    pub stream_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamflowWithdrawParams {
    pub stream_id: String,
    pub amount: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamflowTransferParams {
    pub stream_id: String,
    pub new_recipient: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamflowTopupParams {
    pub stream_id: String,
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamflowUpdateParams {
    pub stream_id: String,
    pub automatic_withdrawal: Option<bool>,
    pub withdrawal_frequency: Option<u64>,
    pub transferable_by_sender: Option<bool>,
    pub transferable_by_recipient: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamflowGetOneParams {
    pub stream_id: String,
}

// ─────────────────────────────────────────────────────────────────────────────
// Output types (returned from TS service proxy)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StreamflowBuildResult {
    pub transaction: Option<String>,
    pub transactions: Option<Vec<String>>,
    pub preview: StreamflowPreview,
    pub additional_signers_required: Option<usize>,
    pub is_cross_chain: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StreamflowPreview {
    pub id: String,
    #[serde(rename = "type", alias = "action_type")]
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    pub params: serde_json::Value,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

// ─────────────────────────────────────────────────────────────────────────────
// Proxy helper
// ─────────────────────────────────────────────────────────────────────────────

fn sdk_url() -> String {
    std::env::var("STREAMFLOW_SDK_URL").unwrap_or_else(|_| "http://localhost:3031".into())
}

/// POST to TS solana-service /actions/build with the given action type and params.
/// Returns raw JSON response which is converted to StreamflowBuildResult.
async fn proxy_build(
    http: &Client,
    user_wallet: &str,
    action_type: &str,
    params: serde_json::Value,
) -> Result<StreamflowBuildResult, AppError> {
    let internal_key = std::env::var("OPRAI_INTERNAL_API_KEY").unwrap_or_default();
    let body = serde_json::json!({
        "type": action_type,
        "params": params,
    });

    let resp = http
        .post(format!("{}/actions/build", sdk_url()))
        .header("x-internal-api-key", &internal_key)
        .header("x-user-wallet", user_wallet)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Streamflow SDK proxy error: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let err = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "Streamflow SDK service returned {status}: {err}"
        )));
    }

    resp.json::<StreamflowBuildResult>()
        .await
        .map_err(|e| AppError::Internal(format!("Streamflow SDK response parse error: {e}")))
}

// ─────────────────────────────────────────────────────────────────────────────
// Build functions — proxied to TS SDK service
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_create_stream(
    http: &Client,
    sender: &str,
    params: &StreamflowCreateParams,
) -> Result<StreamflowBuildResult, AppError> {
    let v = serde_json::to_value(params)
        .map_err(|e| AppError::Internal(format!("Streamflow serialize error: {e}")))?;
    proxy_build(http, sender, "streamflow_create", v).await
}

pub async fn build_create_multiple_streams(
    http: &Client,
    sender: &str,
    params: &StreamflowCreateMultipleParams,
) -> Result<StreamflowBuildResult, AppError> {
    let v = serde_json::to_value(params)
        .map_err(|e| AppError::Internal(format!("Streamflow serialize error: {e}")))?;
    proxy_build(http, sender, "streamflow_create_multiple", v).await
}

pub async fn build_cancel_stream(
    http: &Client,
    sender: &str,
    params: &StreamflowCancelParams,
) -> Result<StreamflowBuildResult, AppError> {
    let v = serde_json::json!({ "stream_id": params.stream_id });
    proxy_build(http, sender, "streamflow_cancel", v).await
}

pub async fn build_withdraw_stream(
    http: &Client,
    invoker: &str,
    params: &StreamflowWithdrawParams,
) -> Result<StreamflowBuildResult, AppError> {
    let mut v = serde_json::json!({ "stream_id": params.stream_id });
    if let Some(ref amt) = params.amount {
        v["amount"] = serde_json::Value::String(amt.clone());
    }
    proxy_build(http, invoker, "streamflow_withdraw", v).await
}

pub async fn build_transfer_stream(
    http: &Client,
    sender: &str,
    params: &StreamflowTransferParams,
) -> Result<StreamflowBuildResult, AppError> {
    let v = serde_json::json!({
        "stream_id": params.stream_id,
        "new_recipient": params.new_recipient,
    });
    proxy_build(http, sender, "streamflow_transfer", v).await
}

pub async fn build_topup_stream(
    http: &Client,
    sender: &str,
    params: &StreamflowTopupParams,
) -> Result<StreamflowBuildResult, AppError> {
    let v = serde_json::json!({
        "stream_id": params.stream_id,
        "amount": params.amount,
    });
    proxy_build(http, sender, "streamflow_topup", v).await
}

pub async fn build_update_stream(
    http: &Client,
    sender: &str,
    params: &StreamflowUpdateParams,
) -> Result<StreamflowBuildResult, AppError> {
    let v = serde_json::to_value(params)
        .map_err(|e| AppError::Internal(format!("Streamflow serialize error: {e}")))?;
    proxy_build(http, sender, "streamflow_update", v).await
}

pub async fn get_stream_by_id(
    http: &Client,
    params: &StreamflowGetOneParams,
) -> Result<StreamflowBuildResult, AppError> {
    let v = serde_json::json!({ "stream_id": params.stream_id });
    // Use a placeholder wallet for read-only calls
    proxy_build(http, "readonly", "streamflow_get_one", v).await
}

pub async fn list_streams(
    http: &Client,
    wallet: &str,
    direction: Option<&str>,
) -> Result<StreamflowBuildResult, AppError> {
    let v = serde_json::json!({ "direction": direction.unwrap_or("all") });
    proxy_build(http, wallet, "streamflow_list", v).await
}

// ─────────────────────────────────────────────────────────────────────────────
// Fallback preview for when SDK service is unavailable
// ─────────────────────────────────────────────────────────────────────────────

pub fn unavailable_preview(action_type: &str) -> StreamflowBuildResult {
    StreamflowBuildResult {
        transaction: None,
        transactions: None,
        preview: StreamflowPreview {
            id: format!("sf_unavail_{}", Uuid::new_v4()),
            action_type: action_type.to_string(),
            description: format!(
                "Streamflow SDK service unavailable. Ensure solana-service-ts is running on {}/.",
                sdk_url(),
            ),
            estimated_fee: "0".into(),
            params: serde_json::Value::Null,
            warnings: vec![
                "Set STREAMFLOW_SDK_URL env var if running on non-default port.".into(),
            ],
            requires_approval: false,
        },
        additional_signers_required: Some(0),
        is_cross_chain: Some(false),
        data: None,
    }
}

//! Read-only protocol queries that delegate to the TS service.
//!
//! Each function builds an `/actions/build` request against the configured
//! TS SDK endpoint (re-using the same `STREAMFLOW_SDK_URL` env var since
//! both services run side-by-side) and returns the SDK's response as a
//! generic `BuildResponse`.
//!
//! New read-only protocols (MarginFi balances, future ones) should
//! be added here rather than reimplementing the proxy pattern per file.

use reqwest::Client;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ProxyResponse {
    preview: ProxyPreview,
    #[serde(default)]
    additional_signers_required: usize,
    #[serde(default)]
    is_cross_chain: bool,
    #[serde(default)]
    data: serde_json::Value,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ProxyPreview {
    #[serde(default)]
    id: String,
    #[serde(rename = "type", default)]
    action_type: String,
    #[serde(default)]
    description: String,
    #[serde(default)]
    estimated_fee: String,
    #[serde(default)]
    params: serde_json::Value,
    #[serde(default)]
    warnings: Vec<String>,
    #[serde(default)]
    requires_approval: bool,
}

fn sdk_url() -> String {
    std::env::var("STREAMFLOW_SDK_URL").unwrap_or_else(|_| "http://localhost:3031".into())
}

/// POST a read-only action to the TS service and shape the response into a
/// builder-compatible `BuildResponse`.
async fn ts_proxy_read(
    http: &Client,
    user_wallet: &str,
    action_type: &str,
    params: serde_json::Value,
) -> Result<BuildResponse, AppError> {
    let internal_key = std::env::var("OPRAI_INTERNAL_API_KEY").unwrap_or_default();
    let body = serde_json::json!({
        "type": action_type,
        "params": params,
    });

    let resp = http
        .post(format!("{}/actions/build", sdk_url()))
        .header("x-internal-api-key", internal_key)
        .header("x-user-wallet", user_wallet)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("TS proxy error ({action_type}): {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let err = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "TS proxy {action_type} returned {status}: {err}"
        )));
    }

    let proxy: ProxyResponse = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("TS proxy {action_type} parse error: {e}")))?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: if proxy.preview.id.is_empty() {
                Uuid::new_v4().to_string()
            } else {
                proxy.preview.id
            },
            action_type: proxy.preview.action_type,
            description: proxy.preview.description,
            estimated_fee: proxy.preview.estimated_fee,
            estimated_refund: None,
            params: proxy.preview.params,
            warnings: proxy.preview.warnings,
            requires_approval: proxy.preview.requires_approval,
        },
        transaction: None,
        additional_signers_required: proxy.additional_signers_required,
        execution_steps: None,
        quote: None,
        is_cross_chain: proxy.is_cross_chain,
        data: Some(proxy.data),
    })
}

/// MarginFi v2 — detailed per-bank balance breakdown (deposit/borrow side,
/// USD value, APY). Goes beyond `marginfi_user_accounts` which only returns
/// PDA addresses + health factor.
pub async fn marginfi_user_balances(
    http: &Client,
    wallet: &str,
) -> Result<BuildResponse, AppError> {
    ts_proxy_read(
        http,
        wallet,
        "marginfi_user_balances",
        serde_json::json!({}),
    )
    .await
}

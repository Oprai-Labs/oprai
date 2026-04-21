/**
 * deBridge DLN Cross-Chain Service
 *
 * Real integration with deBridge DLN (Decentralized Liquidity Network).
 * API: https://api.dln.trade/v1.0/
 * Docs: https://docs.debridge.finance/dln-api/
 *
 * Solana chain ID in deBridge: 7565164
 */
use serde::{Deserialize, Serialize};
use reqwest::Client;
use uuid::Uuid;
use crate::error::AppError;

// deBridge DLN API (v1.0)
const DLN_API: &str = "https://api.dln.trade/v1.0";

pub mod chain_id {
    pub const SOLANA: u64      = 7565164;
    pub const ETHEREUM: u64    = 1;
    pub const BSC: u64         = 56;
    pub const POLYGON: u64     = 137;
    pub const AVALANCHE: u64   = 43114;
    pub const ARBITRUM: u64    = 42161;
    pub const OPTIMISM: u64    = 10;
    pub const BASE: u64        = 8453;
}

/// Native / zero-address token representation used by deBridge on each chain.
const DEBRIDGE_SOLANA_NATIVE: &str = "0x0000000000000000000000000000000000000000";
const DEBRIDGE_EVM_NATIVE:    &str = "0x0000000000000000000000000000000000000000";

// ─────────────────────────────────────────────────────────────────────────────
// Public types
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DebridgeParams {
    /// deBridge chain ID (7565164=Solana, 1=ETH, 56=BSC …)
    pub origin_chain_id: u64,
    pub destination_chain_id: u64,
    /// Token symbol ("SOL", "USDC") or mint/address
    pub origin_currency: String,
    pub destination_currency: String,
    /// Human-readable amount (e.g. "1.5")
    pub amount: String,
    pub recipient: Option<String>,
    pub slippage_bps: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DebridgeQuote {
    pub route_id: String,
    pub src_chain_id: u64,
    pub dst_chain_id: u64,
    pub src_token_symbol: String,
    pub dst_token_symbol: String,
    pub src_amount: String,
    pub dst_amount: String,
    pub bridge_fee: String,
    pub estimated_duration_seconds: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DebridgeBuildResult {
    /// Base64-encoded Solana transaction (when srcChain=Solana)
    pub solana_tx: Option<String>,
    /// Raw EVM tx_data (when srcChain=EVM)
    pub evm_tx: Option<EvmTxData>,
    pub preview: DebridgePreview,
    pub quote: DebridgeQuote,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvmTxData {
    pub to: String,
    pub data: String,
    pub value: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DebridgePreview {
    pub id: String,
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    pub params: serde_json::Value,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

// ─────────────────────────────────────────────────────────────────────────────
// DLN API response shapes
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DlnEstimation {
    #[allow(dead_code)]
    src_chain_token_in: Option<DlnTokenAmount>,
    dst_chain_token_out: Option<DlnTokenAmount>,
    #[allow(dead_code)]
    costs_details: Option<Vec<serde_json::Value>>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DlnTokenAmount {
    symbol: Option<String>,
    #[allow(dead_code)]
    amount: Option<String>,
    recommended_amount: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct DlnCreateTxResponse {
    tx: Option<DlnTxData>,
    estimation: Option<DlnEstimation>,
    #[serde(rename = "orderId")]
    order_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct DlnTxData {
    /// Base64 Solana TX  –OR–  hex EVM calldata
    data: Option<String>,
    /// EVM contract address (absent for Solana)
    to: Option<String>,
    /// EVM value in wei (absent for Solana)
    value: Option<String>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/// Map foreign chain ID formats to deBridge native IDs.
/// Relay protocol uses 900 for Solana; deBridge requires 7565164.
fn normalize_chain_id(id: u64) -> u64 {
    match id {
        900 => chain_id::SOLANA, // Relay → deBridge Solana ID
        _ => id,
    }
}

pub fn validate_debridge_params(p: &DebridgeParams) -> Result<(), AppError> {
    if p.amount.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    let origin = normalize_chain_id(p.origin_chain_id);
    let dest   = normalize_chain_id(p.destination_chain_id);
    if origin == dest {
        return Err(AppError::InvalidParams(
            "Source and destination chains must differ".into(),
        ));
    }
    Ok(())
}

pub fn chain_name(id: u64) -> &'static str {
    match id {
        chain_id::SOLANA    => "Solana",
        chain_id::ETHEREUM  => "Ethereum",
        chain_id::BSC       => "BNB Chain",
        chain_id::POLYGON   => "Polygon",
        chain_id::AVALANCHE => "Avalanche",
        chain_id::ARBITRUM  => "Arbitrum",
        chain_id::OPTIMISM  => "Optimism",
        chain_id::BASE      => "Base",
        _                   => "Unknown",
    }
}

/// Resolve a human-friendly token symbol to deBridge token address format.
fn resolve_token(symbol_or_addr: &str, chain_id: u64) -> String {
    if chain_id == chain_id::SOLANA {
        match symbol_or_addr.to_uppercase().as_str() {
            "SOL"  => DEBRIDGE_SOLANA_NATIVE.to_string(),
            "USDC" => "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v".to_string(),
            "USDT" => "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB".to_string(),
            other  => other.to_string(),
        }
    } else {
        // EVM chains – pass through; native represented as zero address
        match symbol_or_addr.to_uppercase().as_str() {
            "ETH" | "BNB" | "MATIC" | "AVAX" => DEBRIDGE_EVM_NATIVE.to_string(),
            other => other.to_string(),
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Build TX
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_debridge_swap(
    http: &Client,
    user_address: &str,
    params: &DebridgeParams,
) -> Result<DebridgeBuildResult, AppError> {
    validate_debridge_params(params)?;

    let origin_id = normalize_chain_id(params.origin_chain_id);
    let dest_id   = normalize_chain_id(params.destination_chain_id);

    let amount_f: f64 = params.amount.parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid amount: {}", params.amount)))?;
    let decimals: u32 = if origin_id == chain_id::SOLANA { 9 } else { 18 };
    let amount_raw = ((amount_f * 10_f64.powi(decimals as i32)) as u64).to_string();

    let src_token = resolve_token(&params.origin_currency, origin_id);
    let dst_token = resolve_token(&params.destination_currency, dest_id);
    let recipient = params.recipient.as_deref().unwrap_or(user_address);

    // ── 1. Create-tx call ────────────────────────────────────────────────────
    let url = format!(
        "{DLN_API}/dln/order/create-tx\
         ?srcChainId={}&srcChainTokenIn={}&srcChainTokenInAmount={}\
         &dstChainId={}&dstChainTokenOut={}\
         &dstChainTokenOutRecipient={}\
         &senderAddress={}&srcChainOrderAuthorityAddress={}\
         &dstChainOrderAuthorityAddress={}\
         &prependOperatingExpenses=true",
        origin_id, src_token, amount_raw,
        dest_id, dst_token,
        recipient,
        user_address, user_address,
        recipient,
    );

    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("deBridge create-tx request failed: {e}")))?;

    if !resp.status().is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "deBridge create-tx API error: {body}"
        )));
    }

    let data: DlnCreateTxResponse = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("deBridge create-tx parse failed: {e}")))?;

    // ── 2. Extract transaction ────────────────────────────────────────────────
    let tx = data.tx.as_ref();
    let tx_data_str = tx.and_then(|t| t.data.clone());

    let (solana_tx, evm_tx) = if params.origin_chain_id == chain_id::SOLANA {
        // deBridge returns a base64-encoded Solana transaction in `data`
        (tx_data_str, None)
    } else {
        let evm = tx.map(|t| EvmTxData {
            to:    t.to.clone().unwrap_or_default(),
            data:  t.data.clone().unwrap_or_default(),
            value: t.value.clone().unwrap_or_else(|| "0".to_string()),
        });
        (None, evm)
    };

    // ── 3. Quote ──────────────────────────────────────────────────────────────
    let est = data.estimation.as_ref();
    let dst_sym = est
        .and_then(|e| e.dst_chain_token_out.as_ref())
        .and_then(|t| t.symbol.clone())
        .unwrap_or_else(|| params.destination_currency.clone());
    let dst_amount = est
        .and_then(|e| e.dst_chain_token_out.as_ref())
        .and_then(|t| t.recommended_amount.clone())
        .unwrap_or_else(|| "0".to_string());

    let quote = DebridgeQuote {
        route_id: data.order_id.unwrap_or_else(|| Uuid::new_v4().to_string()),
        src_chain_id: origin_id,
        dst_chain_id: dest_id,
        src_token_symbol: params.origin_currency.clone(),
        dst_token_symbol: dst_sym,
        src_amount: amount_raw,
        dst_amount,
        bridge_fee: format!("~{:.6} {}", amount_f * 0.0005, &params.origin_currency),
        estimated_duration_seconds: 120,
    };

    // ── 4. Preview ────────────────────────────────────────────────────────────
    let src_name = chain_name(origin_id);
    let dst_name = chain_name(dest_id);
    let preview = DebridgePreview {
        id: format!("db_{}", Uuid::new_v4()),
        action_type: "debridge_bridge".to_string(),
        description: format!(
            "Bridge {} {} from {} to {} via deBridge DLN",
            params.amount, &params.origin_currency, src_name, dst_name
        ),
        estimated_fee: quote.bridge_fee.clone(),
        params: serde_json::to_value(params)
            .unwrap_or(serde_json::Value::Null),
        warnings: vec![
            "DLN uses an intent-based model – a maker fills your order on the destination chain.".to_string(),
        ],
        requires_approval: true,
    };

    Ok(DebridgeBuildResult {
        solana_tx,
        evm_tx,
        preview,
        quote,
    })
}

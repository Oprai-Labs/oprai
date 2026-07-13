use crate::error::AppError;
use reqwest::Client;
/**
 * Squid Protocol Cross-Chain Swap Service — v2 complete
 *
 * API: https://v2.api.squidrouter.com
 * SDK: @0xsquid/sdk v2 (mirrors SDK parameters exactly)
 * Docs: https://docs.squidrouter.com/
 *
 * Required params: fromAddress, fromChain, fromToken, fromAmount, toChain, toToken, toAddress
 * Optional params: slippage, enableExpress, receiveGasOnDestination, prefer, bypass,
 *                  quoteOnly, enableBoost, collectFees, postHook
 *
 * Status values: SUCCESS | PARTIAL_SUCCESS | NEEDS_GAS | ONGOING | NOT_FOUND | REFUND
 */
use serde::{Deserialize, Serialize};
use uuid::Uuid;

const SQUID_API: &str = "https://v2.api.squidrouter.com";
const SQUID_INTEGRATOR_ID: &str = "oprai-dapp";

// EVM native token sentinel
const EVM_NATIVE: &str = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE";

// ─────────────────────────────────────────────────────────────────────────────
// Chain helpers
// ─────────────────────────────────────────────────────────────────────────────

pub fn chain_id_to_squid(chain_id: u64) -> Option<&'static str> {
    match chain_id {
        0 | 900 | 7565164 => Some("solana"),
        1 => Some("1"),           // Ethereum
        56 => Some("56"),         // BSC
        137 => Some("137"),       // Polygon
        43114 => Some("43114"),   // Avalanche
        42161 => Some("42161"),   // Arbitrum
        10 => Some("10"),         // Optimism
        8453 => Some("8453"),     // Base
        59144 => Some("59144"),   // Linea
        534352 => Some("534352"), // Scroll
        1284 => Some("1284"),     // Moonbeam
        2222 => Some("2222"),     // Kava
        1101 => Some("1101"),     // zkEVM
        324 => Some("324"),       // zkSync
        100 => Some("100"),       // Gnosis
        250 => Some("250"),       // Fantom
        _ => None,
    }
}

pub fn chain_display(squid_chain: &str) -> &'static str {
    match squid_chain {
        "solana" => "Solana",
        "1" => "Ethereum",
        "56" => "BSC",
        "137" => "Polygon",
        "43114" => "Avalanche",
        "42161" => "Arbitrum",
        "10" => "Optimism",
        "8453" => "Base",
        "59144" => "Linea",
        "534352" => "Scroll",
        "1284" => "Moonbeam",
        "2222" => "Kava",
        "1101" => "zkEVM",
        "324" => "zkSync",
        "100" => "Gnosis",
        "250" => "Fantom",
        _ => "unknown",
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Post-hook types (mirrors SDK PostHook interface)
// ─────────────────────────────────────────────────────────────────────────────

/// callType: 0=DEFAULT, 1=FULL_TOKEN_BALANCE, 2=NATIVE_BALANCE, 3=COLLECT_TOKEN_BALANCE
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct PostHookCall {
    pub call_type: u8,
    pub target: String,
    #[serde(default)]
    pub value: String,
    pub call_data: String,
    pub estimated_gas: String,
    /// Required for FULL_TOKEN_BALANCE: {tokenAddress, inputPos}
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payload: Option<serde_json::Value>,
}

/// chainType: "evm" | "cosmos"
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PostHook {
    pub chain_type: String,
    pub calls: Vec<PostHookCall>,
}

/// collectFees: optional integrator fee collection
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CollectFees {
    pub integrator_address: String,
    /// Fee in basis points, e.g. 30 = 0.3%
    pub fee: u32,
}

// ─────────────────────────────────────────────────────────────────────────────
// Public request / response types
// ─────────────────────────────────────────────────────────────────────────────

/// Full parameter set — required fields first, optional below.
/// Maps 1:1 to the Squid v2 SDK getRoute() params.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidParams {
    // ── Required ──────────────────────────────────────────────────────────
    /// Numeric chain ID: 0/7565164=Solana, 1=ETH, 42161=Arbitrum …
    pub origin_chain_id: u64,
    pub destination_chain_id: u64,
    /// Token address or well-known symbol ("SOL", "USDC", "0x…")
    pub origin_token: String,
    pub destination_token: String,
    /// Human-readable amount (e.g. "1.5")
    pub amount: String,
    /// Sender address (EVM 0x… or Solana base58) — auto-populated from JWT wallet if empty
    #[serde(default)]
    pub from_address: String,
    /// Recipient address — defaults to from_address
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recipient: Option<String>,

    // ── Optional ──────────────────────────────────────────────────────────
    /// Slippage % (e.g. 1.0 = 1%); None = Squid auto-calculates
    #[serde(skip_serializing_if = "Option::is_none")]
    pub slippage: Option<f64>,

    /// Enable express route (Chainflip/CCTP) for faster bridging (~2-10 min)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enable_express: Option<bool>,

    /// Airdrop destination gas token so user can transact immediately
    #[serde(skip_serializing_if = "Option::is_none")]
    pub receive_gas_on_destination: Option<bool>,

    /// Preferred bridge types in priority order: ["chainflip","cctp","axelar"]
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prefer: Option<Vec<String>>,

    /// Bridge types to exclude: same options as prefer
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bypass: Option<Vec<String>>,

    /// If true only return estimate, skip TX building
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quote_only: Option<bool>,

    /// Enable Squid Boost for faster Axelar confirmation (default true)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enable_boost: Option<bool>,

    /// Integrator fee collection config
    #[serde(skip_serializing_if = "Option::is_none")]
    pub collect_fees: Option<CollectFees>,

    /// Post-bridge on-chain calls (staking, lending deposits, NFT purchase …)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub post_hook: Option<PostHook>,
}

/// Status poll parameters — transactionId + quoteId required for Coral V2
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidStatusParams {
    pub transaction_id: String,
    pub quote_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub integrator_id: Option<String>,
    /// "chainflip" | "chainflipmultihop" | "axelar" | "cctp" | "hyperlane"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bridge_type: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidQuote {
    pub quote_id: Option<String>,
    pub request_id: Option<String>,
    pub from_chain: String,
    pub to_chain: String,
    pub from_token: String,
    pub to_token: String,
    pub from_amount: String,
    pub from_amount_usd: Option<String>,
    pub to_amount: String,
    pub to_amount_min: Option<String>,
    pub to_amount_usd: Option<String>,
    pub price_impact_pct: f64,
    pub total_fee_usd: f64,
    pub estimated_route_duration_seconds: u64,
    pub exchange_rate: Option<String>,
    pub is_boost_supported: Option<bool>,
    pub aggregator: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvmTxData {
    pub to: String,
    pub data: String,
    pub value: String,
    pub chain_id: String,
    pub gas_limit: Option<String>,
    pub max_fee_per_gas: Option<String>,
    pub max_priority_fee_per_gas: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidBuildResult {
    pub solana_tx: Option<String>,
    pub evm_tx: Option<EvmTxData>,
    pub preview: SquidPreview,
    pub quote: SquidQuote,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidPreview {
    pub id: String,
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    pub params: serde_json::Value,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

/// Coral V2 status response
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidStatusResult {
    pub transaction_id: String,
    pub quote_id: String,
    /// SUCCESS | PARTIAL_SUCCESS | NEEDS_GAS | ONGOING | NOT_FOUND | REFUND
    pub status: String,
    pub is_terminal: bool,
    pub from_chain_tx_hash: Option<String>,
    pub to_chain_tx_hash: Option<String>,
    pub axelarscan_url: String,
    pub status_meaning: String,
}

// ─────────────────────────────────────────────────────────────────────────────
// Squid API response shapes (internal)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SquidRouteResponse {
    route: Option<SquidRoute>,
    #[serde(rename = "requestId")]
    request_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SquidRoute {
    estimate: Option<SquidEstimate>,
    #[serde(rename = "quoteId")]
    quote_id: Option<String>,
    transaction_request: Option<SquidTransactionRequest>,
    #[serde(rename = "isBoostSupported")]
    is_boost_supported: Option<bool>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SquidEstimate {
    from_amount: Option<String>,
    from_amount_usd: Option<String>,
    to_amount: Option<String>,
    to_amount_min: Option<String>,
    to_amount_usd: Option<String>,
    from_token: Option<SquidTokenInfo>,
    to_token: Option<SquidTokenInfo>,
    aggregate_price_impact: Option<String>,
    fee_costs: Option<Vec<SquidFeeCost>>,
    gas_costs: Option<Vec<SquidFeeCost>>,
    estimated_route_duration: Option<u64>,
    exchange_rate: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SquidTokenInfo {
    symbol: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SquidFeeCost {
    amount_usd: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SquidTransactionRequest {
    data: Option<String>,
    target: Option<String>,
    #[serde(rename = "to")]
    to_addr: Option<String>,
    value: Option<String>,
    #[serde(rename = "chainId")]
    chain_id: Option<serde_json::Value>,
    gas_limit: Option<String>,
    max_fee_per_gas: Option<String>,
    max_priority_fee_per_gas: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SquidStatusResponse {
    squid_transaction_status: Option<String>,
    status: Option<String>,
    from_chain: Option<serde_json::Value>,
    to_chain: Option<serde_json::Value>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Token resolution
// ─────────────────────────────────────────────────────────────────────────────

fn resolve_token(token: &str, squid_chain: &str) -> String {
    if squid_chain == "solana" {
        match token.to_uppercase().as_str() {
            "SOL" => "So11111111111111111111111111111111111111112".into(),
            "USDC" => "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v".into(),
            "USDT" => "Es9vMFrzaCERm2cTEVTg9w5zcTreHDfS1YDGgY6m2t8X".into(),
            "BONK" => "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263".into(),
            "JUP" => "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN".into(),
            "RAY" => "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R".into(),
            "ORCA" => "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE".into(),
            "WETH" => "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs".into(),
            other => other.to_string(),
        }
    } else {
        match token.to_uppercase().as_str() {
            "ETH" | "BNB" | "MATIC" | "AVAX" | "NATIVE" | "FTM" | "GLMR" | "KAVA" | "XDAI" => {
                EVM_NATIVE.to_string()
            }
            "USDC" => "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48".into(),
            "USDT" => "0xdAC17F958D2ee523a2206206994597C13D831ec7".into(),
            "WETH" => "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2".into(),
            "WBTC" => "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599".into(),
            "DAI" => "0x6B175474E89094C44Da98b954EedeAC495271d0F".into(),
            other => other.to_string(),
        }
    }
}

fn token_decimals_solana(symbol: &str) -> u32 {
    match symbol.to_uppercase().as_str() {
        "SOL" | "RAY" | "ORCA" | "JUP" => 9,
        "USDC" | "USDT" | "MNGO" => 6,
        "BONK" => 5,
        "WETH" | "WBTC" => 8,
        _ => 9,
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Validation
// ─────────────────────────────────────────────────────────────────────────────

pub fn validate_squid_params(p: &SquidParams) -> Result<(), AppError> {
    let amount: f64 = p
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid amount: {}", p.amount)))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    if p.origin_chain_id == p.destination_chain_id {
        return Err(AppError::InvalidParams(
            "Source and destination chains must differ".into(),
        ));
    }
    if chain_id_to_squid(p.origin_chain_id).is_none() {
        return Err(AppError::InvalidParams(format!(
            "Unsupported origin chain ID: {}. Supported: 0/7565164 (Solana), 1, 56, 137, 43114, 42161, 10, 8453, 59144, 534352",
            p.origin_chain_id
        )));
    }
    if chain_id_to_squid(p.destination_chain_id).is_none() {
        return Err(AppError::InvalidParams(format!(
            "Unsupported destination chain ID: {}",
            p.destination_chain_id
        )));
    }
    if p.from_address.is_empty() {
        return Err(AppError::InvalidParams("from_address is required".into()));
    }
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Build route request body
// ─────────────────────────────────────────────────────────────────────────────

fn build_route_body(
    p: &SquidParams,
    from_chain: &str,
    to_chain: &str,
    from_token: &str,
    to_token: &str,
    from_amount_raw: &str,
    recipient: &str,
) -> serde_json::Value {
    let mut body = serde_json::json!({
        "fromAddress": p.from_address,
        "fromChain":   from_chain,
        "fromToken":   from_token,
        "fromAmount":  from_amount_raw,
        "toChain":     to_chain,
        "toToken":     to_token,
        "toAddress":   recipient,
        "enableBoost":  p.enable_boost.unwrap_or(true),
        "quoteOnly":   p.quote_only.unwrap_or(false),
    });

    let obj = body.as_object_mut().unwrap();

    if let Some(s) = p.slippage {
        obj.insert("slippage".into(), serde_json::json!(s));
    }
    if let Some(e) = p.enable_express {
        obj.insert("enableExpress".into(), serde_json::json!(e));
    }
    if let Some(r) = p.receive_gas_on_destination {
        obj.insert("receiveGasOnDestination".into(), serde_json::json!(r));
    }
    if let Some(ref pref) = p.prefer {
        if !pref.is_empty() {
            obj.insert("prefer".into(), serde_json::json!(pref));
        }
    }
    if let Some(ref byp) = p.bypass {
        if !byp.is_empty() {
            obj.insert("bypass".into(), serde_json::json!(byp));
        }
    }
    if let Some(ref cf) = p.collect_fees {
        obj.insert(
            "collectFees".into(),
            serde_json::to_value(cf).unwrap_or_default(),
        );
    }
    if let Some(ref hook) = p.post_hook {
        obj.insert(
            "postHook".into(),
            serde_json::to_value(hook).unwrap_or_default(),
        );
    }

    body
}

// ─────────────────────────────────────────────────────────────────────────────
// Main: build_squid_swap
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_squid_swap(
    http: &Client,
    user_address: &str,
    params: &SquidParams,
) -> Result<SquidBuildResult, AppError> {
    validate_squid_params(params)?;

    let from_chain = chain_id_to_squid(params.origin_chain_id).ok_or_else(|| {
        AppError::InvalidParams(format!(
            "Unsupported origin chain: {}",
            params.origin_chain_id
        ))
    })?;
    let to_chain = chain_id_to_squid(params.destination_chain_id).ok_or_else(|| {
        AppError::InvalidParams(format!(
            "Unsupported destination chain: {}",
            params.destination_chain_id
        ))
    })?;

    let from_token = resolve_token(&params.origin_token, from_chain);
    let to_token = resolve_token(&params.destination_token, to_chain);

    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid amount: {}", params.amount)))?;
    let recipient = params.recipient.as_deref().unwrap_or(user_address);

    // Convert human amount → smallest units
    let from_amount_raw = if from_chain == "solana" {
        let dec = token_decimals_solana(&params.origin_token);
        ((amount * 10_f64.powi(dec as i32)) as u64).to_string()
    } else {
        ((amount * 1e18) as u128).to_string()
    };

    // ── POST /v2/route ────────────────────────────────────────────────────────
    let url = format!("{SQUID_API}/v2/route");
    let body = build_route_body(
        params,
        from_chain,
        to_chain,
        &from_token,
        &to_token,
        &from_amount_raw,
        recipient,
    );

    let resp = http
        .post(&url)
        .header("x-integrator-id", SQUID_INTEGRATOR_ID)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Squid route request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "Squid route API error {status}: {body_text}"
        )));
    }

    let data: SquidRouteResponse = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Squid route parse failed: {e}")))?;

    let route = data.route.as_ref();
    let est = route.and_then(|r| r.estimate.as_ref());
    let tx_req = route.and_then(|r| r.transaction_request.as_ref());
    let quote_id = route.and_then(|r| r.quote_id.clone());

    // ── Extract estimate fields ───────────────────────────────────────────────
    let from_sym = est
        .and_then(|e| e.from_token.as_ref())
        .and_then(|t| t.symbol.clone())
        .unwrap_or_else(|| params.origin_token.clone());
    let to_sym = est
        .and_then(|e| e.to_token.as_ref())
        .and_then(|t| t.symbol.clone())
        .unwrap_or_else(|| params.destination_token.clone());
    let to_amount = est
        .and_then(|e| e.to_amount.clone())
        .unwrap_or_else(|| "0".into());
    let to_amount_min = est.and_then(|e| e.to_amount_min.clone());
    let to_amount_usd = est.and_then(|e| e.to_amount_usd.clone());
    let from_amount_usd = est.and_then(|e| e.from_amount_usd.clone());
    let exchange_rate = est.and_then(|e| e.exchange_rate.clone());
    let is_boost_supported = route.and_then(|r| r.is_boost_supported);

    let price_impact: f64 = est
        .and_then(|e| e.aggregate_price_impact.as_ref())
        .and_then(|s| s.parse().ok())
        .unwrap_or(0.0);

    let total_fee_usd: f64 = {
        let fee = est
            .and_then(|e| e.fee_costs.as_ref())
            .map(|v| {
                v.iter()
                    .filter_map(|f| f.amount_usd.as_ref()?.parse::<f64>().ok())
                    .sum::<f64>()
            })
            .unwrap_or(0.0);
        let gas = est
            .and_then(|e| e.gas_costs.as_ref())
            .map(|v| {
                v.iter()
                    .filter_map(|f| f.amount_usd.as_ref()?.parse::<f64>().ok())
                    .sum::<f64>()
            })
            .unwrap_or(0.0);
        fee + gas
    };

    let eta = est.and_then(|e| e.estimated_route_duration).unwrap_or(180);

    // ── Split Solana TX vs EVM TX ─────────────────────────────────────────────
    let tx_data_str = tx_req.and_then(|t| t.data.clone());

    let (solana_tx, evm_tx) = if from_chain == "solana" {
        (tx_data_str, None)
    } else {
        let chain_id_str = tx_req
            .and_then(|t| t.chain_id.as_ref())
            .map(|v| v.to_string().trim_matches('"').to_string())
            .unwrap_or_else(|| from_chain.to_string());
        let evm = tx_req.map(|t| EvmTxData {
            to: t
                .target
                .clone()
                .or_else(|| t.to_addr.clone())
                .unwrap_or_default(),
            data: t.data.clone().unwrap_or_default(),
            value: t.value.clone().unwrap_or_else(|| "0".into()),
            chain_id: chain_id_str,
            gas_limit: t.gas_limit.clone(),
            max_fee_per_gas: t.max_fee_per_gas.clone(),
            max_priority_fee_per_gas: t.max_priority_fee_per_gas.clone(),
        });
        (None, evm)
    };

    // ── Quote & preview ───────────────────────────────────────────────────────
    let quote = SquidQuote {
        quote_id,
        request_id: data.request_id,
        from_chain: from_chain.into(),
        to_chain: to_chain.into(),
        from_token: from_sym.clone(),
        to_token: to_sym.clone(),
        from_amount: from_amount_raw,
        from_amount_usd,
        to_amount,
        to_amount_min,
        to_amount_usd,
        price_impact_pct: price_impact,
        total_fee_usd,
        estimated_route_duration_seconds: eta,
        exchange_rate,
        is_boost_supported,
        aggregator: "squid".into(),
    };

    let src_display = chain_display(from_chain);
    let dst_display = chain_display(to_chain);

    let mut warnings = vec![format!(
        "Cross-chain via Squid: {} → {}",
        src_display, dst_display
    )];
    if price_impact > 1.0 {
        warnings.push(format!("Price impact: {:.2}%", price_impact));
    }
    if eta > 300 {
        warnings.push(format!("ETA: ~{} minutes", eta / 60));
    }
    if params.post_hook.is_some() {
        warnings.push("Post-hook will execute on destination chain after token delivery".into());
    }

    let preview = SquidPreview {
        id: format!("sq_{}", Uuid::new_v4()),
        action_type: "squid_bridge".into(),
        description: format!(
            "Swap {} {} ({}) → {} ({}) via Squid",
            params.amount, from_sym, src_display, to_sym, dst_display
        ),
        estimated_fee: format!("~${:.2}", total_fee_usd),
        params: serde_json::to_value(params).unwrap_or(serde_json::Value::Null),
        warnings,
        requires_approval: true,
    };

    Ok(SquidBuildResult {
        solana_tx,
        evm_tx,
        preview,
        quote,
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query types for chains / tokens / quote
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidChain {
    pub chain_id: serde_json::Value,
    pub chain_type: Option<String>,
    pub network_name: Option<String>,
    pub chain_name: Option<String>,
    pub native_token: Option<serde_json::Value>,
    pub rpc: Option<Vec<String>>,
    pub block_explorer: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidToken {
    pub address: String,
    pub chain_id: serde_json::Value,
    pub chain_type: Option<String>,
    pub symbol: Option<String>,
    pub name: Option<String>,
    pub decimals: Option<u32>,
    pub logo_uri: Option<String>,
    pub coingecko_id: Option<String>,
    pub usd_price: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidChainsResult {
    pub chains: Vec<serde_json::Value>,
    pub count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidTokensResult {
    pub tokens: Vec<serde_json::Value>,
    pub count: usize,
    pub chain_filter: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidQuoteParams {
    pub origin_chain_id: u64,
    pub destination_chain_id: u64,
    pub origin_token: String,
    pub destination_token: String,
    pub amount: String,
    #[serde(default)]
    pub from_address: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub slippage: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prefer: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SquidQuoteResult {
    pub quote_id: Option<String>,
    pub from_chain: String,
    pub to_chain: String,
    pub from_token: String,
    pub to_token: String,
    pub from_amount: String,
    pub to_amount: String,
    pub to_amount_min: Option<String>,
    pub price_impact_pct: f64,
    pub total_fee_usd: f64,
    pub estimated_duration_seconds: u64,
    pub exchange_rate: Option<String>,
    pub is_boost_supported: Option<bool>,
}

// GET /v2/chains — list all supported networks
pub async fn get_squid_chains(http: &Client) -> Result<SquidChainsResult, AppError> {
    let url = format!("{SQUID_API}/v2/chains");
    let resp = http
        .get(&url)
        .header("x-integrator-id", SQUID_INTEGRATOR_ID)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Squid chains request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "Squid chains API error {status}: {body}"
        )));
    }

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Squid chains parse failed: {e}")))?;

    let chains: Vec<serde_json::Value> = data
        .get("chains")
        .and_then(|c| c.as_array())
        .cloned()
        .unwrap_or_default();

    let count = chains.len();
    Ok(SquidChainsResult { chains, count })
}

// GET /v2/tokens — list supported tokens, optionally filtered by chainId
pub async fn get_squid_tokens(
    http: &Client,
    chain_id: Option<&str>,
) -> Result<SquidTokensResult, AppError> {
    let url = if let Some(cid) = chain_id {
        format!("{SQUID_API}/v2/tokens?chainId={cid}")
    } else {
        format!("{SQUID_API}/v2/tokens")
    };

    let resp = http
        .get(&url)
        .header("x-integrator-id", SQUID_INTEGRATOR_ID)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Squid tokens request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "Squid tokens API error {status}: {body}"
        )));
    }

    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Squid tokens parse failed: {e}")))?;

    let tokens: Vec<serde_json::Value> = data
        .get("tokens")
        .and_then(|t| t.as_array())
        .cloned()
        .unwrap_or_default();

    let count = tokens.len();
    Ok(SquidTokensResult {
        tokens,
        count,
        chain_filter: chain_id.map(|s| s.to_string()),
    })
}

// POST /v2/route with quoteOnly=true — estimate only, no TX built
pub async fn get_squid_quote(
    http: &Client,
    user_address: &str,
    p: &SquidQuoteParams,
) -> Result<SquidQuoteResult, AppError> {
    let from_chain = chain_id_to_squid(p.origin_chain_id).ok_or_else(|| {
        AppError::InvalidParams(format!("Unsupported origin chain: {}", p.origin_chain_id))
    })?;
    let to_chain = chain_id_to_squid(p.destination_chain_id).ok_or_else(|| {
        AppError::InvalidParams(format!(
            "Unsupported destination chain: {}",
            p.destination_chain_id
        ))
    })?;

    if p.origin_chain_id == p.destination_chain_id {
        return Err(AppError::InvalidParams(
            "Source and destination chains must differ".into(),
        ));
    }

    let amount: f64 = p
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid amount: {}", p.amount)))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }

    let from_token = resolve_token(&p.origin_token, from_chain);
    let to_token = resolve_token(&p.destination_token, to_chain);
    let from_address = if p.from_address.is_empty() {
        user_address
    } else {
        &p.from_address
    };

    let from_amount_raw = if from_chain == "solana" {
        let dec = token_decimals_solana(&p.origin_token);
        ((amount * 10_f64.powi(dec as i32)) as u64).to_string()
    } else {
        ((amount * 1e18) as u128).to_string()
    };

    let mut body = serde_json::json!({
        "fromAddress": from_address,
        "fromChain":   from_chain,
        "fromToken":   from_token,
        "fromAmount":  from_amount_raw,
        "toChain":     to_chain,
        "toToken":     to_token,
        "toAddress":   from_address,
        "quoteOnly":   true,
        "enableBoost": true,
    });
    if let Some(s) = p.slippage {
        body.as_object_mut()
            .unwrap()
            .insert("slippage".into(), serde_json::json!(s));
    }
    if let Some(ref pref) = p.prefer {
        if !pref.is_empty() {
            body.as_object_mut()
                .unwrap()
                .insert("prefer".into(), serde_json::json!(pref));
        }
    }

    let url = format!("{SQUID_API}/v2/route");
    let resp = http
        .post(&url)
        .header("x-integrator-id", SQUID_INTEGRATOR_ID)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Squid quote request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "Squid quote API error {status}: {body_text}"
        )));
    }

    let data: SquidRouteResponse = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Squid quote parse failed: {e}")))?;

    let route = data.route.as_ref();
    let est = route.and_then(|r| r.estimate.as_ref());

    let from_sym = est
        .and_then(|e| e.from_token.as_ref())
        .and_then(|t| t.symbol.clone())
        .unwrap_or_else(|| p.origin_token.clone());
    let to_sym = est
        .and_then(|e| e.to_token.as_ref())
        .and_then(|t| t.symbol.clone())
        .unwrap_or_else(|| p.destination_token.clone());

    let price_impact: f64 = est
        .and_then(|e| e.aggregate_price_impact.as_ref())
        .and_then(|s| s.parse().ok())
        .unwrap_or(0.0);
    let total_fee_usd: f64 = {
        let fee = est
            .and_then(|e| e.fee_costs.as_ref())
            .map(|v| {
                v.iter()
                    .filter_map(|f| f.amount_usd.as_ref()?.parse::<f64>().ok())
                    .sum::<f64>()
            })
            .unwrap_or(0.0);
        let gas = est
            .and_then(|e| e.gas_costs.as_ref())
            .map(|v| {
                v.iter()
                    .filter_map(|f| f.amount_usd.as_ref()?.parse::<f64>().ok())
                    .sum::<f64>()
            })
            .unwrap_or(0.0);
        fee + gas
    };

    Ok(SquidQuoteResult {
        quote_id: route.and_then(|r| r.quote_id.clone()),
        from_chain: from_chain.into(),
        to_chain: to_chain.into(),
        from_token: from_sym,
        to_token: to_sym,
        from_amount: from_amount_raw,
        to_amount: est
            .and_then(|e| e.to_amount.clone())
            .unwrap_or_else(|| "0".into()),
        to_amount_min: est.and_then(|e| e.to_amount_min.clone()),
        price_impact_pct: price_impact,
        total_fee_usd,
        estimated_duration_seconds: est.and_then(|e| e.estimated_route_duration).unwrap_or(180),
        exchange_rate: est.and_then(|e| e.exchange_rate.clone()),
        is_boost_supported: route.and_then(|r| r.is_boost_supported),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// get_squid_status — Coral V2 status tracking
// ─────────────────────────────────────────────────────────────────────────────

pub async fn get_squid_status(
    http: &Client,
    status_params: &SquidStatusParams,
) -> Result<SquidStatusResult, AppError> {
    if status_params.transaction_id.is_empty() {
        return Err(AppError::InvalidParams("transactionId is required".into()));
    }
    if status_params.quote_id.is_empty() {
        return Err(AppError::InvalidParams(
            "quoteId is required for Coral V2 status tracking".into(),
        ));
    }

    let mut url = format!(
        "{SQUID_API}/v2/status?transactionId={}&quoteId={}",
        status_params.transaction_id, status_params.quote_id
    );
    if let Some(ref rid) = status_params.request_id {
        url.push_str(&format!("&requestId={rid}"));
    }
    if let Some(ref iid) = status_params.integrator_id {
        url.push_str(&format!("&integratorId={iid}"));
    } else {
        url.push_str(&format!("&integratorId={SQUID_INTEGRATOR_ID}"));
    }
    if let Some(ref bt) = status_params.bridge_type {
        url.push_str(&format!("&bridgeType={bt}"));
    }

    let resp = http
        .get(&url)
        .header("x-integrator-id", SQUID_INTEGRATOR_ID)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Squid status request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "Squid status API error {status}: {body}"
        )));
    }

    let data: SquidStatusResponse = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Squid status parse failed: {e}")))?;

    let raw_status = data
        .squid_transaction_status
        .or(data.status)
        .unwrap_or_else(|| "UNKNOWN".into())
        .to_uppercase();

    let is_terminal = matches!(
        raw_status.as_str(),
        "SUCCESS" | "PARTIAL_SUCCESS" | "REFUND" | "NOT_FOUND"
    );

    let extract_tx = |chain: &Option<serde_json::Value>| -> Option<String> {
        chain
            .as_ref()
            .and_then(|c| c.get("transactionDetails"))
            .and_then(|td| td.get("txHash"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
    };

    let from_tx = extract_tx(&data.from_chain);
    let to_tx = extract_tx(&data.to_chain);

    let meaning = match raw_status.as_str() {
        "SUCCESS" => "Fully completed on all chains.",
        "PARTIAL_SUCCESS" => "Source chain executed; destination chain reverted.",
        "NEEDS_GAS" => "Axelar gas spike on destination — add gas manually.",
        "ONGOING" => "In progress — keep polling every 10s.",
        "NOT_FOUND" => "No on-chain record found yet — may still propagate.",
        "REFUND" => "Coral route failed; tokens refunded to source.",
        _ => "Unknown status",
    };

    Ok(SquidStatusResult {
        transaction_id: status_params.transaction_id.clone(),
        quote_id: status_params.quote_id.clone(),
        status: raw_status,
        is_terminal,
        from_chain_tx_hash: from_tx,
        to_chain_tx_hash: to_tx,
        axelarscan_url: format!("https://axelarscan.io/gmp/{}", status_params.transaction_id),
        status_meaning: meaning.into(),
    })
}

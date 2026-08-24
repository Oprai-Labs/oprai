//! Uniswap Trading API — same-chain EVM swaps (Phase 1 of the Uniswap program).
//!
//! Mirrors the Relay pattern (`services/relay.rs`) but talks to Uniswap's hosted
//! Trading API (`https://trade-api.gateway.uniswap.org/v1`). Two differences from
//! Relay that shape this module:
//!   1. **Multi-step / Permit2.** A swap is `POST /quote` → (ERC20 only) an
//!      approval tx to Permit2 + an EIP-712 permit the USER signs → `POST /swap`
//!      with that signature → the ready EVM tx. Only the frontend can produce the
//!      permit signature, so the backend can't return one final tx up front the
//!      way Relay does; it returns the quote + permit material, then a second
//!      call turns the signed permit into calldata.
//!   2. **Fee is on the API key.** The Trading API ignores per-request
//!      `portionBips`; the integrator fee (flat 0.50%) is configured on the key
//!      (recipient = our EVM fee wallet), so we send NO appFees here. Economics +
//!      pooled-tier cashback are booked server-side after settlement, like Relay.
//!
//! The API key stays server-side — the frontend calls our gateway → this service
//! → Uniswap, never Uniswap directly.

use serde_json::{json, Value};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::services::relay::{resolve_evm_currency, to_base_units, get_chain_name, NATIVE_TOKEN_ADDRESS, CrossChainSwapParams};

pub const UNISWAP_TRADE_API: &str = "https://trade-api.gateway.uniswap.org/v1";

/// The Uniswap Trading API key. Server-side only (never sent to the client).
fn api_key() -> Result<String, AppError> {
    std::env::var("UNISWAP_API_KEY")
        .ok()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| AppError::Internal("UNISWAP_API_KEY is not configured".into()))
}

/// The EVM wallet our integrator fee is paid to. Reuses RELAY_FEE_RECIPIENT
/// (our existing EVM fee wallet) unless a dedicated UNISWAP_FEE_RECIPIENT is set.
/// None → no integratorFees field is sent at all.
pub fn fee_recipient() -> Option<String> {
    std::env::var("UNISWAP_FEE_RECIPIENT")
        .ok()
        .or_else(|| std::env::var("RELAY_FEE_RECIPIENT").ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Integrator fee in bips (default 50 = 0.50%).
pub fn fee_bps() -> u16 {
    std::env::var("UNISWAP_FEE_BPS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50)
}

/// Whether Uniswap has ENABLED fee-taking for our recipient. Until they do, the
/// integratorFees field validates but nothing is deducted — so booking a fee
/// (and owing cashback on it) would be wrong. Ship false; flip to true only once
/// Uniswap confirms the arrangement is live.
pub fn fee_active() -> bool {
    matches!(
        std::env::var("UNISWAP_FEE_ACTIVE").ok().as_deref(),
        Some("1") | Some("true") | Some("TRUE")
    )
}

/// Whether Uniswap can swap on this chain. Uniswap is EVM, and its Trading API
/// keeps ADDING chains (Base, Arbitrum, Robinhood, Linea, Unichain, Ink, Monad,
/// XLayer, …), so instead of a hardcoded list that drifts, we accept any
/// non-Solana chain and let the Trading API be the authority — it rejects a
/// chain it doesn't route with a clean error we surface as a 400.
pub fn is_uniswap_chain(chain_id: u64) -> bool {
    use crate::services::relay::chain_id as c;
    chain_id != 0 && chain_id != c::SOLANA && chain_id != c::SOLANA_LEGACY_ID
}

/// What `/actions/uniswap/quote` returns: a preview for the card plus everything
/// the frontend needs to finish the swap — the opaque Uniswap `quote` (echoed
/// verbatim to `/swap`), the EIP-712 `permitData` to sign (ERC20), and any
/// Permit2 approval transaction that must land first.
#[derive(Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UniswapQuoteResult {
    pub action_type: String,
    pub chain_id: u64,
    pub chain_name: String,
    pub input: Value,
    pub output: Value,
    /// Human-readable amounts + symbols for the card (so the frontend doesn't
    /// need EVM token decimals).
    pub input_symbol: String,
    pub output_symbol: String,
    pub input_logo: Option<String>,
    pub output_logo: Option<String>,
    pub chain_logo: Option<String>,
    pub input_amount_display: String,
    pub output_amount_display: Option<String>,
    pub rate: Option<String>,
    pub description: String,
    pub estimated_gas_usd: Option<String>,
    pub price_impact: Option<f64>,
    pub slippage: Option<f64>,
    /// Opaque Uniswap quote — echoed back to `/swap` unchanged.
    pub quote: Value,
    /// EIP-712 typed data the user signs for Permit2 (absent for native input).
    pub permit_data: Option<Value>,
    /// A Permit2 approval transaction the user must send first, if the token
    /// isn't approved yet (absent when no approval is needed).
    pub approval: Option<Value>,
    pub needs_permit: bool,
}

/// Resolve the amount into the input token's base units, treating native
/// (zero address) as 18-decimals so we don't depend on it being listed.
async fn amount_base_units(
    http: &reqwest::Client,
    chain_id: u64,
    token_addr: &str,
    amount: &str,
) -> Result<String, AppError> {
    if token_addr.eq_ignore_ascii_case(NATIVE_TOKEN_ADDRESS) {
        // Native ETH: 18 decimals, scale here rather than via the token list.
        let a = amount.trim();
        if !a.contains('.') && !a.contains(',') {
            return Ok(a.to_string());
        }
        let norm = a.replace(',', ".");
        let (whole, frac) = norm.split_once('.').unwrap_or((norm.as_str(), ""));
        if frac.len() > 18 {
            return Err(AppError::InvalidParams("Too many decimal places for ETH (18)".into()));
        }
        let padded = format!("{frac:0<18}");
        let joined = format!("{}{}", whole.trim_start_matches('0'), padded);
        let trimmed = joined.trim_start_matches('0');
        return Ok(if trimmed.is_empty() { "0".into() } else { trimmed.to_string() });
    }
    to_base_units(http, chain_id, token_addr, amount).await
}

/// Call `POST /v1/quote`. Same-chain EVM only (validated by the caller).
pub async fn uniswap_quote(
    http: &reqwest::Client,
    swapper: &str,
    params: &CrossChainSwapParams,
) -> Result<UniswapQuoteResult, AppError> {
    let key = api_key()?;
    let chain = params.origin_chain_id;

    // Resolve token names/symbols → addresses on this chain.
    let token_in = resolve_evm_currency(http, chain, &params.origin_currency).await?;
    let token_out =
        resolve_evm_currency(http, params.destination_chain_id, &params.destination_currency).await?;
    // Scale `amount` to base units of the token it refers to: EXACT_INPUT means
    // the amount is the INPUT (pay) token; EXACT_OUTPUT means it's the OUTPUT
    // (receive) token. Scaling by the wrong side's decimals would misprice the
    // whole swap.
    let is_exact_output = params.trade_type.eq_ignore_ascii_case("EXACT_OUTPUT");
    let amount_base = if is_exact_output {
        amount_base_units(http, params.destination_chain_id, &token_out, &params.amount).await?
    } else {
        amount_base_units(http, chain, &token_in, &params.amount).await?
    };

    let mut body = json!({
        "type": if params.trade_type.eq_ignore_ascii_case("EXACT_OUTPUT") { "EXACT_OUTPUT" } else { "EXACT_INPUT" },
        "tokenInChainId": chain,
        "tokenOutChainId": params.destination_chain_id,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "amount": amount_base,
        "swapper": swapper,
        "routingPreference": "BEST_PRICE",
    });
    // Slippage: Uniswap wants a percentage float; our params carry bps.
    if params.slippage_bps > 0 {
        body["slippageTolerance"] = json!(params.slippage_bps as f64 / 100.0);
    }
    // Integrator fee. The current documented field is `integratorFee` (singular,
    // {bps, recipient}; fractional bps allowed with the x-universal-router-version
    // 2.1.1 header). We ALSO send the older `integratorFees` (plural array) that
    // this gateway version still validates, so whichever the endpoint honours
    // applies. NOTE: verified live 2026-08-24 that neither actually deducts a fee
    // on this key yet (quote portions stay 100% to the swapper) — the field is
    // wired and fee-ready, but the fee is effectively 0 until Uniswap's endpoint
    // honours it for our recipient. Economics only book a fee when
    // UNISWAP_FEE_ACTIVE=true (see the record handler), so no cashback is owed
    // until it truly applies. Recipient reuses RELAY_FEE_RECIPIENT.
    if let Some(recipient) = fee_recipient() {
        body["integratorFee"] = json!({ "bps": fee_bps(), "recipient": recipient });
        body["integratorFees"] = json!([{ "bips": fee_bps(), "recipient": recipient }]);
    }

    let resp = http
        .post(format!("{UNISWAP_TRADE_API}/quote"))
        .header("x-api-key", &key)
        .header("x-universal-router-version", "2.1.1")
        .header("content-type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Uniswap quote request failed: {e}")))?;

    let status = resp.status();
    let data: Value = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Uniswap quote parse failed: {e}")))?;
    if !status.is_success() {
        return Err(uniswap_api_error(status, &data));
    }

    let quote = data.get("quote").cloned().unwrap_or(Value::Null);
    if quote.is_null() {
        return Err(AppError::Internal("Uniswap returned no quote".into()));
    }
    let input = quote.get("input").cloned().unwrap_or(json!({}));
    let output = quote.get("output").cloned().unwrap_or(json!({}));
    let chain_name = get_chain_name(chain);

    // Display symbols: native (zero address) → the chain's native ticker (ETH/
    // BNB/…), so the card never shows "0x0000…0000" as a token. Otherwise prefer
    // the caller's currency token, then the quote leg's symbol, then a short addr.
    let in_sym = if token_in.eq_ignore_ascii_case(NATIVE_TOKEN_ADDRESS) {
        native_symbol(chain).to_string()
    } else {
        display_symbol(&params.origin_currency, &input, &token_in)
    };
    let out_sym = if token_out.eq_ignore_ascii_case(NATIVE_TOKEN_ADDRESS) {
        native_symbol(params.destination_chain_id).to_string()
    } else {
        display_symbol(&params.destination_currency, &output, &token_out)
    };

    // Human-readable pay + receive amounts from the QUOTE's actual legs (not the
    // request `amount`), so both sides are right for EXACT_INPUT AND EXACT_OUTPUT:
    // on EXACT_OUTPUT the pay side is the computed input, not the requested output.
    let in_amount = input.get("amount").and_then(|v| v.as_str()).unwrap_or("0");
    let out_amount = output.get("amount").and_then(|v| v.as_str()).unwrap_or("0");
    let in_decimals = crate::services::relay::relay_token_decimals(http, chain, &token_in)
        .await
        .unwrap_or(18);
    let out_decimals = crate::services::relay::relay_token_decimals(http, params.destination_chain_id, &token_out)
        .await
        .unwrap_or(18);
    let in_display = format_units(in_amount, in_decimals);
    let out_display = format_units(out_amount, out_decimals);
    // Implied rate: 1 in_sym = N out_sym.
    let rate = match (in_display.parse::<f64>().ok(), out_display.parse::<f64>().ok()) {
        (Some(a), Some(o)) if a > 0.0 => Some(format!("1 {in_sym} = {:.6} {out_sym}", o / a)),
        _ => None,
    };

    let description = format!(
        "Swap {} {} → {} {} on {} (via Uniswap)",
        in_display, in_sym, out_display, out_sym, chain_name
    );

    let permit_data = data
        .get("permitData")
        .filter(|v| !v.is_null())
        .cloned();
    // Permit2 needs a one-time ERC20 approve of the input token to the Permit2
    // contract before the permit signature works. The quote flags it via
    // `isTokenApprovalApplicable` but doesn't carry the tx, so fetch it from
    // /check_approval (null if already approved). Native input never needs it.
    let is_native = token_in.eq_ignore_ascii_case(NATIVE_TOKEN_ADDRESS);
    let needs_approval = data
        .get("isTokenApprovalApplicable")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let approval = if !is_native && needs_approval {
        uniswap_check_approval(http, swapper, &token_in, &amount_base, chain).await
    } else {
        None
    };

    Ok(UniswapQuoteResult {
        action_type: "uniswap_swap".to_string(),
        chain_id: chain,
        chain_name: chain_name.to_string(),
        input_symbol: in_sym,
        output_symbol: out_sym,
        input_logo: crate::services::relay::relay_token_logo(http, chain, &token_in).await,
        output_logo: crate::services::relay::relay_token_logo(http, params.destination_chain_id, &token_out).await,
        chain_logo: crate::services::relay::relay_chain_icon(http, chain).await,
        input_amount_display: in_display,
        output_amount_display: Some(out_display),
        rate,
        input,
        output,
        description,
        estimated_gas_usd: quote.get("gasFeeUSD").and_then(|v| v.as_str()).map(|s| s.to_string()),
        price_impact: quote.get("priceImpact").and_then(|v| v.as_f64()),
        slippage: quote.get("slippage").and_then(|v| v.as_f64()),
        quote,
        permit_data,
        approval,
        needs_permit: data.get("permitData").map(|v| !v.is_null()).unwrap_or(false),
    })
}

/// Call `POST /v1/swap` with the (frontend-signed) permit to get the final EVM
/// transaction `{to, from, data, value, chainId, gasLimit, maxFeePerGas, ...}`.
/// `quote` is the opaque object returned by [`uniswap_quote`]; `permit_data` +
/// `signature` are required for ERC20 inputs and omitted for native.
pub async fn uniswap_swap(
    http: &reqwest::Client,
    quote: &Value,
    permit_data: Option<&Value>,
    signature: Option<&str>,
) -> Result<Value, AppError> {
    let key = api_key()?;
    let mut body = json!({ "quote": quote, "simulateTransaction": false });
    if let (Some(pd), Some(sig)) = (permit_data, signature) {
        if !pd.is_null() && !sig.is_empty() {
            body["permitData"] = pd.clone();
            body["signature"] = json!(sig);
        }
    }

    let resp = http
        .post(format!("{UNISWAP_TRADE_API}/swap"))
        .header("x-api-key", &key)
        .header("x-universal-router-version", "2.1.1")
        .header("content-type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Uniswap swap request failed: {e}")))?;

    let status = resp.status();
    let data: Value = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Uniswap swap parse failed: {e}")))?;
    if !status.is_success() {
        return Err(uniswap_api_error(status, &data));
    }

    data.get("swap")
        .cloned()
        .ok_or_else(|| AppError::Internal("Uniswap /swap returned no transaction".into()))
}

/// Native USDC (address, decimals) per chain — the reference asset for pricing a
/// swap's USD notional server-side (we never trust a client-supplied USD value).
/// USDC is 6 decimals everywhere except BSC (18).
fn usdc_for_chain(chain: u64) -> Option<(&'static str, u32)> {
    use crate::services::relay::chain_id as c;
    Some(match chain {
        c::ETHEREUM => ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
        c::BASE => ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        c::ARBITRUM => ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
        c::OPTIMISM => ("0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6),
        c::POLYGON => ("0x3c499c542cEF5E3811e1192ce70d8cc03d5c3359", 6),
        c::BSC => ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18),
        c::AVALANCHE => ("0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", 6),
        _ => return None,
    })
}

/// A valid, well-known EOA used only as the `swapper` for read-only pricing
/// quotes (Uniswap requires the field; nothing is executed).
const PRICE_QUOTE_SWAPPER: &str = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045";

/// Server-side USD value of `amount_base` of `token` on `chain`, priced by
/// quoting the token into USDC. Returns None when we can't price it (no USDC on
/// the chain, or the quote fails) — the caller then records volume without a
/// dollar figure rather than trusting the client. If the token already IS USDC,
/// the amount is converted directly.
pub async fn uniswap_price_usd(
    http: &reqwest::Client,
    chain: u64,
    token: &str,
    amount_base: &str,
) -> Option<f64> {
    let (usdc, usdc_dec) = usdc_for_chain(chain)?;
    let scale = 10f64.powi(usdc_dec as i32);
    if token.eq_ignore_ascii_case(usdc) {
        return amount_base.parse::<f64>().ok().map(|a| a / scale);
    }
    let key = api_key().ok()?;
    let body = json!({
        "type": "EXACT_INPUT",
        "tokenInChainId": chain,
        "tokenOutChainId": chain,
        "tokenIn": token,
        "tokenOut": usdc,
        "amount": amount_base,
        "swapper": PRICE_QUOTE_SWAPPER,
        "routingPreference": "BEST_PRICE",
    });
    let resp = http
        .post(format!("{UNISWAP_TRADE_API}/quote"))
        .header("x-api-key", &key)
        .header("content-type", "application/json")
        .json(&body)
        .send()
        .await
        .ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let data: Value = resp.json().await.ok()?;
    data.pointer("/quote/output/amount")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .map(|a| a / scale)
}

/// Fetch the one-time ERC20 approval tx (input token → Permit2). Returns None
/// when already approved or on any error (the caller treats None as "no approval
/// needed"). The user sends this before the permit signature works.
async fn uniswap_check_approval(
    http: &reqwest::Client,
    wallet: &str,
    token: &str,
    amount: &str,
    chain: u64,
) -> Option<Value> {
    let key = api_key().ok()?;
    let body = json!({
        "walletAddress": wallet,
        "token": token,
        "amount": amount,
        "chainId": chain,
    });
    let resp = http
        .post(format!("{UNISWAP_TRADE_API}/check_approval"))
        .header("x-api-key", &key)
        .header("content-type", "application/json")
        .json(&body)
        .send()
        .await
        .ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let data: Value = resp.json().await.ok()?;
    data.get("approval").filter(|v| !v.is_null()).cloned()
}

/// The native gas token's ticker for an EVM chain (for display when the token is
/// the zero address). Most EVM chains are ETH; a few differ.
fn native_symbol(chain_id: u64) -> &'static str {
    use crate::services::relay::chain_id as c;
    match chain_id {
        c::BSC => "BNB",
        c::AVALANCHE => "AVAX",
        c::POLYGON => "POL",
        c::CELO => "CELO",
        _ => "ETH",
    }
}

/// Display symbol: prefer the caller's currency token when it's a ticker (not an
/// 0x address), else the quote leg's symbol, else a shortened address.
fn display_symbol(param_currency: &str, leg: &Value, addr: &str) -> String {
    let pc = param_currency.trim();
    if !pc.is_empty() && !pc.starts_with("0x") && pc.len() <= 12 {
        return pc.to_uppercase();
    }
    symbol_of(leg, addr)
}

/// Format a base-unit integer string into a human decimal string with `decimals`
/// places, trimming trailing zeros. Text math (no f64) so 18-decimal wei is exact.
fn format_units(amount: &str, decimals: u8) -> String {
    let a = amount.trim().trim_start_matches('0');
    let a = if a.is_empty() { "0" } else { a };
    let d = decimals as usize;
    if d == 0 {
        return a.to_string();
    }
    let padded = format!("{a:0>width$}", width = d + 1); // ensure at least one leading whole digit
    let split = padded.len() - d;
    let whole = &padded[..split];
    let frac = padded[split..].trim_end_matches('0');
    if frac.is_empty() {
        whole.to_string()
    } else {
        format!("{whole}.{frac}")
    }
}

/// Best-effort symbol for a quote input/output leg: the object may carry a
/// `token` address only, so fall back to a shortened address.
fn symbol_of(leg: &Value, addr: &str) -> String {
    leg.get("symbol")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
            if addr.len() > 10 {
                format!("{}…{}", &addr[..6], &addr[addr.len() - 4..])
            } else {
                addr.to_string()
            }
        })
}

/// Turn a Uniswap error body into an AppError. A 4xx is the user's input being
/// rejected (bad token, no route, amount too small) → InvalidParams (400); a 5xx
/// is upstream → Internal.
fn uniswap_api_error(status: reqwest::StatusCode, body: &Value) -> AppError {
    let msg = body
        .get("detail")
        .and_then(|v| v.as_str())
        .or_else(|| body.get("message").and_then(|v| v.as_str()))
        .or_else(|| body.get("errorCode").and_then(|v| v.as_str()))
        .unwrap_or("Uniswap request failed")
        .to_string();
    if status.is_client_error() && status != reqwest::StatusCode::TOO_MANY_REQUESTS {
        AppError::InvalidParams(msg)
    } else {
        AppError::Internal(format!("Uniswap API error ({status}): {msg}"))
    }
}

// ────────────────────────────────────────────────────────────────────────────
// Pool listing (Phase 3a of the Uniswap LP program).
//
// Uniswap's LP API has no pool-discovery endpoint, so we list pools via
// DexScreener (free, no key, already used elsewhere in the stack): its search
// returns Uniswap pairs on a given chain with pair address, v2/v3/v4 label,
// TVL, 24h volume and price. That's everything the list card + the row→add
// hand-off need; the fee tier / tickSpacing (absent from DexScreener) are read
// on-chain at add-liquidity time.
// ────────────────────────────────────────────────────────────────────────────

const DEXSCREENER_TOKEN_PAIRS: &str = "https://api.dexscreener.com/token-pairs/v1";

fn is_zero_address(addr: &str) -> bool {
    let a = addr.trim().trim_start_matches("0x");
    a.is_empty() || a.chars().all(|c| c == '0')
}

/// Wrapped-native ERC-20 per chain — the tradeable stand-in for native ETH/BNB/…
/// (native has no DexScreener token-pairs page). Lowercased.
fn wrapped_native_address(slug: &str) -> &'static str {
    match slug {
        "ethereum"  => "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", // WETH
        "base"      => "0x4200000000000000000000000000000000000006", // WETH
        "optimism"  => "0x4200000000000000000000000000000000000006", // WETH
        "arbitrum"  => "0x82af49447d8a07e3bd95bd0d56f35241523fbab1", // WETH
        "polygon"   => "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270", // WMATIC
        "bsc"       => "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", // WBNB
        "avalanche" => "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7", // WAVAX
        "blast"     => "0x4300000000000000000000000000000000000004", // WETH
        "celo"      => "0x471ece3750da237f93b8e339c536989b8978a438", // CELO
        _           => "0x4200000000000000000000000000000000000006", // WETH default (OP-stack)
    }
}

/// Map a chain name or numeric id to the DexScreener chain slug. Uniswap's
/// major EVM chains only — DexScreener doesn't index every rollup.
fn dexscreener_chain_slug(chain: &str) -> Option<&'static str> {
    match chain.trim().to_lowercase().as_str() {
        "ethereum" | "eth" | "mainnet" | "1" => Some("ethereum"),
        "base" | "8453" => Some("base"),
        "arbitrum" | "arb" | "42161" => Some("arbitrum"),
        "optimism" | "op" | "10" => Some("optimism"),
        "polygon" | "matic" | "pol" | "137" => Some("polygon"),
        "bsc" | "bnb" | "binance" | "56" => Some("bsc"),
        "avalanche" | "avax" | "43114" => Some("avalanche"),
        "unichain" | "130" => Some("unichain"),
        "blast" | "81457" => Some("blast"),
        "celo" | "42220" => Some("celo"),
        "zora" | "7777777" => Some("zora"),
        _ => None,
    }
}

/// A friendly default search term per chain so "show me Uniswap pools on Base"
/// (no pair named) still returns the chain's deepest pools.
fn default_pool_query(slug: &str) -> &'static str {
    match slug {
        "polygon" => "WMATIC USDC",
        "bsc" => "WBNB USDT",
        "avalanche" => "WAVAX USDC",
        _ => "WETH USDC",
    }
}

#[derive(Debug, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UniswapGetPoolsParams {
    /// Chain name or numeric id. Required — Uniswap is multi-chain and a pool
    /// only means anything on a specific chain.
    #[serde(default)]
    pub chain: Option<String>,
    /// Free-text pair / token query (e.g. "ETH USDC", "WETH", a token address).
    #[serde(default)]
    pub query: Option<String>,
    /// Filter to a single protocol version: "v2" | "v3" | "v4" (default: all).
    #[serde(default)]
    pub version: Option<String>,
}

pub fn validate_uniswap_get_pools_params(p: &UniswapGetPoolsParams) -> Result<(), AppError> {
    let chain = p.chain.as_deref().unwrap_or("").trim();
    if chain.is_empty() {
        return Err(AppError::InvalidParams(
            "A chain is required to list Uniswap pools (e.g. base, arbitrum, ethereum).".into(),
        ));
    }
    if dexscreener_chain_slug(chain).is_none() {
        return Err(AppError::InvalidParams(format!(
            "Uniswap pool listing isn't available on '{chain}'. Try ethereum, base, arbitrum, optimism, polygon, bsc."
        )));
    }
    Ok(())
}

pub async fn build_uniswap_get_pools(
    http: &reqwest::Client,
    params: &UniswapGetPoolsParams,
) -> Result<BuildResponse, AppError> {
    let chain = params.chain.as_deref().unwrap_or("").trim();
    let slug = dexscreener_chain_slug(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("Unsupported chain '{chain}'")))?;
    let query = params
        .query
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| default_pool_query(slug));
    let version_filter = params
        .version
        .as_deref()
        .map(|v| v.trim().to_lowercase())
        .filter(|v| matches!(v.as_str(), "v2" | "v3" | "v4"));

    // DexScreener's global /search is IP-filtered for datacenter egress (returns
    // an empty result set from our host), so we use the per-token /token-pairs
    // endpoint — which needs a token ADDRESS. Resolve the query's tokens to
    // addresses on this chain, anchor the lookup on the first, and (when a
    // second token is named) keep only pools that hold BOTH.
    let chain_id = dexscreener_slug_to_chain_id(slug);
    let mut resolved: Vec<String> = Vec::new();
    for tok in query.split_whitespace().filter(|s| !s.is_empty()).take(2) {
        if let Ok(addr) = resolve_evm_currency(http, chain_id, tok).await {
            // Native (all-zero) has no ERC-20 pairs page — anchor on the chain's
            // wrapped native instead so "ETH" still resolves to real pools.
            let a = if is_zero_address(&addr) {
                wrapped_native_address(slug).to_string()
            } else {
                addr.to_lowercase()
            };
            if !a.is_empty() && !resolved.contains(&a) {
                resolved.push(a);
            }
        }
    }
    if resolved.is_empty() {
        resolved.push(wrapped_native_address(slug).to_string());
    }
    let anchor = resolved[0].clone();
    let other: Option<String> = resolved.get(1).cloned();

    let url = format!("{DEXSCREENER_TOKEN_PAIRS}/{slug}/{anchor}");
    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("DexScreener request failed: {e}")))?;
    let status = resp.status();
    let body: Value = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("DexScreener bad JSON: {e}")))?;
    if !status.is_success() {
        return Err(AppError::Internal(format!("DexScreener error ({status})")));
    }

    // /token-pairs returns a top-level array of pairs (not { pairs: [...] }).
    let empty = vec![];
    let pairs = body.as_array().unwrap_or(&empty);
    let mut rows: Vec<Value> = pairs
        .iter()
        .filter(|p| {
            p.get("chainId").and_then(|c| c.as_str()) == Some(slug)
                && p.get("dexId").and_then(|d| d.as_str()) == Some("uniswap")
        })
        .filter(|p| match &other {
            None => true,
            Some(o) => {
                let b = p.get("baseToken").and_then(|t| t.get("address")).and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                let q = p.get("quoteToken").and_then(|t| t.get("address")).and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                b == *o || q == *o
            }
        })
        .filter_map(|p| {
            // Version from DexScreener labels (["v3"]); default v3 when unlabelled.
            let version = p
                .get("labels")
                .and_then(|l| l.as_array())
                .and_then(|a| a.iter().find_map(|x| x.as_str()))
                .map(|s| s.to_lowercase())
                .unwrap_or_else(|| "v3".to_string());
            if let Some(ref vf) = version_filter {
                if &version != vf {
                    return None;
                }
            }
            let base = p.get("baseToken")?;
            let quote = p.get("quoteToken")?;
            let tvl = p
                .get("liquidity")
                .and_then(|l| l.get("usd"))
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0);
            Some(json!({
                "pairAddress":  p.get("pairAddress").and_then(|v| v.as_str()).unwrap_or(""),
                "version":      version,
                "baseSymbol":   base.get("symbol").and_then(|v| v.as_str()).unwrap_or(""),
                "quoteSymbol":  quote.get("symbol").and_then(|v| v.as_str()).unwrap_or(""),
                "baseAddress":  base.get("address").and_then(|v| v.as_str()).unwrap_or(""),
                "quoteAddress": quote.get("address").and_then(|v| v.as_str()).unwrap_or(""),
                "tvlUsd":       tvl,
                "volume24hUsd": p.get("volume").and_then(|v| v.get("h24")).and_then(|v| v.as_f64()).unwrap_or(0.0),
                "priceUsd":     p.get("priceUsd").and_then(|v| v.as_str()).unwrap_or(""),
                "url":          p.get("url").and_then(|v| v.as_str()).unwrap_or(""),
                "chain":        slug,
            }))
        })
        .collect();

    // Deepest liquidity first — that's the pool the user most likely wants.
    rows.sort_by(|a, b| {
        let av = a.get("tvlUsd").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let bv = b.get("tvlUsd").and_then(|v| v.as_f64()).unwrap_or(0.0);
        bv.partial_cmp(&av).unwrap_or(std::cmp::Ordering::Equal)
    });
    rows.truncate(30);

    let chain_name = get_chain_name(dexscreener_slug_to_chain_id(slug)).to_string();
    let description = if rows.is_empty() {
        format!("No Uniswap pools found for \"{query}\" on {chain_name}.")
    } else {
        format!("Uniswap pools on {chain_name} — {} matching \"{query}\".", rows.len())
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "uniswap_pools".to_string(),
            description,
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: json!({}),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(json!({ "chain": slug, "chainName": chain_name, "query": query, "pools": rows })),
    })
}

/// Reverse of dexscreener_chain_slug for the human chain name lookup.
fn dexscreener_slug_to_chain_id(slug: &str) -> u64 {
    match slug {
        "ethereum" => 1,
        "base" => 8453,
        "arbitrum" => 42161,
        "optimism" => 10,
        "polygon" => 137,
        "bsc" => 56,
        "avalanche" => 43114,
        "unichain" => 130,
        "blast" => 81457,
        "celo" => 42220,
        "zora" => 7777777,
        _ => 0,
    }
}

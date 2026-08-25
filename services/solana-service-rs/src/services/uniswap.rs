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
use crate::services::relay::{resolve_evm_currency, to_base_units, get_chain_name, NATIVE_TOKEN_ADDRESS, CrossChainSwapParams, relay_token_logo, relay_chain_icon};

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

fn is_valid_evm_address(s: &str) -> bool {
    let s = s.trim();
    s.len() == 42 && s.starts_with("0x") && s[2..].chars().all(|c| c.is_ascii_hexdigit())
}

/// A gas-token symbol that maps to the chain's wrapped native (WETH/WBNB/…).
fn is_native_symbol(sym: &str) -> bool {
    matches!(
        sym.trim().to_uppercase().as_str(),
        "ETH" | "WETH" | "BNB" | "WBNB" | "MATIC" | "WMATIC" | "POL" | "AVAX" | "WAVAX" | "CELO"
    )
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
        "robinhood" => "0x0bd7d308f8e1639fab988df18a8011f41eacad73", // WETH on Robinhood Chain
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
        "robinhood" | "4663" => Some("robinhood"),
        _ => None,
    }
}

/// Process-wide logo cache (address → URL). Only SUCCESSFUL resolutions are
/// stored, so a transient Blockscout rate-limit (429) never poisons it — and a
/// token resolved once is never re-fetched for the life of the process, which
/// keeps us well under Blockscout's rate limit no matter how many pool lists
/// are requested.
static LOGO_CACHE: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<String, String>>> =
    std::sync::OnceLock::new();

fn logo_cache() -> &'static std::sync::Mutex<std::collections::HashMap<String, String>> {
    LOGO_CACHE.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

/// A token's logo URL for the pool list. DexScreener's image CDN covers the
/// major chains; Robinhood tokens 404 there but carry an icon_url on Robinhood's
/// Blockscout. Backed by a process-wide cache (successes only).
async fn token_logo_for(http: &reqwest::Client, slug: &str, addr: &str) -> Option<String> {
    if addr.is_empty() {
        return None;
    }
    // Uniswap V4 uses the zero address for NATIVE ETH/BNB/…, which isn't an ERC-20
    // and has no token page. Use the NATIVE coin's own logo — not the wrapped
    // token's (a "WETH"-branded icon on a plain-ETH pool reads as wrong).
    if is_zero_address(addr) {
        return native_coin_logo(slug).map(String::from);
    }
    let key = format!("{slug}:{}", addr.to_lowercase());
    if let Some(hit) = logo_cache().lock().ok().and_then(|c| c.get(&key).cloned()) {
        return Some(hit);
    }
    let bare = addr.to_lowercase();
    let logo = if slug == "robinhood" {
        let url = format!("https://robinhoodchain.blockscout.com/api/v2/tokens/{bare}");
        match http.get(&url).send().await {
            Ok(r) if r.status().is_success() => r
                .json::<Value>()
                .await
                .ok()
                .and_then(|j| j.get("icon_url").and_then(|v| v.as_str()).map(String::from))
                .filter(|s| !s.is_empty()),
            _ => None, // transient (429/5xx) — do NOT cache, retry next request
        }
    } else {
        Some(format!("https://dd.dexscreener.com/ds-data/tokens/{slug}/{bare}.png"))
    };
    if let Some(ref l) = logo {
        if let Ok(mut c) = logo_cache().lock() {
            c.insert(key, l.clone());
        }
    }
    logo
}

/// Logo for a chain's NATIVE gas coin (ETH/BNB/…). Native has no token contract
/// to read an icon from, so these are the canonical coin images. Chains whose
/// native is ETH share the ETH mark; a plain-ETH pool must not show a WETH icon.
fn native_coin_logo(slug: &str) -> Option<&'static str> {
    let eth = "https://assets.coingecko.com/coins/images/279/small/ethereum.png";
    Some(match slug {
        "ethereum" | "base" | "optimism" | "arbitrum" | "blast" | "zora" | "robinhood" => eth,
        "polygon" => "https://assets.coingecko.com/coins/images/4713/small/polygon.png",
        "bsc" => "https://assets.coingecko.com/coins/images/825/small/bnb-icon2_2x.png",
        "avalanche" => "https://assets.coingecko.com/coins/images/12559/small/Avalanche_Circle_RedWhite_Trans.png",
        "celo" => "https://assets.coingecko.com/coins/images/11090/small/InjXBNx9_400x400.jpg",
        _ => eth,
    })
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
            "Uniswap pool listing isn't available on '{chain}'. Try ethereum, base, arbitrum, optimism, polygon, bsc, robinhood."
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
    // endpoint — which needs a token ADDRESS. Resolve each query token to a
    // candidate address ON THIS CHAIN: an address stays as-is; a native/wrapped
    // symbol maps to the chain's wrapped native (reliable per-chain); anything
    // else goes through Relay as a HINT (Relay doesn't index every chain — e.g.
    // Robinhood — and may hand back a foreign-chain address, which simply yields
    // no pools here, so we never trust it as gospel).
    let chain_id = dexscreener_slug_to_chain_id(slug);
    let mut candidates: Vec<String> = Vec::new();
    for tok in query.split_whitespace().filter(|s| !s.is_empty()).take(2) {
        let a = if is_valid_evm_address(tok) {
            tok.to_lowercase()
        } else if is_native_symbol(tok) {
            wrapped_native_address(slug).to_string()
        } else {
            match resolve_evm_currency(http, chain_id, tok).await {
                Ok(addr) if !is_zero_address(&addr) => addr.to_lowercase(),
                Ok(_) => wrapped_native_address(slug).to_string(),
                Err(_) => continue,
            }
        };
        if !a.is_empty() && !candidates.contains(&a) {
            candidates.push(a);
        }
    }
    if candidates.is_empty() {
        candidates.push(wrapped_native_address(slug).to_string());
    }

    // Fetch token-pairs for EACH candidate and union the uniswap pools on this
    // chain (deduped by pool address). A candidate that resolved to a foreign
    // address just contributes nothing.
    let mut seen_pairs: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut all_pools: Vec<Value> = Vec::new();
    for cand in &candidates {
        let url = format!("{DEXSCREENER_TOKEN_PAIRS}/{slug}/{cand}");
        let resp = match http.get(&url).send().await {
            Ok(r) => r,
            Err(_) => continue,
        };
        if !resp.status().is_success() {
            continue;
        }
        let body: Value = match resp.json().await {
            Ok(b) => b,
            Err(_) => continue,
        };
        if let Some(arr) = body.as_array() {
            for p in arr {
                if p.get("chainId").and_then(|c| c.as_str()) != Some(slug) {
                    continue;
                }
                if p.get("dexId").and_then(|d| d.as_str()) != Some("uniswap") {
                    continue;
                }
                let pa = p.get("pairAddress").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                if pa.is_empty() || !seen_pairs.insert(pa) {
                    continue;
                }
                all_pools.push(p.clone());
            }
        }
    }

    // When two tokens were named, keep pools whose pair matches BOTH — filtered
    // by SYMBOL, not address. Address filtering was fragile: Relay could resolve
    // a symbol to a different-but-same-ticker contract than a given pool used, so
    // e.g. the deep V3 USDG/WETH pools got dropped and only V4 survived. Symbols
    // (with ETH≡WETH) are stable across a chain's pools and versions. Fall back
    // to the union if the filter empties.
    let norm_sym = |s: &str| -> String {
        let u = s.trim().to_uppercase();
        if u == "ETH" { "WETH".to_string() } else if u == "BNB" { "WBNB".to_string() }
        else if u == "MATIC" || u == "POL" { "WMATIC".to_string() }
        else if u == "AVAX" { "WAVAX".to_string() } else { u }
    };
    let query_syms: Vec<String> = query
        .split_whitespace()
        .filter(|s| !s.is_empty() && !is_valid_evm_address(s))
        .map(|s| norm_sym(s))
        .collect();
    let selected: Vec<Value> = if query_syms.len() >= 2 {
        let want0 = &query_syms[0];
        let want1 = &query_syms[1];
        let both: Vec<Value> = all_pools
            .iter()
            .filter(|p| {
                let bs = norm_sym(p.get("baseToken").and_then(|t| t.get("symbol")).and_then(|v| v.as_str()).unwrap_or(""));
                let qs = norm_sym(p.get("quoteToken").and_then(|t| t.get("symbol")).and_then(|v| v.as_str()).unwrap_or(""));
                let set = [bs.as_str(), qs.as_str()];
                set.contains(&want0.as_str()) && set.contains(&want1.as_str())
            })
            .cloned()
            .collect();
        if both.is_empty() { all_pools } else { both }
    } else {
        all_pools
    };

    let mut rows: Vec<Value> = selected
        .iter()
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

    // Attach a logo to each token. DexScreener's image CDN covers the major
    // chains but 404s on Robinhood, whose tokens carry a real icon_url on its
    // Blockscout. Resolve per UNIQUE address (the list is mostly one pair) and
    // inject baseLogo/quoteLogo so the card shows coins, not letter chips.
    for i in 0..rows.len() {
        let base_addr = rows[i].get("baseAddress").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let quote_addr = rows[i].get("quoteAddress").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let base_logo = token_logo_for(http, slug, &base_addr).await;
        let quote_logo = token_logo_for(http, slug, &quote_addr).await;
        if let Some(obj) = rows[i].as_object_mut() {
            obj.insert("baseLogo".into(), json!(base_logo));
            obj.insert("quoteLogo".into(), json!(quote_logo));
        }
    }

    // Symbol-level fallback: the SAME ticker can appear under different contracts
    // across versions (e.g. a V3 USDG whose contract has no Blockscout icon and a
    // V4 USDG whose contract does). Borrow a logo from any same-symbol token that
    // resolved one, so every USDG row shows the USDG coin, not a "U" chip.
    let mut sym_logo: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for r in &rows {
        for (sym_k, logo_k) in [("baseSymbol", "baseLogo"), ("quoteSymbol", "quoteLogo")] {
            let sym = r.get(sym_k).and_then(|v| v.as_str()).unwrap_or("").to_uppercase();
            if let Some(l) = r.get(logo_k).and_then(|v| v.as_str()) {
                if !sym.is_empty() && !l.is_empty() {
                    sym_logo.entry(sym).or_insert_with(|| l.to_string());
                }
            }
        }
    }
    for r in rows.iter_mut() {
        for (sym_k, logo_k) in [("baseSymbol", "baseLogo"), ("quoteSymbol", "quoteLogo")] {
            let missing = r.get(logo_k).map(|v| v.is_null()).unwrap_or(true);
            if !missing { continue; }
            let sym = r.get(sym_k).and_then(|v| v.as_str()).unwrap_or("").to_uppercase();
            if let Some(l) = sym_logo.get(&sym) {
                if let Some(obj) = r.as_object_mut() {
                    obj.insert(logo_k.into(), json!(l));
                }
            }
        }
    }

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
pub(crate) fn dexscreener_slug_to_chain_id(slug: &str) -> u64 {
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
        "robinhood" => 4663,
        _ => 0,
    }
}

// ────────────────────────────────────────────────────────────────────────────
// Add liquidity — open a V3 position (Phase 3b of the Uniswap LP program).
//
// Unlike a swap, the LP tx is built by Uniswap's LP API on a SEPARATE host
// (liquidity.api.uniswap.org, no /v1 prefix). Flow:
//   1. read the pool on-chain (fee, tickSpacing, token0/1, current tick) via
//      Alchemy RPC eth_call — DexScreener doesn't expose fee/tickSpacing;
//   2. turn the user's chosen price band into aligned tickLower/tickUpper;
//   3. POST /lp/create → the create calldata + the computed amount for BOTH
//      sides (the user supplies one; the pool ratio fixes the other);
//   4. POST /lp/check_approval → the ERC-20 approve() txs the position manager
//      needs (empty when already approved).
// The frontend signs the approvals (if any) then the create, via window.ethereum.
// ────────────────────────────────────────────────────────────────────────────

const UNISWAP_LP_API: &str = "https://liquidity.api.uniswap.org";
const MIN_TICK: i64 = -887272;
const MAX_TICK: i64 = 887272;

/// Alchemy JSON-RPC URL for a chain (reuses the ALCHEMY_API_KEY the EVM
/// portfolio uses). None → we can't read pools on that chain yet.
fn alchemy_rpc(chain_id: u64) -> Option<String> {
    let key = std::env::var("ALCHEMY_API_KEY").ok().filter(|s| !s.is_empty())?;
    let net = match chain_id {
        1 => "eth-mainnet",
        8453 => "base-mainnet",
        42161 => "arb-mainnet",
        10 => "opt-mainnet",
        137 => "polygon-mainnet",
        56 => "bnb-mainnet",
        4663 => "robinhood-mainnet",
        _ => return None,
    };
    Some(format!("https://{net}.g.alchemy.com/v2/{key}"))
}

/// One `eth_call`, returning the hex result string (0x…).
async fn eth_call(http: &reqwest::Client, rpc: &str, to: &str, data: &str) -> Result<String, AppError> {
    let body = json!({
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{ "to": to, "data": data }, "latest"],
    });
    let resp = http.post(rpc).json(&body).send().await
        .map_err(|e| AppError::Internal(format!("EVM RPC failed: {e}")))?;
    let v: Value = resp.json().await
        .map_err(|e| AppError::Internal(format!("EVM RPC bad JSON: {e}")))?;
    v.get("result").and_then(|r| r.as_str()).map(|s| s.to_string())
        .ok_or_else(|| AppError::Internal(format!("EVM RPC error: {}", v.get("error").map(|e| e.to_string()).unwrap_or_default())))
}

fn hex_to_u64(hex: &str) -> u64 {
    u64::from_str_radix(hex.trim_start_matches("0x").trim_start_matches('0'), 16).unwrap_or(0)
}

/// Parse a 32-byte hex word as a signed integer (int24 sign-extended).
fn hex_word_to_i64(word: &str) -> i64 {
    let w = word.trim_start_matches("0x");
    // Take the low 64 bits; a tick fits in far fewer, but sign lives in bit 255.
    let neg = w.chars().next().map(|c| c == 'f').unwrap_or(false) && w.len() >= 64;
    let low = &w[w.len().saturating_sub(16)..];
    let val = i128::from_str_radix(low, 16).unwrap_or(0);
    if neg { (val - (1i128 << 64)) as i64 } else { val as i64 }
}

struct V3Pool {
    fee: u32,
    tick_spacing: i64,
    token0: String,
    token1: String,
    current_tick: i64,
}

async fn read_v3_pool(http: &reqwest::Client, chain_id: u64, pool: &str) -> Result<V3Pool, AppError> {
    let rpc = alchemy_rpc(chain_id).ok_or_else(|| AppError::InvalidParams(format!(
        "Adding liquidity isn't available on {} yet.", get_chain_name(chain_id)
    )))?;
    let fee = hex_to_u64(&eth_call(http, &rpc, pool, "0xddca3f43").await?) as u32;
    let tick_spacing = hex_to_u64(&eth_call(http, &rpc, pool, "0xd0c93a7c").await?) as i64;
    let token0_raw = eth_call(http, &rpc, pool, "0x0dfe1681").await?;
    let token1_raw = eth_call(http, &rpc, pool, "0xd21220a7").await?;
    let slot0 = eth_call(http, &rpc, pool, "0x3850c7bd").await?;
    // token addresses are the low 20 bytes of a 32-byte word.
    let addr_from_word = |w: &str| -> String {
        let h = w.trim_start_matches("0x");
        if h.len() >= 40 { format!("0x{}", &h[h.len() - 40..]) } else { w.to_string() }
    };
    // slot0 word 0 = sqrtPriceX96, word 1 = tick (int24).
    let s = slot0.trim_start_matches("0x");
    let current_tick = if s.len() >= 128 {
        hex_word_to_i64(&s[64..128])
    } else {
        0
    };
    if fee == 0 || tick_spacing == 0 {
        return Err(AppError::InvalidParams(
            "That pool doesn't look like a Uniswap V3 pool (couldn't read its fee/tick spacing).".into(),
        ));
    }
    Ok(V3Pool {
        fee,
        tick_spacing,
        token0: addr_from_word(&token0_raw),
        token1: addr_from_word(&token1_raw),
        current_tick,
    })
}

/// A wallet's balance of a token on `chain_id`, as a human-readable string.
/// Native (zero address) → eth_getBalance; ERC-20 → balanceOf. Returns "0" when
/// the chain has no RPC or the call fails. Also returns the raw base-unit string.
pub async fn token_balance_of(
    http: &reqwest::Client,
    chain_id: u64,
    wallet: &str,
    token: &str,
) -> (String, String, u8) {
    let rpc = match alchemy_rpc(chain_id) { Some(r) => r, None => return ("0".into(), "0".into(), 18) };
    let dec = if is_zero_address(token) { 18 } else { erc20_decimals(http, chain_id, token).await };
    let raw_hex: Option<String> = if is_zero_address(token) {
        // eth_getBalance
        let body = json!({ "jsonrpc":"2.0","id":1,"method":"eth_getBalance","params":[wallet,"latest"] });
        match http.post(&rpc).json(&body).send().await {
            Ok(r) => futures_lite_json(r).await,
            Err(_) => None,
        }
    } else {
        // balanceOf(address) = 0x70a08231 + 32-byte padded address
        let data = format!("0x70a08231000000000000000000000000{}", token_pad(wallet));
        eth_call(http, &rpc, token, &data).await.ok()
    };
    let raw = raw_hex
        .as_deref()
        .map(hex_to_u128_dec)
        .unwrap_or_else(|| "0".to_string());
    (format_units(&raw, dec), raw, dec)
}

fn token_pad(addr: &str) -> String {
    addr.trim().trim_start_matches("0x").to_lowercase()
}

/// Parse a 0x-hex integer into a decimal string (u128 is plenty for balances).
fn hex_to_u128_dec(hex: &str) -> String {
    let h = hex.trim().trim_start_matches("0x").trim_start_matches('0');
    if h.is_empty() { return "0".into(); }
    u128::from_str_radix(h, 16).map(|n| n.to_string()).unwrap_or_else(|_| "0".into())
}

/// Read a JSON-RPC `result` hex string from a response (helper for eth_getBalance).
async fn futures_lite_json(r: reqwest::Response) -> Option<String> {
    let v: Value = r.json().await.ok()?;
    v.get("result").and_then(|x| x.as_str()).map(String::from)
}

/// ERC-20 `decimals()` via RPC (0x313ce567). Falls back to 18 — works for any
/// token on any chain we have an RPC for, unlike Relay's token list (which
/// doesn't cover every chain, e.g. Robinhood).
async fn erc20_decimals(http: &reqwest::Client, chain_id: u64, token: &str) -> u8 {
    // Native (zero address) isn't an ERC-20 — decimals() would read nothing and
    // yield 0, mangling amount formatting. Native gas coins are 18 decimals.
    if is_zero_address(token) {
        return 18;
    }
    let rpc = match alchemy_rpc(chain_id) { Some(r) => r, None => return 18 };
    match eth_call(http, &rpc, token, "0x313ce567").await {
        Ok(hex) => {
            let d = hex_to_u64(&hex) as u8;
            if d == 0 || d > 36 { 18 } else { d } // 0 usually means the call failed
        }
        Err(_) => 18,
    }
}

/// Scale a human decimal amount into base units by `decimals`, RPC-free.
fn scale_to_base_units(amount: &str, decimals: u8) -> Result<String, AppError> {
    let a = amount.trim().replace(',', ".");
    if a.is_empty() { return Err(AppError::InvalidParams("Amount is empty".into())); }
    let (whole, frac) = a.split_once('.').unwrap_or((a.as_str(), ""));
    let d = decimals as usize;
    if frac.len() > d {
        return Err(AppError::InvalidParams(format!("Too many decimal places (max {d})")));
    }
    let padded = format!("{frac:0<width$}", width = d);
    let joined = format!("{}{}", whole.trim_start_matches('0'), padded);
    let trimmed = joined.trim_start_matches('0');
    Ok(if trimmed.is_empty() { "0".into() } else { trimmed.to_string() })
}

fn align_tick(tick: i64, spacing: i64) -> i64 {
    (tick as f64 / spacing as f64).round() as i64 * spacing
}

/// Turn a ±percent band (or "full") into aligned tick bounds around the current
/// tick. `range_percent` None or >= 999 → full range.
fn tick_bounds(current: i64, spacing: i64, range_percent: Option<f64>) -> (i64, i64) {
    match range_percent {
        Some(p) if p > 0.0 && p < 900.0 => {
            // ticks per (1 + p/100): ln(1+p/100)/ln(1.0001)
            let delta = ((1.0 + p / 100.0).ln() / 1.0001_f64.ln()).round() as i64;
            let lo = align_tick(current - delta, spacing).max((MIN_TICK / spacing) * spacing);
            let hi = align_tick(current + delta, spacing).min((MAX_TICK / spacing) * spacing);
            (lo, hi)
        }
        _ => {
            // Full range = the nearest USABLE ticks to the min/max. Integer
            // division truncates toward zero, which for these bounds yields
            // exactly nearestUsableTick(MIN)/(MAX) (e.g. ±887220 for spacing 60).
            let lo = (MIN_TICK / spacing) * spacing;
            let hi = (MAX_TICK / spacing) * spacing;
            (lo, hi)
        }
    }
}

#[derive(Debug, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UniswapAddLiquidityParams {
    pub chain: Option<String>,
    #[serde(alias = "poolAddress", alias = "pool")]
    pub pool_address: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    /// The token the user is depositing an amount OF (address or symbol).
    #[serde(alias = "inputToken", alias = "token", alias = "amountToken")]
    pub input_token: Option<String>,
    /// Human amount of `input_token`.
    pub amount: Option<String>,
    /// Price band as ±percent around the current price; omit / "full" → full range.
    #[serde(default, alias = "rangePercent")]
    pub range_percent: Option<f64>,
    pub wallet: Option<String>,
    /// The pool's two token addresses, carried from the pool row. Required for V4
    /// (its pool is a bytes32 id, not a contract, so we can't read token0/token1
    /// via eth_call); optional for V3 (read on-chain).
    #[serde(default)]
    pub token0: Option<String>,
    #[serde(default)]
    pub token1: Option<String>,
    /// V4 Permit2: the batch-permit object (echoed from a prior build) the user
    /// signed, plus the signature. When present, the create is finalized with
    /// them so the PositionManager can pull the ERC-20 legs via Permit2.
    #[serde(default)]
    pub permit_data: Option<Value>,
    #[serde(default)]
    pub permit_signature: Option<String>,
}

/// Try a full-range V4 create for each standard tick spacing until one is
/// accepted (the API validates ticks against the pool's real spacing, so the
/// wrong ones fail with a TICK_ invariant we skip). V4 pools are bytes32 ids —
/// we can't read their spacing via eth_call — so we discover it this way.
#[allow(clippy::too_many_arguments)]
async fn v4_full_range_create(
    http: &reqwest::Client,
    key: &str,
    chain_id: u64,
    wallet: &str,
    token0: &str,
    token1: &str,
    pool_id: &str,
    input_addr: &str,
    amount_base: &str,
    permit_data: Option<&Value>,
    signature: Option<&str>,
) -> Result<Value, AppError> {
    let mut last: Option<(reqwest::StatusCode, Value)> = None;
    for spacing in [60i64, 10, 200, 1] {
        let lo = (MIN_TICK / spacing) * spacing;
        let hi = (MAX_TICK / spacing) * spacing;
        let signed = permit_data.is_some() && signature.is_some();
        let mut body = json!({
            "walletAddress": wallet, "protocol": "V4", "chainId": chain_id,
            "existingPool": { "token0Address": token0, "token1Address": token1, "poolReference": pool_id },
            "independentToken": { "tokenAddress": input_addr, "amount": amount_base },
            "tickBounds": { "tickLower": lo, "tickUpper": hi },
            // Simulate ONLY the final (signed) create — it validates the full
            // permit+mint on-chain and turns a would-be revert into a clean 4xx
            // we surface before the user signs the tx. The unsigned probe can't
            // simulate (no allowance yet), so leave it off there.
            "simulateTransaction": signed,
        });
        // Finalize with the signed Permit2 batch so the position manager can pull
        // the ERC-20 leg on-chain (V4 pulls via Permit2, not a plain allowance).
        if let (Some(pd), Some(sig)) = (permit_data, signature) {
            body["batchPermitData"] = pd.clone();
            body["signature"] = json!(sig);
        }
        let resp = http.post(format!("{UNISWAP_LP_API}/lp/create"))
            .header("x-api-key", key).json(&body).send().await
            .map_err(|e| AppError::Internal(format!("Uniswap LP create failed: {e}")))?;
        let st = resp.status();
        let j: Value = resp.json().await.unwrap_or(Value::Null);
        if st.is_success() {
            return Ok(j);
        }
        let msg = j.get("message").and_then(|v| v.as_str()).unwrap_or("");
        // Wrong spacing → tick invariant; anything else is a real error, bail.
        if !msg.contains("TICK_") {
            return Err(uniswap_api_error(st, &j));
        }
        last = Some((st, j));
    }
    match last {
        Some((st, j)) => Err(uniswap_api_error(st, &j)),
        None => Err(AppError::Internal("V4 pool tick spacing not found".into())),
    }
}

/// Build the approval + create transactions for opening a Uniswap V3 LP position.
/// Returns a bespoke JSON payload the frontend signs step by step.
pub async fn build_uniswap_add_liquidity(
    http: &reqwest::Client,
    wallet: &str,
    params: &UniswapAddLiquidityParams,
) -> Result<Value, AppError> {
    let key = api_key()?;
    let chain = params.chain.as_deref().unwrap_or("").trim();
    let chain_id = dexscreener_slug_to_chain_id(&chain.to_lowercase());
    let chain_id = if chain_id != 0 { chain_id } else {
        chain.parse::<u64>().unwrap_or(0)
    };
    if chain_id == 0 {
        return Err(AppError::InvalidParams("A valid EVM chain is required.".into()));
    }
    let version = params.version.as_deref().unwrap_or("v3").trim().to_uppercase();
    if version != "V3" && version != "V4" {
        return Err(AppError::InvalidParams(format!(
            "Adding liquidity to Uniswap {version} pools is coming soon — V3 and V4 are supported now."
        )));
    }
    let pool = params.pool_address.as_deref().map(str::trim).filter(|s| !s.is_empty())
        .ok_or_else(|| AppError::InvalidParams("A pool is required.".into()))?;
    let amount = params.amount.as_deref().map(str::trim).filter(|s| !s.is_empty())
        .ok_or_else(|| AppError::InvalidParams("An amount is required.".into()))?;
    let input_token_in = params.input_token.as_deref().map(str::trim).filter(|s| !s.is_empty())
        .ok_or_else(|| AppError::InvalidParams("Which token to deposit is required.".into()))?;

    // Resolve the input token to an address on this chain + its decimals, and
    // scale the amount to base units (common to V3 and V4).
    let input_addr = resolve_evm_currency(http, chain_id, input_token_in).await?;
    let input_addr_l = input_addr.to_lowercase();
    // Decimals from the token contract (RPC), not Relay's list — Relay doesn't
    // cover every chain (e.g. Robinhood), and the pool tokens are the authority.
    let in_dec = erc20_decimals(http, chain_id, &input_addr_l).await;
    let amount_base = scale_to_base_units(amount, in_dec)?;

    // Build the position. V3: read the pool contract (fee/tickSpacing/current
    // tick) → aligned tick band. V4: the pool is a bytes32 id (not a contract),
    // so tokens come from the card and we open FULL RANGE, discovering the
    // spacing via the LP API (concentrated ±% on V4 needs its StateView — later).
    let (token0, token1, fee_val, create_json, tick_lower, tick_upper) = if version == "V4" {
        let mut a = params.token0.clone().unwrap_or_default().trim().to_lowercase();
        let mut b = params.token1.clone().unwrap_or_default().trim().to_lowercase();
        if a.is_empty() || b.is_empty() {
            return Err(AppError::InvalidParams("V4 add-liquidity needs the pool's token addresses.".into()));
        }
        if a > b { std::mem::swap(&mut a, &mut b); } // token0 = lower address (V4 PoolKey order)
        let cj = v4_full_range_create(
            http, &key, chain_id, wallet, &a, &b, pool, &input_addr_l, &amount_base,
            params.permit_data.as_ref(), params.permit_signature.as_deref(),
        ).await?;
        let tl = cj.get("tickLower").and_then(|v| v.as_i64()).unwrap_or(0);
        let tu = cj.get("tickUpper").and_then(|v| v.as_i64()).unwrap_or(0);
        (a, b, 0u32, cj, tl, tu)
    } else {
        let p = read_v3_pool(http, chain_id, pool).await?;
        let (tl, tu) = tick_bounds(p.current_tick, p.tick_spacing, params.range_percent);
        let create_body = json!({
            "walletAddress": wallet,
            "protocol": "V3",
            "chainId": chain_id,
            "existingPool": {
                "token0Address": p.token0,
                "token1Address": p.token1,
                "fee": p.fee,
                "tickSpacing": p.tick_spacing,
                "poolReference": pool,
            },
            "independentToken": { "tokenAddress": input_addr_l, "amount": amount_base },
            "tickBounds": { "tickLower": tl, "tickUpper": tu },
            "simulateTransaction": false,
        });
        let create_resp = http
            .post(format!("{UNISWAP_LP_API}/lp/create"))
            .header("x-api-key", &key)
            .json(&create_body)
            .send()
            .await
            .map_err(|e| AppError::Internal(format!("Uniswap LP create failed: {e}")))?;
        let cstatus = create_resp.status();
        let cj: Value = create_resp.json().await
            .map_err(|e| AppError::Internal(format!("Uniswap LP create bad JSON: {e}")))?;
        if !cstatus.is_success() {
            return Err(uniswap_api_error(cstatus, &cj));
        }
        (p.token0, p.token1, p.fee, cj, tl, tu)
    };

    let amount0 = create_json.pointer("/token0/amount").and_then(|v| v.as_str()).unwrap_or("0").to_string();
    let amount1 = create_json.pointer("/token1/amount").and_then(|v| v.as_str()).unwrap_or("0").to_string();
    let create_tx = create_json.get("create").cloned().unwrap_or(Value::Null);

    // Approvals for both legs.
    let approve_body = json!({
        "walletAddress": wallet,
        "chainId": chain_id,
        "protocol": version,
        "lpTokens": [
            { "tokenAddress": token0, "amount": amount0 },
            { "tokenAddress": token1, "amount": amount1 },
        ],
    });
    let mut approvals: Vec<Value> = vec![];
    // V4 pulls ERC-20 legs via Permit2 → the user signs this batch permit and we
    // pass it back to /lp/create. Absent for V3 (plain allowance).
    let mut v4_batch_permit: Value = Value::Null;
    if let Ok(r) = http
        .post(format!("{UNISWAP_LP_API}/lp/check_approval"))
        .header("x-api-key", &key)
        .json(&approve_body)
        .send()
        .await
    {
        if r.status().is_success() {
            let j: Value = r.json().await.unwrap_or(Value::Null);
            approvals = j.get("transactions").and_then(|t| t.as_array()).map(|a| {
                a.iter().filter_map(|t| t.get("transaction").cloned()).collect()
            }).unwrap_or_default();
            v4_batch_permit = j.get("v4BatchPermitData").cloned().unwrap_or(Value::Null);
        }
    }
    // V4 needs a signed permit before the create tx will succeed; it's "ready" to
    // sign only once we have the batch-permit and don't yet hold a signature.
    let needs_permit = version == "V4"
        && !v4_batch_permit.is_null()
        && params.permit_signature.as_deref().unwrap_or("").is_empty();

    // Display decimals for both legs — read from the contracts (RPC), so pairs
    // with a 6-decimal stable (USDG/USDC) render correctly on every chain.
    let dec0 = erc20_decimals(http, chain_id, &token0).await;
    let dec1 = erc20_decimals(http, chain_id, &token1).await;

    Ok(json!({
        "actionType": "uniswap_add_liquidity",
        "chainId": chain_id,
        "chainName": get_chain_name(chain_id),
        "version": version.to_lowercase(),
        "poolAddress": pool,
        "fee": fee_val,
        "token0": {
            "address": token0,
            "amount": amount0,
            "amountDisplay": format_units(&amount0, dec0),
            "logo": relay_token_logo(http, chain_id, &token0).await,
        },
        "token1": {
            "address": token1,
            "amount": amount1,
            "amountDisplay": format_units(&amount1, dec1),
            "logo": relay_token_logo(http, chain_id, &token1).await,
        },
        "tickLower": tick_lower,
        "tickUpper": tick_upper,
        "minPrice": create_json.get("adjustedMinPrice").cloned().unwrap_or(Value::Null),
        "maxPrice": create_json.get("adjustedMaxPrice").cloned().unwrap_or(Value::Null),
        "approvals": approvals,
        "create": create_tx,
        "permitData": v4_batch_permit,
        "needsPermit": needs_permit,
        "chainLogo": relay_chain_icon(http, chain_id).await,
    }))
}

// ────────────────────────────────────────────────────────────────────────────
// Position listing (Phase 3c). Uniswap has no REST "list positions" on the
// trade/liquidity hosts, but the interface gateway that powers app.uniswap.org
// does — decoded across V2/V3/V4 and every chain (incl. Robinhood), no key,
// just an origin header. We proxy + normalize it for the portfolio.
// ────────────────────────────────────────────────────────────────────────────

const UNISWAP_INTERFACE_GATEWAY: &str = "https://interface.gateway.uniswap.org/v2/pools.v1.PoolsService/ListPositions";

/// Every chain we surface Uniswap positions for.
const POSITION_CHAIN_IDS: &[u64] = &[1, 8453, 42161, 10, 137, 56, 4663, 43114, 130, 81457];

/// Fetch the wallet's Uniswap positions (V2+V3+V4, all chains), normalized for
/// the portfolio: pair, per-leg amounts+symbols, fee tier, value, fees, range.
pub async fn uniswap_positions(http: &reqwest::Client, wallet: &str) -> Result<Value, AppError> {
    let body = json!({
        "address": wallet,
        "chainIds": POSITION_CHAIN_IDS,
        "protocolVersions": ["PROTOCOL_VERSION_V2", "PROTOCOL_VERSION_V3", "PROTOCOL_VERSION_V4"],
        "positionStatuses": ["POSITION_STATUS_IN_RANGE", "POSITION_STATUS_OUT_OF_RANGE"],
    });
    let resp = http
        .post(UNISWAP_INTERFACE_GATEWAY)
        .header("content-type", "application/json")
        .header("origin", "https://app.uniswap.org")
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Uniswap positions request failed: {e}")))?;
    if !resp.status().is_success() {
        return Err(AppError::Internal(format!("Uniswap positions error ({})", resp.status())));
    }
    let data: Value = resp.json().await
        .map_err(|e| AppError::Internal(format!("Uniswap positions bad JSON: {e}")))?;

    let empty = vec![];
    let raw = data.get("positions").and_then(|p| p.as_array()).unwrap_or(&empty);
    let mut out: Vec<Value> = Vec::new();
    for p in raw {
        let version = match p.get("protocolVersion").and_then(|v| v.as_str()) {
            Some(s) if s.contains("V2") => "v2",
            Some(s) if s.contains("V3") => "v3",
            _ => "v4",
        };
        let chain_id = p.get("chainId").and_then(|v| v.as_u64()).unwrap_or(0);
        // The pool position lives under v{2,3,4}Position.poolPosition.
        let pp = ["v4Position", "v3Position", "v2Position"].iter()
            .find_map(|k| p.get(*k))
            .and_then(|vp| vp.get("poolPosition").or(Some(vp)))
            .cloned()
            .unwrap_or(Value::Null);
        if pp.is_null() { continue; }

        let leg = |t: &str, amt: &str| -> Value {
            let tok = pp.get(t).cloned().unwrap_or(json!({}));
            let dec = tok.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18) as u8;
            let amount = pp.get(amt).and_then(|v| v.as_str()).unwrap_or("0");
            json!({
                "symbol": tok.get("symbol").and_then(|v| v.as_str()).unwrap_or("?"),
                "address": tok.get("address").and_then(|v| v.as_str()).unwrap_or(""),
                "amount": amount,
                "amountDisplay": format_units(amount, dec),
                "isNative": tok.get("isNative").and_then(|v| v.as_bool()).unwrap_or(false),
            })
        };
        let sym0 = pp.pointer("/token0/symbol").and_then(|v| v.as_str()).unwrap_or("?");
        let sym1 = pp.pointer("/token1/symbol").and_then(|v| v.as_str()).unwrap_or("?");
        // feeTier is in hundredths of a bip (e.g. 460 = 0.046%); show as percent.
        let fee_pct = pp.get("feeTier").and_then(|v| v.as_str())
            .and_then(|s| s.parse::<f64>().ok())
            .map(|f| f / 10_000.0);

        let slug = match chain_id {
            1 => "ethereum", 8453 => "base", 42161 => "arbitrum", 10 => "optimism",
            137 => "polygon", 56 => "bsc", 4663 => "robinhood", 43114 => "avalanche",
            130 => "unichain", 81457 => "blast", _ => "ethereum",
        };
        out.push(json!({
            "chainId": chain_id,
            "chainName": get_chain_name(chain_id),
            "chain": slug,
            "version": version,
            "pair": format!("{sym0}/{sym1}"),
            "token0": leg("token0", "amount0"),
            "token1": leg("token1", "amount1"),
            "feePercent": fee_pct,
            "valueUsd": p.get("valueUsd").and_then(|v| v.as_f64()).unwrap_or(0.0),
            "uncollectedFeesUsd": p.get("uncollectedFeesUsd").and_then(|v| v.as_f64()).unwrap_or(0.0),
            "inRange": p.get("status").and_then(|v| v.as_str()).map(|s| s.contains("IN_RANGE")).unwrap_or(false),
            "apr": pp.get("apr").and_then(|v| v.as_f64()),
            "tokenId": pp.get("tokenId").and_then(|v| v.as_str()).unwrap_or(""),
            "poolId": pp.get("poolId").and_then(|v| v.as_str()).unwrap_or(""),
            "chainLogo": relay_chain_icon(http, chain_id).await,
        }));
    }
    // Deepest value first.
    out.sort_by(|a, b| {
        let av = a.get("valueUsd").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let bv = b.get("valueUsd").and_then(|v| v.as_f64()).unwrap_or(0.0);
        bv.partial_cmp(&av).unwrap_or(std::cmp::Ordering::Equal)
    });
    Ok(json!({ "positions": out }))
}

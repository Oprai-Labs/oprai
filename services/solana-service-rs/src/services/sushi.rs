//! SushiSwap on Robinhood Chain (chain 4663).
//!
//! Three surfaces, all EVM/non-custodial (unsigned `{to,data,value,chainId}` txs
//! the user's own wallet signs):
//!   - SWAP: Sushi's own aggregator API (`api.sushi.com/swap/v7/4663`) returns a
//!     directly-signable tx routed through RedSnwapper. NO Permit2 — an ERC-20
//!     input just needs a plain approve to RedSnwapper first.
//!   - POOLS: listed from GeckoTerminal (Sushi's `api.sushi.com/pools` is
//!     WAF-blocked, and DexScreener doesn't index Sushi on 4663).
//!   - ADD LIQUIDITY: Sushi has no hosted LP API, so we ABI-encode the V3
//!     `NonfungiblePositionManager.mint` ourselves (Sushi V3 is a Uniswap-V3
//!     fork — identical ABI/selector, verified live).
//!
//! Addresses/selectors below were verified live against the deployed 4663 chain.

use serde_json::{json, Value};

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::services::uniswap::eth_call;
use uuid::Uuid;

const CHAIN: u64 = 4663;
const RPC_PUBLIC: &str = "https://rpc.mainnet.chain.robinhood.com";
fn rpc() -> String {
    std::env::var("ROBINHOOD_RPC").ok().filter(|s| !s.is_empty()).unwrap_or_else(|| RPC_PUBLIC.to_string())
}

pub const SUSHI_WETH: &str = "0x0bd7d308f8e1639fab988df18a8011f41eacad73";
/// Sushi API's native-ETH sentinel (NOT the zero address — that 422s).
const NATIVE_SENTINEL: &str = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE";
const ZERO: &str = "0x0000000000000000000000000000000000000000";
/// RedSnwapper — Sushi's RouteProcessor on 4663; the swap tx target + approve spender.
const RED_SNWAPPER: &str = "0x8e6fd69a77e88ee20ba4b4fbd59dfcda3ec0e98a";
/// Sushi V3 NonfungiblePositionManager — LP mint + the approve spender for LP.
const SUSHI_V3_NPM: &str = "0x51d0e5188afe12d502e29d982d20c190e7816107";

const SWAP_API: &str = "https://api.sushi.com/swap/v7/4663";
const GT_V3_POOLS: &str = "https://api.geckoterminal.com/api/v2/networks/robinhood/dexes/sushiswap-v3-robinhood/pools";

const SEL_APPROVE: &str = "095ea7b3";
const SEL_ALLOWANCE: &str = "0xdd62ed3e";
const SEL_DECIMALS: &str = "0x313ce567";
const SEL_MINT: &str = "88316456"; // mint((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,address,uint256))
// V3 pool view selectors
const SEL_SLOT0: &str = "0x3850c7bd"; // slot0()->(sqrtPriceX96, tick, ...)
const SEL_FEE: &str = "0xddca3f43"; // fee()
const SEL_TICK_SPACING: &str = "0xd0c93a7c"; // tickSpacing()
const SEL_TOKEN0: &str = "0x0dfe1681";
const SEL_TOKEN1: &str = "0xd21220a7";

// ── ABI helpers ──────────────────────────────────────────────────────────────
fn w_u128(v: u128) -> String { format!("{v:064x}") }
fn w_addr(a: &str) -> String { format!("{:0>64}", a.trim_start_matches("0x").to_lowercase()) }
/// int24 (tick) as a 256-bit two's-complement word.
fn w_int(v: i64) -> String {
    if v >= 0 { format!("{v:064x}") } else { format!("{}{:016x}", "f".repeat(48), v as u64) }
}
fn word_hex(hex: &str, i: usize) -> &str {
    let h = hex.trim_start_matches("0x");
    let s = i * 64;
    if h.len() < s + 64 { "" } else { &h[s..s + 64] }
}
/// Full 256-bit word → f64 (for sqrtPriceX96, which exceeds u128).
fn word_f64_full(hex: &str, i: usize) -> f64 {
    let w = word_hex(hex, i);
    if w.len() < 64 { return 0.0; }
    let mut v = 0.0f64;
    for c in w.chars() {
        v = v * 16.0 + c.to_digit(16).unwrap_or(0) as f64;
    }
    v
}
/// Word i as a signed int (two's complement, for the current tick).
fn word_i64(hex: &str, i: usize) -> i64 {
    let w = word_hex(hex, i);
    if w.len() < 64 { return 0; }
    // top nibble 8..f ⇒ negative
    let neg = w.chars().next().map(|c| c.to_digit(16).unwrap_or(0) >= 8).unwrap_or(false);
    let low = u64::from_str_radix(&w[48..64], 16).unwrap_or(0);
    if neg { low as i64 } else { low as i64 }
}
fn word_u128(hex: &str, i: usize) -> u128 {
    let w = word_hex(hex, i);
    if w.len() < 64 { return 0; }
    u128::from_str_radix(w[32..64].trim_start_matches('0'), 16).unwrap_or(0)
}
fn word_addr(hex: &str, i: usize) -> String {
    let w = word_hex(hex, i);
    if w.len() < 64 { return ZERO.to_string(); }
    format!("0x{}", &w[24..64])
}
fn to_u128(s: &str) -> u128 {
    let s = s.trim();
    if let Some(h) = s.strip_prefix("0x") { u128::from_str_radix(h.trim_start_matches('0'), 16).unwrap_or(0) }
    else if let Some(dot) = s.find('.') { s[..dot].parse::<u128>().unwrap_or(0) }
    else { s.parse::<u128>().unwrap_or(0) }
}
fn parse_scaled(s: &str, dec: u32) -> u128 {
    let s = s.trim();
    let (int_part, frac_part) = s.split_once('.').unwrap_or((s, ""));
    let int_v = int_part.parse::<u128>().unwrap_or(0);
    let mut frac: String = frac_part.chars().take(dec as usize).collect();
    while (frac.len() as u32) < dec { frac.push('0'); }
    let frac_v = if frac.is_empty() { 0 } else { frac.parse::<u128>().unwrap_or(0) };
    int_v * 10u128.pow(dec) + frac_v
}

async fn token_decimals(http: &reqwest::Client, rpc: &str, token: &str) -> u32 {
    if token.eq_ignore_ascii_case(NATIVE_SENTINEL) || token.eq_ignore_ascii_case(ZERO) { return 18; }
    match eth_call(http, rpc, token, SEL_DECIMALS).await {
        Ok(h) => { let d = word_u128(&h, 0); if d == 0 || d > 36 { 18 } else { d as u32 } }
        Err(_) => 18,
    }
}
async fn allowance(http: &reqwest::Client, rpc: &str, token: &str, owner: &str, spender: &str) -> u128 {
    let data = format!("{SEL_ALLOWANCE}{}{}", w_addr(owner), w_addr(spender));
    match eth_call(http, rpc, token, &data).await { Ok(h) => word_u128(&h, 0), Err(_) => 0 }
}
fn approve_tx(token: &str, spender: &str, amount: u128) -> Value {
    let data = format!("{SEL_APPROVE}{}{}", w_addr(spender), w_u128(amount));
    json!({ "to": token, "data": format!("0x{data}"), "value": "0", "chainId": CHAIN })
}
/// Native symbol / zero / sentinel → the Sushi API native sentinel; else the address.
fn norm_token(t: &str) -> String {
    let s = t.trim();
    if s.is_empty() || s.eq_ignore_ascii_case("eth") || s.eq_ignore_ascii_case("native")
        || s.eq_ignore_ascii_case(ZERO) || s.eq_ignore_ascii_case(NATIVE_SENTINEL) {
        NATIVE_SENTINEL.to_string()
    } else {
        s.to_lowercase()
    }
}
fn is_native(t: &str) -> bool { t.eq_ignore_ascii_case(NATIVE_SENTINEL) }

// ── (a) SWAP ────────────────────────────────────────────────────────────────

/// Build a Sushi swap. Body: {tokenIn, tokenOut, amount|amountBaseUnits,
/// walletAddress|sender, slippagePct?}. Returns unsigned txs (approve? + swap).
pub async fn build_swap(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let rpc = rpc();
    let token_in = norm_token(body.get("tokenIn").and_then(|v| v.as_str()).unwrap_or(""));
    let token_out = norm_token(body.get("tokenOut").and_then(|v| v.as_str()).unwrap_or(""));
    if token_in.len() < 42 && !is_native(&token_in) {
        return Err(AppError::InvalidParams("sushi: tokenIn required".into()));
    }
    if token_out.len() < 42 && !is_native(&token_out) {
        return Err(AppError::InvalidParams("sushi: tokenOut required".into()));
    }
    let wallet = body.get("walletAddress").or_else(|| body.get("sender")).and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
        .ok_or_else(|| AppError::InvalidParams("sushi: walletAddress required".into()))?;
    let slippage = body.get("slippagePct").and_then(|v| v.as_f64()).unwrap_or(0.5).clamp(0.05, 50.0);
    let max_slippage = slippage / 100.0;

    // amount → base units of tokenIn.
    let amount = if let Some(b) = body.get("amountBaseUnits").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
        to_u128(b)
    } else {
        let human = body.get("amount").and_then(|v| v.as_str().map(|s| s.to_string()).or_else(|| v.as_f64().map(|f| format!("{f}")))).unwrap_or_default();
        if human.trim().is_empty() { 0 } else { parse_scaled(human.trim(), token_decimals(http, &rpc, &token_in).await) }
    };
    if amount == 0 { return Err(AppError::InvalidParams("sushi: amount required".into())); }

    let url = format!(
        "{SWAP_API}?tokenIn={token_in}&tokenOut={token_out}&amount={amount}&maxSlippage={max_slippage}&sender={wallet}"
    );
    let resp = http.get(&url).timeout(std::time::Duration::from_secs(20)).send().await
        .map_err(|e| AppError::Internal(format!("sushi swap request failed: {e}")))?;
    let status = resp.status();
    let data: Value = resp.json().await.map_err(|e| AppError::Internal(format!("sushi swap bad JSON: {e}")))?;
    if !status.is_success() {
        let msg = data.get("message").and_then(|v| v.as_str())
            .or_else(|| data.get("error").and_then(|v| v.as_str())).unwrap_or("swap unavailable");
        return Err(AppError::InvalidParams(format!("sushi: {msg}")));
    }
    let s = data.get("status").and_then(|v| v.as_str()).unwrap_or("");
    if s.eq_ignore_ascii_case("NoWay") || data.get("tx").is_none() {
        return Err(AppError::InvalidParams("sushi: no route for this pair/size".into()));
    }
    let tx = data.get("tx").cloned().unwrap_or(Value::Null);
    let to = tx.get("to").and_then(|v| v.as_str()).unwrap_or(RED_SNWAPPER).to_string();
    let calldata = tx.get("data").and_then(|v| v.as_str()).unwrap_or("0x").to_string();
    let value = tx.get("value").and_then(|v| v.as_str().map(|s| s.to_string()).or_else(|| v.as_u64().map(|n| n.to_string())))
        .unwrap_or_else(|| if is_native(&token_in) { amount.to_string() } else { "0".to_string() });

    let mut txs = vec![];
    // ERC-20 input needs a plain approve to RedSnwapper (no Permit2).
    if !is_native(&token_in) {
        let have = allowance(http, &rpc, &token_in, wallet, &to).await;
        if have < amount { txs.push(approve_tx(&token_in, &to, amount)); }
    }
    txs.push(json!({ "to": to, "data": calldata, "value": value, "chainId": CHAIN }));

    Ok(json!({
        "transactions": txs,
        "chainId": CHAIN,
        "amountInBaseUnits": amount.to_string(),
        "expectedAmountOut": data.get("assumedAmountOut").cloned().unwrap_or(Value::Null),
        "priceImpact": data.get("priceImpact").cloned().unwrap_or(Value::Null),
        "swapPrice": data.get("swapPrice").cloned().unwrap_or(Value::Null),
    }))
}

// ── (b) POOL LISTING (GeckoTerminal) ──────────────────────────────────────────

#[derive(Debug, Clone, serde::Deserialize)]
pub struct SushiPoolsParams {
    #[serde(default)]
    pub limit: Option<usize>,
    #[serde(default, alias = "search", alias = "q")]
    pub query: Option<String>,
}

/// List Sushi V3 pools on Robinhood Chain from GeckoTerminal, richest first.
pub async fn fetch_pools(http: &reqwest::Client, limit: usize, search: Option<&str>) -> Vec<Value> {
    let url = format!("{GT_V3_POOLS}?page=1&include=base_token,quote_token");
    let resp = match http.get(&url).header("accept", "application/json")
        .timeout(std::time::Duration::from_secs(15)).send().await {
        Ok(r) if r.status().is_success() => r,
        _ => return vec![],
    };
    let body: Value = match resp.json().await { Ok(b) => b, Err(_) => return vec![] };

    // Build id→{symbol,address} from included tokens.
    let mut tok: std::collections::HashMap<String, (String, String)> = std::collections::HashMap::new();
    if let Some(inc) = body.get("included").and_then(|v| v.as_array()) {
        for it in inc {
            let id = it.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let sym = it.pointer("/attributes/symbol").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let addr = it.pointer("/attributes/address").and_then(|v| v.as_str()).unwrap_or("").to_string();
            if !id.is_empty() { tok.insert(id, (sym, addr)); }
        }
    }

    let items = body.get("data").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let ql = search.map(|s| s.trim().to_lowercase()).filter(|s| !s.is_empty());
    let mut rows: Vec<Value> = items.iter().filter_map(|p| shape_pool(p, &tok)).collect();
    if let Some(ql) = &ql {
        rows.retain(|r| {
            let n = r.get("name").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
            n.contains(ql)
        });
    }
    rows.truncate(limit);
    rows
}

fn shape_pool(p: &Value, tok: &std::collections::HashMap<String, (String, String)>) -> Option<Value> {
    let a = p.get("attributes")?;
    let address = a.get("address").and_then(|v| v.as_str())?.to_string();
    let name = a.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
    // fee tier is embedded in the name ("WETH / USDG 0.05%").
    let fee_pct = name.split_whitespace().last()
        .and_then(|s| s.trim_end_matches('%').parse::<f64>().ok());
    let tvl = a.get("reserve_in_usd").and_then(numstr);
    let vol24 = a.pointer("/volume_usd/h24").and_then(numstr);
    let apr = match (vol24, tvl, fee_pct) {
        (Some(v), Some(t), Some(f)) if t > 0.0 => Some(v * (f / 100.0) / t * 365.0 * 100.0),
        _ => None,
    };
    let base_id = p.pointer("/relationships/base_token/data/id").and_then(|v| v.as_str()).unwrap_or("");
    let quote_id = p.pointer("/relationships/quote_token/data/id").and_then(|v| v.as_str()).unwrap_or("");
    let (base_sym, base_addr) = tok.get(base_id).cloned().unwrap_or_default();
    let (quote_sym, quote_addr) = tok.get(quote_id).cloned().unwrap_or_default();
    Some(json!({
        "poolAddress": address,
        "name": name,
        "feePct": fee_pct,
        "tvlUsd": tvl,
        "volume24hUsd": vol24,
        "aprEst": apr,
        "token0Symbol": base_sym,
        "token0Address": base_addr,
        "token1Symbol": quote_sym,
        "token1Address": quote_addr,
        "dex": "sushiswap-v3",
        "chain": "robinhood",
    }))
}
fn numstr(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse::<f64>().ok()))
}

pub async fn build_pools(http: &reqwest::Client, params: &SushiPoolsParams) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(20).clamp(1, 40);
    let search = params.query.as_deref().map(str::trim).filter(|s| !s.is_empty());
    let rows = fetch_pools(http, limit, search).await;
    let description = match search {
        Some(q) if rows.is_empty() => format!("No Sushi pools match “{q}”."),
        Some(q) => format!("{} Sushi pools match “{q}”.", rows.len()),
        None if rows.is_empty() => "No Sushi pools found on Robinhood Chain.".to_string(),
        None => format!("SushiSwap V3 pools — {} on Robinhood Chain.", rows.len()),
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "sushi_pools".to_string(),
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
        data: Some(json!({ "chain": "robinhood", "chainName": "Robinhood", "pools": rows })),
    })
}

// ── (c) ADD LIQUIDITY (Sushi V3 NonfungiblePositionManager.mint) ──────────────

struct V3Pool { fee: u32, tick_spacing: i64, token0: String, token1: String, sqrt_price_x96: f64, tick: i64 }

async fn read_pool(http: &reqwest::Client, rpc: &str, pool: &str) -> Result<V3Pool, AppError> {
    let slot0 = eth_call(http, rpc, pool, SEL_SLOT0).await?;
    let fee = word_u128(&eth_call(http, rpc, pool, SEL_FEE).await?, 0) as u32;
    let tick_spacing = word_u128(&eth_call(http, rpc, pool, SEL_TICK_SPACING).await?, 0) as i64;
    let token0 = word_addr(&eth_call(http, rpc, pool, SEL_TOKEN0).await?, 0);
    let token1 = word_addr(&eth_call(http, rpc, pool, SEL_TOKEN1).await?, 0);
    let sqrt_price_x96 = word_f64_full(&slot0, 0);
    let tick = word_i64(&slot0, 1);
    if sqrt_price_x96 <= 0.0 || token0.eq_ignore_ascii_case(ZERO) {
        return Err(AppError::InvalidParams("sushi: could not read this pool".into()));
    }
    Ok(V3Pool { fee, tick_spacing, token0, token1, sqrt_price_x96, tick })
}

/// Tick band centred on the current tick, aligned to spacing. `range_percent`
/// (e.g. 10) → ±band; None = a wide default (±50 spacings), always in-range.
fn tick_bounds(current: i64, spacing: i64, range_percent: Option<f64>) -> (i64, i64) {
    let sp = spacing.max(1);
    let half = match range_percent {
        Some(p) if p > 0.0 => {
            // ln(1+p/100) / ln(1.0001) ticks on each side.
            let ticks = ((1.0 + p / 100.0).ln() / 1.0001_f64.ln()).round() as i64;
            (ticks / sp).max(1) * sp
        }
        _ => sp * 50,
    };
    let lower = ((current - half) as f64 / sp as f64).floor() as i64 * sp;
    let upper = ((current + half) as f64 / sp as f64).ceil() as i64 * sp;
    (lower, upper.max(lower + sp))
}

fn tick_to_sqrt(tick: i64) -> f64 { 1.0001_f64.powf(tick as f64 / 2.0) }

/// Build a Sushi V3 add-liquidity (mint). Body: {poolAddress, inputToken,
/// amount|amountBaseUnits, walletAddress, rangePercent?, slippagePct?}.
pub async fn build_add_liquidity(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let rpc = rpc();
    let pool = body.get("poolAddress").and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
        .ok_or_else(|| AppError::InvalidParams("sushi: poolAddress required".into()))?;
    let wallet = body.get("walletAddress").and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
        .ok_or_else(|| AppError::InvalidParams("sushi: walletAddress required".into()))?;
    let input = body.get("inputToken").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    if input.len() != 42 { return Err(AppError::InvalidParams("sushi: inputToken (which token to deposit) required".into())); }
    let slippage = body.get("slippagePct").and_then(|v| v.as_f64()).unwrap_or(1.0).clamp(0.1, 50.0);
    let range_percent = body.get("rangePercent").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())));

    let p = read_pool(http, &rpc, pool).await?;
    if !input.eq_ignore_ascii_case(&p.token0) && !input.eq_ignore_ascii_case(&p.token1) {
        return Err(AppError::InvalidParams("sushi: input token is not in this pool".into()));
    }
    let dec_in = token_decimals(http, &rpc, &input).await;
    let amount_in = if let Some(b) = body.get("amountBaseUnits").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
        to_u128(b)
    } else {
        let human = body.get("amount").and_then(|v| v.as_str().map(|s| s.to_string()).or_else(|| v.as_f64().map(|f| format!("{f}")))).unwrap_or_default();
        parse_scaled(human.trim(), dec_in)
    };
    if amount_in == 0 { return Err(AppError::InvalidParams("sushi: amount required".into())); }

    let (tl, tu) = tick_bounds(p.tick, p.tick_spacing, range_percent);
    // Clamp current sqrtP inside the band so the position is two-sided.
    let sqrt_a = tick_to_sqrt(tl);
    let sqrt_b = tick_to_sqrt(tu);
    let sqrt_p = p.sqrt_price_x96 / 2f64.powi(96);
    let sqrt_p = sqrt_p.clamp(sqrt_a * 1.000001, sqrt_b * 0.999999);

    let input_is_0 = input.eq_ignore_ascii_case(&p.token0);
    let a_in = amount_in as f64;
    // V3 liquidity math (in raw base units).
    let (amount0, amount1) = if input_is_0 {
        let l = a_in * (sqrt_p * sqrt_b) / (sqrt_b - sqrt_p);
        (a_in, l * (sqrt_p - sqrt_a))
    } else {
        let l = a_in / (sqrt_p - sqrt_a);
        (l * (sqrt_b - sqrt_p) / (sqrt_p * sqrt_b), a_in)
    };
    let amount0 = amount0.max(0.0) as u128;
    let amount1 = amount1.max(0.0) as u128;
    let min0 = ((amount0 as f64) * (1.0 - slippage / 100.0)).max(0.0) as u128;
    let min1 = ((amount1 as f64) * (1.0 - slippage / 100.0)).max(0.0) as u128;

    // mint(MintParams) — a single static struct (11 words), encoded inline.
    let deadline = 1_900_000_000u128; // year 2030; the card is short-lived anyway.
    let data = format!(
        "{SEL_MINT}{}{}{}{}{}{}{}{}{}{}{}",
        w_addr(&p.token0),
        w_addr(&p.token1),
        w_u128(p.fee as u128),
        w_int(tl),
        w_int(tu),
        w_u128(amount0),
        w_u128(amount1),
        w_u128(min0),
        w_u128(min1),
        w_addr(wallet),
        w_u128(deadline),
    );

    // Approvals: both legs to the NPM (no Permit2). ETH-native legs would need
    // WETH-wrapping; Robinhood Sushi pools pair WETH (ERC-20), so plain approves.
    let mut txs = vec![];
    if amount0 > 0 {
        let have = allowance(http, &rpc, &p.token0, wallet, SUSHI_V3_NPM).await;
        if have < amount0 { txs.push(approve_tx(&p.token0, SUSHI_V3_NPM, amount0)); }
    }
    if amount1 > 0 {
        let have = allowance(http, &rpc, &p.token1, wallet, SUSHI_V3_NPM).await;
        if have < amount1 { txs.push(approve_tx(&p.token1, SUSHI_V3_NPM, amount1)); }
    }
    txs.push(json!({ "to": SUSHI_V3_NPM, "data": format!("0x{data}"), "value": "0", "chainId": CHAIN }));

    let dec0 = token_decimals(http, &rpc, &p.token0).await;
    let dec1 = token_decimals(http, &rpc, &p.token1).await;
    Ok(json!({
        "transactions": txs,
        "chainId": CHAIN,
        "poolAddress": pool,
        "fee": p.fee,
        "tickLower": tl,
        "tickUpper": tu,
        "token0": { "address": p.token0, "amountBaseUnits": amount0.to_string(), "decimals": dec0 },
        "token1": { "address": p.token1, "amountBaseUnits": amount1.to_string(), "decimals": dec1 },
    }))
}

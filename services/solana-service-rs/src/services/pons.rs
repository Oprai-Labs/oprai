//! Pons launchpad (ponsfamily.com) on Robinhood Chain (chain 4663).
//!
//! Unlike pools.trade (which exposes a public tRPC API that builds the txs for
//! us), Pons is CONTRACT-ONLY — no server API. So we integrate straight against
//! the verified V2 contracts:
//!   - DISCOVERY: scan the V2 factory's `TokenLaunched` logs (via Blockscout),
//!     then hydrate each with a few `eth_call`s (curve reserves for price, the
//!     graduated flag, the token's on-chain logo).
//!   - TRADE: buy/sell on the per-token bonding **curve** contract; a graduated
//!     token has moved to a Uniswap V4 pool and is no longer curve-tradeable.
//!   - LAUNCH: `PonsV2LaunchFactory.launchToken`.
//!
//! Signatures/selectors below were verified live against the deployed contracts.
//! V2 factory `0x7eD5…EC7e`; config 0: supply 1e27, curveFee 1%, phantomQuote
//! 1.68 ETH, graduationThreshold 4.2 ETH, launchFee 0.0005 ETH.

use futures::future::join_all;
use serde_json::{json, Value};

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::services::uniswap::{eth_call, pools_eth_usd};
use uuid::Uuid;

pub const PONS_V2_FACTORY: &str = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e";
pub const PONS_BLOCKSCOUT: &str = "https://robinhoodchain.blockscout.com/api/v2";
const ROBINHOOD_CHAIN: u64 = 4663;
const NATIVE: &str = "0x0000000000000000000000000000000000000000";
const TOTAL_SUPPLY_TOKENS: f64 = 1_000_000_000.0; // config 0: 1e27 base units / 1e18

// curve view selectors
const SEL_GET_RESERVES: &str = "0x0902f1ac"; // getReserves() -> (quote, token)
const SEL_GRADUATED: &str = "0xe7c2b772"; // graduated() -> bool
const SEL_PAIR_TOKEN: &str = "0x3de35b79"; // pairToken() -> address
const SEL_REAL_QUOTE: &str = "0x4f1f58fd"; // realQuoteReserve() -> uint256
// token view selectors
const SEL_LOGO: &str = "0xfb7f21eb"; // logo() -> string

/// Robinhood Chain's public RPC (Alchemy does not reliably serve chain 4663).
/// Overridable via ROBINHOOD_RPC.
const ROBINHOOD_PUBLIC_RPC: &str = "https://rpc.mainnet.chain.robinhood.com";
fn rpc() -> Option<String> {
    Some(
        std::env::var("ROBINHOOD_RPC")
            .ok()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| ROBINHOOD_PUBLIC_RPC.to_string()),
    )
}

/// Last 32 hex chars of a 256-bit word → u128 → f64 (reserves/thresholds fit u128).
fn word_f64(hex: &str, word_index: usize) -> f64 {
    let h = hex.trim_start_matches("0x");
    let start = word_index * 64;
    if h.len() < start + 64 {
        return 0.0;
    }
    let w = &h[start + 32..start + 64]; // low 128 bits
    u128::from_str_radix(w.trim_start_matches('0'), 16).unwrap_or(0) as f64
}

/// Last 20 bytes of a word → 0x address (lowercased).
fn word_addr(hex: &str, word_index: usize) -> String {
    let h = hex.trim_start_matches("0x");
    let start = word_index * 64;
    if h.len() < start + 64 {
        return NATIVE.to_string();
    }
    format!("0x{}", &h[start + 24..start + 64])
}

/// Decode a single ABI dynamic-string return (offset, length, bytes).
fn decode_abi_string(hex: &str) -> String {
    let b = match hex::decode(hex.trim_start_matches("0x")) {
        Ok(b) => b,
        Err(_) => return String::new(),
    };
    if b.len() < 64 {
        return String::new();
    }
    let off = u256_low(&b[0..32]);
    if off + 32 > b.len() {
        return String::new();
    }
    let len = u256_low(&b[off..off + 32]);
    let start = off + 32;
    if len == 0 || start + len > b.len() {
        return String::new();
    }
    String::from_utf8_lossy(&b[start..start + len]).trim().to_string()
}
fn u256_low(w: &[u8]) -> usize {
    let mut v = 0usize;
    for &byte in &w[w.len().saturating_sub(8)..] {
        v = (v << 8) | byte as usize;
    }
    v
}

/// `ipfs://CID` → gateway URL; https passes through; empty stays empty.
fn resolve_logo(u: &str) -> String {
    let u = u.trim();
    if u.is_empty() || u.starts_with("http://") || u.starts_with("https://") {
        return u.to_string();
    }
    let cid = u.strip_prefix("ipfs://").unwrap_or(u).trim_start_matches('/');
    if cid.is_empty() {
        String::new()
    } else {
        format!("https://ipfs.io/ipfs/{cid}")
    }
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct PonsLaunchesParams {
    #[serde(default)]
    pub sort: Option<String>,
    #[serde(default)]
    pub limit: Option<usize>,
    #[serde(default, alias = "search", alias = "q")]
    pub query: Option<String>,
}

/// One launch pulled from a `TokenLaunched` log, before enrichment.
struct RawLaunch {
    token: String,
    curve: String,
    pair_token: String,
    graduation_threshold: f64,
}

/// Discover recent Pons V2 launches: scan factory `TokenLaunched` logs, then
/// hydrate each concurrently (reserves→price, graduated flag, on-chain logo,
/// Blockscout name/symbol/holders). Returns rows in the same shape the launches
/// card renders, tagged `launchpad:"Pons"` / `kind:"pons-curve"`.
pub async fn fetch_pons_rows(
    http: &reqwest::Client,
    limit: usize,
    search: Option<&str>,
) -> Vec<Value> {
    let url = format!("{PONS_BLOCKSCOUT}/addresses/{PONS_V2_FACTORY}/logs");
    // Blockscout /logs intermittently 500s — retry a couple of times.
    let mut body: Value = Value::Null;
    for _ in 0..3 {
        match http.get(&url).header("user-agent", "oprai").send().await {
            Ok(r) if r.status().is_success() => {
                if let Ok(v) = r.json::<Value>().await {
                    if v.get("items").and_then(|i| i.as_array()).is_some() {
                        body = v;
                        break;
                    }
                }
            }
            _ => {}
        }
        tokio::time::sleep(std::time::Duration::from_millis(400)).await;
    }
    let items = match body.get("items").and_then(|v| v.as_array()) {
        Some(a) => a,
        None => return vec![],
    };

    // Newest-first; pull extra so a name search / failed hydration still fills.
    let want = (limit * 3).clamp(limit, 60);
    let mut raws: Vec<RawLaunch> = Vec::new();
    for it in items {
        let dec = it.get("decoded");
        let mc = dec
            .and_then(|d| d.get("method_call"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if !mc.starts_with("TokenLaunched") {
            continue;
        }
        let params = match dec.and_then(|d| d.get("parameters")).and_then(|v| v.as_array()) {
            Some(p) => p,
            None => continue,
        };
        let get = |name: &str| -> Option<String> {
            params
                .iter()
                .find(|p| p.get("name").and_then(|v| v.as_str()) == Some(name))
                .and_then(|p| p.get("value"))
                .map(|v| match v {
                    Value::String(s) => s.clone(),
                    other => other.to_string().trim_matches('"').to_string(),
                })
        };
        let (token, curve) = match (get("token"), get("curve")) {
            (Some(t), Some(c)) if t.starts_with("0x") && c.starts_with("0x") => (t, c),
            _ => continue,
        };
        let pair_token = get("pairToken").unwrap_or_else(|| NATIVE.to_string());
        let graduation_threshold = get("graduationThreshold")
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(0.0);
        raws.push(RawLaunch {
            token,
            curve,
            pair_token,
            graduation_threshold,
        });
        if raws.len() >= want {
            break;
        }
    }
    if raws.is_empty() {
        return vec![];
    }

    let eth_usd = pools_eth_usd(http).await;
    let futs = raws.into_iter().map(|r| enrich(http, r, eth_usd));
    let mut rows: Vec<Value> = join_all(futs).await.into_iter().flatten().collect();

    if let Some(q) = search {
        let ql = q.trim().to_lowercase();
        if !ql.is_empty() {
            rows.retain(|r| {
                let sym = r.get("symbol").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                let name = r.get("name").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                sym.contains(&ql) || name.contains(&ql)
            });
        }
    }
    rows.truncate(limit);
    rows
}

/// Hydrate one launch. Returns None if the token can't be priced/identified.
async fn enrich(http: &reqwest::Client, r: RawLaunch, eth_usd: Option<f64>) -> Option<Value> {
    let rpc = rpc()?;
    let native_pair = r.pair_token.eq_ignore_ascii_case(NATIVE);

    // Reserves + graduated + logo in parallel.
    let (reserves, graduated_hex, real_quote_hex, logo_hex) = futures::join!(
        eth_call(http, &rpc, &r.curve, SEL_GET_RESERVES),
        eth_call(http, &rpc, &r.curve, SEL_GRADUATED),
        eth_call(http, &rpc, &r.curve, SEL_REAL_QUOTE),
        eth_call(http, &rpc, &r.token, SEL_LOGO),
    );
    let reserves = reserves.ok()?;
    let quote_reserve = word_f64(&reserves, 0);
    let token_reserve = word_f64(&reserves, 1);
    let graduated = graduated_hex
        .ok()
        .map(|h| h.trim_start_matches("0x").trim_start_matches('0') != "")
        .unwrap_or(false);
    let real_quote = real_quote_hex.ok().map(|h| word_f64(&h, 0)).unwrap_or(0.0);
    let logo = resolve_logo(&logo_hex.map(|h| decode_abi_string(&h)).unwrap_or_default());

    // price in pair units per whole token (both reserves share 1e18 → ratio is
    // pair-per-token-base; ×1 gives pair per base unit, so per whole token too).
    let price_pair = if token_reserve > 0.0 {
        quote_reserve / token_reserve
    } else {
        0.0
    };
    let (price_usd, price_eth) = if native_pair {
        let pe = price_pair; // ETH per token
        (eth_usd.map(|u| pe * u).unwrap_or(0.0), pe)
    } else {
        (0.0, 0.0) // ERC-20-quoted launch: USD needs the pair's price; skip for now
    };
    let fdv_usd = price_usd * TOTAL_SUPPLY_TOKENS;
    let graduation_progress = if r.graduation_threshold > 0.0 {
        (real_quote / r.graduation_threshold).clamp(0.0, 1.0)
    } else {
        0.0
    };

    // Name / symbol straight from the ERC-20 via the public RPC (reliable);
    // holders from Blockscout is a nice-to-have (don't drop the row if it fails).
    let (sym_hex, name_hex) = futures::join!(
        eth_call(http, &rpc, &r.token, "0x95d89b41"), // symbol()
        eth_call(http, &rpc, &r.token, "0x06fdde03"), // name()
    );
    let symbol = decode_abi_string(&sym_hex.unwrap_or_default());
    let name = decode_abi_string(&name_hex.unwrap_or_default());
    if symbol.is_empty() && name.is_empty() {
        return None; // couldn't identify the token — drop it
    }
    let mut holders = Value::Null;
    let turl = format!("{PONS_BLOCKSCOUT}/tokens/{}", r.token);
    if let Ok(resp) = http.get(&turl).header("user-agent", "oprai").send().await {
        if let Ok(tj) = resp.json::<Value>().await {
            holders = tj
                .get("holders_count")
                .or_else(|| tj.get("holders"))
                .and_then(|v| v.as_str())
                .and_then(|s| s.parse::<u64>().ok())
                .map(Value::from)
                .unwrap_or(Value::Null);
        }
    }

    Some(json!({
        "tokenAddress":       r.token,
        "curve":              r.curve,
        "symbol":             symbol,
        "name":               name,
        "launchpadId":        "pons-v2",
        "launchpad":          "Pons",
        "kind":               "pons-curve",
        "logo":               if logo.is_empty() { Value::Null } else { Value::from(logo) },
        "priceUsd":           if price_usd > 0.0 { format!("{price_usd}") } else { String::new() },
        "priceEth":           if price_eth > 0.0 { Value::from(price_eth) } else { Value::Null },
        "fdvUsd":             fdv_usd,
        "holders":            holders,
        "graduated":          graduated,
        "graduationProgress": graduation_progress,
        "graduationThreshold": r.graduation_threshold,
        "pairToken":          r.pair_token,
        "quoteSymbol":        if native_pair { "ETH" } else { "" },
        "status":             if graduated { "graduated" } else { "curveLive" },
        "url":                format!("https://www.ponsfamily.com/token/{}", r.token),
        "chain":              "robinhood",
    }))
}

// ── ABI encode helpers ────────────────────────────────────────────────────
fn enc_u256(v: u128) -> String {
    format!("{v:064x}")
}
fn enc_addr(a: &str) -> String {
    format!("{:0>64}", a.trim_start_matches("0x").to_lowercase())
}
/// Decimal string / hex → u128 (amounts fit u128; reserves ≤ ~1e27 < 2^90).
fn to_u128(s: &str) -> u128 {
    let s = s.trim();
    if let Some(h) = s.strip_prefix("0x") {
        u128::from_str_radix(h.trim_start_matches('0'), 16).unwrap_or(0)
    } else {
        s.parse::<u128>().unwrap_or(0)
    }
}

// bonding-curve trade selectors (verified live)
const SEL_BUY: &str = "0x59a87bc1"; // buy(uint256 quoteIn, uint256 minTokensOut, address recipient) payable
const SEL_SELL: &str = "0xd04c6983"; // sell(uint256 tokensIn, uint256 minQuoteOut, address recipient)
const SEL_ERC20_APPROVE: &str = "0x095ea7b3"; // approve(address,uint256)
const SEL_FEE_BPS: &str = "0x24a9d853"; // feeBps()
const SEL_CREATOR_TAX_BPS: &str = "0xc1bb8901"; // creatorTaxBps()
const SEL_GET_LAUNCHED: &str = "0x3cf28b5a"; // factory.getLaunchedToken(address)

/// Resolve a token's Pons curve + pair + status from the factory, so a chat
/// buy/sell by raw 0x address shows the coin and routes to the right curve.
pub async fn pons_token_meta(http: &reqwest::Client, token: &str) -> Result<Value, AppError> {
    let rpc = rpc().ok_or_else(|| AppError::Internal("no Robinhood RPC".into()))?;
    let data = format!("{SEL_GET_LAUNCHED}{}", enc_addr(token));
    let raw = eth_call(http, &rpc, PONS_V2_FACTORY, &data).await?;
    // LaunchedToken tuple (all static words): 1=curve, 4=pairToken, 5=gradThreshold,
    // 10=phase, 14=exists.
    let exists = word_f64(&raw, 14) != 0.0;
    if !exists {
        // Not a V2 launch — maybe a Pons V1 token (CREATE2 + Uniswap V3). V1
        // tokens live on a normal Uniswap V3 pool, so trade them via uniswap_swap
        // (mark graduated:true). Resolve name/symbol/logo so the card shows them.
        return pons_v1_meta(http, &rpc, token).await;
    }
    let curve = word_addr(&raw, 1);
    let pair_token = word_addr(&raw, 4);
    let graduation_threshold = word_f64(&raw, 5);
    let phase = word_f64(&raw, 10) as u64;
    let eth_usd = pools_eth_usd(http).await;
    let row = enrich(
        http,
        RawLaunch { token: token.to_string(), curve: curve.clone(), pair_token, graduation_threshold },
        eth_usd,
    )
    .await;
    let mut row = row.unwrap_or(json!({
        "tokenAddress": token, "curve": curve, "kind": "pons-curve", "launchpad": "Pons",
    }));
    if let Some(o) = row.as_object_mut() {
        o.insert("phase".to_string(), Value::from(phase));
    }
    Ok(row)
}

pub const PONS_V1_FACTORY: &str = "0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB";

/// Resolve a Pons V1 token (Uniswap V3). Returns a meta row tagged
/// `kind:"pons-v1"` + `graduated:true` so the frontend trades it via uniswap_swap.
async fn pons_v1_meta(http: &reqwest::Client, rpc: &str, token: &str) -> Result<Value, AppError> {
    let raw = eth_call(http, rpc, PONS_V1_FACTORY, &format!("{SEL_GET_LAUNCHED}{}", enc_addr(token))).await?;
    // V1 getLaunchedToken word0 = token; a match means it's a Pons V1 launch.
    if !word_addr(&raw, 0).eq_ignore_ascii_case(token) {
        return Ok(Value::Null);
    }
    let (sym_hex, name_hex, logo_hex) = futures::join!(
        eth_call(http, rpc, token, "0x95d89b41"),
        eth_call(http, rpc, token, "0x06fdde03"),
        eth_call(http, rpc, token, SEL_LOGO),
    );
    let mut symbol = decode_abi_string(&sym_hex.unwrap_or_default());
    let mut name = decode_abi_string(&name_hex.unwrap_or_default());
    let mut logo = resolve_logo(&logo_hex.map(|h| decode_abi_string(&h)).unwrap_or_default());
    // V1 tokens sometimes expose name/symbol/icon only via Blockscout.
    if symbol.is_empty() || name.is_empty() || logo.is_empty() {
        let turl = format!("{PONS_BLOCKSCOUT}/tokens/{token}");
        if let Ok(resp) = http.get(&turl).header("user-agent", "oprai").send().await {
            if let Ok(tj) = resp.json::<Value>().await {
                if symbol.is_empty() { symbol = tj.get("symbol").and_then(|v| v.as_str()).unwrap_or("").to_string(); }
                if name.is_empty() { name = tj.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string(); }
                if logo.is_empty() { logo = tj.get("icon_url").and_then(|v| v.as_str()).unwrap_or("").to_string(); }
            }
        }
    }
    Ok(json!({
        "tokenAddress": token,
        "symbol":       symbol,
        "name":         name,
        "launchpadId":  "pons-v1",
        "launchpad":    "Pons",
        "kind":         "pons-v1",
        "graduated":    true,   // trade via uniswap_swap (it's a Uniswap V3 pool)
        "logo":         if logo.is_empty() { Value::Null } else { Value::from(logo) },
        "quoteSymbol":  "ETH",
        "chain":        "robinhood",
        "url":          format!("https://www.ponsfamily.com/token/{token}"),
    }))
}

/// Build a Pons bonding-curve BUY. Native-pair launches pay ETH (msg.value);
/// ERC-20-pair launches need an approve first. Returns the same
/// `{transactions, expectedAmountOut, amountInWei}` envelope the frontend swap
/// card already consumes. Body: {tokenAddress|curve, walletAddress, amountWei, slippagePct}.
pub async fn build_pons_buy(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let rpc = rpc().ok_or_else(|| AppError::Internal("no Robinhood RPC".into()))?;
    let wallet = body.get("walletAddress").and_then(|v| v.as_str()).unwrap_or(NATIVE);
    let slippage = body.get("slippagePct").and_then(|v| v.as_f64()).unwrap_or(15.0).clamp(0.5, 90.0);
    let quote_in = to_u128(body.get("amountWei").and_then(|v| v.as_str()).unwrap_or("0"));
    if quote_in == 0 {
        return Err(AppError::InvalidParams("pons: amount required".into()));
    }
    let curve = resolve_curve(http, &rpc, body).await?;
    if is_graduated(http, &rpc, &curve).await {
        // Graduated: it's a normal Uniswap pool on Robinhood now — trade it with
        // our own Uniswap swap (uniswap_swap), not the curve. The `graduated`
        // flag lets the frontend route there.
        return Ok(json!({ "graduated": true, "tokenAddress": resolve_token(http, &rpc, body, &curve).await.unwrap_or_default() }));
    }
    let pair_token = word_addr(&eth_call(http, &rpc, &curve, SEL_PAIR_TOKEN).await?, 0);
    let native = pair_token.eq_ignore_ascii_case(NATIVE);

    // expected tokens out = getAmountOut(net, quoteReserve, tokenReserve), fee off input.
    let reserves = eth_call(http, &rpc, &curve, SEL_GET_RESERVES).await?;
    let qr = word_f64(&reserves, 0);
    let tr = word_f64(&reserves, 1);
    let fee_bps = word_f64(&eth_call(http, &rpc, &curve, SEL_FEE_BPS).await?, 0);
    let tax_bps = word_f64(&eth_call(http, &rpc, &curve, SEL_CREATOR_TAX_BPS).await.unwrap_or_default(), 0);
    let qin = quote_in as f64;
    let net = qin * (1.0 - (fee_bps + tax_bps) / 10_000.0);
    let expected = if qr + net > 0.0 { net * tr / (qr + net) } else { 0.0 };
    let min_out = (expected * (1.0 - slippage / 100.0)).max(0.0) as u128;

    let data = format!(
        "{SEL_BUY}{}{}{}",
        enc_u256(quote_in),
        enc_u256(min_out),
        enc_addr(wallet)
    );
    let mut txs = vec![];
    if !native {
        // approve(pairToken, curve, quoteIn) then buy with value 0
        let approve = format!("{SEL_ERC20_APPROVE}{}{}", enc_addr(&curve), enc_u256(quote_in));
        txs.push(json!({ "to": pair_token, "data": approve, "value": "0", "chainId": ROBINHOOD_CHAIN }));
        txs.push(json!({ "to": curve, "data": data.clone(), "value": "0", "chainId": ROBINHOOD_CHAIN }));
    } else {
        txs.push(json!({ "to": curve, "data": data.clone(), "value": quote_in.to_string(), "chainId": ROBINHOOD_CHAIN }));
    }
    Ok(json!({
        "transactions": txs,
        "expectedAmountOut": format!("{}", expected as u128),
        "amountInWei": quote_in.to_string(),
        "curve": curve,
    }))
}

/// Build a Pons bonding-curve SELL: approve the token to the curve, then sell.
/// Body: {tokenAddress|curve, walletAddress, amountInWei (token base units), slippagePct}.
pub async fn build_pons_sell(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let rpc = rpc().ok_or_else(|| AppError::Internal("no Robinhood RPC".into()))?;
    let wallet = body.get("walletAddress").and_then(|v| v.as_str()).unwrap_or(NATIVE);
    let slippage = body.get("slippagePct").and_then(|v| v.as_f64()).unwrap_or(15.0).clamp(0.5, 90.0);
    let tokens_in = to_u128(body.get("amountInWei").and_then(|v| v.as_str()).unwrap_or("0"));
    if tokens_in == 0 {
        return Err(AppError::InvalidParams("pons: amount required".into()));
    }
    let token = body.get("tokenAddress").and_then(|v| v.as_str()).unwrap_or("");
    let curve = resolve_curve(http, &rpc, body).await?;
    if is_graduated(http, &rpc, &curve).await {
        // Graduated → sell it as a normal Uniswap swap (frontend routes to uniswap_swap).
        return Ok(json!({ "graduated": true, "tokenAddress": resolve_token(http, &rpc, body, &curve).await.unwrap_or_default() }));
    }
    // expected quote out = getAmountOut(tokensIn, tokenReserve, quoteReserve), fee off output.
    let reserves = eth_call(http, &rpc, &curve, SEL_GET_RESERVES).await?;
    let qr = word_f64(&reserves, 0);
    let tr = word_f64(&reserves, 1);
    let fee_bps = word_f64(&eth_call(http, &rpc, &curve, SEL_FEE_BPS).await?, 0);
    let tax_bps = word_f64(&eth_call(http, &rpc, &curve, SEL_CREATOR_TAX_BPS).await.unwrap_or_default(), 0);
    let ti = tokens_in as f64;
    let gross = if tr + ti > 0.0 { ti * qr / (tr + ti) } else { 0.0 };
    let expected = gross * (1.0 - (fee_bps + tax_bps) / 10_000.0);
    let min_out = (expected * (1.0 - slippage / 100.0)).max(0.0) as u128;

    let token_addr = if token.starts_with("0x") {
        token.to_string()
    } else {
        word_addr(&eth_call(http, &rpc, &curve, "0xfc0c546a").await?, 0) // curve.token()
    };
    let approve = format!("{SEL_ERC20_APPROVE}{}{}", enc_addr(&curve), enc_u256(tokens_in));
    let sell = format!("{SEL_SELL}{}{}{}", enc_u256(tokens_in), enc_u256(min_out), enc_addr(wallet));
    let txs = vec![
        json!({ "to": token_addr, "data": approve, "value": "0", "chainId": ROBINHOOD_CHAIN }),
        json!({ "to": curve, "data": sell, "value": "0", "chainId": ROBINHOOD_CHAIN }),
    ];
    Ok(json!({
        "transactions": txs,
        "expectedAmountOut": format!("{}", expected as u128),
        "amountInWei": tokens_in.to_string(),
        "curve": curve,
    }))
}

// ── Launch: ABI-encode the factory launchToken calls ──
// V2 (bonding curve): launchToken(TokenParams, launchConfigId, pairToken) payable
const SEL_LAUNCH_TOKEN: &str = "f35abbcf";
// V1 (CREATE2 + Uniswap V3): launchToken(TokenParams_v1, launchConfigId, dexId, salt) payable
const SEL_LAUNCH_TOKEN_V1: &str = "686399cb";
const SEL_LAUNCH_FEE: &str = "0xcf3cf573"; // launchFee()
const PONS_LAUNCH_FEE_FALLBACK: u128 = 500_000_000_000_000; // 0.0005 ETH

fn w_u(n: u128) -> Vec<u8> {
    let mut v = vec![0u8; 32];
    v[16..32].copy_from_slice(&n.to_be_bytes());
    v
}
fn w_addr_bytes(a: &str) -> Vec<u8> {
    let mut v = vec![0u8; 32];
    if let Ok(bytes) = hex::decode(format!("{:0>40}", a.trim_start_matches("0x"))) {
        if bytes.len() == 20 {
            v[12..32].copy_from_slice(&bytes);
        }
    }
    v
}
/// ABI `string`: 32-byte length + data zero-padded to a 32-byte boundary.
fn enc_string(s: &str) -> Vec<u8> {
    let b = s.as_bytes();
    let mut out = w_u(b.len() as u128);
    out.extend_from_slice(b);
    let pad = (32 - (b.len() % 32)) % 32;
    out.extend(std::iter::repeat(0u8).take(pad));
    out
}
/// ABI dynamic tuple of N strings (offsets head + string tail).
fn enc_tuple_strings(strs: &[&str]) -> Vec<u8> {
    let tails: Vec<Vec<u8>> = strs.iter().map(|s| enc_string(s)).collect();
    let mut offset = strs.len() * 32;
    let mut head = Vec::new();
    for t in &tails {
        head.extend(w_u(offset as u128));
        offset += t.len();
    }
    for t in tails {
        head.extend(t);
    }
    head
}

#[allow(clippy::too_many_arguments)]
fn enc_token_params(
    name: &str,
    symbol: &str,
    logo: &str,
    description: &str,
    socials: Vec<u8>,
    fee_recipient: &str,
    tax_bps: u128,
    buyback: bool,
    salt: [u8; 32],
) -> Vec<u8> {
    // TokenParams: 4 dynamic strings + socials(dynamic tuple) + address + uint16
    // + bool + bytes32(expectedEconomics=0) + bytes32(salt). 10 head words.
    let dyn_fields = [
        enc_string(name),
        enc_string(symbol),
        enc_string(logo),
        enc_string(description),
        socials,
    ];
    let mut offset = 10 * 32;
    let mut head = Vec::new();
    for f in &dyn_fields {
        head.extend(w_u(offset as u128));
        offset += f.len();
    }
    head.extend(w_addr_bytes(fee_recipient)); // creatorFeeRecipient (0 → deployer)
    head.extend(w_u(tax_bps)); // creatorTaxBps
    head.extend(w_u(if buyback { 1 } else { 0 })); // buybackEnabled
    head.extend(vec![0u8; 32]); // expectedEconomics = 0 (waives the check)
    head.extend(salt.to_vec()); // salt
    for f in dyn_fields {
        head.extend(f);
    }
    head
}

/// V1 TokenParams: (name, symbol, logo, description, socials, feeWallet). 6 head
/// words (4 dynamic strings + socials offset + feeWallet address).
fn enc_token_params_v1(
    name: &str,
    symbol: &str,
    logo: &str,
    description: &str,
    socials: Vec<u8>,
    fee_wallet: &str,
) -> Vec<u8> {
    let dyn_fields = [
        enc_string(name),
        enc_string(symbol),
        enc_string(logo),
        enc_string(description),
        socials,
    ];
    let mut offset = 6 * 32;
    let mut head = Vec::new();
    for f in &dyn_fields {
        head.extend(w_u(offset as u128));
        offset += f.len();
    }
    head.extend(w_addr_bytes(fee_wallet)); // feeWallet (creator fee recipient)
    for f in dyn_fields {
        head.extend(f);
    }
    head
}

/// Build a Pons token-launch tx. `version`="v1" (CREATE2 + Uniswap V3) or "v2"
/// (bonding curve → Uniswap V4, default). Body: {version?, walletAddress, name,
/// symbol, logo?, description?, twitter?/telegram?, creatorTaxBps?, buyback?,
/// pairToken?}. Native-pair; value = launchFee (0.0005 ETH on both).
pub async fn build_pons_launch(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let rpc = rpc().ok_or_else(|| AppError::Internal("no Robinhood RPC".into()))?;
    let s = |k: &str| body.get(k).and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    let is_v1 = s("version").eq_ignore_ascii_case("v1");
    let name = s("name");
    let symbol = s("symbol");
    if name.is_empty() || symbol.is_empty() {
        return Err(AppError::InvalidParams("pons: token name and symbol are required".into()));
    }
    // V1: name ≤32 / ticker ≤10; V2: name ≤64 / ticker ≤16.
    let (name_max, sym_max) = if is_v1 { (32, 10) } else { (64, 16) };
    if name.chars().count() > name_max {
        return Err(AppError::InvalidParams(format!("pons: name must be ≤ {name_max} characters")));
    }
    if symbol.chars().count() > sym_max {
        return Err(AppError::InvalidParams(format!("pons: ticker must be ≤ {sym_max} characters")));
    }
    let mut logo = s("logo");
    if logo.len() > 512 {
        logo.truncate(512);
    }
    let mut description = s("description");
    let desc_max = if is_v1 { 256 } else { 2048 };
    if description.chars().count() > desc_max {
        description = description.chars().take(desc_max).collect();
    }
    let socials = enc_tuple_strings(&[&s("twitter"), &s("telegram"), &s("discord"), &s("website"), &s("farcaster")]);
    let wallet = s("walletAddress");

    // Unique CREATE2 salt (namespaced per account) — a uuid avoids collisions.
    let mut salt = [0u8; 32];
    salt[16..32].copy_from_slice(uuid::Uuid::new_v4().as_bytes());

    let (factory, data) = if is_v1 {
        // launchToken(TokenParams_v1, launchConfigId=0, dexId=0, salt)
        let fee_wallet = if wallet.starts_with("0x") { wallet.as_str() } else { NATIVE };
        let params_bytes = enc_token_params_v1(&name, &symbol, &logo, &description, socials, fee_wallet);
        let mut d = hex::decode(SEL_LAUNCH_TOKEN_V1).unwrap_or_default();
        d.extend(w_u(0x80)); // offset to params (4 args × 32)
        d.extend(w_u(0)); // launchConfigId = 0
        d.extend(w_u(0)); // dexId = 0 (Uniswap V3)
        d.extend(salt.to_vec()); // salt
        d.extend(params_bytes);
        (PONS_V1_FACTORY, d)
    } else {
        // launchToken(TokenParams, launchConfigId=0, pairToken) payable
        let tax_bps = body.get("creatorTaxBps").and_then(|v| v.as_str()).and_then(|x| x.parse::<u128>().ok()).unwrap_or(0).min(1000);
        let buyback = body.get("buyback").and_then(|v| v.as_str()).map(|x| x == "true").unwrap_or(false);
        let pair_token = body.get("pairToken").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x")).unwrap_or(NATIVE);
        let params_bytes = enc_token_params(&name, &symbol, &logo, &description, socials, NATIVE, tax_bps, buyback, salt);
        let mut d = hex::decode(SEL_LAUNCH_TOKEN).unwrap_or_default();
        d.extend(w_u(0x60)); // offset to params (3 args × 32)
        d.extend(w_u(0)); // launchConfigId = 0
        d.extend(w_addr_bytes(pair_token)); // pairToken (native)
        d.extend(params_bytes);
        (PONS_V2_FACTORY, d)
    };

    // launchFee (read live from the chosen factory; fall back to 0.0005 ETH).
    let fee = match eth_call(http, &rpc, factory, SEL_LAUNCH_FEE).await {
        Ok(h) => {
            let v = to_u128(&h);
            if v > 0 { v } else { PONS_LAUNCH_FEE_FALLBACK }
        }
        Err(_) => PONS_LAUNCH_FEE_FALLBACK,
    };

    Ok(json!({
        "transactions": [{
            "to": factory,
            "data": format!("0x{}", hex::encode(data)),
            "value": fee.to_string(),
            "chainId": ROBINHOOD_CHAIN,
        }],
        "launchFeeWei": fee.to_string(),
        "version": if is_v1 { "v1" } else { "v2" },
    }))
}

async fn resolve_curve(http: &reqwest::Client, rpc: &str, body: &Value) -> Result<String, AppError> {
    if let Some(c) = body.get("curve").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x") && s.len() == 42) {
        return Ok(c.to_string());
    }
    let token = body
        .get("tokenAddress")
        .and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x"))
        .ok_or_else(|| AppError::InvalidParams("pons: token or curve required".into()))?;
    let raw = eth_call(http, rpc, PONS_V2_FACTORY, &format!("{SEL_GET_LAUNCHED}{}", enc_addr(token))).await?;
    let curve = word_addr(&raw, 1);
    if curve.eq_ignore_ascii_case(NATIVE) {
        return Err(AppError::InvalidParams("pons: not a Pons launch".into()));
    }
    Ok(curve)
}

/// The token address for a request — from the body, else curve.token().
async fn resolve_token(http: &reqwest::Client, rpc: &str, body: &Value, curve: &str) -> Result<String, AppError> {
    if let Some(t) = body.get("tokenAddress").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x") && s.len() == 42) {
        return Ok(t.to_string());
    }
    let raw = eth_call(http, rpc, curve, "0xfc0c546a").await?; // curve.token()
    Ok(word_addr(&raw, 0))
}

async fn is_graduated(http: &reqwest::Client, rpc: &str, curve: &str) -> bool {
    eth_call(http, rpc, curve, SEL_GRADUATED)
        .await
        .ok()
        .map(|h| h.trim_start_matches("0x").trim_start_matches('0') != "")
        .unwrap_or(false)
}

/// Full launches feed for the `Pons` launchpad filter — same envelope as the
/// pools.trade feed so the frontend card renders it unchanged.
pub async fn build_pons_launches(
    http: &reqwest::Client,
    params: &PonsLaunchesParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(20).clamp(1, 30);
    let search = params.query.as_deref().map(str::trim).filter(|s| !s.is_empty());
    let rows = fetch_pons_rows(http, limit, search).await;

    let description = match search {
        Some(q) if rows.is_empty() => format!("No Pons tokens match “{q}”."),
        Some(q) => format!("{} Pons tokens match “{q}”.", rows.len()),
        None if rows.is_empty() => "No recent Pons launches found.".to_string(),
        None => format!("Pons launches — {} on Robinhood Chain.", rows.len()),
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "pons_launches".to_string(),
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
        data: Some(json!({ "chain": "robinhood", "chainName": "Robinhood", "launches": rows })),
    })
}

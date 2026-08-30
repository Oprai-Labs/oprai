//! Morpho Blue lending on Robinhood Chain (chain 4663).
//!
//! Morpho Blue is a single immutable **singleton** contract; every market is an
//! isolated (loanToken, collateralToken, oracle, irm, lltv) tuple identified by
//! `Id = keccak256(abi.encode(MarketParams))`. On Robinhood Chain the singleton
//! is `0x9D53…1010` (a per-chain address — NOT the mainnet 0xBBBB vanity). The
//! Robinhood "Earn" 7% USDG product is a Morpho market/vault underneath, so most
//! markets here lend USDG against stable/RWA collateral (USDe, syrupUSDG, sGOV…).
//!
//! Like Pons, this is CONTRACT-ONLY for writes: we ABI-encode calls straight
//! against the singleton and return unsigned `{to,data,value,chainId}` txs the
//! user's own wallet signs (non-custodial). Reads (market list, user positions)
//! come from Morpho's public GraphQL API, and the tx path resolves MarketParams
//! ON-CHAIN via `idToMarketParams(id)` so calldata never trusts the API.
//!
//! Selectors below were verified live against the deployed 4663 bytecode.

use serde_json::{json, Value};

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::services::uniswap::eth_call;
use uuid::Uuid;

/// Morpho Blue singleton on Robinhood Chain (4663). Per-chain — do not reuse the
/// Ethereum-mainnet address.
pub const MORPHO_SINGLETON: &str = "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010";
const NATIVE: &str = "0x0000000000000000000000000000000000000000";
const MORPHO_API: &str = "https://blue-api.morpho.org/graphql";

/// Morpho Blue singleton per chain (verified live via the API `morphoBlues`).
/// These are the OPRAI-supported chains Morpho is deployed on. Robinhood (4663)
/// and Ethereum/Base share no address — the address is genuinely per-chain.
fn chain_singleton(chain: u64) -> Option<&'static str> {
    Some(match chain {
        1 => "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",     // Ethereum
        8453 => "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",  // Base
        42161 => "0x6c247b1F6182318877311737BaC0844bAa518F5e", // Arbitrum
        10 => "0xce95AfbB8EA029495c66020883F87aaE8864AF92",    // Optimism
        137 => "0x1bF0c2541F820E775182832f06c0B7Fc27A25f67",   // Polygon
        130 => "0x8f5ae9CddB9f68de460C77730b018Ae7E04a140A",   // Unichain (reads only — no Alchemy RPC)
        4663 => MORPHO_SINGLETON,                              // Robinhood
        _ => return None,
    })
}

/// Chains OPRAI shows Morpho reads for (market list / positions). Superset of the
/// write-capable chains — Unichain has no Alchemy RPC so it's read-only.
const MORPHO_READ_CHAINS: &[u64] = &[1, 8453, 42161, 10, 137, 130, 4663];

/// RPC for a chain's write path (idToMarketParams / position / allowance).
/// Robinhood uses its public RPC; the rest go through Alchemy.
fn rpc_for(chain: u64) -> Option<String> {
    if chain == 4663 { return Some(rpc()); }
    crate::services::uniswap::alchemy_rpc(chain)
}

/// The chain a request targets — `chain` may be a numeric id or a name; default
/// Robinhood (4663), OPRAI's home chain for Morpho.
fn chain_of(body: &Value) -> u64 {
    match body.get("chain").or_else(|| body.get("chainId")) {
        Some(Value::Number(n)) => n.as_u64().unwrap_or(4663),
        Some(Value::String(s)) => {
            let s = s.trim().to_lowercase();
            if let Ok(n) = s.parse::<u64>() { return n; }
            match s.as_str() {
                "ethereum" | "eth" | "mainnet" => 1,
                "base" => 8453,
                "arbitrum" | "arb" => 42161,
                "optimism" | "op" => 10,
                "polygon" | "matic" => 137,
                "unichain" => 130,
                "robinhood" | "rh" => 4663,
                _ => 4663,
            }
        }
        _ => 4663,
    }
}
fn chain_name(chain: u64) -> &'static str {
    match chain {
        1 => "Ethereum", 8453 => "Base", 42161 => "Arbitrum", 10 => "Optimism",
        137 => "Polygon", 130 => "Unichain", 4663 => "Robinhood", _ => "EVM",
    }
}

// Morpho Blue write selectors (verified present in deployed 4663 bytecode).
const SEL_SUPPLY: &str = "a99aad89"; // supply(MarketParams,assets,shares,onBehalf,data)
const SEL_WITHDRAW: &str = "5c2bea49"; // withdraw(MarketParams,assets,shares,onBehalf,receiver)
const SEL_BORROW: &str = "50d8cd4b"; // borrow(MarketParams,assets,shares,onBehalf,receiver)
const SEL_REPAY: &str = "20b76e81"; // repay(MarketParams,assets,shares,onBehalf,data)
const SEL_SUPPLY_COLLATERAL: &str = "238d6579"; // supplyCollateral(MarketParams,assets,onBehalf,data)
const SEL_WITHDRAW_COLLATERAL: &str = "8720316d"; // withdrawCollateral(MarketParams,assets,onBehalf,receiver)
// read selectors
const SEL_ID_TO_PARAMS: &str = "0x2c3c9157"; // idToMarketParams(bytes32)
const SEL_POSITION: &str = "0x93c52062"; // position(bytes32,address)->(supplyShares,borrowShares,collateral)
const SEL_MARKET: &str = "0x5c60e39a"; // market(bytes32)->(tSupplyA,tSupplyS,tBorrowA,tBorrowS,lastUpdate,fee)
// ERC-20 selectors
const SEL_APPROVE: &str = "095ea7b3"; // approve(address,uint256)
const SEL_ALLOWANCE: &str = "0xdd62ed3e"; // allowance(address,address)
const SEL_DECIMALS: &str = "0x313ce567"; // decimals()

/// Robinhood Chain public RPC (shared with Pons). Overridable via ROBINHOOD_RPC.
const ROBINHOOD_PUBLIC_RPC: &str = "https://rpc.mainnet.chain.robinhood.com";
fn rpc() -> String {
    std::env::var("ROBINHOOD_RPC")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| ROBINHOOD_PUBLIC_RPC.to_string())
}

// ── ABI helpers ────────────────────────────────────────────────────────────
/// 32-byte word from a u128 value, hex (no 0x).
fn w_u128(v: u128) -> String {
    format!("{v:064x}")
}
/// 32-byte word for an address (12 zero bytes + 20-byte addr), hex (no 0x).
fn w_addr(a: &str) -> String {
    format!("{:0>64}", a.trim_start_matches("0x").to_lowercase())
}
/// A raw 32-byte hex word (no 0x) at word `i` of a hex payload.
fn word_hex(hex: &str, i: usize) -> &str {
    let h = hex.trim_start_matches("0x");
    let s = i * 64;
    if h.len() < s + 64 {
        ""
    } else {
        &h[s..s + 64]
    }
}
/// Low 128 bits of word `i` → u128 (reserves/amounts/shares all fit u128 here).
fn word_u128(hex: &str, i: usize) -> u128 {
    let w = word_hex(hex, i);
    if w.len() < 64 {
        return 0;
    }
    u128::from_str_radix(w[32..64].trim_start_matches('0'), 16).unwrap_or(0)
}
/// Address from word `i` (last 20 bytes) → 0x-prefixed lowercase.
fn word_addr(hex: &str, i: usize) -> String {
    let w = word_hex(hex, i);
    if w.len() < 64 {
        return NATIVE.to_string();
    }
    format!("0x{}", &w[24..64])
}
/// Decimal / hex string → u128.
fn to_u128(s: &str) -> u128 {
    let s = s.trim();
    if let Some(h) = s.strip_prefix("0x") {
        u128::from_str_radix(h.trim_start_matches('0'), 16).unwrap_or(0)
    } else if let Some(dot) = s.find('.') {
        // tolerate an accidental "5.0" — take the integer part
        s[..dot].parse::<u128>().unwrap_or(0)
    } else {
        s.parse::<u128>().unwrap_or(0)
    }
}
/// mulDiv rounding up (for shares→assets on repay so we never under-repay).
fn mul_div_up(a: u128, b: u128, d: u128) -> u128 {
    if d == 0 {
        return 0;
    }
    let (n, _) = a.overflowing_mul(b);
    // values here are < 2^107, no real overflow; guard anyway.
    if b != 0 && n / b != a {
        // fall back to f64 if it ever overflows
        return ((a as f64) * (b as f64) / (d as f64)).ceil() as u128;
    }
    n.div_ceil(d)
}

/// The 5 MarketParams words (320 hex chars, no 0x) read straight from the
/// singleton — the authoritative source for calldata. Returns (params_words,
/// loanToken, collateralToken).
async fn market_params(
    http: &reqwest::Client,
    rpc: &str,
    singleton: &str,
    market_id: &str,
) -> Result<(String, String, String), AppError> {
    let id = market_id.trim();
    let id = if let Some(h) = id.strip_prefix("0x") { h } else { id };
    if id.len() != 64 || hex::decode(id).is_err() {
        return Err(AppError::InvalidParams("morpho: invalid marketId".into()));
    }
    let data = format!("{SEL_ID_TO_PARAMS}{id}");
    let raw = eth_call(http, rpc, singleton, &data).await?;
    let h = raw.trim_start_matches("0x");
    if h.len() < 320 {
        return Err(AppError::InvalidParams("morpho: market not found".into()));
    }
    let params_words = h[..320].to_string();
    let loan = word_addr(&raw, 0);
    let collateral = word_addr(&raw, 1);
    if loan.eq_ignore_ascii_case(NATIVE) {
        return Err(AppError::InvalidParams("morpho: market not found".into()));
    }
    Ok((params_words, loan, collateral))
}

/// ERC-20 `decimals()` (defaults to 18 on failure).
async fn token_decimals(http: &reqwest::Client, rpc: &str, token: &str) -> u32 {
    match eth_call(http, rpc, token, SEL_DECIMALS).await {
        Ok(h) => {
            let d = word_u128(&h, 0);
            if d == 0 || d > 36 {
                18
            } else {
                d as u32
            }
        }
        Err(_) => 18,
    }
}

/// Current ERC-20 allowance owner→spender (the chain's singleton).
async fn allowance(http: &reqwest::Client, rpc: &str, token: &str, owner: &str, spender: &str) -> u128 {
    let data = format!("{SEL_ALLOWANCE}{}{}", w_addr(owner), w_addr(spender));
    match eth_call(http, rpc, token, &data).await {
        Ok(h) => word_u128(&h, 0),
        Err(_) => 0,
    }
}

/// Resolve an amount to base units. Prefer an explicit `<key>BaseUnits` (the card
/// already knows decimals from the balance probe); else scale the human `<key>`
/// by the token's on-chain decimals.
async fn resolve_amount(
    http: &reqwest::Client,
    rpc: &str,
    body: &Value,
    human_key: &str,
    base_key: &str,
    token: &str,
) -> u128 {
    if let Some(b) = body.get(base_key).and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
        return to_u128(b);
    }
    if let Some(b) = body.get(base_key).and_then(|v| v.as_u64()) {
        return b as u128;
    }
    let human = body
        .get(human_key)
        .and_then(|v| v.as_str().map(|s| s.to_string()).or_else(|| v.as_f64().map(|f| format!("{f}"))))
        .unwrap_or_default();
    let human = human.trim();
    if human.is_empty() {
        return 0;
    }
    let dec = token_decimals(http, rpc, token).await;
    parse_scaled(human, dec)
}
/// "1.5" @ 6 dp → 1500000. Truncates beyond `dec` places.
fn parse_scaled(s: &str, dec: u32) -> u128 {
    let s = s.trim();
    let (int_part, frac_part) = match s.split_once('.') {
        Some((i, f)) => (i, f),
        None => (s, ""),
    };
    let int_v = int_part.parse::<u128>().unwrap_or(0);
    let mut frac: String = frac_part.chars().take(dec as usize).collect();
    while (frac.len() as u32) < dec {
        frac.push('0');
    }
    let frac_v = if frac.is_empty() { 0 } else { frac.parse::<u128>().unwrap_or(0) };
    int_v * 10u128.pow(dec) + frac_v
}

/// Build an ERC-20 approve tx (spender = the chain's singleton) if allowance is short.
#[allow(clippy::too_many_arguments)]
async fn maybe_approve(
    http: &reqwest::Client,
    rpc: &str,
    chain: u64,
    singleton: &str,
    token: &str,
    owner: &str,
    need: u128,
    txs: &mut Vec<Value>,
) {
    if need == 0 {
        return;
    }
    let have = allowance(http, rpc, token, owner, singleton).await;
    if have >= need {
        return;
    }
    let data = format!("{SEL_APPROVE}{}{}", w_addr(singleton), w_u128(need));
    txs.push(json!({ "to": token, "data": format!("0x{data}"), "value": "0", "chainId": chain }));
}

fn ok(chain: u64, txs: Vec<Value>, extra: Value) -> Value {
    let mut out = json!({ "transactions": txs, "chainId": chain });
    if let (Some(o), Some(e)) = (out.as_object_mut(), extra.as_object()) {
        for (k, v) in e {
            o.insert(k.clone(), v.clone());
        }
    }
    out
}

// ── Write builders ─────────────────────────────────────────────────────────

/// LEND (supply loan asset → earn supplyApy). Body: {marketId, walletAddress,
/// amount|amountBaseUnits}. → [approve loan?, supply(params, assets, 0, wallet, "")].
pub async fn build_lend(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let chain = chain_of(body);
    let singleton = chain_singleton(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("morpho: not deployed on {}", chain_name(chain))))?;
    let rpc = rpc_for(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("morpho: writes not available on {} (no RPC)", chain_name(chain))))?;
    let market_id = req_market(body)?;
    let wallet = req_wallet(body)?;
    let (params, loan,_coll) = market_params(http, &rpc, singleton, &market_id).await?;
    let assets = resolve_amount(http, &rpc, body, "amount", "amountBaseUnits", &loan).await;
    if assets == 0 {
        return Err(AppError::InvalidParams("morpho: amount required".into()));
    }
    let mut txs = vec![];
    maybe_approve(http, &rpc, chain, singleton, &loan, &wallet, assets, &mut txs).await;
    // supply(params, assets, shares=0, onBehalf=wallet, data=empty)
    let data = format!(
        "{SEL_SUPPLY}{params}{}{}{}{}{}",
        w_u128(assets),
        w_u128(0),
        w_addr(&wallet),
        w_u128(0x120), // offset to data (9 words)
        w_u128(0),     // data length 0
    );
    txs.push(json!({ "to": singleton, "data": format!("0x{data}"), "value": "0", "chainId": chain }));
    Ok(ok(chain, txs, json!({ "amountBaseUnits": assets.to_string(), "loanToken": loan, "chainName": chain_name(chain) })))
}

/// BORROW (optionally add collateral first, then borrow loan asset). Body:
/// {marketId, walletAddress, collateralAmount|collateralBaseUnits?, borrowAmount|borrowBaseUnits}.
/// → [approve coll?, supplyCollateral?, borrow(params, borrow, 0, wallet, wallet)].
pub async fn build_borrow(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let chain = chain_of(body);
    let singleton = chain_singleton(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("morpho: not deployed on {}", chain_name(chain))))?;
    let rpc = rpc_for(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("morpho: writes not available on {} (no RPC)", chain_name(chain))))?;
    let market_id = req_market(body)?;
    let wallet = req_wallet(body)?;
    let (params, loan,collateral) = market_params(http, &rpc, singleton, &market_id).await?;
    let coll = resolve_amount(http, &rpc, body, "collateralAmount", "collateralBaseUnits", &collateral).await;
    let borrow = resolve_amount(http, &rpc, body, "borrowAmount", "borrowBaseUnits", &loan).await;
    if borrow == 0 {
        return Err(AppError::InvalidParams("morpho: borrow amount required".into()));
    }
    let mut txs = vec![];
    if coll > 0 {
        maybe_approve(http, &rpc, chain, singleton, &collateral, &wallet, coll, &mut txs).await;
        // supplyCollateral(params, assets, onBehalf=wallet, data=empty)
        let data = format!(
            "{SEL_SUPPLY_COLLATERAL}{params}{}{}{}{}",
            w_u128(coll),
            w_addr(&wallet),
            w_u128(0x100), // offset to data (8 words)
            w_u128(0),     // data length 0
        );
        txs.push(json!({ "to": singleton, "data": format!("0x{data}"), "value": "0", "chainId": chain }));
    }
    // borrow(params, assets, shares=0, onBehalf=wallet, receiver=wallet)
    let data = format!(
        "{SEL_BORROW}{params}{}{}{}{}",
        w_u128(borrow),
        w_u128(0),
        w_addr(&wallet),
        w_addr(&wallet),
    );
    txs.push(json!({ "to": singleton, "data": format!("0x{data}"), "value": "0", "chainId": chain }));
    Ok(ok(
        chain,
        txs,
        json!({ "borrowBaseUnits": borrow.to_string(), "collateralBaseUnits": coll.to_string(), "loanToken": loan, "collateralToken": collateral }),
    ))
}

/// REPAY (partial by amount, or full via `max` using borrow shares). Body:
/// {marketId, walletAddress, amount|amountBaseUnits, max?}. → [approve loan?, repay(...)].
pub async fn build_repay(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let chain = chain_of(body);
    let singleton = chain_singleton(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("morpho: not deployed on {}", chain_name(chain))))?;
    let rpc = rpc_for(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("morpho: writes not available on {} (no RPC)", chain_name(chain))))?;
    let market_id = req_market(body)?;
    let wallet = req_wallet(body)?;
    let (params, loan,_coll) = market_params(http, &rpc, singleton, &market_id).await?;
    let max = body.get("max").and_then(|v| v.as_bool()).unwrap_or(false)
        || body.get("max").and_then(|v| v.as_str()) == Some("true");

    let (assets, shares, approve_amt);
    if max {
        // repay ALL by shares: assets=0, shares=userBorrowShares. Approve the
        // asset value (shares→assets, rounded up) + a small buffer for interest
        // that accrues between build and signature.
        let pos = eth_call(http, &rpc, singleton, &format!("{SEL_POSITION}{}{}", strip_id(&market_id), w_addr(&wallet))).await?;
        let borrow_shares = word_u128(&pos, 1);
        if borrow_shares == 0 {
            return Err(AppError::InvalidParams("morpho: no debt to repay in this market".into()));
        }
        let mkt = eth_call(http, &rpc, singleton, &format!("{SEL_MARKET}{}", strip_id(&market_id))).await?;
        let total_borrow_assets = word_u128(&mkt, 2);
        let total_borrow_shares = word_u128(&mkt, 3);
        let owed = mul_div_up(borrow_shares, total_borrow_assets, total_borrow_shares);
        assets = 0;
        shares = borrow_shares;
        approve_amt = owed + owed / 100 + 1; // +1% buffer
    } else {
        let a = resolve_amount(http, &rpc, body, "amount", "amountBaseUnits", &loan).await;
        if a == 0 {
            return Err(AppError::InvalidParams("morpho: amount required".into()));
        }
        assets = a;
        shares = 0;
        approve_amt = a;
    }

    let mut txs = vec![];
    maybe_approve(http, &rpc, chain, singleton, &loan, &wallet, approve_amt, &mut txs).await;
    // repay(params, assets, shares, onBehalf=wallet, data=empty)
    let data = format!(
        "{SEL_REPAY}{params}{}{}{}{}{}",
        w_u128(assets),
        w_u128(shares),
        w_addr(&wallet),
        w_u128(0x120),
        w_u128(0),
    );
    txs.push(json!({ "to": singleton, "data": format!("0x{data}"), "value": "0", "chainId": chain }));
    Ok(ok(
        chain,
        txs,
        json!({ "repayAll": max, "amountBaseUnits": assets.to_string(), "sharesRepaid": shares.to_string(), "loanToken": loan }),
    ))
}

/// WITHDRAW — supplied loan asset (`target`="supply", default) or collateral
/// (`target`="collateral"). `max` withdraws everything (supply→by shares,
/// collateral→by read balance). Body: {marketId, walletAddress, amount|amountBaseUnits, target?, max?}.
pub async fn build_withdraw(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let chain = chain_of(body);
    let singleton = chain_singleton(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("morpho: not deployed on {}", chain_name(chain))))?;
    let rpc = rpc_for(chain)
        .ok_or_else(|| AppError::InvalidParams(format!("morpho: writes not available on {} (no RPC)", chain_name(chain))))?;
    let market_id = req_market(body)?;
    let wallet = req_wallet(body)?;
    let (params, loan,collateral) = market_params(http, &rpc, singleton, &market_id).await?;
    let target = body.get("target").and_then(|v| v.as_str()).unwrap_or("supply");
    let max = body.get("max").and_then(|v| v.as_bool()).unwrap_or(false)
        || body.get("max").and_then(|v| v.as_str()) == Some("true");
    let is_collateral = target.eq_ignore_ascii_case("collateral");
    let token = if is_collateral { &collateral } else { &loan };

    let data = if is_collateral {
        // withdrawCollateral(params, assets, onBehalf=wallet, receiver=wallet)
        let assets = if max {
            let pos = eth_call(http, &rpc, singleton, &format!("{SEL_POSITION}{}{}", strip_id(&market_id), w_addr(&wallet))).await?;
            word_u128(&pos, 2) // collateral
        } else {
            resolve_amount(http, &rpc, body, "amount", "amountBaseUnits", token).await
        };
        if assets == 0 {
            return Err(AppError::InvalidParams("morpho: amount required".into()));
        }
        format!(
            "{SEL_WITHDRAW_COLLATERAL}{params}{}{}{}",
            w_u128(assets),
            w_addr(&wallet),
            w_addr(&wallet),
        )
    } else {
        // withdraw(params, assets, shares, onBehalf=wallet, receiver=wallet)
        let (assets, shares) = if max {
            let pos = eth_call(http, &rpc, singleton, &format!("{SEL_POSITION}{}{}", strip_id(&market_id), w_addr(&wallet))).await?;
            (0u128, word_u128(&pos, 0)) // supplyShares
        } else {
            (resolve_amount(http, &rpc, body, "amount", "amountBaseUnits", token).await, 0u128)
        };
        if assets == 0 && shares == 0 {
            return Err(AppError::InvalidParams("morpho: amount required".into()));
        }
        format!(
            "{SEL_WITHDRAW}{params}{}{}{}{}",
            w_u128(assets),
            w_u128(shares),
            w_addr(&wallet),
            w_addr(&wallet),
        )
    };
    let txs = vec![json!({ "to": singleton, "data": format!("0x{data}"), "value": "0", "chainId": chain })];
    Ok(ok(chain, txs, json!({ "target": if is_collateral { "collateral" } else { "supply" }, "withdrawAll": max })))
}

fn strip_id(market_id: &str) -> String {
    let id = market_id.trim();
    let id = id.strip_prefix("0x").unwrap_or(id);
    id.to_lowercase()
}
fn req_market(body: &Value) -> Result<String, AppError> {
    body.get("marketId")
        .and_then(|v| v.as_str())
        .filter(|s| s.len() >= 64)
        .map(|s| s.to_string())
        .ok_or_else(|| AppError::InvalidParams("morpho: marketId required".into()))
}
fn req_wallet(body: &Value) -> Result<String, AppError> {
    body.get("walletAddress")
        .or_else(|| body.get("wallet"))
        .and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
        .map(|s| s.to_string())
        .ok_or_else(|| AppError::InvalidParams("morpho: walletAddress required".into()))
}

// ── Reads (Morpho GraphQL API) ─────────────────────────────────────────────

#[derive(Debug, Clone, serde::Deserialize)]
pub struct MorphoMarketsParams {
    #[serde(default)]
    pub limit: Option<usize>,
    #[serde(default, alias = "search", alias = "q")]
    pub query: Option<String>,
    /// Chain id or name (default Robinhood 4663).
    #[serde(default, alias = "chainId")]
    pub chain: Option<Value>,
}

async fn api_query(http: &reqwest::Client, query: &str) -> Option<Value> {
    let resp = http
        .post(MORPHO_API)
        .header("content-type", "application/json")
        .json(&json!({ "query": query }))
        .timeout(std::time::Duration::from_secs(12))
        .send()
        .await
        .ok()?;
    resp.json::<Value>().await.ok()
}

/// List Morpho markets on `chain`, richest first. Optional name filter. Applies
/// the clean-market filter (borrowApy ≤ 200%, supplied ≥ $100k, listed) so the
/// spam/broken markets with absurd APRs and fake TVL never surface.
pub async fn fetch_markets(http: &reqwest::Client, chain: u64, limit: usize, search: Option<&str>) -> Vec<Value> {
    let q = format!(
        r#"{{ markets(first: {}, where: {{ chainId_in: [{chain}], borrowApy_lte: 2.0, supplyAssetsUsd_gte: 100000, listed: true }}, orderBy: SupplyAssetsUsd, orderDirection: Desc) {{ items {{ marketId lltv loanAsset {{ address symbol decimals priceUsd logoURI }} collateralAsset {{ address symbol decimals priceUsd logoURI }} state {{ supplyApy borrowApy utilization supplyAssetsUsd borrowAssetsUsd liquidityAssetsUsd }} }} }} }}"#,
        (limit * 2).clamp(10, 60)
    );
    let body = match api_query(http, &q).await {
        Some(b) => b,
        None => return vec![],
    };
    let items = body
        .pointer("/data/markets/items")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let ql = search.map(|s| s.trim().to_lowercase()).filter(|s| !s.is_empty());
    let mut rows: Vec<Value> = items
        .into_iter()
        .filter_map(|m| shape_market(&m, chain))
        .filter(|r| {
            let Some(ql) = &ql else { return true };
            let ls = r.get("loanSymbol").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
            let cs = r.get("collateralSymbol").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
            ls.contains(ql) || cs.contains(ql)
        })
        .collect();
    rows.truncate(limit);
    rows
}

fn shape_market(m: &Value, chain: u64) -> Option<Value> {
    let market_id = m.get("marketId").and_then(|v| v.as_str())?.to_string();
    let loan = m.get("loanAsset")?;
    let coll = m.get("collateralAsset");
    // Skip markets with an unknown/idle collateral (broken metadata).
    let loan_symbol = loan.get("symbol").and_then(|v| v.as_str()).unwrap_or("");
    if loan_symbol.is_empty() || loan_symbol == "UNKNOWN" {
        return None;
    }
    let lltv_pct = m
        .get("lltv")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .map(|w| w / 1e16) // 1e18 = 100%
        .unwrap_or(0.0);
    let st = m.get("state");
    Some(json!({
        "marketId": market_id,
        "loanSymbol": loan_symbol,
        "loanAddress": loan.get("address").and_then(|v| v.as_str()).unwrap_or(""),
        "loanDecimals": loan.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18),
        "loanLogo": loan.get("logoURI").cloned().unwrap_or(Value::Null),
        "loanPriceUsd": loan.get("priceUsd").cloned().unwrap_or(Value::Null),
        "collateralSymbol": coll.and_then(|c| c.get("symbol")).and_then(|v| v.as_str()).unwrap_or(""),
        "collateralAddress": coll.and_then(|c| c.get("address")).and_then(|v| v.as_str()).unwrap_or(""),
        "collateralDecimals": coll.and_then(|c| c.get("decimals")).and_then(|v| v.as_u64()).unwrap_or(18),
        "collateralLogo": coll.and_then(|c| c.get("logoURI")).cloned().unwrap_or(Value::Null),
        "collateralPriceUsd": coll.and_then(|c| c.get("priceUsd")).cloned().unwrap_or(Value::Null),
        "lltvPct": lltv_pct,
        "supplyApy": st.and_then(|s| s.get("supplyApy")).cloned().unwrap_or(Value::Null),
        "borrowApy": st.and_then(|s| s.get("borrowApy")).cloned().unwrap_or(Value::Null),
        "utilization": st.and_then(|s| s.get("utilization")).cloned().unwrap_or(Value::Null),
        "supplyUsd": st.and_then(|s| s.get("supplyAssetsUsd")).cloned().unwrap_or(Value::Null),
        "borrowUsd": st.and_then(|s| s.get("borrowAssetsUsd")).cloned().unwrap_or(Value::Null),
        "liquidityUsd": st.and_then(|s| s.get("liquidityAssetsUsd")).cloned().unwrap_or(Value::Null),
        "chainId": chain,
        "chainName": chain_name(chain),
        "chain": chain_name(chain).to_lowercase(),
    }))
}

/// A user's Morpho positions across every OPRAI-supported Morpho chain (supplied,
/// collateral, borrowed, health factor per market).
pub async fn fetch_positions(http: &reqwest::Client, wallet: &str) -> Vec<Value> {
    let w = wallet.trim().to_lowercase();
    if !(w.starts_with("0x") && w.len() == 42) {
        return vec![];
    }
    let chains: Vec<String> = MORPHO_READ_CHAINS.iter().map(|c| c.to_string()).collect();
    let q = format!(
        r#"{{ marketPositions(first: 100, where: {{ chainId_in: [{}], userAddress_in: ["{w}"] }}) {{ items {{ healthFactor market {{ marketId chain {{ id }} loanAsset {{ symbol decimals logoURI }} collateralAsset {{ symbol decimals logoURI }} state {{ supplyApy borrowApy }} }} state {{ supplyAssets supplyAssetsUsd borrowAssets borrowAssetsUsd collateral collateralUsd }} }} }} }}"#,
        chains.join(", ")
    );
    let body = match api_query(http, &q).await {
        Some(b) => b,
        None => return vec![],
    };
    let items = body
        .pointer("/data/marketPositions/items")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    items
        .into_iter()
        .filter_map(|p| shape_position(&p))
        .collect()
}

fn shape_position(p: &Value) -> Option<Value> {
    let market = p.get("market")?;
    let market_id = market.get("marketId").and_then(|v| v.as_str())?.to_string();
    let st = p.get("state")?;
    // Drop empty rows (no supply, no collateral, no debt).
    let supply = st.get("supplyAssets").and_then(num).unwrap_or(0.0);
    let borrow = st.get("borrowAssets").and_then(num).unwrap_or(0.0);
    let coll = st.get("collateral").and_then(num).unwrap_or(0.0);
    if supply == 0.0 && borrow == 0.0 && coll == 0.0 {
        return None;
    }
    let loan = market.get("loanAsset");
    let collateral = market.get("collateralAsset");
    let mst = market.get("state");
    let chain_id = market.pointer("/chain/id").and_then(|v| v.as_u64()).unwrap_or(4663);
    Some(json!({
        "marketId": market_id,
        "chainId": chain_id,
        "chainName": chain_name(chain_id),
        "loanSymbol": loan.and_then(|l| l.get("symbol")).and_then(|v| v.as_str()).unwrap_or(""),
        "loanDecimals": loan.and_then(|l| l.get("decimals")).and_then(|v| v.as_u64()).unwrap_or(18),
        "loanLogo": loan.and_then(|l| l.get("logoURI")).cloned().unwrap_or(Value::Null),
        "collateralSymbol": collateral.and_then(|c| c.get("symbol")).and_then(|v| v.as_str()).unwrap_or(""),
        "collateralDecimals": collateral.and_then(|c| c.get("decimals")).and_then(|v| v.as_u64()).unwrap_or(18),
        "collateralLogo": collateral.and_then(|c| c.get("logoURI")).cloned().unwrap_or(Value::Null),
        "supplyAssets": st.get("supplyAssets").cloned().unwrap_or(Value::Null),
        "supplyUsd": st.get("supplyAssetsUsd").cloned().unwrap_or(Value::Null),
        "borrowAssets": st.get("borrowAssets").cloned().unwrap_or(Value::Null),
        "borrowUsd": st.get("borrowAssetsUsd").cloned().unwrap_or(Value::Null),
        "collateral": st.get("collateral").cloned().unwrap_or(Value::Null),
        "collateralUsd": st.get("collateralUsd").cloned().unwrap_or(Value::Null),
        "healthFactor": p.get("healthFactor").cloned().unwrap_or(Value::Null),
        "supplyApy": mst.and_then(|s| s.get("supplyApy")).cloned().unwrap_or(Value::Null),
        "borrowApy": mst.and_then(|s| s.get("borrowApy")).cloned().unwrap_or(Value::Null),
        "chain": chain_name(chain_id).to_lowercase(),
    }))
}
fn num(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse::<f64>().ok()))
}

/// `/actions/build` envelope for the markets card (read-only, wallet-independent).
pub async fn build_markets(http: &reqwest::Client, params: &MorphoMarketsParams) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(12).clamp(1, 40);
    let search = params.query.as_deref().map(str::trim).filter(|s| !s.is_empty());
    let chain = params.chain.as_ref()
        .map(|c| chain_of(&json!({ "chain": c })))
        .filter(|c| chain_singleton(*c).is_some())
        .unwrap_or(4663);
    let rows = fetch_markets(http, chain, limit, search).await;
    let cname = chain_name(chain);
    let description = match search {
        Some(q) if rows.is_empty() => format!("No Morpho markets match “{q}” on {cname}."),
        Some(q) => format!("{} Morpho markets match “{q}” on {cname}.", rows.len()),
        None if rows.is_empty() => format!("No Morpho markets found on {cname}."),
        None => format!("Morpho lending markets — {} on {cname}.", rows.len()),
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "morpho_markets".to_string(),
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
        data: Some(json!({ "chainId": chain, "chainName": cname, "markets": rows })),
    })
}

/// `/actions/build` envelope for the positions card. `wallet` comes from the
/// request params (the user's linked EVM address), not the Solana session.
pub async fn build_positions(http: &reqwest::Client, wallet: &str) -> Result<BuildResponse, AppError> {
    let rows = fetch_positions(http, wallet).await;
    let description = if rows.is_empty() {
        "No Morpho positions on Robinhood Chain.".to_string()
    } else {
        format!("{} Morpho position(s) on Robinhood Chain.", rows.len())
    };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "morpho_positions".to_string(),
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
        data: Some(json!({ "wallet": wallet, "positions": rows })),
    })
}

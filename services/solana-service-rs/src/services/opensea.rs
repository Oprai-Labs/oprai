//! OpenSea (Seaport 1.6) NFT marketplace on Robinhood Chain (chain 4663).
//!
//! OpenSea supports Robinhood Chain (API chain slug `robinhood`). Reads go
//! through the OpenSea API v2 (needs `OPENSEA_API_KEY`); buying is non-custodial:
//! `POST /listings/fulfillment_data` returns the DECODED Seaport call (function +
//! input_data + calldata_suffix), which we ABI-encode ourselves into an unsigned
//! `{to,data,value,chainId}` tx the user's own wallet signs. The buy calldata
//! encoding was verified byte-for-byte against `foundry cast`.
//!
//! Selling (Seaport listings) is a gasless EIP-712 order the user signs; that
//! path builds the order for the frontend to sign — see `build_list`.

use futures::future::join_all;
use serde_json::{json, Value};

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use uuid::Uuid;

const OPENSEA_API: &str = "https://api.opensea.io/api/v2";
const CHAIN: u64 = 4663;
const CHAIN_SLUG: &str = "robinhood";
/// Seaport 1.6 — canonical address, verified deployed on 4663.
pub const SEAPORT: &str = "0x0000000000000068F116a894984e2DB1123eB395";

fn api_key() -> Result<String, AppError> {
    std::env::var("OPENSEA_API_KEY").ok().filter(|s| !s.is_empty())
        .ok_or_else(|| AppError::Internal("OpenSea is not configured (OPENSEA_API_KEY unset).".into()))
}

async fn os_get(http: &reqwest::Client, path: &str) -> Result<Value, AppError> {
    let key = api_key()?;
    let resp = http
        .get(format!("{OPENSEA_API}{path}"))
        .header("x-api-key", key)
        .header("accept", "application/json")
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("OpenSea request failed: {e}")))?;
    let status = resp.status();
    let v: Value = resp.json().await.map_err(|e| AppError::Internal(format!("OpenSea bad JSON: {e}")))?;
    if !status.is_success() {
        let msg = v.pointer("/error/message").and_then(|m| m.as_str())
            .or_else(|| v.get("error").and_then(|m| m.as_str())).unwrap_or("OpenSea error");
        return Err(AppError::InvalidParams(format!("OpenSea: {msg}")));
    }
    Ok(v)
}

// ── ABI helpers (buy calldata) ───────────────────────────────────────────────
fn w_u128(v: u128) -> String { format!("{v:064x}") }
fn w_addr(a: &str) -> String { format!("{:0>64}", a.trim_start_matches("0x").to_lowercase()) }
fn b32(s: &str) -> String { format!("{:0>64}", s.trim_start_matches("0x").to_lowercase()) }
/// Decimal (or 0x-hex) string → a 256-bit big-endian hex word (handles values
/// beyond u128 — salts and token ids can exceed it).
fn dec_to_word(s: &str) -> String {
    let s = s.trim();
    if let Some(h) = s.strip_prefix("0x") {
        return format!("{:0>64}", h.to_lowercase());
    }
    let mut bytes = [0u8; 32];
    for ch in s.chars() {
        let d = match ch.to_digit(10) { Some(d) => d as u16, None => continue };
        let mut carry = d;
        for b in bytes.iter_mut().rev() {
            let v = (*b as u16) * 10 + carry;
            *b = (v & 0xff) as u8;
            carry = v >> 8;
        }
    }
    hex::encode(bytes)
}
fn s(v: &Value, k: &str) -> String { v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string() }

// ── Reads ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, serde::Deserialize)]
pub struct OpenseaCollectionsParams {
    #[serde(default)]
    pub limit: Option<usize>,
    #[serde(default, alias = "search", alias = "q")]
    pub query: Option<String>,
}

/// List NFT collections on Robinhood Chain.
pub async fn fetch_collections(http: &reqwest::Client, limit: usize, search: Option<&str>) -> Result<Vec<Value>, AppError> {
    let body = os_get(http, &format!("/collections?chain={CHAIN_SLUG}&limit=100&order_by=market_cap")).await?;
    let items = body.get("collections").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let ql = search.map(|s| s.trim().to_lowercase()).filter(|s| !s.is_empty());
    let mut rows: Vec<Value> = items.iter().filter_map(|c| {
        let slug = s(c, "collection");
        let name = s(c, "name");
        if slug.is_empty() { return None; }
        if let Some(ql) = &ql {
            if !name.to_lowercase().contains(ql) && !slug.to_lowercase().contains(ql) { return None; }
        }
        let contract = c.pointer("/contracts/0/address").and_then(|v| v.as_str()).unwrap_or("").to_string();
        Some(json!({
            "slug": slug,
            "name": if name.is_empty() { slug.clone() } else { name },
            "image": c.get("image_url").cloned().unwrap_or(Value::Null),
            "description": c.get("description").cloned().unwrap_or(Value::Null),
            "contract": contract,
            "url": c.get("opensea_url").cloned().unwrap_or(Value::Null),
            "chain": CHAIN_SLUG,
        }))
    }).collect();
    rows.truncate(limit);
    Ok(rows)
}

/// Active listings in a collection, enriched with each NFT's name/image.
pub async fn fetch_listings(http: &reqwest::Client, slug: &str, limit: usize) -> Result<Vec<Value>, AppError> {
    let body = os_get(http, &format!("/listings/collection/{slug}/all?limit={}", limit.clamp(1, 50))).await?;
    let items = body.get("listings").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let raws: Vec<Value> = items.into_iter().filter_map(|l| shape_listing(&l)).collect();
    // Enrich name/image per NFT (best-effort, concurrent).
    let futs = raws.into_iter().map(|r| enrich_listing(http, r));
    Ok(join_all(futs).await)
}

fn shape_listing(l: &Value) -> Option<Value> {
    let order_hash = l.get("order_hash").and_then(|v| v.as_str())?.to_string();
    let protocol = s(l, "protocol_address");
    let params = l.pointer("/protocol_data/parameters")?;
    let offer0 = params.get("offer").and_then(|o| o.as_array()).and_then(|a| a.first())?;
    let token = offer0.get("token").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let token_id = offer0.get("identifierOrCriteria").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let cur = l.pointer("/price/current")?;
    let value = cur.get("value").and_then(|v| v.as_str()).unwrap_or("0").to_string();
    let decimals = cur.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18);
    let currency = cur.get("currency").and_then(|v| v.as_str()).unwrap_or("ETH").to_string();
    let price_eth = value.parse::<f64>().unwrap_or(0.0) / 10f64.powi(decimals as i32);
    Some(json!({
        "orderHash": order_hash,
        "protocolAddress": if protocol.is_empty() { SEAPORT.to_string() } else { protocol },
        "token": token,
        "tokenId": token_id,
        "priceWei": value,
        "price": price_eth,
        "currency": currency,
        "chain": CHAIN_SLUG,
    }))
}

async fn enrich_listing(http: &reqwest::Client, mut row: Value) -> Value {
    let token = row.get("token").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let id = row.get("tokenId").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if token.is_empty() || id.is_empty() { return row; }
    if let Ok(v) = os_get(http, &format!("/chain/{CHAIN_SLUG}/contract/{token}/nfts/{id}")).await {
        let nft = v.get("nft").unwrap_or(&Value::Null);
        let name = nft.get("name").and_then(|x| x.as_str()).filter(|s| !s.is_empty())
            .map(|s| s.to_string()).unwrap_or_else(|| format!("#{id}"));
        let image = nft.get("display_image_url").and_then(|x| x.as_str())
            .or_else(|| nft.get("image_url").and_then(|x| x.as_str()))
            .map(|s| s.to_string());
        if let Some(o) = row.as_object_mut() {
            o.insert("name".into(), Value::from(name));
            o.insert("image".into(), image.map(Value::from).unwrap_or(Value::Null));
            o.insert("collection".into(), nft.get("collection").cloned().unwrap_or(Value::Null));
        }
    }
    row
}

pub async fn build_collections(http: &reqwest::Client, params: &OpenseaCollectionsParams) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(24).clamp(1, 60);
    let search = params.query.as_deref().map(str::trim).filter(|s| !s.is_empty());
    let rows = fetch_collections(http, limit, search).await.unwrap_or_default();
    let description = match search {
        Some(q) => format!("{} OpenSea collections match “{q}” on Robinhood Chain.", rows.len()),
        None => format!("OpenSea collections — {} on Robinhood Chain.", rows.len()),
    };
    Ok(read_envelope("opensea_collections", description, json!({ "collections": rows })))
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct OpenseaListingsParams {
    #[serde(alias = "collection", alias = "collectionSlug")]
    pub slug: String,
    #[serde(default)]
    pub limit: Option<usize>,
}

pub async fn build_listings(http: &reqwest::Client, params: &OpenseaListingsParams) -> Result<BuildResponse, AppError> {
    let slug = params.slug.trim();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let limit = params.limit.unwrap_or(24).clamp(1, 40);
    let rows = fetch_listings(http, slug, limit).await.unwrap_or_default();
    let description = if rows.is_empty() {
        format!("No active OpenSea listings in {slug} on Robinhood Chain.")
    } else {
        format!("{} OpenSea listing(s) in {slug} on Robinhood Chain.", rows.len())
    };
    Ok(read_envelope("opensea_listings", description, json!({ "slug": slug, "listings": rows })))
}

fn read_envelope(action_type: &str, description: String, data: Value) -> BuildResponse {
    let mut d = data;
    if let Some(o) = d.as_object_mut() {
        o.insert("chain".into(), Value::from(CHAIN_SLUG));
        o.insert("chainName".into(), Value::from("Robinhood"));
    }
    BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: action_type.to_string(),
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
        data: Some(d),
    }
}

// ── Buy (fulfill a listing) ──────────────────────────────────────────────────

/// Build a non-custodial BUY. Body: {orderHash, protocolAddress?, walletAddress}.
/// Fetches OpenSea's fulfillment data, then ABI-encodes the Seaport
/// `fulfillBasicOrder_efficient_6GL6yc` call (selector 0x00000000) + the referral
/// calldata_suffix into an unsigned tx. Verified byte-for-byte vs `cast`.
pub async fn build_buy(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let key = api_key()?;
    let order_hash = body.get("orderHash").and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x"))
        .ok_or_else(|| AppError::InvalidParams("opensea: orderHash required".into()))?;
    let wallet = body.get("walletAddress").or_else(|| body.get("fulfiller")).and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
        .ok_or_else(|| AppError::InvalidParams("opensea: walletAddress required".into()))?;
    let protocol = body.get("protocolAddress").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x")).unwrap_or(SEAPORT);

    let req = json!({
        "listing": { "hash": order_hash, "chain": CHAIN_SLUG, "protocol_address": protocol },
        "fulfiller": { "address": wallet },
    });
    let resp = http
        .post(format!("{OPENSEA_API}/listings/fulfillment_data"))
        .header("x-api-key", key)
        .header("content-type", "application/json")
        .json(&req)
        .timeout(std::time::Duration::from_secs(20))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("OpenSea fulfillment failed: {e}")))?;
    let status = resp.status();
    let v: Value = resp.json().await.map_err(|e| AppError::Internal(format!("OpenSea fulfillment bad JSON: {e}")))?;
    if !status.is_success() {
        let msg = v.pointer("/error/message").and_then(|m| m.as_str()).unwrap_or("could not prepare this purchase");
        return Err(AppError::InvalidParams(format!("OpenSea: {msg}")));
    }
    let tx = v.pointer("/fulfillment_data/transaction")
        .ok_or_else(|| AppError::Internal("OpenSea returned no fulfillment transaction".into()))?;
    let to = tx.get("to").and_then(|v| v.as_str()).unwrap_or(SEAPORT).to_string();
    let value = tx.get("value").and_then(|v| v.as_str().map(|s| s.to_string()).or_else(|| v.as_u64().map(|n| n.to_string())))
        .unwrap_or_else(|| "0".to_string());
    let function = tx.get("function").and_then(|v| v.as_str()).unwrap_or("");
    let suffix = tx.get("calldata_suffix").and_then(|v| v.as_str()).unwrap_or("").trim_start_matches("0x").to_string();

    let calldata = if function.starts_with("fulfillBasicOrder_efficient_6GL6yc") {
        let p = tx.pointer("/input_data/parameters")
            .ok_or_else(|| AppError::Internal("OpenSea fulfillment missing parameters".into()))?;
        encode_fulfill_basic_order(p, &suffix)?
    } else {
        // Non-basic orders (auctions / bundles / criteria) need a different Seaport
        // call we don't encode yet — most fixed-price single-NFT buys are basic.
        return Err(AppError::InvalidParams(
            "opensea: this listing type isn't buyable in-app yet — open it on OpenSea.".into(),
        ));
    };

    Ok(json!({
        "transactions": [{ "to": to, "data": calldata, "value": value, "chainId": CHAIN }],
        "chainId": CHAIN,
    }))
}

/// ABI-encode `fulfillBasicOrder_efficient_6GL6yc(BasicOrderParameters)` (selector
/// 0x00000000) + append the referral suffix. Matches `cast` byte-for-byte.
fn encode_fulfill_basic_order(p: &Value, suffix: &str) -> Result<String, AppError> {
    let ar = p.get("additionalRecipients").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let n = ar.len();

    let mut head = String::new();
    head.push_str(&w_addr(&s(p, "considerationToken")));
    head.push_str(&dec_to_word(&s(p, "considerationIdentifier")));
    head.push_str(&dec_to_word(&s(p, "considerationAmount")));
    head.push_str(&w_addr(&s(p, "offerer")));
    head.push_str(&w_addr(&s(p, "zone")));
    head.push_str(&w_addr(&s(p, "offerToken")));
    head.push_str(&dec_to_word(&s(p, "offerIdentifier")));
    head.push_str(&dec_to_word(&s(p, "offerAmount")));
    // basicOrderType may arrive as a number or string.
    let bot = p.get("basicOrderType").map(|v| v.as_u64().map(|n| n.to_string()).unwrap_or_else(|| v.as_str().unwrap_or("0").to_string())).unwrap_or_else(|| "0".into());
    head.push_str(&dec_to_word(&bot));
    head.push_str(&dec_to_word(&s(p, "startTime")));
    head.push_str(&dec_to_word(&s(p, "endTime")));
    head.push_str(&b32(&s(p, "zoneHash")));
    head.push_str(&dec_to_word(&s(p, "salt")));
    head.push_str(&b32(&s(p, "offererConduitKey")));
    head.push_str(&b32(&s(p, "fulfillerConduitKey")));
    head.push_str(&dec_to_word(&s(p, "totalOriginalAdditionalRecipients")));
    // dynamic offsets (relative to tuple start): 18 head words.
    let ar_off = 18 * 32;
    head.push_str(&w_u128(ar_off as u128));
    let sig_off = ar_off + (1 + n * 2) * 32;
    head.push_str(&w_u128(sig_off as u128));

    let mut tail = String::new();
    tail.push_str(&w_u128(n as u128));
    for a in &ar {
        tail.push_str(&dec_to_word(&a.get("amount").and_then(|v| v.as_str()).unwrap_or("0").to_string()));
        tail.push_str(&w_addr(a.get("recipient").and_then(|v| v.as_str()).unwrap_or("0x0")));
    }
    // signature (dynamic bytes)
    let sig = s(p, "signature");
    let sig = sig.trim_start_matches("0x");
    let sig_len = sig.len() / 2;
    let mut sig_data = sig.to_lowercase();
    while sig_data.len() % 64 != 0 { sig_data.push('0'); }
    tail.push_str(&w_u128(sig_len as u128));
    tail.push_str(&sig_data);

    // args = offset(0x20) + tuple(head+tail); calldata = selector + args + suffix.
    Ok(format!("0x00000000{}{head}{tail}{suffix}", w_u128(0x20)))
}

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
use crate::services::sushi::SUSHI_WETH; // WETH on Robinhood — the offer currency
use uuid::Uuid;

const OPENSEA_API: &str = "https://api.opensea.io/api/v2";
const CHAIN: u64 = 4663;
const CHAIN_SLUG: &str = "robinhood";
const ZERO: &str = "0x0000000000000000000000000000000000000000";
/// Seaport 1.6 — canonical address, verified deployed on 4663.
pub const SEAPORT: &str = "0x0000000000000068F116a894984e2DB1123eB395";

/// Robinhood Chain public RPC (for the Seaport getCounter read). Overridable.
fn rpc() -> String {
    std::env::var("ROBINHOOD_RPC").ok().filter(|s| !s.is_empty())
        .unwrap_or_else(|| "https://rpc.mainnet.chain.robinhood.com".to_string())
}

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

/// ERC-20 `approve(spender, amount)` calldata: selector + 32-byte spender +
/// 32-byte amount. The amount is an EXACT value (e.g. the bid), never an
/// unbounded `u128::MAX` allowance.
fn erc20_approve_calldata(spender: &str, amount_wei: u128) -> String {
    format!("095ea7b3{}{}", w_addr(spender), format!("{amount_wei:064x}"))
}
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
    enrich_collection_stats(http, &mut rows).await;
    Ok(rows)
}

/// Fetch live floor / volume / owners for each row (by slug) concurrently and
/// merge them in, so an OpenSea collection card shows the SAME Floor/Vol stats
/// as the Magic Eden cards (one `/stats` call per collection, fanned out).
async fn enrich_collection_stats(http: &reqwest::Client, rows: &mut [Value]) {
    let slugs: Vec<String> = rows
        .iter()
        .filter_map(|r| r.get("slug").and_then(|v| v.as_str()).map(String::from))
        .collect();
    let futs = slugs.into_iter().map(|sl| {
        let http = http.clone();
        async move {
            let st = os_get(&http, &format!("/collections/{sl}/stats")).await.ok();
            let total = st.as_ref().and_then(|s| s.get("total"));
            (
                sl,
                total.and_then(|t| t.get("floor_price")).cloned().unwrap_or(Value::Null),
                total.and_then(|t| t.get("floor_price_symbol")).cloned().unwrap_or(Value::Null),
                total.and_then(|t| t.get("volume")).cloned().unwrap_or(Value::Null),
                total.and_then(|t| t.get("num_owners")).cloned().unwrap_or(Value::Null),
                total.and_then(|t| t.get("sales")).cloned().unwrap_or(Value::Null),
            )
        }
    });
    let map: std::collections::HashMap<String, (Value, Value, Value, Value, Value)> = join_all(futs)
        .await
        .into_iter()
        .map(|(sl, f, fs, v, o, sa)| (sl, (f, fs, v, o, sa)))
        .collect();
    for r in rows.iter_mut() {
        if let Some(sl) = r.get("slug").and_then(|v| v.as_str()).map(String::from) {
            if let Some((f, fs, v, o, sa)) = map.get(&sl) {
                r["floorPrice"] = f.clone();
                r["floorSymbol"] = fs.clone();
                r["volume"] = v.clone();
                r["numOwners"] = o.clone();
                r["sales"] = sa.clone();
            }
        }
    }
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

/// Trending collections — ordered by 7-day volume.
pub async fn build_trending(http: &reqwest::Client, params: &OpenseaCollectionsParams) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(24).clamp(1, 60);
    let body = os_get(http, &format!("/collections?chain={CHAIN_SLUG}&order_by=seven_day_volume&limit=100")).await?;
    let items = body.get("collections").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let mut rows: Vec<Value> = items.iter().filter_map(|c| {
        let slug = s(c, "collection");
        if slug.is_empty() { return None; }
        let name = { let n = s(c, "name"); if n.is_empty() { slug.clone() } else { n } };
        Some(json!({
            "slug": slug, "name": name,
            "image": c.get("image_url").cloned().unwrap_or(Value::Null),
            "description": c.get("description").cloned().unwrap_or(Value::Null),
            "contract": c.pointer("/contracts/0/address").and_then(|v| v.as_str()).unwrap_or(""),
            "chain": CHAIN_SLUG,
        }))
    }).collect();
    rows.truncate(limit);
    enrich_collection_stats(http, &mut rows).await;
    Ok(read_envelope("opensea_trending", format!("Trending OpenSea collections — {} on Robinhood Chain.", rows.len()), json!({ "collections": rows, "trending": true })))
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct OpenseaCollectionParams {
    #[serde(alias = "collection", alias = "collectionSlug")]
    pub slug: String,
}

/// Collection detail + live stats (floor, volume, owners, supply, fees).
pub async fn build_collection(http: &reqwest::Client, p: &OpenseaCollectionParams) -> Result<BuildResponse, AppError> {
    let slug = p.slug.trim();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let url_detail = format!("/collections/{slug}");
    let url_stats = format!("/collections/{slug}/stats");
    let (detail, stats) = futures::join!(
        os_get(http, &url_detail),
        os_get(http, &url_stats),
    );
    let d = detail?;
    let st = stats.ok();
    let total = st.as_ref().and_then(|s| s.get("total"));
    let intervals = st.as_ref().and_then(|s| s.get("intervals")).cloned().unwrap_or(Value::Null);
    let cname = { let n = s(&d, "name"); if n.is_empty() { slug.to_string() } else { n } };
    let row = json!({
        "slug": slug,
        "name": cname,
        "image": d.get("image_url").cloned().unwrap_or(Value::Null),
        "banner": d.get("banner_image_url").cloned().unwrap_or(Value::Null),
        "description": d.get("description").cloned().unwrap_or(Value::Null),
        "contract": d.pointer("/contracts/0/address").and_then(|v| v.as_str()).unwrap_or(""),
        "totalSupply": d.get("total_supply").cloned().unwrap_or(Value::Null),
        "url": d.get("opensea_url").cloned().unwrap_or(Value::Null),
        "twitter": d.get("twitter_username").cloned().unwrap_or(Value::Null),
        "floorPrice": total.and_then(|t| t.get("floor_price")).cloned().unwrap_or(Value::Null),
        "floorSymbol": total.and_then(|t| t.get("floor_price_symbol")).cloned().unwrap_or(Value::Null),
        "volume": total.and_then(|t| t.get("volume")).cloned().unwrap_or(Value::Null),
        "sales": total.and_then(|t| t.get("sales")).cloned().unwrap_or(Value::Null),
        "numOwners": total.and_then(|t| t.get("num_owners")).cloned().unwrap_or(Value::Null),
        "intervals": intervals,
        "chain": CHAIN_SLUG,
    });
    Ok(read_envelope("opensea_collection", format!("{slug} — OpenSea collection on Robinhood Chain."), json!({ "collection": row })))
}

/// NFTs in a collection (browse — includes traits/image).
pub async fn build_nfts(http: &reqwest::Client, p: &OpenseaListingsParams) -> Result<BuildResponse, AppError> {
    let slug = p.slug.trim();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let limit = p.limit.unwrap_or(30).clamp(1, 50);
    let body = os_get(http, &format!("/collection/{slug}/nfts?limit={limit}")).await?;
    let rows: Vec<Value> = body.get("nfts").and_then(|v| v.as_array()).map(|a| a.iter().map(shape_nft).collect()).unwrap_or_default();
    Ok(read_envelope("opensea_nfts", format!("{} NFTs in {slug}.", rows.len()), json!({ "slug": slug, "nfts": rows })))
}

fn shape_nft(n: &Value) -> Value {
    json!({
        "identifier": n.get("identifier").cloned().unwrap_or(Value::Null),
        "name": n.get("name").cloned().unwrap_or(Value::Null),
        "image": n.get("display_image_url").and_then(|v| v.as_str()).filter(|s| !s.is_empty())
            .or_else(|| n.get("image_url").and_then(|v| v.as_str())).map(Value::from).unwrap_or(Value::Null),
        "contract": n.get("contract").cloned().unwrap_or(Value::Null),
        "collection": n.get("collection").cloned().unwrap_or(Value::Null),
        "traits": n.get("traits").cloned().unwrap_or(Value::Null),
    })
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct OpenseaNftParams {
    #[serde(alias = "contract", alias = "address")]
    pub token: String,
    #[serde(alias = "tokenId", alias = "identifier")]
    pub token_id: String,
}

/// One NFT's detail + its best listing + best offer.
pub async fn build_nft(http: &reqwest::Client, p: &OpenseaNftParams) -> Result<BuildResponse, AppError> {
    let token = p.token.trim();
    let id = p.token_id.trim();
    if !token.starts_with("0x") || id.is_empty() { return Err(AppError::InvalidParams("opensea: contract + tokenId required".into())); }
    let nft = os_get(http, &format!("/chain/{CHAIN_SLUG}/contract/{token}/nfts/{id}")).await?;
    let mut row = shape_nft(nft.get("nft").unwrap_or(&Value::Null));
    if let Some(o) = row.as_object_mut() {
        o.insert("description".into(), nft.pointer("/nft/description").cloned().unwrap_or(Value::Null));
        o.insert("owners".into(), nft.pointer("/nft/owners").cloned().unwrap_or(Value::Null));
    }
    Ok(read_envelope("opensea_nft", format!("NFT #{id}."), json!({ "nft": row })))
}

/// Collection offers (bids), highest first.
pub async fn build_offers(http: &reqwest::Client, p: &OpenseaListingsParams) -> Result<BuildResponse, AppError> {
    let slug = p.slug.trim();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let body = os_get(http, &format!("/offers/collection/{slug}")).await?;
    let rows: Vec<Value> = body.get("offers").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(shape_offer).collect()).unwrap_or_default();
    Ok(read_envelope("opensea_offers", format!("{} offer(s) on {slug}.", rows.len()), json!({ "slug": slug, "offers": rows })))
}

fn shape_offer(o: &Value) -> Option<Value> {
    let hash = o.get("order_hash").and_then(|v| v.as_str())?.to_string();
    let cur = o.get("price").and_then(|p| p.get("value")).and_then(|v| v.as_str()).unwrap_or("0");
    let dec = o.pointer("/price/decimals").and_then(|v| v.as_u64()).unwrap_or(18);
    let sym = o.pointer("/price/currency").and_then(|v| v.as_str()).unwrap_or("WETH");
    Some(json!({
        "orderHash": hash,
        "protocolAddress": o.get("protocol_address").cloned().unwrap_or(Value::from(SEAPORT)),
        "price": cur.parse::<f64>().unwrap_or(0.0) / 10f64.powi(dec as i32),
        "currency": sym,
        "quantity": o.get("remaining_quantity").cloned().unwrap_or(Value::Null),
    }))
}

/// Recent collection activity (sales).
pub async fn build_events(http: &reqwest::Client, p: &OpenseaListingsParams) -> Result<BuildResponse, AppError> {
    let slug = p.slug.trim();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let limit = p.limit.unwrap_or(20).clamp(1, 40);
    let body = os_get(http, &format!("/events/collection/{slug}?event_type=sale&limit={limit}")).await?;
    let rows: Vec<Value> = body.get("asset_events").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(shape_event).collect()).unwrap_or_default();
    Ok(read_envelope("opensea_activity", format!("{} recent sale(s) in {slug}.", rows.len()), json!({ "slug": slug, "events": rows })))
}

fn shape_event(e: &Value) -> Option<Value> {
    let pay = e.get("payment");
    let qty = pay.and_then(|p| p.get("quantity")).and_then(|v| v.as_str()).unwrap_or("0");
    let dec = pay.and_then(|p| p.get("decimals")).and_then(|v| v.as_u64()).unwrap_or(18);
    let sym = pay.and_then(|p| p.get("symbol")).and_then(|v| v.as_str()).unwrap_or("ETH");
    let nft = e.get("nft");
    Some(json!({
        "type": e.get("event_type").cloned().unwrap_or(Value::Null),
        "timestamp": e.get("event_timestamp").cloned().unwrap_or(Value::Null),
        "price": qty.parse::<f64>().unwrap_or(0.0) / 10f64.powi(dec as i32),
        "currency": sym,
        "nftName": nft.and_then(|n| n.get("name")).cloned().unwrap_or(Value::Null),
        "nftImage": nft.and_then(|n| n.get("display_image_url").or_else(|| n.get("image_url"))).cloned().unwrap_or(Value::Null),
        "seller": e.get("seller").cloned().unwrap_or(Value::Null),
        "buyer": e.get("buyer").cloned().unwrap_or(Value::Null),
    }))
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct OpenseaWalletParams {
    #[serde(alias = "address", alias = "wallet")]
    pub wallet: String,
    #[serde(default)]
    pub limit: Option<usize>,
}

/// A wallet's NFTs on Robinhood Chain (to list/sell from).
pub async fn build_wallet_nfts(http: &reqwest::Client, p: &OpenseaWalletParams) -> Result<BuildResponse, AppError> {
    let w = p.wallet.trim().to_lowercase();
    if !(w.starts_with("0x") && w.len() == 42) { return Err(AppError::InvalidParams("opensea: wallet address required".into())); }
    let limit = p.limit.unwrap_or(40).clamp(1, 50);
    let body = os_get(http, &format!("/chain/{CHAIN_SLUG}/account/{w}/nfts?limit={limit}")).await?;
    let rows: Vec<Value> = body.get("nfts").and_then(|v| v.as_array()).map(|a| a.iter().map(shape_nft).collect()).unwrap_or_default();
    Ok(read_envelope("opensea_wallet_nfts", format!("{} NFT(s) held on Robinhood Chain.", rows.len()), json!({ "wallet": w, "nfts": rows })))
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

// ── Accept an offer (seller fulfills a bid) ──────────────────────────────────

/// Build a non-custodial ACCEPT-OFFER. Body: {orderHash, token, tokenId,
/// walletAddress, protocolAddress?}. Fulfils an offer via OpenSea's
/// offers/fulfillment_data → encoded Seaport tx (basic-order case).
pub async fn build_accept_offer(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let key = api_key()?;
    let order_hash = body.get("orderHash").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x"))
        .ok_or_else(|| AppError::InvalidParams("opensea: orderHash required".into()))?;
    let token = body.get("token").or_else(|| body.get("contract")).and_then(|v| v.as_str())
        .ok_or_else(|| AppError::InvalidParams("opensea: NFT contract required".into()))?;
    let token_id = body.get("tokenId").or_else(|| body.get("identifier")).and_then(|v| v.as_str())
        .ok_or_else(|| AppError::InvalidParams("opensea: tokenId required".into()))?;
    let wallet = body.get("walletAddress").and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
        .ok_or_else(|| AppError::InvalidParams("opensea: walletAddress required".into()))?;
    let protocol = body.get("protocolAddress").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x")).unwrap_or(SEAPORT);

    let req = json!({
        "offer": { "hash": order_hash, "chain": CHAIN_SLUG, "protocol_address": protocol },
        "consideration": { "asset_contract_address": token, "token_id": token_id },
        "fulfiller": { "address": wallet },
    });
    let resp = http.post(format!("{OPENSEA_API}/offers/fulfillment_data"))
        .header("x-api-key", key).header("content-type", "application/json").json(&req)
        .timeout(std::time::Duration::from_secs(20)).send().await
        .map_err(|e| AppError::Internal(format!("OpenSea fulfillment failed: {e}")))?;
    let status = resp.status();
    let v: Value = resp.json().await.map_err(|e| AppError::Internal(format!("OpenSea bad JSON: {e}")))?;
    if !status.is_success() {
        let msg = v.pointer("/error/message").and_then(|m| m.as_str())
            .or_else(|| v.get("errors").and_then(|e| e.as_array()).and_then(|a| a.first()).and_then(|m| m.as_str()))
            .unwrap_or("could not accept this offer");
        return Err(AppError::InvalidParams(format!("OpenSea: {msg}")));
    }
    let tx = v.pointer("/fulfillment_data/transaction")
        .ok_or_else(|| AppError::Internal("OpenSea returned no fulfillment transaction".into()))?;
    let to = tx.get("to").and_then(|v| v.as_str()).unwrap_or(SEAPORT).to_string();
    let value = tx.get("value").and_then(|v| v.as_str().map(|s| s.to_string()).or_else(|| v.as_u64().map(|n| n.to_string()))).unwrap_or_else(|| "0".into());
    let function = tx.get("function").and_then(|v| v.as_str()).unwrap_or("");
    let suffix = tx.get("calldata_suffix").and_then(|v| v.as_str()).unwrap_or("").trim_start_matches("0x").to_string();
    let calldata = if function.starts_with("fulfillBasicOrder_efficient_6GL6yc") {
        let pr = tx.pointer("/input_data/parameters").ok_or_else(|| AppError::Internal("missing parameters".into()))?;
        encode_fulfill_basic_order(pr, &suffix)?
    } else {
        return Err(AppError::InvalidParams("opensea: this offer type isn't acceptable in-app yet — use OpenSea.".into()));
    };
    Ok(json!({ "transactions": [{ "to": to, "data": calldata, "value": value, "chainId": CHAIN }], "chainId": CHAIN }))
}

// ── Sell (list) & Make offer — Seaport EIP-712 orders (build → sign → submit) ──

const OPENSEA_CONDUIT_KEY: &str = "0x0000007b02230091a7ed01230072f7006a004d60a8d4e71d599b8104250f0000";
const SEL_GET_COUNTER: &str = "0xf07ec373"; // getCounter(address)

async fn collection_fees(http: &reqwest::Client, slug: &str) -> Vec<(f64, String)> {
    match os_get(http, &format!("/collections/{slug}")).await {
        Ok(d) => d.get("fees").and_then(|f| f.as_array()).map(|a| a.iter().filter_map(|f| {
            let pct = f.get("fee").and_then(|v| v.as_f64())?;
            let rec = f.get("recipient").and_then(|v| v.as_str())?.to_string();
            Some((pct, rec))
        }).collect()).unwrap_or_default(),
        Err(_) => vec![],
    }
}

async fn seaport_counter(http: &reqwest::Client, owner: &str) -> u128 {
    let rpc = rpc();
    let data = format!("{SEL_GET_COUNTER}{}", w_addr(owner));
    match crate::services::uniswap::eth_call(http, &rpc, SEAPORT, &data).await {
        Ok(h) => u128::from_str_radix(h.trim_start_matches("0x").trim_start_matches('0'), 16).unwrap_or(0),
        Err(_) => 0,
    }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(1_790_000_000)
}
fn salt_hex() -> String {
    // A per-order salt namespaced under OpenSea's tag; uuid gives uniqueness.
    let u = Uuid::new_v4();
    format!("0x{}{}", "360c6ebe", hex::encode(u.as_bytes()))
}

/// A Seaport item as the OpenSea `parameters` + EIP-712 message expect it.
fn item(item_type: u8, token: &str, id: &str, start: &str, end: &str, recipient: Option<&str>) -> Value {
    let mut o = json!({
        "itemType": item_type,
        "token": token,
        "identifierOrCriteria": id,
        "startAmount": start,
        "endAmount": end,
    });
    if let Some(r) = recipient { o["recipient"] = Value::from(r); }
    o
}

/// Build a Seaport LISTING (sell) order + its EIP-712 typed data for the wallet
/// to sign. Body: {token/contract, tokenId, priceEth, walletAddress, slug?,
/// durationDays?}. Returns {typedData, parameters, protocolAddress, slug} — the
/// frontend signs typedData, then calls /opensea/order/submit.
pub async fn build_list(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    api_key()?;
    let token = body.get("token").or_else(|| body.get("contract")).and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x")).ok_or_else(|| AppError::InvalidParams("opensea: NFT contract required".into()))?.to_lowercase();
    let token_id = body.get("tokenId").or_else(|| body.get("identifier")).and_then(|v| v.as_str())
        .ok_or_else(|| AppError::InvalidParams("opensea: tokenId required".into()))?.to_string();
    let wallet = body.get("walletAddress").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x") && s.len() == 42)
        .ok_or_else(|| AppError::InvalidParams("opensea: walletAddress required".into()))?.to_lowercase();
    let price_eth = body.get("priceEth").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
        .filter(|p| *p > 0.0).ok_or_else(|| AppError::InvalidParams("opensea: a price is required".into()))?;
    let slug = body.get("slug").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let days = body.get("durationDays").and_then(|v| v.as_u64()).unwrap_or(30).clamp(1, 180);

    let price_wei = (price_eth * 1e18) as u128;
    let fees = if slug.is_empty() { vec![] } else { collection_fees(http, &slug).await };
    // consideration: fee items first summed, seller gets the remainder.
    let mut consideration = vec![];
    let mut fee_total: u128 = 0;
    for (pct, rec) in &fees {
        let amt = ((price_wei as f64) * pct / 100.0) as u128;
        if amt == 0 { continue; }
        fee_total += amt;
        consideration.push((amt, rec.clone()));
    }
    let seller_amt = price_wei.saturating_sub(fee_total);
    // Order: seller proceeds first, then fees (OpenSea's canonical ordering).
    let mut cons_items = vec![item(0, ZERO, "0", &seller_amt.to_string(), &seller_amt.to_string(), Some(&wallet))];
    for (amt, rec) in &consideration {
        cons_items.push(item(0, ZERO, "0", &amt.to_string(), &amt.to_string(), Some(rec)));
    }
    let offer = vec![item(2, &token, &token_id, "1", "1", None)];
    let counter = seaport_counter(http, &wallet).await;
    let start = now_secs();
    let end = start + days * 86400;
    let salt = salt_hex();

    let parameters = json!({
        "offerer": wallet,
        "zone": ZERO,
        "offer": offer,
        "consideration": cons_items,
        "orderType": 0,
        "startTime": start.to_string(),
        "endTime": end.to_string(),
        "zoneHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "salt": salt,
        "conduitKey": OPENSEA_CONDUIT_KEY,
        "totalOriginalConsiderationItems": cons_items.len(),
        "counter": counter.to_string(),
    });
    Ok(order_response(parameters, &slug))
}

/// Build a Seaport OFFER (bid, paid in WETH) + EIP-712 typed data. Body:
/// {token/contract, tokenId, priceEth (WETH), walletAddress, slug?, durationDays?}.
/// Also returns a WETH approve tx to the conduit (the offerer must have WETH).
pub async fn build_make_offer(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    api_key()?;
    let token = body.get("token").or_else(|| body.get("contract")).and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x")).ok_or_else(|| AppError::InvalidParams("opensea: NFT contract required".into()))?.to_lowercase();
    let token_id = body.get("tokenId").or_else(|| body.get("identifier")).and_then(|v| v.as_str())
        .ok_or_else(|| AppError::InvalidParams("opensea: tokenId required".into()))?.to_string();
    let wallet = body.get("walletAddress").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x") && s.len() == 42)
        .ok_or_else(|| AppError::InvalidParams("opensea: walletAddress required".into()))?.to_lowercase();
    let price_eth = body.get("priceEth").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
        .filter(|p| *p > 0.0).ok_or_else(|| AppError::InvalidParams("opensea: an offer amount is required".into()))?;
    let slug = body.get("slug").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let days = body.get("durationDays").and_then(|v| v.as_u64()).unwrap_or(7).clamp(1, 90);

    let price_wei = (price_eth * 1e18) as u128;
    let fees = if slug.is_empty() { vec![] } else { collection_fees(http, &slug).await };
    // offer: WETH from the bidder. consideration: the NFT to the bidder + fees (WETH).
    let offer = vec![item(1, SUSHI_WETH, "0", &price_wei.to_string(), &price_wei.to_string(), None)];
    let mut cons_items = vec![item(2, &token, &token_id, "1", "1", Some(&wallet))];
    for (pct, rec) in &fees {
        let amt = ((price_wei as f64) * pct / 100.0) as u128;
        if amt == 0 { continue; }
        cons_items.push(item(1, SUSHI_WETH, "0", &amt.to_string(), &amt.to_string(), Some(rec)));
    }
    let counter = seaport_counter(http, &wallet).await;
    let start = now_secs();
    let end = start + days * 86400;
    let parameters = json!({
        "offerer": wallet, "zone": ZERO, "offer": offer, "consideration": cons_items,
        "orderType": 0, "startTime": start.to_string(), "endTime": end.to_string(),
        "zoneHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "salt": salt_hex(), "conduitKey": OPENSEA_CONDUIT_KEY,
        "totalOriginalConsiderationItems": cons_items.len(), "counter": counter.to_string(),
    });
    let mut resp = order_response(parameters, &slug);
    // WETH approve to the conduit's Conduit contract is required to pull the bid
    // on acceptance. Approve the canonical OpenSea conduit spender for EXACTLY the
    // bid amount (not an unbounded allowance), so a future conduit compromise
    // can't pull more than this one offer's WETH.
    let approve = erc20_approve_calldata("0x1E0049783F008A0085193E00003D00cd54003c71", price_wei);
    if let Some(o) = resp.as_object_mut() {
        o.insert("wethApprove".into(), json!({ "to": SUSHI_WETH, "data": format!("0x{approve}"), "value": "0", "chainId": CHAIN }));
        o.insert("offerCurrency".into(), Value::from(SUSHI_WETH));
    }
    Ok(resp)
}

/// Wrap Seaport order `parameters` with its EIP-712 typed data for signing.
fn order_response(parameters: Value, slug: &str) -> Value {
    let types = json!({
        "EIP712Domain": [
            {"name":"name","type":"string"},{"name":"version","type":"string"},
            {"name":"chainId","type":"uint256"},{"name":"verifyingContract","type":"address"}
        ],
        "OrderComponents": [
            {"name":"offerer","type":"address"},{"name":"zone","type":"address"},
            {"name":"offer","type":"OfferItem[]"},{"name":"consideration","type":"ConsiderationItem[]"},
            {"name":"orderType","type":"uint8"},{"name":"startTime","type":"uint256"},
            {"name":"endTime","type":"uint256"},{"name":"zoneHash","type":"bytes32"},
            {"name":"salt","type":"uint256"},{"name":"conduitKey","type":"bytes32"},
            {"name":"counter","type":"uint256"}
        ],
        "OfferItem": [
            {"name":"itemType","type":"uint8"},{"name":"token","type":"address"},
            {"name":"identifierOrCriteria","type":"uint256"},{"name":"startAmount","type":"uint256"},
            {"name":"endAmount","type":"uint256"}
        ],
        "ConsiderationItem": [
            {"name":"itemType","type":"uint8"},{"name":"token","type":"address"},
            {"name":"identifierOrCriteria","type":"uint256"},{"name":"startAmount","type":"uint256"},
            {"name":"endAmount","type":"uint256"},{"name":"recipient","type":"address"}
        ]
    });
    let typed_data = json!({
        "types": types,
        "primaryType": "OrderComponents",
        "domain": { "name": "Seaport", "version": "1.6", "chainId": CHAIN, "verifyingContract": SEAPORT },
        "message": parameters,
    });
    json!({ "typedData": typed_data, "parameters": parameters, "protocolAddress": SEAPORT, "slug": slug, "chainId": CHAIN })
}

/// Submit a signed order to OpenSea. Body: {parameters, signature, kind
/// ("listing"|"offer"), protocolAddress?}. Called after the wallet signs.
pub async fn submit_order(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let key = api_key()?;
    let parameters = body.get("parameters").cloned().ok_or_else(|| AppError::InvalidParams("opensea: order parameters required".into()))?;
    let signature = body.get("signature").and_then(|v| v.as_str()).filter(|s| s.starts_with("0x"))
        .ok_or_else(|| AppError::InvalidParams("opensea: signature required".into()))?;
    let kind = body.get("kind").and_then(|v| v.as_str()).unwrap_or("listing");
    let protocol = body.get("protocolAddress").and_then(|v| v.as_str()).unwrap_or(SEAPORT);
    let endpoint = if kind == "offer" {
        format!("/orders/{CHAIN_SLUG}/seaport/offers")
    } else {
        format!("/orders/{CHAIN_SLUG}/seaport/listings")
    };
    let req = json!({ "parameters": parameters, "signature": signature, "protocol_address": protocol });
    let resp = http.post(format!("{OPENSEA_API}{endpoint}"))
        .header("x-api-key", key).header("content-type", "application/json").json(&req)
        .timeout(std::time::Duration::from_secs(20)).send().await
        .map_err(|e| AppError::Internal(format!("OpenSea order submit failed: {e}")))?;
    let status = resp.status();
    let v: Value = resp.json().await.map_err(|e| AppError::Internal(format!("OpenSea bad JSON: {e}")))?;
    if !status.is_success() {
        let msg = v.pointer("/error/message").and_then(|m| m.as_str())
            .or_else(|| v.get("errors").and_then(|e| e.as_array()).and_then(|a| a.first()).and_then(|m| m.as_str()))
            .unwrap_or("order rejected");
        return Err(AppError::InvalidParams(format!("OpenSea: {msg}")));
    }
    let order_hash = v.pointer("/order/order_hash").and_then(|h| h.as_str()).unwrap_or("").to_string();
    Ok(json!({ "ok": true, "orderHash": order_hash, "kind": kind }))
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

#[cfg(test)]
mod approve_bound_tests {
    use super::erc20_approve_calldata;

    const CONDUIT: &str = "0x1E0049783F008A0085193E00003D00cd54003c71";

    #[test]
    fn approve_encodes_exact_bid_not_unbounded() {
        let bid: u128 = 1_500_000_000_000_000_000; // 1.5 WETH
        let cd = erc20_approve_calldata(CONDUIT, bid);
        assert!(cd.starts_with("095ea7b3"), "ERC-20 approve selector");
        assert_eq!(cd.len(), 8 + 64 + 64, "selector + spender word + amount word");
        // amount is the trailing 32-byte word
        let amount_word = &cd[cd.len() - 64..];
        assert_eq!(
            u128::from_str_radix(amount_word.trim_start_matches('0'), 16).unwrap(),
            bid,
            "approved amount must equal the bid"
        );
        // and NOT the old unbounded u128::MAX (…ffff… low 128 bits)
        let unbounded = format!("{:064x}", u128::MAX);
        assert_ne!(amount_word, unbounded, "must not be an unbounded allowance");
    }

    #[test]
    fn spender_is_the_conduit() {
        let cd = erc20_approve_calldata(CONDUIT, 1);
        // spender word = first 32 bytes after the selector, right-aligned address
        let spender_word = &cd[8..8 + 64];
        assert!(spender_word.ends_with(&CONDUIT.trim_start_matches("0x").to_lowercase()));
    }
}

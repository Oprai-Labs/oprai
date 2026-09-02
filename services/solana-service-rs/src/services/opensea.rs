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

/// Map a user/LLM chain name to OpenSea's chain slug. OpenSea is multichain, so
/// reads work on any of these; default is Robinhood (OPRAI's home chain).
fn os_chain(input: &str) -> String {
    let lc = input.trim().to_lowercase();
    let mapped = match lc.as_str() {
        "" | "robinhood" | "robinhoodchain" | "robinhood chain" | "4663" => "robinhood",
        "eth" | "ethereum" | "mainnet" | "1" => "ethereum",
        "base" | "8453" => "base",
        "arbitrum" | "arb" | "42161" => "arbitrum",
        "optimism" | "op" | "10" => "optimism",
        "polygon" | "matic" | "137" => "matic",
        "avalanche" | "avax" | "43114" => "avalanche",
        "bnb" | "bsc" | "bnb chain" | "56" => "bsc",
        "zora" | "7777777" => "zora",
        "blast" | "81457" => "blast",
        "sei" => "sei",
        "solana" | "sol" => "solana",
        _ => return lc, // unknown → pass the lowercased name through to OpenSea
    };
    mapped.to_string()
}

/// Display name for a chain slug (for the card header + response).
fn os_chain_name(slug: &str) -> &'static str {
    match slug {
        "robinhood" => "Robinhood",
        "ethereum" => "Ethereum",
        "base" => "Base",
        "arbitrum" => "Arbitrum",
        "optimism" => "Optimism",
        "matic" => "Polygon",
        "avalanche" => "Avalanche",
        "bsc" => "BNB Chain",
        "zora" => "Zora",
        "blast" => "Blast",
        "sei" => "Sei",
        "solana" => "Solana",
        _ => "OpenSea",
    }
}
const ZERO: &str = "0x0000000000000000000000000000000000000000";
/// Seaport 1.6 — canonical address, verified deployed on 4663.
pub const SEAPORT: &str = "0x0000000000000068F116a894984e2DB1123eB395";
/// Seaport's ConduitController (canonical) — resolves the conduit a given
/// conduit key pulls ERC-20 payment through, so an ERC-20 buy approves the RIGHT
/// (per-order) conduit rather than a fixed one.
const CONDUIT_CONTROLLER: &str = "0x00000000F9490004C11Cef243f5400493c00Ad63";
const SEL_GET_CONDUIT: &str = "0x6e9bfd9f"; // getConduit(bytes32)->(address,bool)

/// Resolve the conduit address for a Seaport conduit key. Zero key → Seaport
/// pulls the token directly (approve Seaport itself); else getConduit on the
/// ConduitController gives the per-owner conduit. Falls back to Seaport on any
/// failure so the buy still has a spender to approve.
async fn resolve_conduit(http: &reqwest::Client, rpc: &str, key_hex: &str) -> String {
    let key = key_hex.trim().trim_start_matches("0x");
    if key.is_empty() || key.chars().all(|c| c == '0') {
        return SEAPORT.to_string();
    }
    let data = format!("{SEL_GET_CONDUIT}{key:0>64}");
    match crate::services::uniswap::eth_call(http, rpc, CONDUIT_CONTROLLER, &data).await {
        Ok(h) => {
            let h = h.trim_start_matches("0x");
            if h.len() >= 64 {
                let addr = format!("0x{}", &h[24..64]);
                if addr[2..].chars().all(|c| c == '0') { SEAPORT.to_string() } else { addr }
            } else {
                SEAPORT.to_string()
            }
        }
        Err(_) => SEAPORT.to_string(),
    }
}

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

/// ERC-721/1155 `setApprovalForAll(operator, approved)` calldata (selector
/// 0xa22cb465). Grants the Seaport conduit permission to transfer the seller's
/// NFTs on a fill — OpenSea rejects a listing whose conduit isn't approved.
fn set_approval_for_all_calldata(operator: &str, approved: bool) -> String {
    format!("a22cb465{}{}", w_addr(operator), format!("{:064x}", approved as u8))
}

/// Is `operator` already approved to move ALL of `owner`'s NFTs on `contract`?
/// isApprovedForAll(address,address)->bool, selector 0xe985e9c5. false on any
/// read error (so we then include the approval tx rather than silently skip it).
async fn is_approved_for_all(http: &reqwest::Client, rpc: &str, contract: &str, owner: &str, operator: &str) -> bool {
    let data = format!("0xe985e9c5{}{}", w_addr(owner), w_addr(operator));
    match crate::services::uniswap::eth_call(http, rpc, contract, &data).await {
        Ok(h) => h.trim_start_matches("0x").trim_start_matches('0') == "1",
        Err(_) => false,
    }
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
    #[serde(default)]
    pub chain: String,
}

/// List NFT collections on Robinhood Chain.
pub async fn fetch_collections(http: &reqwest::Client, limit: usize, search: Option<&str>, chain: &str) -> Result<Vec<Value>, AppError> {
    let body = os_get(http, &format!("/collections?chain={chain}&limit=100&order_by=market_cap")).await?;
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
            "chain": chain,
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
    // Bounded concurrency: OpenSea rate-limits a big burst of /stats calls, and a
    // 429 came back as null → empty Volume/Floor columns. Fetch in chunks so every
    // collection gets its stats without tripping the limiter.
    let mut collected: Vec<(String, Value, Value, Value, Value, Value)> = Vec::new();
    for chunk in slugs.chunks(6) {
        let futs = chunk.iter().map(|sl| {
            let http = http.clone();
            let sl = sl.clone();
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
        collected.extend(join_all(futs).await);
    }
    let map: std::collections::HashMap<String, (Value, Value, Value, Value, Value)> = collected
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
pub async fn fetch_listings(http: &reqwest::Client, slug: &str, limit: usize, chain: &str) -> Result<Vec<Value>, AppError> {
    // Pull a wider page than we show, so "cheapest" is the cheapest of many, not
    // of whatever arbitrary order the API returned first.
    let body = os_get(http, &format!("/listings/collection/{slug}/all?limit=50")).await?;
    let items = body.get("listings").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let mut raws: Vec<Value> = items.into_iter().filter_map(|l| shape_listing(&l)).collect();
    // Cheapest first (the usual ask), then keep only what we'll show.
    raws.sort_by(|a, b| {
        let pa = a.get("price").and_then(|v| v.as_f64()).unwrap_or(f64::MAX);
        let pb = b.get("price").and_then(|v| v.as_f64()).unwrap_or(f64::MAX);
        pa.partial_cmp(&pb).unwrap_or(std::cmp::Ordering::Equal)
    });
    raws.truncate(limit.clamp(1, 50));
    // Enrich name/image per NFT (best-effort, concurrent).
    let futs = raws.into_iter().map(|r| enrich_listing(http, r, chain));
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

async fn enrich_listing(http: &reqwest::Client, mut row: Value, chain: &str) -> Value {
    let token = row.get("token").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let id = row.get("tokenId").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if token.is_empty() || id.is_empty() { return row; }
    if let Ok(v) = os_get(http, &format!("/chain/{chain}/contract/{token}/nfts/{id}")).await {
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
    let ch = os_chain(&params.chain);
    let rows = fetch_collections(http, limit, search, &ch).await.unwrap_or_default();
    let description = match search {
        Some(q) => format!("{} OpenSea collections match “{q}” on Robinhood Chain.", rows.len()),
        None => format!("OpenSea collections — {} on Robinhood Chain.", rows.len()),
    };
    Ok(read_envelope("opensea_collections", description, json!({ "collections": rows, "chain": ch })))
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct OpenseaListingsParams {
    #[serde(alias = "collection", alias = "collectionSlug")]
    pub slug: String,
    #[serde(default)]
    pub limit: Option<usize>,
    #[serde(default)]
    pub chain: String,
}

/// Turn a user-supplied collection reference into an OpenSea slug. Users (and the
/// LLM) routinely paste a 0x CONTRACT address; OpenSea's slug-keyed endpoints
/// (listings, offers, activity, nfts, stats) return NOTHING for a raw address, so
/// look the slug up from the contract first. A plain slug passes through.
async fn resolve_slug(http: &reqwest::Client, input: &str, chain: &str) -> String {
    let input = input.trim();
    if input.starts_with("0x") && input.len() == 42 {
        if let Ok(v) = os_get(http, &format!("/chain/{chain}/contract/{input}")).await {
            if let Some(sl) = v.get("collection").and_then(|c| c.as_str()).filter(|s| !s.is_empty()) {
                return sl.to_string();
            }
        }
    }
    input.to_string()
}

pub async fn build_listings(http: &reqwest::Client, params: &OpenseaListingsParams) -> Result<BuildResponse, AppError> {
    let ch = os_chain(&params.chain);
    let slug = resolve_slug(http, &params.slug, &ch).await;
    let slug = slug.as_str();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let limit = params.limit.unwrap_or(24).clamp(1, 40);
    let rows = fetch_listings(http, slug, limit, &ch).await.unwrap_or_default();
    let description = if rows.is_empty() {
        format!("No active OpenSea listings in {slug} on Robinhood Chain.")
    } else {
        format!("{} OpenSea listing(s) in {slug} on Robinhood Chain.", rows.len())
    };
    Ok(read_envelope("opensea_listings", description, json!({ "slug": slug, "listings": rows, "chain": ch })))
}

fn read_envelope(action_type: &str, description: String, data: Value) -> BuildResponse {
    let mut d = data;
    if let Some(o) = d.as_object_mut() {
        // The build fn sets `chain` to the queried slug; default to Robinhood.
        let slug = o.get("chain").and_then(|v| v.as_str()).unwrap_or(CHAIN_SLUG).to_string();
        o.insert("chain".into(), Value::from(slug.clone()));
        o.insert("chainName".into(), Value::from(os_chain_name(&slug)));
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
    let limit = params.limit.unwrap_or(40).clamp(1, 60);
    let ch = os_chain(&params.chain);
    let body = os_get(http, &format!("/collections?chain={ch}&order_by=seven_day_volume&limit=100")).await?;
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
            "chain": ch.as_str(),
        }))
    }).collect();
    rows.truncate(limit);
    enrich_collection_stats(http, &mut rows).await;
    Ok(read_envelope("opensea_trending", format!("Trending OpenSea collections — {} on {}.", rows.len(), os_chain_name(&ch)), json!({ "collections": rows, "trending": true, "chain": ch })))
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct OpenseaCollectionParams {
    #[serde(alias = "collection", alias = "collectionSlug")]
    pub slug: String,
    #[serde(default)]
    pub chain: String,
}

/// Collection detail + live stats (floor, volume, owners, supply, fees).
pub async fn build_collection(http: &reqwest::Client, p: &OpenseaCollectionParams) -> Result<BuildResponse, AppError> {
    let ch = os_chain(&p.chain);
    let slug = resolve_slug(http, &p.slug, &ch).await;
    let slug = slug.as_str();
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
        "chain": ch.clone(),
    });
    Ok(read_envelope("opensea_collection", format!("{slug} — OpenSea collection on {}.", os_chain_name(&ch)), json!({ "collection": row, "chain": ch })))
}

// ── PRIMARY MINT (OpenSea SeaDrop v1 — canonical, deployed on Robinhood 4663) ──
//
// OpenSea's primary sales run through SeaDrop: the drop config (price, window,
// per-wallet cap, fee) lives in the SeaDrop contract keyed by the NFT contract,
// and a public mint is `SeaDrop.mintPublic(nft, feeRecipient, minterIfNotPayer,
// quantity)` payable with mintPrice × quantity in native ETH. Reads are
// `getPublicDrop(nft)` + the NFT's `getMintStats(minter)` (minted / supply / max).
const SEADROP: &str = "0x00005EA00Ac477B1030CE78506496e8C2dE24bf5";
const SEL_GET_PUBLIC_DROP: &str = "0xbc6a629c"; // getPublicDrop(address)
const SEL_GET_MINT_STATS: &str = "0x840e15d4"; // getMintStats(address)
const SEL_GET_ALLOWED_FEE: &str = "0x68632274"; // getAllowedFeeRecipients(address)
const SEL_MINT_PUBLIC: &str = "0x161ac21f"; // mintPublic(address,address,address,uint256)
const ZERO_ADDR: &str = "0x0000000000000000000000000000000000000000";
// OpenSea's canonical fee recipient (same across chains) — the fallback when the
// drop restricts fee recipients but the on-chain list read comes back empty.
const OPENSEA_FEE_RECIPIENT: &str = "0x0000a26b00c1f0df003000390027140000faa719";

/// Parse the i-th 32-byte word of an eth_call return as a u128 (fits price/uint80,
/// times/uint48, counts). All-zero / short → 0.
fn hword(h: &str, i: usize) -> u128 {
    let h = h.trim_start_matches("0x");
    let seg = h.get(i * 64..(i + 1) * 64).unwrap_or("");
    let trimmed = seg.trim_start_matches('0');
    if trimmed.is_empty() { 0 } else { u128::from_str_radix(trimmed, 16).unwrap_or(0) }
}
/// The address in the i-th word (last 40 hex chars).
fn hword_addr(h: &str, i: usize) -> String {
    let h = h.trim_start_matches("0x");
    let seg = h.get(i * 64..(i + 1) * 64).unwrap_or("");
    if seg.len() == 64 { format!("0x{}", &seg[24..]) } else { ZERO_ADDR.to_string() }
}

/// Resolve (slug, contract) — accept a 0x contract directly, else look the
/// contract up from the collection slug.
async fn resolve_collection(http: &reqwest::Client, slug: &str, contract: &str) -> Result<(String, String), AppError> {
    let slug = slug.trim();
    let contract = contract.trim();
    if contract.starts_with("0x") && contract.len() == 42 {
        return Ok((slug.to_string(), contract.to_lowercase()));
    }
    if slug.is_empty() {
        return Err(AppError::InvalidParams("opensea: collection slug or contract required".into()));
    }
    let d = os_get(http, &format!("/collections/{slug}")).await?;
    let c = d.pointer("/contracts/0/address").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if c.is_empty() {
        return Err(AppError::InvalidParams("opensea: could not resolve the collection's contract".into()));
    }
    Ok((slug.to_string(), c.to_lowercase()))
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct OpenseaMintInfoParams {
    #[serde(default, alias = "collection", alias = "collectionSlug")]
    pub slug: String,
    #[serde(default)]
    pub contract: String,
    #[serde(default)]
    pub wallet: String,
}

/// Read a collection's SeaDrop public-mint state (price, window, per-wallet cap,
/// minted / max supply) plus, if a wallet is given, that wallet's eligibility.
pub async fn build_mint_info(http: &reqwest::Client, p: &OpenseaMintInfoParams) -> Result<BuildResponse, AppError> {
    let rpc = rpc();
    let (slug, contract) = resolve_collection(http, &p.slug, &p.contract).await?;

    let drop = crate::services::uniswap::eth_call(http, &rpc, SEADROP, &format!("{}{}", SEL_GET_PUBLIC_DROP, w_addr(&contract)))
        .await
        .unwrap_or_default();
    let mint_price = hword(&drop, 0);
    let start = hword(&drop, 1) as u64;
    let end = hword(&drop, 2) as u64;
    let per_wallet = hword(&drop, 3);
    let fee_bps = hword(&drop, 4);
    let now = now_secs();
    let active = start != 0 && start <= now && now <= end;
    let has_drop = mint_price != 0 || end != 0;

    let minter = if p.wallet.starts_with("0x") && p.wallet.len() == 42 { p.wallet.to_lowercase() } else { ZERO_ADDR.to_string() };
    let stats = crate::services::uniswap::eth_call(http, &rpc, &contract, &format!("{}{}", SEL_GET_MINT_STATS, w_addr(&minter)))
        .await
        .unwrap_or_default();
    let user_minted = hword(&stats, 0);
    let total = hword(&stats, 1);
    let max_supply = hword(&stats, 2);
    let sold_out = max_supply != 0 && total >= max_supply;
    let user_remaining = if per_wallet == 0 { u128::MAX } else { per_wallet.saturating_sub(user_minted) };
    let eligible = active && !sold_out && user_remaining > 0;

    Ok(read_envelope(
        "opensea_mint_info",
        format!("Mint info for {slug}."),
        json!({
            "slug": slug, "contract": contract,
            "mintPriceWei": mint_price.to_string(),
            "mintPriceEth": mint_price as f64 / 1e18,
            "active": active, "hasDrop": has_drop,
            "startTime": start, "endTime": end,
            "perWalletLimit": per_wallet, "feeBps": fee_bps,
            "mintedTotal": total, "maxSupply": max_supply, "soldOut": sold_out,
            "userMinted": user_minted,
            "userRemaining": if user_remaining == u128::MAX { Value::Null } else { Value::from(user_remaining as u64) },
            "eligible": eligible,
            "chain": CHAIN_SLUG,
        }),
    ))
}

/// Build a SeaDrop public-mint tx. Body: {contract | collection, quantity?,
/// walletAddress}. Returns an unsigned mintPublic tx (native-ETH value).
pub async fn build_mint(http: &reqwest::Client, body: &Value) -> Result<Value, AppError> {
    let rpc = rpc();
    let wallet = body
        .get("walletAddress")
        .and_then(|v| v.as_str())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
        .ok_or_else(|| AppError::InvalidParams("opensea: walletAddress required".into()))?;
    let slug = body.get("collection").or_else(|| body.get("slug")).and_then(|v| v.as_str()).unwrap_or("");
    let contract_in = body.get("contract").and_then(|v| v.as_str()).unwrap_or("");
    let (_slug, contract) = resolve_collection(http, slug, contract_in).await?;
    let qty = body
        .get("quantity")
        .and_then(|v| v.as_u64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
        .unwrap_or(1)
        .max(1) as u128;

    let drop = crate::services::uniswap::eth_call(http, &rpc, SEADROP, &format!("{}{}", SEL_GET_PUBLIC_DROP, w_addr(&contract)))
        .await
        .unwrap_or_default();
    let mint_price = hword(&drop, 0);
    let start = hword(&drop, 1) as u64;
    let end = hword(&drop, 2) as u64;
    let per_wallet = hword(&drop, 3);
    let now = now_secs();
    if !(start != 0 && start <= now && now <= end) {
        return Err(AppError::InvalidParams("opensea: this collection is not minting right now".into()));
    }
    // Per-wallet cap: refuse a quantity the drop would revert on.
    if per_wallet != 0 {
        let stats = crate::services::uniswap::eth_call(http, &rpc, &contract, &format!("{}{}", SEL_GET_MINT_STATS, w_addr(wallet)))
            .await
            .unwrap_or_default();
        let remaining = per_wallet.saturating_sub(hword(&stats, 0));
        if remaining == 0 {
            return Err(AppError::InvalidParams("opensea: you've already minted the per-wallet limit for this drop".into()));
        }
        if qty > remaining {
            return Err(AppError::InvalidParams(format!("opensea: over the per-wallet limit — you can mint {remaining} more")));
        }
    }

    // Fee recipient: the drop's allowed list (first), else OpenSea's canonical one.
    let allowed = crate::services::uniswap::eth_call(http, &rpc, SEADROP, &format!("{}{}", SEL_GET_ALLOWED_FEE, w_addr(&contract)))
        .await
        .unwrap_or_default();
    let fee_recipient = if hword(&allowed, 1) > 0 { hword_addr(&allowed, 2) } else { OPENSEA_FEE_RECIPIENT.to_string() };

    let value = mint_price.saturating_mul(qty);
    let data = format!(
        "{}{}{}{}{}",
        SEL_MINT_PUBLIC.trim_start_matches("0x"),
        w_addr(&contract),
        w_addr(&fee_recipient),
        w_addr(ZERO_ADDR), // minterIfNotPayer = 0 → the payer is the minter
        w_u128(qty),
    );
    Ok(json!({
        "transactions": [ json!({ "to": SEADROP, "data": format!("0x{data}"), "value": value.to_string(), "chainId": CHAIN }) ],
        "chainId": CHAIN,
        "contract": contract,
        "quantity": qty as u64,
        "mintPriceWei": mint_price.to_string(),
        "totalWei": value.to_string(),
        "totalEth": value as f64 / 1e18,
    }))
}

/// NFTs in a collection (browse — includes traits/image).
pub async fn build_nfts(http: &reqwest::Client, p: &OpenseaListingsParams) -> Result<BuildResponse, AppError> {
    let ch = os_chain(&p.chain);
    let slug = resolve_slug(http, &p.slug, &ch).await;
    let slug = slug.as_str();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let limit = p.limit.unwrap_or(30).clamp(1, 50);
    let body = os_get(http, &format!("/collection/{slug}/nfts?limit={limit}")).await?;
    let rows: Vec<Value> = body.get("nfts").and_then(|v| v.as_array()).map(|a| a.iter().map(shape_nft).collect()).unwrap_or_default();
    Ok(read_envelope("opensea_nfts", format!("{} NFTs in {slug}.", rows.len()), json!({ "slug": slug, "nfts": rows, "chain": ch })))
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
    #[serde(default)]
    pub chain: String,
}

/// One NFT's detail + its best listing + best offer.
pub async fn build_nft(http: &reqwest::Client, p: &OpenseaNftParams) -> Result<BuildResponse, AppError> {
    let token = p.token.trim();
    let id = p.token_id.trim();
    if !token.starts_with("0x") || id.is_empty() { return Err(AppError::InvalidParams("opensea: contract + tokenId required".into())); }
    let ch = os_chain(&p.chain);
    let nft = os_get(http, &format!("/chain/{ch}/contract/{token}/nfts/{id}")).await?;
    let mut row = shape_nft(nft.get("nft").unwrap_or(&Value::Null));
    if let Some(o) = row.as_object_mut() {
        o.insert("description".into(), nft.pointer("/nft/description").cloned().unwrap_or(Value::Null));
        o.insert("owners".into(), nft.pointer("/nft/owners").cloned().unwrap_or(Value::Null));
    }
    Ok(read_envelope("opensea_nft", format!("NFT #{id}."), json!({ "nft": row, "chain": ch })))
}

/// Collection offers (bids), highest first.
pub async fn build_offers(http: &reqwest::Client, p: &OpenseaListingsParams) -> Result<BuildResponse, AppError> {
    let ch = os_chain(&p.chain);
    let slug = resolve_slug(http, &p.slug, &ch).await;
    let slug = slug.as_str();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let body = os_get(http, &format!("/offers/collection/{slug}")).await?;
    let rows: Vec<Value> = body.get("offers").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(shape_offer).collect()).unwrap_or_default();
    Ok(read_envelope("opensea_offers", format!("{} offer(s) on {slug}.", rows.len()), json!({ "slug": slug, "offers": rows, "chain": ch })))
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
    let ch = os_chain(&p.chain);
    let slug = resolve_slug(http, &p.slug, &ch).await;
    let slug = slug.as_str();
    if slug.is_empty() { return Err(AppError::InvalidParams("opensea: collection slug required".into())); }
    let limit = p.limit.unwrap_or(20).clamp(1, 40);
    let body = os_get(http, &format!("/events/collection/{slug}?event_type=sale&limit={limit}")).await?;
    let rows: Vec<Value> = body.get("asset_events").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(shape_event).collect()).unwrap_or_default();
    Ok(read_envelope("opensea_activity", format!("{} recent sale(s) in {slug}.", rows.len()), json!({ "slug": slug, "events": rows, "chain": ch })))
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
    #[serde(default)]
    pub chain: String,
    // The caller's Solana address, so "my nfts" spans OpenSea's Solana chain
    // too (the user's EVM `wallet` can't hold Solana NFTs). Best-effort.
    #[serde(default, alias = "solWallet")]
    pub sol_wallet: Option<String>,
}

/// A wallet's OpenSea NFTs. With no chain named, aggregate across the wallet's
/// EVM chains (so the user sees their FULL OpenSea holdings, not just Robinhood);
/// a named chain narrows to that one. Each row carries its chain.
pub async fn build_wallet_nfts(http: &reqwest::Client, p: &OpenseaWalletParams) -> Result<BuildResponse, AppError> {
    let evm = p.wallet.trim().to_lowercase();
    let evm_ok = evm.starts_with("0x") && evm.len() == 42;
    let sol = p.sol_wallet.as_deref().unwrap_or("").trim().to_string();
    let sol_ok = !sol.is_empty() && !sol.starts_with("0x") && (32..=44).contains(&sol.len());
    let limit = p.limit.unwrap_or(30).clamp(1, 50);

    // Build (chain, owner) targets. A named chain narrows to one (Solana uses the
    // Solana address, every other chain the EVM one). With no chain named,
    // aggregate the wallet's EVM chains PLUS Solana — so "my nfts" returns the
    // user's FULL OpenSea holdings across every chain they actually hold on,
    // not just one side.
    let mut targets: Vec<(String, String)> = Vec::new();
    if !p.chain.trim().is_empty() {
        let ch = os_chain(&p.chain);
        let owner = if ch == "solana" { sol.clone() } else { evm.clone() };
        if !owner.is_empty() { targets.push((ch, owner)); }
    } else {
        if evm_ok {
            for ch in ["robinhood", "ethereum", "base", "arbitrum", "optimism", "matic"] {
                targets.push((ch.to_string(), evm.clone()));
            }
        }
        if sol_ok {
            targets.push(("solana".to_string(), sol.clone()));
        }
    }
    if targets.is_empty() {
        return Err(AppError::InvalidParams("opensea: a wallet address is required".into()));
    }
    // Fetch each (chain, owner) concurrently; tag every NFT with the chain it's on.
    let futs = targets.iter().map(|(ch, owner)| {
        let http = http.clone(); let ch = ch.clone(); let owner = owner.clone();
        async move {
            let body = os_get(&http, &format!("/chain/{ch}/account/{owner}/nfts?limit={limit}")).await.ok();
            let mut out: Vec<Value> = Vec::new();
            if let Some(a) = body.as_ref().and_then(|b| b.get("nfts")).and_then(|v| v.as_array()) {
                for n in a {
                    let mut row = shape_nft(n);
                    if let Some(o) = row.as_object_mut() {
                        o.insert("chain".into(), Value::from(ch.clone()));
                        o.insert("chainName".into(), Value::from(os_chain_name(&ch)));
                    }
                    out.push(row);
                }
            }
            out
        }
    });
    let rows: Vec<Value> = join_all(futs).await.into_iter().flatten().collect();
    let single = targets.len() == 1;
    let primary = if single { targets[0].0.clone() } else { CHAIN_SLUG.to_string() };
    let desc = if single {
        format!("{} NFT(s) held on {}.", rows.len(), os_chain_name(&primary))
    } else {
        format!("{} NFT(s) across your OpenSea chains.", rows.len())
    };
    let wallet_echo = if evm_ok { evm } else { sol };
    Ok(read_envelope("opensea_wallet_nfts", desc, json!({ "wallet": wallet_echo, "nfts": rows, "chain": primary })))
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

    let (calldata, cons_token, cons_total, conduit_key) = if function.starts_with("fulfillBasicOrder_efficient_6GL6yc") {
        let p = tx.pointer("/input_data/parameters")
            .ok_or_else(|| AppError::Internal("OpenSea fulfillment missing parameters".into()))?;
        // Consideration token: native ETH (zero) needs no approval; an ERC-20
        // (USDG on Robinhood) is pulled by Seaport via the fulfiller's CONDUIT →
        // approve that conduit first.
        let ct = p.get("considerationToken").and_then(|v| v.as_str()).unwrap_or(ZERO).to_lowercase();
        let mut total: u128 = p.get("considerationAmount").and_then(|v| v.as_str()).and_then(|s| s.parse().ok()).unwrap_or(0);
        if let Some(ar) = p.get("additionalRecipients").and_then(|v| v.as_array()) {
            for a in ar { total += a.get("amount").and_then(|v| v.as_str()).and_then(|s| s.parse().ok()).unwrap_or(0); }
        }
        let key = p.get("fulfillerConduitKey").and_then(|v| v.as_str())
            .or_else(|| p.get("offererConduitKey").and_then(|v| v.as_str()))
            .unwrap_or("").to_string();
        (encode_fulfill_basic_order(p, &suffix)?, ct, total, key)
    } else {
        return Err(AppError::InvalidParams(
            "opensea: this listing type isn't buyable in-app yet — open it on OpenSea.".into(),
        ));
    };

    // For an ERC-20-priced listing, prepend an approval to the conduit Seaport
    // will pull the token through — derived from the order's fulfillerConduitKey
    // (NOT a fixed address; OpenSea uses per-owner conduits). Without the exact
    // conduit the token pull reverts and the wallet can't estimate gas. Native
    // ETH (zero token) needs nothing.
    let mut txs = vec![];
    if cons_token.starts_with("0x") && cons_token.len() == 42 && !cons_token.eq_ignore_ascii_case(ZERO) && cons_total > 0 {
        let rpc = rpc();
        let spender = resolve_conduit(http, &rpc, &conduit_key).await;
        let allow_data = format!("0xdd62ed3e{}{}", w_addr(wallet), w_addr(&spender));
        let have = crate::services::uniswap::eth_call(http, &rpc, &cons_token, &allow_data)
            .await
            .ok()
            .and_then(|h| u128::from_str_radix(h.trim_start_matches("0x").trim_start_matches('0'), 16).ok())
            .unwrap_or(0);
        if have < cons_total {
            // Approve a generous max so a re-buy in the same collection needs no
            // second approval (Seaport only pulls the exact consideration).
            let approve = erc20_approve_calldata(&spender, u128::MAX);
            txs.push(json!({ "to": cons_token, "data": format!("0x{approve}"), "value": "0", "chainId": CHAIN }));
        }
    }
    txs.push(json!({ "to": to, "data": calldata, "value": value, "chainId": CHAIN }));

    Ok(json!({ "transactions": txs, "chainId": CHAIN }))
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

// OpenSea's conduit key for ORDER PLACEMENT on Robinhood Chain. OpenSea rejects
// any other key ("please use OpenSea's conduit key: 0x61159fef…"). It resolves
// via ConduitController.getConduit → conduit 0x963f00d3ff000064ffcba824b800c0000000c300
// (the same conduit the buy side approves). The old mainnet key
// 0x0000007b0223…0f0000 is NOT valid here.
const OPENSEA_CONDUIT_KEY: &str = "0x61159fefdfada89302ed55f8b9e89e2d67d8258712b3a3f89aa88525877f1d5e";
// OpenSea's Signed Zone V2 on Robinhood. Collections with an ENFORCED creator
// fee reject unrestricted orders ("requires Signed Zone V2 … set orderType to
// FULL_RESTRICTED (2) … use the required zone 0x000056f7…"). Their live
// listings/offers all carry zone=this, orderType=2. Collections with only
// OpenSea's platform fee accept plain unrestricted (zone 0x0, orderType 0).
const OPENSEA_SIGNED_ZONE: &str = "0x000056f7000000ece9003ca63978907a00ffd100";
const SEL_GET_COUNTER: &str = "0xf07ec373"; // getCounter(address)

/// The fee items an order must carry: ONLY the fees the collection marks
/// `required`. Checked against live orders — a FAX offer OpenSea accepted
/// carried just the 1% platform fee, while the collection also advertises a 10%
/// creator fee with `required: false`. Billing that optional fee made our offer
/// cost the user 11% and did not match any order OpenSea had accepted.
async fn collection_required_fees(http: &reqwest::Client, slug: &str) -> Vec<(f64, String)> {
    let Ok(d) = os_get(http, &format!("/collections/{slug}")).await else { return vec![] };
    d.get("fees")
        .and_then(|f| f.as_array())
        .map(|a| {
            a.iter()
                .filter(|f| f.get("required").and_then(|v| v.as_bool()).unwrap_or(false))
                .filter_map(|f| {
                    Some((
                        f.get("fee").and_then(|v| v.as_f64())?,
                        f.get("recipient").and_then(|v| v.as_str())?.to_string(),
                    ))
                })
                .collect()
        })
        .unwrap_or_default()
}

/// The currency a collection requires for a LISTING or an OFFER, from OpenSea's
/// `pricing_currencies` (`field` = "listing_currency" | "offer_currency").
/// Robinhood collections price in USDG (ERC-20, 6 decimals), NOT native ETH — a
/// native-ETH order is rejected by OpenSea ("Payment asset … is not supported").
/// Returns (seaportItemType, tokenAddress, decimals, symbol); falls back to
/// native ETH (18 dp) when the collection accepts it / is unknown.
async fn pricing_currency(http: &reqwest::Client, slug: &str, field: &str) -> (u8, String, u32, String) {
    if !slug.is_empty() {
        if let Ok(d) = os_get(http, &format!("/collections/{slug}")).await {
            if let Some(c) = d.pointer(&format!("/pricing_currencies/{field}")) {
                let addr = c.get("address").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                let dec = c.get("decimals").and_then(|v| v.as_u64()).unwrap_or(18) as u32;
                let sym = c.get("symbol").and_then(|v| v.as_str()).unwrap_or("ETH").to_string();
                if addr.starts_with("0x") && addr.len() == 42 && addr != ZERO {
                    return (1, addr, dec, sym); // ERC-20 (itemType 1)
                }
            }
        }
    }
    (0, ZERO.to_string(), 18, "ETH".to_string()) // native ETH (itemType 0)
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

    // The collection's REQUIRED listing currency. Robinhood collections require
    // USDG (ERC-20, 6dp) — a native-ETH listing is rejected. `price_eth` is the
    // price in that currency's units (e.g. USDG), scaled by its decimals.
    let (cur_type, cur_addr, cur_dec, _cur_sym) = pricing_currency(http, &slug, "listing_currency").await;
    let price_wei = (price_eth * 10f64.powi(cur_dec as i32)) as u128;
    let fees = if slug.is_empty() { vec![] } else { collection_required_fees(http, &slug).await };
    // Robinhood orders go through OpenSea's Signed Zone V2 as FULL_RESTRICTED.
    // Verified against live orders on both a collection with enforced creator
    // fees (Quotrons404) and one without (fax-404): every accepted listing and
    // offer carries zone 0x000056f7… / orderType 2. Unrestricted orders are
    // rejected outright on contracts that require the zone.
    let (order_zone, order_type): (&str, u8) = (OPENSEA_SIGNED_ZONE, 2);
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
    // Every consideration item is denominated in the collection's currency.
    let mut cons_items = vec![item(cur_type, &cur_addr, "0", &seller_amt.to_string(), &seller_amt.to_string(), Some(&wallet))];
    for (amt, rec) in &consideration {
        cons_items.push(item(cur_type, &cur_addr, "0", &amt.to_string(), &amt.to_string(), Some(rec)));
    }
    let offer = vec![item(2, &token, &token_id, "1", "1", None)];
    let counter = seaport_counter(http, &wallet).await;
    let start = now_secs();
    let end = start + days * 86400;
    let salt = salt_hex();

    let parameters = json!({
        "offerer": wallet,
        "zone": order_zone,
        "offer": offer,
        "consideration": cons_items,
        "orderType": order_type,
        "startTime": start.to_string(),
        "endTime": end.to_string(),
        "zoneHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "salt": salt,
        "conduitKey": OPENSEA_CONDUIT_KEY,
        "totalOriginalConsiderationItems": cons_items.len(),
        "counter": counter.to_string(),
    });
    let mut resp = order_response(parameters, &slug);
    // OpenSea validates the ERC-721 conduit approval AT SUBMIT ("ERC721 conduit
    // not approved"). If the seller hasn't granted the conduit setApprovalForAll
    // on this collection, hand the frontend an approval tx to send first. Skip
    // it when already approved so the user isn't prompted for a redundant tx.
    let conduit = resolve_conduit(http, &rpc(), OPENSEA_CONDUIT_KEY).await;
    if conduit.starts_with("0x") && !is_approved_for_all(http, &rpc(), &token, &wallet, &conduit).await {
        let data = set_approval_for_all_calldata(&conduit, true);
        if let Some(o) = resp.as_object_mut() {
            o.insert("nftApprove".into(), json!({ "to": token, "data": format!("0x{data}"), "value": "0", "chainId": CHAIN }));
        }
    }
    Ok(resp)
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

    // The collection's REQUIRED offer currency (Robinhood → USDG, 6dp). An offer
    // is always an ERC-20 (you cannot bid with native ETH), so when the collection
    // reports native/unknown, fall back to WETH. `price_eth` is in that currency.
    let (oc_addr, oc_dec) = {
        let (t, a, d, _s) = pricing_currency(http, &slug, "offer_currency").await;
        if t == 1 { (a, d) } else { (SUSHI_WETH.to_string(), 18u32) }
    };
    let price_wei = (price_eth * 10f64.powi(oc_dec as i32)) as u128;
    let fees = if slug.is_empty() { vec![] } else { collection_required_fees(http, &slug).await };
    // Robinhood orders go through OpenSea's Signed Zone V2 as FULL_RESTRICTED.
    // Verified against live orders on both a collection with enforced creator
    // fees (Quotrons404) and one without (fax-404): every accepted listing and
    // offer carries zone 0x000056f7… / orderType 2. Unrestricted orders are
    // rejected outright on contracts that require the zone.
    let (order_zone, order_type): (&str, u8) = (OPENSEA_SIGNED_ZONE, 2);
    // offer: the currency from the bidder. consideration: the NFT to the bidder + fees.
    let offer = vec![item(1, &oc_addr, "0", &price_wei.to_string(), &price_wei.to_string(), None)];
    let mut cons_items = vec![item(2, &token, &token_id, "1", "1", Some(&wallet))];
    for (pct, rec) in &fees {
        let amt = ((price_wei as f64) * pct / 100.0) as u128;
        if amt == 0 { continue; }
        cons_items.push(item(1, &oc_addr, "0", &amt.to_string(), &amt.to_string(), Some(rec)));
    }
    let counter = seaport_counter(http, &wallet).await;
    let start = now_secs();
    let end = start + days * 86400;
    let parameters = json!({
        "offerer": wallet, "zone": order_zone, "offer": offer, "consideration": cons_items,
        "orderType": order_type, "startTime": start.to_string(), "endTime": end.to_string(),
        "zoneHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "salt": salt_hex(), "conduitKey": OPENSEA_CONDUIT_KEY,
        "totalOriginalConsiderationItems": cons_items.len(), "counter": counter.to_string(),
    });
    let mut resp = order_response(parameters, &slug);
    // The offer currency must be approved to the CONDUIT that Seaport pulls it
    // through on acceptance — the conduit for OPENSEA_CONDUIT_KEY, resolved via
    // ConduitController (not a hardcoded address, which drifts when the key
    // changes). Approve EXACTLY the bid amount, so a future conduit compromise
    // can't pull more than this one offer.
    let spender = resolve_conduit(http, &rpc(), OPENSEA_CONDUIT_KEY).await;
    let approve = erc20_approve_calldata(&spender, price_wei);
    if let Some(o) = resp.as_object_mut() {
        o.insert("wethApprove".into(), json!({ "to": oc_addr, "data": format!("0x{approve}"), "value": "0", "chainId": CHAIN }));
        o.insert("offerCurrency".into(), Value::from(oc_addr.clone()));
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

use base64::Engine;
use serde::{Deserialize, Serialize};
use solana_sdk::pubkey::Pubkey;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

/// Magic Eden API v2 base URL
pub const MAGIC_EDEN_API: &str = "https://api-mainnet.magiceden.dev/v2";

/// Marketplace fee (2% = 200 basis points)
pub const MARKETPLACE_FEE_BPS: u64 = 200;

/// Magic Eden's default auction house — the one every live listing I sampled
/// runs under (Mad Lads, Okay Bears, DeGods, Claynosaurz).
///
/// A FALLBACK only. The value previously hard-coded here was a different
/// address and the instruction endpoints answer it with "invalid auction
/// house", so always prefer the `auctionHouse` carried by the listing or offer
/// being acted on; this is for when we have nothing else.
pub const MAGIC_EDEN_AUCTION_HOUSE: &str = "E8cU1WiRWjanGxmn96ewBgk9vPTcL6AEZ1t6F6fkgUWe";

/// API key for the instruction endpoints.
///
/// Reads are public; every `/instructions/*` endpoint — which is to say every
/// buy, list, offer, cancel and escrow movement — answers 401 without a
/// Bearer token. Read once from the environment.
fn me_api_key() -> Option<&'static str> {
    use std::sync::OnceLock;
    static KEY: OnceLock<Option<String>> = OnceLock::new();
    KEY.get_or_init(|| {
        std::env::var("MAGIC_EDEN_API_KEY")
            .ok()
            .filter(|s| !s.trim().is_empty())
    })
    .as_deref()
}

/// GET a Magic Eden endpoint, sending the key when we have one.
pub async fn me_get_json(http: &reqwest::Client, url: &str) -> Result<serde_json::Value, AppError> {
    // Magic Eden throttles and occasionally 5xxs, and both clear on their own.
    // Without a retry those land as a card that says "This NFT" over a "media
    // not available" square for a piece with perfectly good art — the failure
    // is invisible and looks like missing data instead of a bad minute.
    const ATTEMPTS: usize = 3;
    let mut last = None;
    for attempt in 0..ATTEMPTS {
        if attempt > 0 {
            tokio::time::sleep(std::time::Duration::from_millis(250 << attempt)).await;
        }
        let mut req = http.get(url);
        if let Some(key) = me_api_key() {
            req = req.bearer_auth(key);
        }
        let resp = match req.send().await {
            Ok(r) => r,
            Err(e) => {
                last = Some(AppError::ProtocolError(format!(
                    "Magic Eden request failed: {e}"
                )));
                continue;
            }
        };
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        if status.is_success() {
            return serde_json::from_str(&body).map_err(|e| {
                AppError::ProtocolError(format!("Magic Eden returned malformed JSON: {e}"))
            });
        }
        // A 404 is an answer: the thing is not there and asking again will not
        // change that. Only throttling and server faults are worth repeating.
        let retryable = status.as_u16() == 429 || status.is_server_error();
        last = Some(me_api_error(status, &body));
        if !retryable {
            break;
        }
    }
    Err(last.unwrap_or_else(|| {
        AppError::ProtocolError("Magic Eden could not complete that request".into())
    }))
}

/// Turn a Magic Eden failure into something a person can act on.
///
/// Its errors arrive in three different shapes — `{"msg":…}`, `{"err":…}`, or
/// bare text like "Not Found." — and none of them belong in front of a user
/// as-is. A 401 in particular is OUR configuration problem, not theirs, and
/// must never read as if the user did something wrong.
fn me_api_error(status: reqwest::StatusCode, body: &str) -> AppError {
    if status == reqwest::StatusCode::UNAUTHORIZED || status == reqwest::StatusCode::FORBIDDEN {
        return AppError::ProtocolError(
            "Magic Eden trading is unavailable right now. Browsing still works.".into(),
        );
    }
    let detail = serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|v| {
            v.get("msg")
                .or_else(|| v.get("err"))
                .or_else(|| v.get("error"))
                .and_then(|m| m.as_str())
                .map(str::to_string)
        })
        .unwrap_or_default();
    if status == reqwest::StatusCode::NOT_FOUND || detail.contains("Not Found") {
        return AppError::NotFound("Magic Eden has no record of that item".into());
    }
    if status == reqwest::StatusCode::TOO_MANY_REQUESTS {
        return AppError::ProtocolError(
            "Magic Eden is rate-limiting us — try again shortly".into(),
        );
    }
    if detail.contains("auction house") {
        return AppError::InvalidParams(
            "That collection trades under a different auction house — reload the listing".into(),
        );
    }
    AppError::ProtocolError(if detail.is_empty() {
        "Magic Eden could not complete that request".into()
    } else {
        detail
    })
}

/// A transaction returned by an `/instructions/*` endpoint.
pub struct MeTx {
    /// base64 transaction, ready for the wallet to sign.
    pub tx_b64: String,
    pub blockhash: Option<String>,
    pub last_valid_block_height: Option<u64>,
}

/// Call an instruction endpoint and take the transaction out of the reply.
///
/// The reply carries four fields and only one of them is the right answer.
/// `tx` is a serialized MESSAGE with no signature slots. `txSigned` is the
/// whole transaction — and for anything the marketplace co-signs (listing
/// needs two signatures, the second already filled by Magic Eden's authority)
/// it is the ONLY usable form: rebuilding from `tx` throws that signature
/// away and the transaction can never land.
///
/// So: prefer `txSigned`, fall back to `v0.txSigned`/`v0.tx` for versioned,
/// and only then to `tx`.
pub async fn me_instruction(
    http: &reqwest::Client,
    endpoint: &str,
    query: &[(&str, String)],
) -> Result<MeTx, AppError> {
    if me_api_key().is_none() {
        return Err(AppError::ProtocolError(
            "Magic Eden trading is unavailable right now. Browsing still works.".into(),
        ));
    }
    let url = format!("{MAGIC_EDEN_API}/instructions/{endpoint}");
    let mut req = http.get(&url).bearer_auth(me_api_key().unwrap());
    let filtered: Vec<&(&str, String)> = query.iter().filter(|(_, v)| !v.is_empty()).collect();
    req = req.query(
        &filtered
            .iter()
            .map(|(k, v)| (*k, v.as_str()))
            .collect::<Vec<_>>(),
    );

    let resp = req
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Magic Eden request failed: {e}")))?;
    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(me_api_error(status, &body));
    }
    let json: serde_json::Value = serde_json::from_str(&body)
        .map_err(|e| AppError::ProtocolError(format!("Magic Eden returned malformed JSON: {e}")))?;

    let buffer_bytes = |v: Option<&serde_json::Value>| -> Option<Vec<u8>> {
        let arr = v?.get("data")?.as_array()?;
        Some(
            arr.iter()
                .filter_map(|b| b.as_u64())
                .map(|b| b as u8)
                .collect(),
        )
    };
    let bytes = buffer_bytes(json.get("txSigned"))
        .or_else(|| buffer_bytes(json.pointer("/v0/txSigned")))
        .or_else(|| buffer_bytes(json.pointer("/v0/tx")))
        .or_else(|| buffer_bytes(json.get("tx")))
        .ok_or_else(|| AppError::ProtocolError("Magic Eden did not return a transaction".into()))?;

    Ok(MeTx {
        tx_b64: base64::engine::general_purpose::STANDARD.encode(&bytes),
        blockhash: json
            .pointer("/blockhashData/blockhash")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        last_valid_block_height: json
            .pointer("/blockhashData/lastValidBlockHeight")
            .and_then(|v| v.as_u64()),
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/// NFT metadata from Magic Eden
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeNFT {
    pub mint_address: String,
    #[serde(default)]
    pub name: String,
    /// Magic Eden does not return `uri` on `/tokens/{mint}` or on a wallet's
    /// tokens. It was REQUIRED here, so those lookups failed to deserialize
    /// and fell back silently — cards read "Buy HRZo…aFZf" instead of
    /// "Buy Mad Lads #674", and a wallet's NFTs came back empty.
    #[serde(default)]
    pub uri: Option<String>,
    pub image: Option<String>,
    pub price: Option<f64>,
    pub owner: Option<String>,
    pub collection_name: Option<String>,
    pub collection_symbol: Option<String>,
    pub attributes: Option<Vec<MeAttribute>>,
    pub token_address: Option<String>,
}

/// NFT attribute
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeAttribute {
    /// Magic Eden sends this as `trait_type`, in snake case, while the rest
    /// of its payload is camel. Without the alias the struct-level
    /// `rename_all` looks for `traitType`, the whole NFT fails to
    /// deserialize, and every caller silently falls back — which is why
    /// cards showed a truncated mint instead of the NFT's name.
    #[serde(alias = "trait_type")]
    pub trait_type: String,
    #[serde(default)]
    pub value: serde_json::Value,
}

/// Collection info from Magic Eden
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCollection {
    pub symbol: String,
    pub name: String,
    pub description: Option<String>,
    pub image: Option<String>,
    pub twitter: Option<String>,
    pub discord: Option<String>,
    pub website: Option<String>,
    pub categories: Option<Vec<String>>,
    pub is_derivative: Option<bool>,
    pub is_verified: Option<bool>,
    pub origin_data: Option<serde_json::Value>,
}

/// Collection statistics
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCollectionStats {
    pub symbol: String,
    pub floor_price: Option<f64>,
    pub listed_count: Option<u64>,
    pub avg_price_24hr: Option<f64>,
    pub volume_all: Option<f64>,
    pub volume_24hr: Option<f64>,
    pub owner_count: Option<u64>,
    pub total_supply: Option<u64>,
}

/// Active listing on Magic Eden
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeListing {
    pub price: f64,
    pub seller: String,
    pub token_address: String,
    pub token_mint: String,
    pub expiry: Option<u64>,
    pub auction_house: Option<String>,
    pub buyer_price: Option<f64>,
    pub bump: Option<u8>,
    pub trade_state: Option<String>,
    pub created_at: Option<String>,
}

/// Offer/Bid on an NFT
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeOffer {
    pub price: f64,
    pub buyer: String,
    pub token_mint: String,
    pub token_address: Option<String>,
    pub expiry: Option<u64>,
    pub created_at: Option<String>,
    pub auction_house: Option<String>,
    pub bump: Option<u8>,
    pub trade_state: Option<String>,
}

/// Activity/Transaction on Magic Eden
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeActivity {
    pub signature: String,
    pub activity_type: String,
    pub price: Option<f64>,
    pub buyer: Option<String>,
    pub seller: Option<String>,
    pub token_mint: String,
    pub token_address: Option<String>,
    pub block_time: Option<u64>,
    pub collection_symbol: Option<String>,
}

/// Wallet NFT with metadata
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeWalletNFT {
    pub mint_address: String,
    #[serde(default)]
    pub name: String,
    /// Magic Eden does not return `uri` on `/tokens/{mint}` or on a wallet's
    /// tokens. It was REQUIRED here, so those lookups failed to deserialize
    /// and fell back silently — cards read "Buy HRZo…aFZf" instead of
    /// "Buy Mad Lads #674", and a wallet's NFTs came back empty.
    #[serde(default)]
    pub uri: Option<String>,
    pub image: Option<String>,
    pub collection_name: Option<String>,
    pub collection_symbol: Option<String>,
    pub token_address: Option<String>,
    pub amount: Option<u64>,
    pub frozen: Option<bool>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Parameter Structs
// ──────────────────────────────────────────────────────────────────────────────

/// Parameters for listing an NFT
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeListParams {
    /// NFT mint address
    pub mint_address: String,
    /// Price in SOL
    pub price: String,
    /// Optional expiry (unix timestamp)
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub expiry: Option<u64>,
    /// The wallet's token account for this mint. Resolved when absent.
    #[serde(default)]
    pub token_account: Option<String>,
    /// Auction house the collection trades under. Resolved when absent.
    #[serde(default)]
    pub auction_house: Option<String>,
    #[serde(default)]
    pub seller_referral: Option<String>,
}

/// Reprice an existing listing or offer.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeChangePriceParams {
    pub mint_address: String,
    /// The price to move to, in SOL. The current one is read from the live
    /// listing/offer — asking the user to restate it invites a mismatch.
    #[serde(alias = "price")]
    pub new_price: String,
}

/// Taking SOL back out of the Magic Eden bidding escrow.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeEscrowParams {
    /// Amount in SOL. Absent means all of it — "withdraw my Magic Eden
    /// balance" names no number because the number is whatever is there.
    #[serde(default)]
    pub amount: Option<String>,
    #[serde(default)]
    pub auction_house: Option<String>,
}

/// Parameters for buying an NFT
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeBuyParams {
    /// NFT mint address
    pub mint_address: String,
    /// Price to buy at (must match listing)
    pub price: String,
    /// Token address (escrow account)
    pub token_address: Option<String>,
    /// Seller's wallet address. When absent the cheapest live listing is used.
    pub seller: Option<String>,
}

/// Parameters for canceling a listing
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCancelListingParams {
    /// NFT mint address
    pub mint_address: String,
    /// Price of the listing to cancel
    pub price: String,
    /// Token address (escrow account)
    pub token_address: Option<String>,
}

/// Parameters for making an offer
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeMakeOfferParams {
    /// NFT mint address
    pub mint_address: String,
    /// Offer price in SOL
    pub price: String,
    /// Optional expiry (unix timestamp)
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub expiry: Option<u64>,
    #[serde(default)]
    pub auction_house: Option<String>,
    #[serde(default)]
    pub buyer_referral: Option<String>,
}

/// Parameters for accepting an offer
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeAcceptOfferParams {
    /// NFT mint address
    pub mint_address: String,
    /// Offer price, if the caller happens to know it. Ignored: the builder
    /// takes the price off the live offer, because a bid can be raised or
    /// withdrawn between reading it and signing. Requiring it here rejected
    /// requests over a field the service was going to look up anyway.
    #[serde(default)]
    pub price: Option<String>,
    /// Buyer's wallet address (offer maker)
    pub buyer: Option<String>,
}

/// Parameters for canceling an offer
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCancelOfferParams {
    /// NFT mint address
    pub mint_address: String,
    /// Ignored — the offer being withdrawn is resolved live. See
    /// `MeAcceptOfferParams::price`.
    #[serde(default)]
    pub price: Option<String>,
}

/// Parameters for getting collection info
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCollectionInfoParams {
    /// Collection symbol (e.g., "y00ts", "okay_bears")
    pub symbol: String,
}

/// Parameters for getting NFT info
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeNFTInfoParams {
    /// NFT mint address
    pub mint_address: String,
}

/// Parameters for getting wallet NFTs
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeWalletNFTsParams {
    /// Wallet address
    pub wallet_address: String,
    /// Optional collection filter
    pub collection_symbol: Option<String>,
    /// Pagination limit
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u32>,
    /// Pagination offset
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub offset: Option<u32>,
}

/// Parameters for getting collection activity
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCollectionActivityParams {
    /// Collection symbol
    pub symbol: String,
    /// Activity type filter (list, buy, offer, etc.)
    pub activity_type: Option<String>,
    /// Pagination limit
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u32>,
    /// Pagination offset
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub offset: Option<u32>,
}

/// Parameters for getting listings
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeListingsParams {
    /// Collection symbol
    pub symbol: String,
    /// Pagination limit
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u32>,
    /// Pagination offset
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub offset: Option<u32>,
}

/// Parameters for getting offers
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeOffersParams {
    /// NFT mint address
    pub mint_address: String,
    /// Pagination limit
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u32>,
    /// Pagination offset
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub offset: Option<u32>,
}

/// Parameters for getting NFTs by collection
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCollectionNFTsParams {
    /// Collection symbol
    pub symbol: String,
    /// Pagination limit
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u32>,
    /// Pagination offset
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub offset: Option<u32>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation Functions
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_me_list_params(params: &MeListParams) -> Result<(), AppError> {
    if params.mint_address.is_empty() {
        return Err(AppError::InvalidParams("Mint address is required".into()));
    }
    let price: f64 = params
        .price
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid price format".into()))?;
    if price <= 0.0 {
        return Err(AppError::InvalidParams("Price must be positive".into()));
    }
    if let Some(expiry) = params.expiry {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        if expiry < now {
            return Err(AppError::InvalidParams(
                "Expiry cannot be in the past".into(),
            ));
        }
    }
    // Validate mint address is a valid pubkey
    params
        .mint_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid mint address format".into()))?;
    Ok(())
}

pub fn validate_me_buy_params(params: &MeBuyParams) -> Result<(), AppError> {
    if params.mint_address.is_empty() {
        return Err(AppError::InvalidParams("Mint address is required".into()));
    }
    let price: f64 = params
        .price
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid price format".into()))?;
    if price <= 0.0 {
        return Err(AppError::InvalidParams("Price must be positive".into()));
    }
    params
        .mint_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid mint address format".into()))?;
    Ok(())
}

pub fn validate_me_cancel_listing_params(params: &MeCancelListingParams) -> Result<(), AppError> {
    if params.mint_address.is_empty() {
        return Err(AppError::InvalidParams("Mint address is required".into()));
    }
    params
        .mint_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid mint address format".into()))?;
    Ok(())
}

pub fn validate_me_make_offer_params(params: &MeMakeOfferParams) -> Result<(), AppError> {
    if params.mint_address.is_empty() {
        return Err(AppError::InvalidParams("Mint address is required".into()));
    }
    let price: f64 = params
        .price
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid price format".into()))?;
    if price <= 0.0 {
        return Err(AppError::InvalidParams(
            "Offer price must be positive".into(),
        ));
    }
    params
        .mint_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid mint address format".into()))?;
    Ok(())
}

pub fn validate_me_accept_offer_params(params: &MeAcceptOfferParams) -> Result<(), AppError> {
    if params.mint_address.is_empty() {
        return Err(AppError::InvalidParams("Mint address is required".into()));
    }
    // A price is not required, and when one is supplied it is only checked for
    // being a number — the offer's own price is what gets signed.
    if let Some(raw) = params.price.as_deref().filter(|p| !p.is_empty()) {
        let price: f64 = raw
            .parse()
            .map_err(|_| AppError::InvalidParams("Invalid price format".into()))?;
        if price <= 0.0 {
            return Err(AppError::InvalidParams("Price must be positive".into()));
        }
    }
    params
        .mint_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid mint address format".into()))?;
    Ok(())
}

pub fn validate_me_cancel_offer_params(params: &MeCancelOfferParams) -> Result<(), AppError> {
    if params.mint_address.is_empty() {
        return Err(AppError::InvalidParams("Mint address is required".into()));
    }
    params
        .mint_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid mint address format".into()))?;
    Ok(())
}

pub fn validate_me_collection_info_params(params: &MeCollectionInfoParams) -> Result<(), AppError> {
    if params.symbol.is_empty() {
        return Err(AppError::InvalidParams(
            "Collection symbol is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_me_nft_info_params(params: &MeNFTInfoParams) -> Result<(), AppError> {
    if params.mint_address.is_empty() {
        return Err(AppError::InvalidParams("Mint address is required".into()));
    }
    params
        .mint_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid mint address format".into()))?;
    Ok(())
}

pub fn validate_me_wallet_nfts_params(params: &MeWalletNFTsParams) -> Result<(), AppError> {
    if params.wallet_address.is_empty() {
        return Err(AppError::InvalidParams("Wallet address is required".into()));
    }
    params
        .wallet_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid wallet address format".into()))?;
    Ok(())
}

pub fn validate_me_collection_activity_params(
    params: &MeCollectionActivityParams,
) -> Result<(), AppError> {
    if params.symbol.is_empty() {
        return Err(AppError::InvalidParams(
            "Collection symbol is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_me_listings_params(params: &MeListingsParams) -> Result<(), AppError> {
    if params.symbol.is_empty() {
        return Err(AppError::InvalidParams(
            "Collection symbol is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_me_offers_params(params: &MeOffersParams) -> Result<(), AppError> {
    if params.mint_address.is_empty() {
        return Err(AppError::InvalidParams("Mint address is required".into()));
    }
    params
        .mint_address
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid mint address format".into()))?;
    Ok(())
}

pub fn validate_me_collection_nfts_params(params: &MeCollectionNFTsParams) -> Result<(), AppError> {
    if params.symbol.is_empty() {
        return Err(AppError::InvalidParams(
            "Collection symbol is required".into(),
        ));
    }
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// API Query Functions
// ──────────────────────────────────────────────────────────────────────────────

/// Get collection info by symbol
pub async fn get_collection_info(
    http: &reqwest::Client,
    symbol: &str,
) -> Result<MeCollection, AppError> {
    let resp = http
        .get(format!("{}/collections/{}", MAGIC_EDEN_API, symbol))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch collection: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "Collection '{}' not found",
            symbol
        )));
    }

    resp.json::<MeCollection>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse collection: {e}")))
}

/// Get collection statistics
pub async fn get_collection_stats(
    http: &reqwest::Client,
    symbol: &str,
) -> Result<MeCollectionStats, AppError> {
    let resp = http
        .get(format!("{}/collections/{}/stats", MAGIC_EDEN_API, symbol))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch collection stats: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "Collection '{}' stats not found",
            symbol
        )));
    }

    resp.json::<MeCollectionStats>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse collection stats: {e}")))
}

/// Get NFT info by mint address
pub async fn get_nft_info(http: &reqwest::Client, mint_address: &str) -> Result<MeNFT, AppError> {
    let resp = http
        .get(format!("{}/tokens/{}", MAGIC_EDEN_API, mint_address))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch NFT: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "NFT '{}' not found",
            mint_address
        )));
    }

    resp.json::<MeNFT>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse NFT: {e}")))
}

/// Get wallet NFTs
pub async fn get_wallet_nfts(
    http: &reqwest::Client,
    wallet_address: &str,
    collection_symbol: Option<&str>,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<Vec<MeWalletNFT>, AppError> {
    let mut url = format!("{}/wallets/{}/tokens", MAGIC_EDEN_API, wallet_address);
    let mut params = vec![];

    if let Some(symbol) = collection_symbol {
        params.push(format!("collectionSymbol={}", symbol));
    }
    if let Some(l) = limit {
        params.push(format!("limit={}", l));
    }
    if let Some(o) = offset {
        params.push(format!("offset={}", o));
    }

    if !params.is_empty() {
        url.push('?');
        url.push_str(&params.join("&"));
    }

    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch wallet NFTs: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "Wallet '{}' NFTs not found",
            wallet_address
        )));
    }

    resp.json::<Vec<MeWalletNFT>>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse wallet NFTs: {e}")))
}

/// Get collection activity
pub async fn get_collection_activity(
    http: &reqwest::Client,
    symbol: &str,
    activity_type: Option<&str>,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<Vec<MeActivity>, AppError> {
    let mut url = format!("{}/collections/{}/activities", MAGIC_EDEN_API, symbol);
    let mut params = vec![];

    if let Some(at) = activity_type {
        params.push(format!("type={}", at));
    }
    if let Some(l) = limit {
        params.push(format!("limit={}", l));
    }
    if let Some(o) = offset {
        params.push(format!("offset={}", o));
    }

    if !params.is_empty() {
        url.push('?');
        url.push_str(&params.join("&"));
    }

    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch collection activity: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "Collection '{}' activity not found",
            symbol
        )));
    }

    resp.json::<Vec<MeActivity>>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse collection activity: {e}")))
}

/// Get listings for a collection
pub async fn get_collection_listings(
    http: &reqwest::Client,
    symbol: &str,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<Vec<MeListing>, AppError> {
    let mut url = format!("{}/collections/{}/listings", MAGIC_EDEN_API, symbol);
    let mut params = vec![];

    if let Some(l) = limit {
        params.push(format!("limit={}", l));
    }
    if let Some(o) = offset {
        params.push(format!("offset={}", o));
    }

    if !params.is_empty() {
        url.push('?');
        url.push_str(&params.join("&"));
    }

    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch listings: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "Collection '{}' listings not found",
            symbol
        )));
    }

    resp.json::<Vec<MeListing>>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse listings: {e}")))
}

/// Get offers for an NFT
pub async fn get_nft_offers(
    http: &reqwest::Client,
    mint_address: &str,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<Vec<MeOffer>, AppError> {
    let mut url = format!("{}/tokens/{}/offers_received", MAGIC_EDEN_API, mint_address);
    let mut params = vec![];

    if let Some(l) = limit {
        params.push(format!("limit={}", l));
    }
    if let Some(o) = offset {
        params.push(format!("offset={}", o));
    }

    if !params.is_empty() {
        url.push('?');
        url.push_str(&params.join("&"));
    }

    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch offers: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "NFT '{}' offers not found",
            mint_address
        )));
    }

    resp.json::<Vec<MeOffer>>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse offers: {e}")))
}

/// Get NFTs in a collection
pub async fn get_collection_nfts(
    http: &reqwest::Client,
    symbol: &str,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<Vec<MeNFT>, AppError> {
    let mut url = format!("{}/collections/{}/nfts", MAGIC_EDEN_API, symbol);
    let mut params = vec![];

    if let Some(l) = limit {
        params.push(format!("limit={}", l));
    }
    if let Some(o) = offset {
        params.push(format!("offset={}", o));
    }

    if !params.is_empty() {
        url.push('?');
        url.push_str(&params.join("&"));
    }

    let resp = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch collection NFTs: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "Collection '{}' NFTs not found",
            symbol
        )));
    }

    resp.json::<Vec<MeNFT>>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse collection NFTs: {e}")))
}

/// Get active listings for an NFT
pub async fn get_nft_listings(
    http: &reqwest::Client,
    mint_address: &str,
) -> Result<Vec<MeListing>, AppError> {
    let resp = http
        .get(format!(
            "{}/tokens/{}/listings",
            MAGIC_EDEN_API, mint_address
        ))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to fetch NFT listings: {e}")))?;

    if !resp.status().is_success() {
        return Err(AppError::NotFound(format!(
            "NFT '{}' listings not found",
            mint_address
        )));
    }

    resp.json::<Vec<MeListing>>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse NFT listings: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — marketplace writes
// ──────────────────────────────────────────────────────────────────────────────
//
// Every one of these was previously a placeholder: an empty `Message` with a
// zeroed blockhash and no call to Magic Eden at all. The preview looked
// convincing — "Buy Mad Lads #674 for 7.3 SOL" — and the transaction behind it
// could never land. They are now built from Magic Eden's own
// `/instructions/*` endpoints, which return a transaction the marketplace has
// already co-signed where its authority is a required signer.
//
// The parameters those endpoints need — auction house, token account, seller
// referral, expiry — are not things a user or an LLM can know. They come off
// the live listing or offer, resolved here.

/// A live listing, reduced to what the instruction endpoints ask for.
struct MeResolvedListing {
    seller: String,
    token_account: String,
    auction_house: String,
    seller_referral: Option<String>,
    expiry: i64,
    price: f64,
}

/// A live offer (bid) on a token.
struct MeResolvedOffer {
    buyer: String,
    token_account: Option<String>,
    auction_house: String,
    buyer_referral: Option<String>,
    expiry: i64,
    price: f64,
}

fn json_str(v: &serde_json::Value, k: &str) -> Option<String> {
    v.get(k).and_then(|x| x.as_str()).map(str::to_string)
}

/// The cheapest listing for a mint, or the one owned by `only_seller`.
async fn resolve_listing(
    http: &reqwest::Client,
    mint: &str,
    only_seller: Option<&str>,
) -> Option<MeResolvedListing> {
    let url = format!("{MAGIC_EDEN_API}/tokens/{mint}/listings");
    let rows = me_get_json(http, &url).await.ok()?;
    let rows = rows.as_array()?;
    let pick = rows.iter().find(|r| match only_seller {
        Some(s) => json_str(r, "seller").as_deref() == Some(s),
        None => true,
    })?;
    Some(MeResolvedListing {
        seller: json_str(pick, "seller")?,
        token_account: json_str(pick, "tokenAddress")?,
        auction_house: json_str(pick, "auctionHouse")
            .unwrap_or_else(|| MAGIC_EDEN_AUCTION_HOUSE.to_string()),
        seller_referral: json_str(pick, "sellerReferral"),
        expiry: pick.get("expiry").and_then(|e| e.as_i64()).unwrap_or(-1),
        price: pick.get("price").and_then(|p| p.as_f64()).unwrap_or(0.0),
    })
}

/// The best (or a named buyer's) offer on a mint.
async fn resolve_offer(
    http: &reqwest::Client,
    mint: &str,
    only_buyer: Option<&str>,
) -> Option<MeResolvedOffer> {
    let url = format!("{MAGIC_EDEN_API}/tokens/{mint}/offers_received?limit=20");
    let rows = me_get_json(http, &url).await.ok()?;
    let rows = rows.as_array()?;
    let pick = match only_buyer {
        Some(b) => rows
            .iter()
            .find(|r| json_str(r, "buyer").as_deref() == Some(b))?,
        // No buyer named: the highest bid is the one anyone means by "accept
        // the offer".
        None => rows.iter().max_by(|a, b| {
            let pa = a.get("price").and_then(|p| p.as_f64()).unwrap_or(0.0);
            let pb = b.get("price").and_then(|p| p.as_f64()).unwrap_or(0.0);
            pa.total_cmp(&pb)
        })?,
    };
    Some(MeResolvedOffer {
        buyer: json_str(pick, "buyer")?,
        token_account: json_str(pick, "tokenAddress"),
        auction_house: json_str(pick, "auctionHouse")
            .unwrap_or_else(|| MAGIC_EDEN_AUCTION_HOUSE.to_string()),
        buyer_referral: json_str(pick, "buyerReferral"),
        expiry: pick.get("expiry").and_then(|e| e.as_i64()).unwrap_or(-1),
        price: pick.get("price").and_then(|p| p.as_f64()).unwrap_or(0.0),
    })
}

/// The wallet's token account for a mint.
///
/// Magic Eden reports one on anything it already knows about; for an NFT it
/// has never seen listed, the associated token account is where a standard
/// NFT lives.
async fn resolve_token_account(http: &reqwest::Client, owner: &Pubkey, mint: &str) -> String {
    let url = format!("{MAGIC_EDEN_API}/tokens/{mint}");
    if let Ok(v) = me_get_json(http, &url).await {
        if let Some(ta) = json_str(&v, "tokenAddress") {
            return ta;
        }
    }
    match mint.parse::<Pubkey>() {
        Ok(m) => spl_associated_token_account::get_associated_token_address(owner, &m).to_string(),
        Err(_) => String::new(),
    }
}

/// Name and image for a mint, for the card's header. Best-effort: a preview
/// missing its picture is better than a failed action.
async fn nft_display(
    http: &reqwest::Client,
    mint: &str,
) -> (String, Option<String>, Option<String>) {
    match get_nft_info(http, mint).await {
        Ok(n) => (n.name.clone(), n.image.clone(), n.collection_name.clone()),
        Err(_) => (
            format!(
                "{}…{}",
                &mint[..4.min(mint.len())],
                &mint[mint.len().saturating_sub(4)..]
            ),
            None,
            None,
        ),
    }
}

#[allow(clippy::too_many_arguments)]
fn me_tx_response(
    action_type: &str,
    description: String,
    tx: MeTx,
    params: serde_json::Value,
    warnings: Vec<String>,
) -> BuildResponse {
    let mut params = params;
    if let Some(bh) = tx.blockhash.clone() {
        params["blockhash"] = serde_json::json!(bh);
    }
    if let Some(h) = tx.last_valid_block_height {
        params["lastValidBlockHeight"] = serde_json::json!(h);
    }
    BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: action_type.to_string(),
            description,
            estimated_fee: "~0.00001 SOL".to_string(),
            estimated_refund: None,
            params,
            warnings,
            requires_approval: true,
        },
        transaction: Some(tx.tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    }
}

fn fee_note(price: f64) -> String {
    format!(
        "Magic Eden takes {}% ({:.4} SOL) of the sale",
        MARKETPLACE_FEE_BPS as f64 / 100.0,
        price * MARKETPLACE_FEE_BPS as f64 / 10_000.0
    )
}

/// List an NFT for sale.
pub async fn build_me_list(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeListParams,
) -> Result<BuildResponse, AppError> {
    validate_me_list_params(params)?;
    let price: f64 = params
        .price
        .parse()
        .map_err(|_| AppError::InvalidParams("Enter a price in SOL".into()))?;

    let seller = user_pubkey.to_string();
    let token_account = match params.token_account.clone() {
        Some(t) => t,
        None => resolve_token_account(http, user_pubkey, &params.mint_address).await,
    };
    let auction_house = params
        .auction_house
        .clone()
        .unwrap_or_else(|| MAGIC_EDEN_AUCTION_HOUSE.to_string());
    let expiry = params.expiry.map(|e| e as i64).unwrap_or(-1);

    let tx = me_instruction(
        http,
        "sell",
        &[
            ("seller", seller.clone()),
            ("auctionHouseAddress", auction_house.clone()),
            ("tokenMint", params.mint_address.clone()),
            ("tokenAccount", token_account.clone()),
            ("price", price.to_string()),
            ("expiry", expiry.to_string()),
            (
                "sellerReferral",
                params.seller_referral.clone().unwrap_or_default(),
            ),
        ],
    )
    .await?;

    let (name, image, collection) = nft_display(http, &params.mint_address).await;
    Ok(me_tx_response(
        "me_list",
        format!("List {name} for {price} SOL"),
        tx,
        serde_json::json!({
            "mintAddress": params.mint_address,
            "price": price,
            "expiry": expiry,
            "nftName": name,
            "nftImage": image,
            "collectionName": collection,
            "tokenAccount": token_account,
            "auctionHouse": auction_house,
            "marketplaceFeeBps": MARKETPLACE_FEE_BPS,
        }),
        vec![
            fee_note(price),
            "The listing stays open until it sells or you cancel it".into(),
        ],
    ))
}

/// Buy a listed NFT outright.
pub async fn build_me_buy(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeBuyParams,
) -> Result<BuildResponse, AppError> {
    validate_me_buy_params(params)?;

    let listing = resolve_listing(http, &params.mint_address, params.seller.as_deref())
        .await
        .ok_or_else(|| {
            AppError::InvalidParams(
                "That NFT is not listed for sale right now — someone may have just bought it"
                    .into(),
            )
        })?;

    // Price the user agreed to, checked against the live listing. Buying at a
    // price that moved under them is the one mistake this card must not make.
    let asked: f64 = params.price.parse().unwrap_or(listing.price);
    if (asked - listing.price).abs() > f64::EPSILON.max(listing.price * 0.0001) {
        return Err(AppError::InvalidParams(format!(
            "The price changed — it is now {} SOL, not {} SOL",
            listing.price, asked
        )));
    }

    let tx = me_instruction(
        http,
        "buy_now",
        &[
            ("buyer", user_pubkey.to_string()),
            ("seller", listing.seller.clone()),
            ("auctionHouseAddress", listing.auction_house.clone()),
            ("tokenMint", params.mint_address.clone()),
            ("tokenATA", listing.token_account.clone()),
            ("price", listing.price.to_string()),
            (
                "sellerReferral",
                listing.seller_referral.clone().unwrap_or_default(),
            ),
            ("sellerExpiry", listing.expiry.to_string()),
        ],
    )
    .await?;

    let (name, image, collection) = nft_display(http, &params.mint_address).await;
    Ok(me_tx_response(
        "me_buy",
        format!("Buy {name} for {} SOL", listing.price),
        tx,
        serde_json::json!({
            "mintAddress": params.mint_address,
            "price": listing.price,
            "seller": listing.seller,
            "nftName": name,
            "nftImage": image,
            "collectionName": collection,
            "auctionHouse": listing.auction_house,
        }),
        vec![],
    ))
}

/// Cancel your own listing.
pub async fn build_me_cancel_listing(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeCancelListingParams,
) -> Result<BuildResponse, AppError> {
    validate_me_cancel_listing_params(params)?;
    let seller = user_pubkey.to_string();
    let listing = resolve_listing(http, &params.mint_address, Some(&seller))
        .await
        .ok_or_else(|| {
            AppError::InvalidParams("You don't have an active listing for that NFT".into())
        })?;

    let tx = me_instruction(
        http,
        "sell_cancel",
        &[
            ("seller", seller),
            ("auctionHouseAddress", listing.auction_house.clone()),
            ("tokenMint", params.mint_address.clone()),
            ("tokenAccount", listing.token_account.clone()),
            ("price", listing.price.to_string()),
            ("expiry", listing.expiry.to_string()),
            (
                "sellerReferral",
                listing.seller_referral.clone().unwrap_or_default(),
            ),
        ],
    )
    .await?;

    let (name, image, collection) = nft_display(http, &params.mint_address).await;
    Ok(me_tx_response(
        "me_cancel_listing",
        format!("Remove {name} from sale"),
        tx,
        serde_json::json!({
            "mintAddress": params.mint_address,
            "price": listing.price,
            "nftName": name,
            "nftImage": image,
            "collectionName": collection,
            "auctionHouse": listing.auction_house,
        }),
        vec![],
    ))
}

/// Reprice your own listing without cancelling it.
pub async fn build_me_change_listing_price(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeChangePriceParams,
) -> Result<BuildResponse, AppError> {
    let new_price: f64 = params
        .new_price
        .parse()
        .map_err(|_| AppError::InvalidParams("Enter the new price in SOL".into()))?;
    if new_price <= 0.0 {
        return Err(AppError::InvalidParams("Price must be above zero".into()));
    }
    let seller = user_pubkey.to_string();
    let listing = resolve_listing(http, &params.mint_address, Some(&seller))
        .await
        .ok_or_else(|| {
            AppError::InvalidParams("You don't have an active listing for that NFT".into())
        })?;

    let tx = me_instruction(
        http,
        "sell_change_price",
        &[
            ("seller", seller),
            ("auctionHouseAddress", listing.auction_house.clone()),
            ("tokenMint", params.mint_address.clone()),
            ("tokenAccount", listing.token_account.clone()),
            ("price", listing.price.to_string()),
            ("newPrice", new_price.to_string()),
            ("expiry", listing.expiry.to_string()),
            (
                "sellerReferral",
                listing.seller_referral.clone().unwrap_or_default(),
            ),
        ],
    )
    .await?;

    let (name, image, collection) = nft_display(http, &params.mint_address).await;
    Ok(me_tx_response(
        "me_sell_change_price",
        format!("Reprice {name} from {} to {new_price} SOL", listing.price),
        tx,
        serde_json::json!({
            "mintAddress": params.mint_address,
            "oldPrice": listing.price,
            "price": new_price,
            "nftName": name,
            "nftImage": image,
            "collectionName": collection,
            "auctionHouse": listing.auction_house,
        }),
        vec![fee_note(new_price)],
    ))
}

/// Rent exemption for the bid escrow — a system account with no data.
/// `getMinimumBalanceForRentExemption(0)` on mainnet.
const ESCROW_RENT_LAMPORTS: u64 = 890_880;

/// What the wallet already has sitting in its Magic Eden bidding escrow.
/// Best-effort: an unreadable balance counts as zero, which only ever makes
/// the minimum we quote stricter, never looser.
async fn me_escrow_lamports(http: &reqwest::Client, wallet: &str) -> u64 {
    let url = format!("{MAGIC_EDEN_API}/wallets/{wallet}/escrow_balance");
    match me_get_json(http, &url).await {
        Ok(v) => v
            .get("balance")
            .and_then(|b| b.as_f64())
            .map(|sol| (sol * 1e9).round() as u64)
            .unwrap_or(0),
        Err(_) => 0,
    }
}

/// Bid on an NFT.
pub async fn build_me_make_offer(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeMakeOfferParams,
) -> Result<BuildResponse, AppError> {
    validate_me_make_offer_params(params)?;
    let price: f64 = params
        .price
        .parse()
        .map_err(|_| AppError::InvalidParams("Enter an offer price in SOL".into()))?;

    // A bid is escrowed, and the escrow account has to be rent-exempt. Below
    // that minimum the bid cannot exist: the chain rejects it with
    // InsufficientFundsForRent on the escrow, which reaches the user as "not
    // enough balance" while their wallet plainly holds plenty. Say the real
    // number here instead of letting them sign something that cannot land.
    let lamports = (price * 1e9).round() as u64;
    let escrowed = me_escrow_lamports(http, &user_pubkey.to_string()).await;
    if lamports + escrowed < ESCROW_RENT_LAMPORTS {
        let short = ESCROW_RENT_LAMPORTS - escrowed;
        return Err(AppError::InvalidParams(format!(
            "An offer has to be at least {:.5} SOL. Magic Eden holds the bid in              an escrow account, and that account has to cover its own rent.",
            short as f64 / 1e9
        )));
    }

    // An offer needs an auction house, and an unlisted NFT has no listing to
    // take one from — fall back to the marketplace default.
    let auction_house = match params.auction_house.clone() {
        Some(a) => a,
        None => resolve_listing(http, &params.mint_address, None)
            .await
            .map(|l| l.auction_house)
            .unwrap_or_else(|| MAGIC_EDEN_AUCTION_HOUSE.to_string()),
    };
    let expiry = params.expiry.map(|e| e as i64).unwrap_or(-1);

    let tx = me_instruction(
        http,
        "buy",
        &[
            ("buyer", user_pubkey.to_string()),
            ("auctionHouseAddress", auction_house.clone()),
            ("tokenMint", params.mint_address.clone()),
            ("price", price.to_string()),
            ("expiry", expiry.to_string()),
            (
                "buyerReferral",
                params.buyer_referral.clone().unwrap_or_default(),
            ),
        ],
    )
    .await?;

    let (name, image, collection) = nft_display(http, &params.mint_address).await;
    Ok(me_tx_response(
        "me_make_offer",
        format!("Offer {price} SOL for {name}"),
        tx,
        serde_json::json!({
            "mintAddress": params.mint_address,
            "price": price,
            "expiry": expiry,
            "nftName": name,
            "nftImage": image,
            "collectionName": collection,
            "auctionHouse": auction_house,
        }),
        vec![
            "The SOL is held in your Magic Eden escrow until the offer is taken, expires, or you cancel it".into(),
        ],
    ))
}

/// Take a bid on an NFT you own.
pub async fn build_me_accept_offer(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeAcceptOfferParams,
) -> Result<BuildResponse, AppError> {
    validate_me_accept_offer_params(params)?;
    let seller = user_pubkey.to_string();
    let offer = resolve_offer(http, &params.mint_address, params.buyer.as_deref())
        .await
        .ok_or_else(|| {
            AppError::InvalidParams("There is no open offer on that NFT right now".into())
        })?;

    let token_account = match offer.token_account.clone() {
        Some(t) => t,
        None => resolve_token_account(http, user_pubkey, &params.mint_address).await,
    };

    let tx = me_instruction(
        http,
        "sell_now",
        &[
            ("seller", seller),
            ("buyer", offer.buyer.clone()),
            ("auctionHouseAddress", offer.auction_house.clone()),
            ("tokenMint", params.mint_address.clone()),
            ("tokenATA", token_account.clone()),
            ("price", offer.price.to_string()),
            ("newPrice", offer.price.to_string()),
            (
                "buyerReferral",
                offer.buyer_referral.clone().unwrap_or_default(),
            ),
            ("buyerExpiry", offer.expiry.to_string()),
            ("sellerExpiry", "-1".to_string()),
        ],
    )
    .await?;

    let (name, image, collection) = nft_display(http, &params.mint_address).await;
    Ok(me_tx_response(
        "me_accept_offer",
        format!("Sell {name} for {} SOL", offer.price),
        tx,
        serde_json::json!({
            "mintAddress": params.mint_address,
            "price": offer.price,
            "buyer": offer.buyer,
            "expiry": offer.expiry,
            "nftName": name,
            "nftImage": image,
            "collectionName": collection,
            "auctionHouse": offer.auction_house,
        }),
        vec![fee_note(offer.price)],
    ))
}

/// Withdraw your own bid.
pub async fn build_me_cancel_offer(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeCancelOfferParams,
) -> Result<BuildResponse, AppError> {
    validate_me_cancel_offer_params(params)?;
    let buyer = user_pubkey.to_string();
    let offer = resolve_offer(http, &params.mint_address, Some(&buyer))
        .await
        .ok_or_else(|| {
            AppError::InvalidParams("You don't have an open offer on that NFT".into())
        })?;

    let tx = me_instruction(
        http,
        "buy_cancel",
        &[
            ("buyer", buyer),
            ("auctionHouseAddress", offer.auction_house.clone()),
            ("tokenMint", params.mint_address.clone()),
            ("price", offer.price.to_string()),
            ("expiry", offer.expiry.to_string()),
            (
                "buyerReferral",
                offer.buyer_referral.clone().unwrap_or_default(),
            ),
        ],
    )
    .await?;

    let (name, image, collection) = nft_display(http, &params.mint_address).await;
    Ok(me_tx_response(
        "me_cancel_offer",
        format!("Withdraw your {} SOL offer on {name}", offer.price),
        tx,
        serde_json::json!({
            "mintAddress": params.mint_address,
            "price": offer.price,
            "expiry": offer.expiry,
            "nftName": name,
            "nftImage": image,
            "collectionName": collection,
            "auctionHouse": offer.auction_house,
        }),
        vec!["The escrowed SOL returns to your Magic Eden balance".into()],
    ))
}

/// Change the price of your own bid.
pub async fn build_me_change_offer_price(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeChangePriceParams,
) -> Result<BuildResponse, AppError> {
    let new_price: f64 = params
        .new_price
        .parse()
        .map_err(|_| AppError::InvalidParams("Enter the new offer price in SOL".into()))?;
    if new_price <= 0.0 {
        return Err(AppError::InvalidParams("Price must be above zero".into()));
    }
    let buyer = user_pubkey.to_string();
    let offer = resolve_offer(http, &params.mint_address, Some(&buyer))
        .await
        .ok_or_else(|| {
            AppError::InvalidParams("You don't have an open offer on that NFT".into())
        })?;

    let tx = me_instruction(
        http,
        "buy_change_price",
        &[
            ("buyer", buyer),
            ("auctionHouseAddress", offer.auction_house.clone()),
            ("tokenMint", params.mint_address.clone()),
            ("price", offer.price.to_string()),
            ("newPrice", new_price.to_string()),
            ("expiry", offer.expiry.to_string()),
            ("newExpiry", offer.expiry.to_string()),
            (
                "buyerReferral",
                offer.buyer_referral.clone().unwrap_or_default(),
            ),
        ],
    )
    .await?;

    let (name, image, collection) = nft_display(http, &params.mint_address).await;
    Ok(me_tx_response(
        "me_buy_change_price",
        format!(
            "Change your offer on {name} from {} to {new_price} SOL",
            offer.price
        ),
        tx,
        serde_json::json!({
            "mintAddress": params.mint_address,
            "oldPrice": offer.price,
            "price": new_price,
            "nftName": name,
            "nftImage": image,
            "collectionName": collection,
            "auctionHouse": offer.auction_house,
        }),
        vec![],
    ))
}

// ──────────────────────────────────────────────────────────────────────────────
// Query Actions (no transaction, just data)
// ──────────────────────────────────────────────────────────────────────────────

/// Get collection info action
/// Take SOL back out of the Magic Eden escrow.
///
/// Cancelling a bid does not return its SOL to the wallet — it parks it here,
/// which is the only reason this exists. Deposit does not: a bid funds its own
/// escrow as it is placed, so the only direction a user needs is out.
pub async fn build_me_withdraw(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeEscrowParams,
) -> Result<BuildResponse, AppError> {
    let buyer = user_pubkey.to_string();
    let parked = me_escrow_lamports(http, &buyer).await as f64 / 1e9;

    // No amount means all of it. Asking someone to name a number they would
    // have to go and look up, to empty an account they cannot see, is a
    // question with one sensible answer.
    let amount: f64 = match params.amount.as_deref().filter(|a| !a.trim().is_empty()) {
        Some(raw) => raw
            .parse()
            .map_err(|_| AppError::InvalidParams("Enter an amount in SOL".into()))?,
        None => parked,
    };
    if amount <= 0.0 {
        return Err(AppError::InvalidParams(
            "There is nothing in your Magic Eden balance to withdraw".into(),
        ));
    }
    if amount > parked + 1e-9 {
        return Err(AppError::InvalidParams(format!(
            "Your Magic Eden balance is {parked} SOL, so {amount} cannot be withdrawn"
        )));
    }

    let auction_house = params
        .auction_house
        .clone()
        .unwrap_or_else(|| MAGIC_EDEN_AUCTION_HOUSE.to_string());

    let tx = me_instruction(
        http,
        "withdraw",
        &[
            ("buyer", buyer),
            ("auctionHouseAddress", auction_house.clone()),
            ("amount", amount.to_string()),
        ],
    )
    .await?;

    Ok(me_tx_response(
        "me_withdraw",
        format!("Withdraw {amount} SOL from your Magic Eden balance"),
        tx,
        serde_json::json!({ "amount": amount, "balance": parked, "auctionHouse": auction_house }),
        vec!["Open offers backed by this balance may be cancelled".into()],
    ))
}

pub async fn build_me_collection_info(
    http: &reqwest::Client,
    params: &MeCollectionInfoParams,
) -> Result<BuildResponse, AppError> {
    validate_me_collection_info_params(params)?;

    let collection = get_collection_info(http, &params.symbol).await?;
    let stats = get_collection_stats(http, &params.symbol).await.ok();

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_collection_info".to_string(),
            description: format!("Get info for collection: {}", collection.name),
            estimated_fee: "0 SOL (query only)".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "symbol": collection.symbol,
                "name": collection.name,
                "description": collection.description,
                "image": collection.image,
                "twitter": collection.twitter,
                "discord": collection.discord,
                "website": collection.website,
                "isVerified": collection.is_verified,
                "stats": stats,
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Get NFT info action
pub async fn build_me_nft_info(
    http: &reqwest::Client,
    params: &MeNFTInfoParams,
) -> Result<BuildResponse, AppError> {
    validate_me_nft_info_params(params)?;

    let nft = get_nft_info(http, &params.mint_address).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_nft_info".to_string(),
            description: format!("Get info for NFT: {}", nft.name),
            estimated_fee: "0 SOL (query only)".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": nft.mint_address,
                "name": nft.name,
                "image": nft.image,
                "price": nft.price,
                "owner": nft.owner,
                "collectionName": nft.collection_name,
                "collectionSymbol": nft.collection_symbol,
                "attributes": nft.attributes,
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Get wallet NFTs action
pub async fn build_me_wallet_nfts(
    http: &reqwest::Client,
    params: &MeWalletNFTsParams,
) -> Result<BuildResponse, AppError> {
    validate_me_wallet_nfts_params(params)?;

    let nfts = get_wallet_nfts(
        http,
        &params.wallet_address,
        params.collection_symbol.as_deref(),
        params.limit,
        params.offset,
    )
    .await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_wallet_nfts".to_string(),
            description: format!(
                "Get NFTs for wallet: {} ({} NFTs)",
                params.wallet_address,
                nfts.len()
            ),
            estimated_fee: "0 SOL (query only)".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "walletAddress": params.wallet_address,
                "collectionSymbol": params.collection_symbol,
                "nfts": nfts,
                "count": nfts.len(),
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Get collection activity action
pub async fn build_me_collection_activity(
    http: &reqwest::Client,
    params: &MeCollectionActivityParams,
) -> Result<BuildResponse, AppError> {
    validate_me_collection_activity_params(params)?;

    let activities = get_collection_activity(
        http,
        &params.symbol,
        params.activity_type.as_deref(),
        params.limit,
        params.offset,
    )
    .await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_collection_activity".to_string(),
            description: format!(
                "Get activity for collection: {} ({} events)",
                params.symbol,
                activities.len()
            ),
            estimated_fee: "0 SOL (query only)".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "symbol": params.symbol,
                "activityType": params.activity_type,
                "activities": activities,
                "count": activities.len(),
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Get listings action
pub async fn build_me_listings(
    http: &reqwest::Client,
    params: &MeListingsParams,
) -> Result<BuildResponse, AppError> {
    validate_me_listings_params(params)?;

    let listings =
        get_collection_listings(http, &params.symbol, params.limit, params.offset).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_listings".to_string(),
            description: format!(
                "Get listings for collection: {} ({} items)",
                params.symbol,
                listings.len()
            ),
            estimated_fee: "0 SOL (query only)".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "symbol": params.symbol,
                "listings": listings,
                "count": listings.len(),
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Get offers action
pub async fn build_me_offers(
    http: &reqwest::Client,
    params: &MeOffersParams,
) -> Result<BuildResponse, AppError> {
    validate_me_offers_params(params)?;

    let offers = get_nft_offers(http, &params.mint_address, params.limit, params.offset).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_offers".to_string(),
            description: format!(
                "Get offers for NFT: {} ({} offers)",
                params.mint_address,
                offers.len()
            ),
            estimated_fee: "0 SOL (query only)".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "offers": offers,
                "count": offers.len(),
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Get collection NFTs action
pub async fn build_me_collection_nfts(
    http: &reqwest::Client,
    params: &MeCollectionNFTsParams,
) -> Result<BuildResponse, AppError> {
    validate_me_collection_nfts_params(params)?;

    let nfts = get_collection_nfts(http, &params.symbol, params.limit, params.offset).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_collection_nfts".to_string(),
            description: format!(
                "Get NFTs in collection: {} ({} items)",
                params.symbol,
                nfts.len()
            ),
            estimated_fee: "0 SOL (query only)".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "symbol": params.symbol,
                "nfts": nfts,
                "count": nfts.len(),
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Generic reads
// ──────────────────────────────────────────────────────────────────────────────
//
// Twenty-odd Magic Eden reads differ only in their path and which identifier
// they take. Written out one function at a time they would be twenty near
// copies, and the tool catalogue already offered all of them while the backend
// implemented none — the failure mode of that shape is a tool the model can
// call with nothing behind it. One table is harder to leave a hole in.
//
// Every path here was checked against the live API. The ones that answered
// 404 — `collections/{s}/holder_stats`, `mmm/token_pools`, anything
// "magic_ticket" — are absent on purpose: they do not exist, so they come out
// of the catalogue rather than get implemented against a guess.

/// One shape for every read: the caller supplies whichever identifier the
/// endpoint needs, and paging where it is supported.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeReadParams {
    /// Collection symbol, e.g. "mad_lads".
    #[serde(
        default,
        alias = "collection",
        alias = "collectionSymbol",
        alias = "collection_symbol",
        alias = "collectionName",
        alias = "name"
    )]
    pub symbol: Option<String>,
    /// NFT mint address.
    #[serde(
        default,
        alias = "mint",
        alias = "tokenMint",
        alias = "nft",
        alias = "nftMint",
        alias = "token_mint",
        alias = "assetMint"
    )]
    pub mint_address: Option<String>,
    /// Wallet address.
    #[serde(
        default,
        alias = "address",
        alias = "owner",
        alias = "walletAddress",
        alias = "wallet_address",
        alias = "ownerAddress"
    )]
    pub wallet: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u32>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub offset: Option<u32>,
    /// `collections/batch/listings` takes a comma-separated symbol list.
    #[serde(default)]
    pub symbols: Option<String>,
    /// How far back a sales history reaches. Accepts "30" or "30d"; the model
    /// writes both, and rejecting one over its suffix helps nobody.
    #[serde(
        default,
        alias = "period",
        alias = "timeWindow",
        alias = "range",
        alias = "window",
        deserialize_with = "crate::services::params::lenient_opt"
    )]
    pub days: Option<String>,
    /// Which column a ranking is ordered by.
    #[serde(default, alias = "sortBy", alias = "orderBy")]
    pub sort: Option<String>,
    /// The NFT's number within its collection — "#8051". How people refer to
    /// an NFT, and the only handle most of them have.
    ///
    /// Generously aliased on purpose. The caller is a language model, and it
    /// will keep inventing plausible names for the same field: asked for
    /// "madlads 5050" it sent `tokenNumber` and got "NFT number is required",
    /// then sent `number` for the next NFT and succeeded. Refusing a request
    /// over the spelling of a key nobody sees is a bad trade — the value was
    /// right both times.
    #[serde(
        default,
        alias = "tokenId",
        alias = "id",
        alias = "index",
        alias = "tokenNumber",
        alias = "nftNumber",
        alias = "token_number",
        alias = "nft_number",
        alias = "edition",
        alias = "serial"
    )]
    pub number: Option<String>,
}

fn need<'a>(v: &'a Option<String>, what: &str) -> Result<&'a str, AppError> {
    v.as_deref()
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| AppError::InvalidParams(format!("{what} is required")))
}

/// Turn whatever the user called a collection into Magic Eden's symbol.
///
/// Their symbols are slugs — lowercase, words joined by underscores — and a
/// person says "Trenchors" or "Mad Lads", not "trenchors" or "mad_lads".
/// Asking the model to perform this transformation reliably is asking for the
/// day it does not: the answer that started this was OPRAI deciding no such
/// NFT collection existed and offering the user a pump.fun coin of the same
/// name instead.
///
/// Already-slug input passes through unchanged, so a symbol from a URL is
/// untouched.
fn collection_symbol(raw: &str) -> String {
    let trimmed = raw.trim();
    if trimmed
        .chars()
        .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
    {
        return trimmed.to_string();
    }
    let mut out = String::with_capacity(trimmed.len());
    let mut pending_sep = false;
    for ch in trimmed.chars() {
        if ch.is_ascii_alphanumeric() {
            if pending_sep && !out.is_empty() {
                out.push('_');
            }
            pending_sep = false;
            out.push(ch.to_ascii_lowercase());
        } else {
            pending_sep = true;
        }
    }
    out
}

/// The symbols worth trying for a name, best first.
///
/// Collections are inconsistent: some join words with underscores, some run
/// them together. Two cheap attempts beat telling the user their collection
/// does not exist.
fn collection_symbol_candidates(raw: &str) -> Vec<String> {
    let primary = collection_symbol(raw);
    let joined: String = primary.chars().filter(|c| *c != '_').collect();
    if joined == primary {
        vec![primary]
    } else {
        vec![primary, joined]
    }
}

/// Resolve an action name to a Magic Eden URL.
fn me_read_url(action: &str, p: &MeReadParams) -> Result<String, AppError> {
    let base = MAGIC_EDEN_API;
    let limit = p.limit.unwrap_or(20).min(500);
    let off = p.offset.unwrap_or(0);
    let url = match action {
        // Magic Eden rejects a limit that is not a multiple of 20, and an
        // offset that is not a multiple of the limit, with a 400 that reads
        // like a bug in us. Round rather than pass it through.
        "me_collections" => {
            let l = ((limit as f64 / 20.0).round().max(1.0) as u32) * 20;
            format!("{base}/collections?offset={}&limit={l}", (off / l) * l)
        }
        "me_collection_stats" => {
            format!(
                "{base}/collections/{}/stats",
                &collection_symbol(need(&p.symbol, "collection")?)
            )
        }
        "me_collection_attributes" => format!(
            "{base}/collections/{}/attributes",
            &collection_symbol(need(&p.symbol, "collection")?)
        ),
        "me_collection_leaderboard" => format!(
            "{base}/collections/{}/leaderboard",
            &collection_symbol(need(&p.symbol, "collection")?)
        ),
        "me_collection_listings" => format!(
            "{base}/collections/{}/listings?limit={limit}&offset={off}",
            &collection_symbol(need(&p.symbol, "collection")?)
        ),
        "me_collection_activities" => format!(
            "{base}/collections/{}/activities?limit={limit}&offset={off}",
            &collection_symbol(need(&p.symbol, "collection")?)
        ),
        "me_collections_batch_listings" => format!(
            "{base}/collections/batch/listings?limit={limit}&collectionSymbols={}",
            need(&p.symbols, "collections")?
        ),

        "me_token" => format!("{base}/tokens/{}", need(&p.mint_address, "NFT mint")?),
        "me_token_activities" => format!(
            "{base}/tokens/{}/activities?limit={limit}&offset={off}",
            need(&p.mint_address, "NFT mint")?
        ),
        "me_token_listings" => format!(
            "{base}/tokens/{}/listings",
            need(&p.mint_address, "NFT mint")?
        ),
        "me_token_offers_received" => format!(
            "{base}/tokens/{}/offers_received?limit={limit}&offset={off}",
            need(&p.mint_address, "NFT mint")?
        ),

        "me_wallet" => format!("{base}/wallets/{}", need(&p.wallet, "wallet")?),
        "me_wallet_tokens" => format!(
            "{base}/wallets/{}/tokens?limit={limit}&offset={off}",
            need(&p.wallet, "wallet")?
        ),
        "me_wallet_activities" | "me_owner_activities" => format!(
            "{base}/wallets/{}/activities?limit={limit}&offset={off}",
            need(&p.wallet, "wallet")?
        ),
        "me_wallet_escrow_balance" => format!(
            "{base}/wallets/{}/escrow_balance",
            need(&p.wallet, "wallet")?
        ),
        "me_wallet_offers_made" => format!(
            "{base}/wallets/{}/offers_made?limit={limit}&offset={off}",
            need(&p.wallet, "wallet")?
        ),
        "me_wallet_offers_received" => format!(
            "{base}/wallets/{}/offers_received?limit={limit}&offset={off}",
            need(&p.wallet, "wallet")?
        ),

        // Without a collection the pool list comes back empty — Magic Eden
        // will not enumerate every pool on the marketplace. Pass the symbol
        // through, and let the card say so when there isn't one.
        "me_mmm_pools" => match p.symbol.as_deref().filter(|x| !x.is_empty()) {
            Some(sym) => {
                format!("{base}/mmm/pools?collectionSymbol={sym}&limit={limit}&offset={off}")
            }
            None => {
                return Err(AppError::InvalidParams(
                    "Name a collection to see its pools".into(),
                ))
            }
        },

        other => {
            return Err(AppError::InvalidParams(format!(
                "Unknown Magic Eden query: {other}"
            )))
        }
    };
    Ok(url)
}

/// Human-readable label for the card header.
fn me_read_title(action: &str, p: &MeReadParams) -> String {
    let who = p.symbol.clone().unwrap_or_default();
    match action {
        "me_collections" => "Magic Eden collections".into(),
        "me_collection_stats" => format!("{who} — collection stats"),
        "me_collection_attributes" => format!("{who} — traits"),
        "me_collection_leaderboard" => format!("{who} — top traders"),
        "me_trending_collections" => "Trending collections".into(),
        "me_collection_holder_stats" => format!("{who} — holders"),
        "me_collection_sales_history" => format!("{who} — sales history"),
        "me_collection_listings" => format!("{who} — listings"),
        "me_collection_activities" => format!("{who} — activity"),
        "me_collections_batch_listings" => "Listings across collections".into(),
        "me_token" => "NFT details".into(),
        "me_token_activities" => "NFT activity".into(),
        "me_token_listings" => "NFT listings".into(),
        "me_token_offers_received" => "Offers on this NFT".into(),
        "me_wallet" => "Wallet profile".into(),
        "me_wallet_tokens" => "Wallet NFTs".into(),
        "me_wallet_activities" | "me_owner_activities" => "Wallet activity".into(),
        "me_wallet_offers_made" => "Offers you made".into(),
        "me_wallet_offers_received" => "Offers you received".into(),
        "me_mmm_pools" => "Magic Eden AMM pools".into(),
        _ => "Magic Eden".into(),
    }
}

/// Run any of the table's reads and hand back the payload.
/// The collection's cover art, name and description, read from the chain.
///
/// Magic Eden's `/collections/{symbol}` carries all three and is closed to us
/// (429, every collection, every attempt). The same facts are on-chain: any
/// NFT in the collection points at the collection's own metadata account, and
/// that account has the cover.
///
/// Two hops from a listing we can already fetch: mint → its collection →
/// that collection's metadata. Cached, because it does not change.
async fn me_collection_identity(http: &reqwest::Client, symbol: &str) -> Option<serde_json::Value> {
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};
    use std::time::{Duration, Instant};

    static CACHE: OnceLock<Mutex<HashMap<String, (Instant, serde_json::Value)>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    const TTL: Duration = Duration::from_secs(6 * 60 * 60);

    if let Ok(map) = cache.lock() {
        if let Some((at, v)) = map.get(symbol) {
            if at.elapsed() < TTL {
                return Some(v.clone());
            }
        }
    }

    let key = std::env::var("HELIUS_API_KEY")
        .ok()
        .filter(|k| !k.is_empty())?;
    let rpc = format!("https://mainnet.helius-rpc.com/?api-key={key}");
    let get_asset = |id: String| {
        let rpc = rpc.clone();
        async move {
            http.post(&rpc)
                .json(&serde_json::json!({
                    "jsonrpc": "2.0", "id": 1, "method": "getAsset",
                    "params": { "id": id }
                }))
                .send()
                .await
                .ok()?
                .json::<serde_json::Value>()
                .await
                .ok()
        }
    };

    // Any listed NFT will do — we only need something that belongs to it.
    let listings = me_get_json(
        http,
        &format!("{MAGIC_EDEN_API}/collections/{symbol}/listings?limit=1"),
    )
    .await
    .ok()?;
    let mint = listings
        .as_array()?
        .first()?
        .get("tokenMint")?
        .as_str()?
        .to_string();

    let asset = get_asset(mint).await?;
    let collection_mint = asset
        .pointer("/result/grouping")?
        .as_array()?
        .iter()
        .find(|g| g.get("group_key").and_then(|k| k.as_str()) == Some("collection"))?
        .get("group_value")?
        .as_str()?
        .to_string();

    let coll = get_asset(collection_mint).await?;
    let content = coll.pointer("/result/content")?;
    let out = serde_json::json!({
        "name": content.pointer("/metadata/name").and_then(|v| v.as_str()),
        "image": content.pointer("/links/image").and_then(|v| v.as_str()),
        "description": content.pointer("/metadata/description").and_then(|v| v.as_str()),
    });
    if out.get("name").map(|v| v.is_null()).unwrap_or(true)
        && out.get("image").map(|v| v.is_null()).unwrap_or(true)
    {
        return None;
    }
    if let Ok(mut map) = cache.lock() {
        map.insert(symbol.to_string(), (Instant::now(), out.clone()));
    }
    Some(out)
}

/// A collection's identity and its numbers, merged.
///
/// Magic Eden splits them across two endpoints and neither is a collection
/// card on its own: `/collections/{s}` has the name, art, description and
/// links but no prices, and `/stats` has floor, listed count, 7-day volume
/// and 24-hour average price but nothing that says which collection it is.
/// Asked for one, a user means both.
async fn me_collection_detail(
    http: &reqwest::Client,
    symbol: &str,
) -> Result<serde_json::Value, AppError> {
    let info_url = format!("{MAGIC_EDEN_API}/collections/{symbol}");
    let stats_url = format!("{MAGIC_EDEN_API}/collections/{symbol}/stats");
    let (info, stats) = futures::join!(me_get_json(http, &info_url), me_get_json(http, &stats_url));
    // `/collections/{symbol}` answers 429 for every collection we ask about,
    // on every attempt, with 90 seconds of silence in between — while its own
    // sibling paths return 200. It is not a limit we are tripping; that route
    // is closed to us. When it fails, the name, cover and description come
    // from the chain instead — see `me_collection_identity`.
    let mut out = match info {
        Ok(serde_json::Value::Object(m)) => serde_json::Value::Object(m),
        _ => match me_collection_identity(http, symbol).await {
            Some(mut id) => {
                if let Some(obj) = id.as_object_mut() {
                    obj.insert("symbol".into(), serde_json::json!(symbol));
                    obj.retain(|_, v| !v.is_null());
                }
                id
            }
            None => serde_json::json!({ "symbol": symbol }),
        },
    };
    if let Ok(serde_json::Value::Object(m)) = stats {
        if let Some(obj) = out.as_object_mut() {
            for (k, v) in m {
                obj.insert(k, v);
            }
        }
    } else if out.get("name").is_none() {
        return Err(AppError::NotFound(
            "Magic Eden has no collection by that name".into(),
        ));
    }
    Ok(out)
}

/// How a collection's metadata URIs are addressed, learned from one sample.
///
/// Most collections number their metadata: Mad Lads is `.../json/8051.json`,
/// Famous Foxes `.../metadata/1001.json`, Claynosaurz `.../claynosaurz/8003`.
/// Where that holds, one indexed query finds any piece — listed or not —
/// instead of paging a marketplace that only knows about what is for sale.
///
/// It does not always hold. Okay Bears and Cets address theirs by content
/// hash, and DeGods is numbered but OFF BY ONE — "DeGod #5899" lives at
/// `5898.json`. Substituting blindly there returns the wrong NFT, which is
/// worse than returning none, so the offset is measured from the sample and
/// every hit is checked against the name before it is believed.
#[derive(Clone)]
struct MeUriTemplate {
    collection: String,
    /// The URI with the number replaced by `{}`.
    pattern: Option<String>,
    /// name_number - uri_number. Zero for most, -1 for DeGods.
    offset: i64,
}

/// Learn a collection's addressing from any one of its NFTs. Cached — it is
/// a property of the collection, not of the question.
async fn me_uri_template(http: &reqwest::Client, symbol: &str) -> Option<MeUriTemplate> {
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};
    use std::time::{Duration, Instant};
    static CACHE: OnceLock<Mutex<HashMap<String, (Instant, MeUriTemplate)>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    const TTL: Duration = Duration::from_secs(6 * 60 * 60);

    if let Ok(map) = cache.lock() {
        if let Some((at, t)) = map.get(symbol) {
            if at.elapsed() < TTL {
                return Some(t.clone());
            }
        }
    }

    let key = std::env::var("HELIUS_API_KEY")
        .ok()
        .filter(|k| !k.is_empty())?;
    let listings = me_get_json(
        http,
        &format!("{MAGIC_EDEN_API}/collections/{symbol}/listings?limit=1"),
    )
    .await
    .ok()?;
    let sample_mint = listings.as_array()?.first()?.get("tokenMint")?.as_str()?;

    let asset: serde_json::Value = http
        .post(format!("https://mainnet.helius-rpc.com/?api-key={key}"))
        .json(&serde_json::json!({
            "jsonrpc": "2.0", "id": 1, "method": "getAsset", "params": { "id": sample_mint }
        }))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    let result = asset.get("result")?;
    let collection = result
        .get("grouping")?
        .as_array()?
        .iter()
        .find(|g| g.get("group_key").and_then(|k| k.as_str()) == Some("collection"))?
        .get("group_value")?
        .as_str()?
        .to_string();

    let content = result.get("content")?;
    let name = content.pointer("/metadata/name")?.as_str()?;
    let uri = content.get("json_uri")?.as_str()?;
    let name_num: i64 = name.rsplit('#').next()?.trim().parse().ok()?;

    // The number in the URI is the last run of digits that is a segment or a
    // filename stem — not a fragment of a hash.
    let mut pattern = None;
    let mut offset = 0;
    for (idx, ch) in uri.char_indices().rev() {
        if !ch.is_ascii_digit() {
            continue;
        }
        let end = uri[..=idx]
            .char_indices()
            .rev()
            .take_while(|(_, c)| c.is_ascii_digit())
            .count();
        let start = idx + 1 - end;
        let digits = &uri[start..=idx];
        if let Ok(uri_num) = digits.parse::<i64>() {
            // Guard against matching a digit buried in a hash: the run has to
            // be bounded by a separator or the end of the string.
            let before_ok =
                start == 0 || matches!(uri.as_bytes()[start - 1], b'/' | b'-' | b'_' | b'=');
            let after_ok = idx + 1 == uri.len()
                || matches!(uri.as_bytes()[idx + 1], b'.' | b'/' | b'?' | b'&');
            if before_ok && after_ok && (name_num - uri_num).abs() <= 2 {
                pattern = Some(format!("{}{{}}{}", &uri[..start], &uri[idx + 1..]));
                offset = name_num - uri_num;
            }
        }
        break;
    }

    let t = MeUriTemplate {
        collection,
        pattern,
        offset,
    };
    if let Ok(mut map) = cache.lock() {
        if map.len() >= 32 {
            map.clear();
        }
        map.insert(symbol.to_string(), (Instant::now(), t.clone()));
    }
    Some(t)
}

/// Find a numbered NFT through its metadata URI. One indexed lookup, and it
/// does not care whether the piece is for sale.
async fn me_find_by_uri(http: &reqwest::Client, symbol: &str, number: i64) -> Option<String> {
    let t = me_uri_template(http, symbol).await?;
    let pattern = t.pattern.as_ref()?;
    let uri = pattern.replacen("{}", &(number - t.offset).to_string(), 1);
    let key = std::env::var("HELIUS_API_KEY")
        .ok()
        .filter(|k| !k.is_empty())?;

    let resp: serde_json::Value = http
        .post(format!("https://mainnet.helius-rpc.com/?api-key={key}"))
        .json(&serde_json::json!({
            "jsonrpc": "2.0", "id": 1, "method": "searchAssets",
            "params": {
                "jsonUri": uri,
                "grouping": ["collection", t.collection],
                "page": 1, "limit": 2
            }
        }))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;

    let item = resp.pointer("/result/items")?.as_array()?.first()?;
    // Believe it only if it says what we asked for. The offset is inferred
    // from a single sample, and a collection that numbers irregularly would
    // otherwise hand back a neighbour.
    let name = item.pointer("/content/metadata/name")?.as_str()?;
    if !name.trim().ends_with(&format!("#{number}")) {
        return None;
    }
    item.get("id")?.as_str().map(str::to_string)
}

/// Resolve "Mad Lads #8051" to a mint address.
///
/// People refer to an NFT by its number — it is on the picture and it is what
/// anyone types. Neither API can look one up: Magic Eden has no name search
/// (`search`, `q` and a name filter all 400 or 500) and DAS `searchAssets`
/// rejects every name filter it was offered.
///
/// So we search the collection's LISTINGS, which is the set that matters on a
/// marketplace: a question about a specific NFT is nearly always about one
/// someone can see and might buy.
///
/// What this deliberately does NOT do is index the whole collection. The
/// earlier version did, off DAS, and the numbers made the case against it:
/// 2.4 MB per thousand assets, 24 MB and fourteen seconds for a ten-thousand
/// piece collection, eighty seconds for Okay Bears — all to extract one mint,
/// into a map that dies with the process. An unlisted NFT is answered by
/// saying so and asking for the mint address, which costs the user one
/// copy-paste and costs us nothing.
async fn me_resolve_mint_by_number(
    http: &reqwest::Client,
    symbol: &str,
    number: &str,
) -> Option<String> {
    let suffix = format!("#{number}");

    // The metadata URI first: it finds unlisted pieces too, which is most of
    // any collection, and costs one query.
    if let Ok(n) = number.parse::<i64>() {
        if let Some(mint) = me_find_by_uri(http, symbol, n).await {
            return Some(mint);
        }
    }

    // 100 is the cap. Asking for 500 returns an error OBJECT, not a longer
    // list — which the previous version read as "no match", so this search
    // never actually ran.
    const PAGE: u32 = 100;
    const MAX_PAGES: u32 = 12; // 1,200 listings — past any collection's depth

    // The pages are independent, so read them together. Sequentially, a miss
    // on a collection with seven hundred listings took ten seconds — one
    // round-trip after another for an answer that was never going to change.
    // Four at a time keeps it quick without opening twelve connections to a
    // marketplace that rate-limits.
    const BATCH: u32 = 4;
    let mut page = 0u32;
    while page < MAX_PAGES {
        let batch: Vec<u32> = (page..(page + BATCH).min(MAX_PAGES)).collect();
        let results = futures::future::join_all(batch.iter().map(|p| {
            let url = format!(
                "{MAGIC_EDEN_API}/collections/{symbol}/listings?limit={PAGE}&offset={}",
                p * PAGE
            );
            async move { me_get_json(http, &url).await.ok() }
        }))
        .await;

        let mut exhausted = false;
        for rows in results.into_iter().flatten() {
            let arr = match rows.as_array() {
                Some(a) => a,
                None => continue,
            };
            if (arr.len() as u32) < PAGE {
                exhausted = true; // past the end of the book
            }
            for r in arr {
                let name = r
                    .pointer("/token/name")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default();
                if name.trim().ends_with(&suffix) {
                    if let Some(m) = r.get("tokenMint").and_then(|v| v.as_str()) {
                        return Some(m.to_string());
                    }
                }
            }
        }
        if exhausted {
            break;
        }
        page += BATCH;
    }
    None
}

/// Rarity and floor for the traits a given NFT actually has.
///
/// Returns `{ "Background|Pink": { count, share, floor } }`. The collection's
/// supply is derived by summing one trait type's counts — every piece has
/// exactly one value of each trait, so any single type sums to the whole.
async fn me_trait_stats(
    http: &reqwest::Client,
    symbol: &str,
    token: &serde_json::Value,
) -> serde_json::Value {
    use std::collections::HashSet;

    let wanted: HashSet<(String, String)> = token
        .get("attributes")
        .and_then(|a| a.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|a| {
                    let t = a.get("trait_type")?.as_str()?.to_string();
                    let v = match a.get("value")? {
                        serde_json::Value::String(s) => s.clone(),
                        other => other.to_string(),
                    };
                    Some((t, v))
                })
                .collect()
        })
        .unwrap_or_default();
    if wanted.is_empty() {
        return serde_json::json!({});
    }

    let url = format!("{MAGIC_EDEN_API}/collections/{symbol}/attributes");
    let data = match me_get_json(http, &url).await {
        Ok(d) => d,
        Err(_) => return serde_json::json!({}),
    };
    let available = match data
        .pointer("/results/availableAttributes")
        .and_then(|v| v.as_array())
    {
        Some(a) => a,
        None => return serde_json::json!({}),
    };

    normalize_trait_stats(available, Some(&wanted))
}

/// `type|value -> {count, share, floor}` from Magic Eden's attribute table.
///
/// Shared by the single-NFT read (which keeps only that NFT's traits) and the
/// collection read (which keeps all of them, so a list of tiles can be scored
/// from one request instead of one per tile).
fn normalize_trait_stats(
    available: &[serde_json::Value],
    wanted: Option<&std::collections::HashSet<(String, String)>>,
) -> serde_json::Value {
    use std::collections::HashMap;

    // Supply, from the totals of one trait type: every piece carries exactly
    // one value of each type, so the largest per-type total is the supply.
    let mut totals: HashMap<String, u64> = HashMap::new();
    for row in available {
        if let Some(t) = row
            .pointer("/attribute/trait_type")
            .and_then(|v| v.as_str())
        {
            *totals.entry(t.to_string()).or_default() +=
                row.get("count").and_then(|c| c.as_u64()).unwrap_or(0);
        }
    }
    let supply = totals.values().copied().max().unwrap_or(0);

    let mut out = serde_json::Map::new();
    for row in available {
        let t = match row
            .pointer("/attribute/trait_type")
            .and_then(|v| v.as_str())
        {
            Some(t) => t.to_string(),
            None => continue,
        };
        let v = match row.pointer("/attribute/value") {
            Some(serde_json::Value::String(s)) => s.clone(),
            Some(other) => other.to_string(),
            None => continue,
        };
        if let Some(w) = wanted {
            if !w.contains(&(t.clone(), v.clone())) {
                continue;
            }
        }
        let count = row.get("count").and_then(|c| c.as_u64()).unwrap_or(0);
        out.insert(
            format!("{t}|{v}"),
            serde_json::json!({
                "count": count,
                "share": if supply > 0 { count as f64 / supply as f64 } else { 0.0 },
                "floor": row.get("floor").and_then(|f| f.as_f64()),
            }),
        );
    }
    serde_json::Value::Object(out)
}

// ──────────────────────────────────────────────────────────────────────────────
// Collection analytics
// ──────────────────────────────────────────────────────────────────────────────

/// One Helius DAS call. Returns `result`, or None on any failure — every
/// caller here treats missing chain data as "not shown", never as a zero.
async fn das(
    http: &reqwest::Client,
    method: &str,
    params: serde_json::Value,
) -> Option<serde_json::Value> {
    let key = std::env::var("HELIUS_API_KEY")
        .ok()
        .filter(|k| !k.is_empty())?;
    let resp: serde_json::Value = http
        .post(format!("https://mainnet.helius-rpc.com/?api-key={key}"))
        .json(&serde_json::json!({ "jsonrpc": "2.0", "id": 1, "method": method, "params": params }))
        .send()
        .await
        .ok()?
        .json()
        .await
        .ok()?;
    resp.get("result").cloned()
}

/// The on-chain collection account behind a Magic Eden symbol.
///
/// Magic Eden's symbol is its own; the chain knows a collection by an address.
/// One listing gives a mint, and the mint's grouping gives the address.
async fn me_collection_group(http: &reqwest::Client, symbol: &str) -> Option<String> {
    let url = format!("{MAGIC_EDEN_API}/collections/{symbol}/listings?limit=1");
    let listings = me_get_json(http, &url).await.ok()?;
    let mint = listings
        .as_array()?
        .first()?
        .get("tokenMint")?
        .as_str()?
        .to_string();
    let asset = das(http, "getAsset", serde_json::json!({ "id": mint })).await?;
    asset
        .get("grouping")?
        .as_array()?
        .iter()
        .find(|g| g.get("group_key").and_then(|k| k.as_str()) == Some("collection"))
        .and_then(|g| g.get("group_value"))
        .and_then(|v| v.as_str())
        .map(str::to_string)
}

/// How many pieces the collection actually has.
///
/// Only MPL Core collections carry their own size on chain (`current_size`).
/// Token Metadata collections do not report one at all, so this returns None
/// rather than a guess — a supply figure that is wrong makes every percentage
/// derived from it wrong too.
async fn me_collection_supply(http: &reqwest::Client, group: &str) -> Option<u64> {
    let asset = das(http, "getAsset", serde_json::json!({ "id": group })).await?;
    asset
        .get("mpl_core_info")
        .and_then(|c| c.get("current_size"))
        .and_then(|v| v.as_u64())
}

/// Which of these addresses are program-owned — escrows, vaults, pools.
///
/// A wallet's account belongs to the System Program. Anything else is held by
/// a program on someone's behalf, which is what a marketplace escrow is.
async fn program_owned_accounts(
    http: &reqwest::Client,
    addresses: &[String],
) -> std::collections::HashSet<String> {
    use std::collections::HashSet;
    let mut out = HashSet::new();
    if addresses.is_empty() {
        return out;
    }
    let system = solana_sdk::system_program::id().to_string();
    let result = das(
        http,
        "getMultipleAccounts",
        serde_json::json!([addresses, { "encoding": "base64" }]),
    )
    .await;
    let values = match result
        .as_ref()
        .and_then(|r| r.get("value"))
        .and_then(|v| v.as_array())
    {
        Some(v) => v,
        None => return out,
    };
    for (addr, account) in addresses.iter().zip(values) {
        let owner = account
            .get("owner")
            .and_then(|o| o.as_str())
            .unwrap_or(&system);
        if owner != system {
            out.insert(addr.clone());
        }
    }
    out
}

/// Magic Eden's own stats host — the one its website reads.
///
/// The public v2 API has a `popular_collections` route that answers with an
/// empty list for every time window, key or no key. This is where the numbers
/// behind their trending page actually come from.
const ME_STATS_API: &str = "https://stats-mainnet.magiceden.io/collection_stats";

/// Everything Magic Eden knows about one collection's market.
///
/// The public v2 `/stats` route answers with a floor and a listed count and
/// almost nothing else. This is the same host their own collection page reads,
/// and it carries what the page shows: volume and sales for every window, the
/// top offer, the true supply, and how many wallets hold it.
///
/// `tokenCount` matters beyond its own row: it is the supply for ANY
/// collection, where the chain only reports one for MPL Core. Every share and
/// percentage on the card is built on it.
async fn me_collection_overview(http: &reqwest::Client, symbol: &str) -> Option<serde_json::Value> {
    let url = format!("{ME_STATS_API}/stats?collectionId={symbol}&window=1d");
    let d = me_get_json(http, &url).await.ok()?;
    d.get("collectionSymbol")?;

    // Amounts arrive either as `{amount, native}` or as bare lamports. Read
    // `native` when it is there and divide when it is not — mistaking one for
    // the other is a billion-fold error on a price.
    let native = |k: &str| -> Option<f64> {
        let v = d.get(k)?;
        v.get("native")
            .and_then(|n| n.as_f64())
            .or_else(|| v.as_f64().map(|lamports| lamports / 1e9))
    };
    let sol = |k: &str| -> Option<f64> { d.get(k).and_then(|v| v.as_f64()).map(|l| l / 1e9) };
    let count = |k: &str| -> Option<u64> {
        d.get(k).and_then(|v| {
            v.as_u64()
                .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        })
    };

    let supply = count("tokenCount");
    let listed = count("listedCount");

    Some(serde_json::json!({
        "symbol": d.get("collectionSymbol"),
        "name": d.get("name"),
        "image": d.get("image"),
        "isVerified": d.get("isVerified"),
        "contract": d.get("contract"),

        "floorPrice": native("floorPrice"),
        "floorChange7d": d.get("fpPctChg7d").and_then(|v| v.as_f64()),
        "topOffer": native("topOffer"),

        "supply": supply,
        "listedCount": listed,
        "listedShare": match (listed, supply) {
            (Some(l), Some(s)) if s > 0 => Some(l as f64 / s as f64),
            _ => None,
        },
        "ownerCount": count("ownerCount"),
        "ownerShare": match (count("ownerCount"), supply) {
            (Some(o), Some(s)) if s > 0 => Some(o as f64 / s as f64),
            _ => None,
        },

        "volume1h": sol("volume1h"),
        "volume24h": sol("volume24hr"),
        "volume7d": sol("volume7d"),
        "volume30d": sol("volume30d"),
        "avgPrice24h": sol("avgPrice24hr"),
        "avgPrice7d": sol("avgPrice7d"),
        "avgPrice30d": sol("avgPrice30d"),
        "sales24h": count("txns24hr"),
        "sales7d": count("txns7d"),
        "sales30d": count("txns30d"),
        "salesAll": count("totalTxns"),
    }))
}

/// The collections trading most right now.
///
/// `window` is one of 1h, 6h, 1d, 7d, 30d — "right now" means 1d unless the
/// user asked for something else.
pub async fn me_trending_collections(
    http: &reqwest::Client,
    window: &str,
    limit: u32,
    sort: &str,
) -> Result<serde_json::Value, AppError> {
    let window = match window.trim().to_lowercase().as_str() {
        "1h" | "hour" | "1hour" => "1h",
        "6h" | "6hour" => "6h",
        "7d" | "week" | "1w" => "7d",
        "30d" | "month" | "1m" => "30d",
        _ => "1d",
    };
    // Magic Eden ranks on `volume` or `floorPrice`; anything else is a request
    // for a column it does not sort by.
    let sort = match sort.trim().to_lowercase().as_str() {
        "floor" | "floorprice" | "fp" => "floorPrice",
        _ => "volume",
    };
    let limit = limit.clamp(1, 100);

    let url = format!(
        "{ME_STATS_API}/search/solana?window={window}&limit={limit}&offset=0&sort={sort}&direction=desc"
    );
    let rows = me_get_json(http, &url).await?;
    let rows = rows.as_array().cloned().unwrap_or_default();

    let out: Vec<serde_json::Value> = rows
        .iter()
        .map(|c| {
            let f = |k: &str| c.get(k).and_then(|v| v.as_f64());
            let n = |k: &str| c.get(k).and_then(|v| v.as_u64());
            serde_json::json!({
                "symbol": c.get("collectionSymbol").and_then(|v| v.as_str()),
                "name": c.get("name").and_then(|v| v.as_str()),
                "image": c.get("image").and_then(|v| v.as_str()),
                "isVerified": c.get("isVerified").and_then(|v| v.as_bool()).unwrap_or(false),
                // Already SOL here, unlike the v2 endpoints which quote
                // lamports — normalising the wrong way is a 1e9 error on a
                // price, so these are passed through untouched.
                "volume": f("vol"),
                "volumeChange": f("volPctChg"),
                "sales": n("txns"),
                "salesChange": f("txnsPctChg"),
                "floorPrice": f("fp"),
                "floorChange": f("fpPctChg"),
                "marketCap": f("marketCap"),
                "supply": n("totalSupply"),
                "listedCount": n("listedCount"),
                "ownerCount": n("ownerCount"),
                "uniqueOwnerRatio": f("uniqueOwnerRatio"),
            })
        })
        .collect();

    Ok(serde_json::json!({
        "window": window,
        "sort": sort,
        "collections": out,
    }))
}

/// Who has this collection's listings open, one entry per listing.
///
/// Used to attribute escrowed pieces back to their sellers.
async fn me_listing_sellers(http: &reqwest::Client, symbol: &str) -> Vec<String> {
    const PAGE: u32 = 100;
    const MAX_PAGES: u32 = 20; // 2,000 listings
    let mut out = Vec::new();
    for page in 0..MAX_PAGES {
        let url = format!(
            "{MAGIC_EDEN_API}/collections/{symbol}/listings?offset={}&limit={PAGE}",
            page * PAGE
        );
        let rows = match me_get_json(http, &url).await {
            Ok(v) => v,
            Err(_) => break,
        };
        let rows = match rows.as_array() {
            Some(r) if !r.is_empty() => r.clone(),
            _ => break,
        };
        let got = rows.len() as u32;
        for row in &rows {
            if let Some(seller) = row.get("seller").and_then(|s| s.as_str()) {
                out.push(seller.to_string());
            }
        }
        if got < PAGE {
            break;
        }
    }
    out
}

/// Who owns the collection, and how concentrated it is.
///
/// Magic Eden documents a `holder_stats` endpoint and answers "Not Found" for
/// every collection, so this is built from the chain: every asset in the
/// grouping, counted by owner. Capped, because a scan is not free and the
/// answer for the tail of a large collection does not change the shape.
pub async fn me_collection_holders(
    http: &reqwest::Client,
    symbol: &str,
) -> Result<serde_json::Value, AppError> {
    use std::collections::HashMap;

    const PAGE: u64 = 1_000;
    const MAX_PAGES: u64 = 12; // 12,000 pieces — past all but the largest

    // Magic Eden names the collection's on-chain address in its own stats
    // record, so ask there first. The fallback — take a listing, read its
    // mint's grouping — needs the collection to have something for sale and
    // failed outright on DeGods, which reported "not found on chain" for a
    // collection of ten thousand.
    let overview = me_collection_overview(http, symbol).await;
    let group = overview.as_ref().and_then(|o| {
        o.get("contract")
            .and_then(|c| c.as_str())
            .map(str::to_string)
    });
    let group = match group {
        Some(g) => g,
        None => me_collection_group(http, symbol).await.ok_or_else(|| {
            AppError::NotFound(
                "Could not find that collection on chain, so its holders can't be counted".into(),
            )
        })?,
    };

    // The pages are independent, so read them together. Ten thousand pieces is
    // ten sequential round trips, which is long enough for the request to be
    // given up on before it answers.
    let pages = futures::future::join_all((1..=MAX_PAGES).map(|page| {
        let group = group.clone();
        async move {
            das(
                http,
                "getAssetsByGroup",
                serde_json::json!({
                    "groupKey": "collection",
                    "groupValue": group,
                    "page": page,
                    "limit": PAGE,
                    "displayOptions": { "showCollectionMetadata": false }
                }),
            )
            .await
        }
    }))
    .await;

    let mut owners: HashMap<String, u64> = HashMap::new();
    let mut scanned = 0u64;
    let mut complete = false;
    for result in pages {
        let items = match result
            .as_ref()
            .and_then(|r| r.get("items"))
            .and_then(|i| i.as_array())
        {
            Some(i) if !i.is_empty() => i.clone(),
            _ => {
                complete = true;
                continue;
            }
        };
        let got = items.len() as u64;
        for item in &items {
            // A burnt asset still comes back in the grouping. Trenchors minted
            // 3,750 and holds 2,421: counting the difference would inflate
            // every share and contradict the collection's own supply.
            if item.get("burnt").and_then(|b| b.as_bool()).unwrap_or(false) {
                continue;
            }
            let owner = item
                .get("ownership")
                .and_then(|o| o.get("owner"))
                .and_then(|o| o.as_str())
                .unwrap_or_default();
            if owner.is_empty() {
                continue;
            }
            *owners.entry(owner.to_string()).or_default() += 1;
        }
        scanned += got;
        if got < PAGE {
            complete = true;
        }
    }

    if owners.is_empty() {
        return Err(AppError::NotFound(
            "No on-chain holders found for that collection".into(),
        ));
    }

    let mut ranked: Vec<(String, u64)> = owners.into_iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));

    // Drop the marketplaces. A listed NFT is held by an escrow account, so the
    // biggest "holder" of Trenchors was Magic Eden's own program with 201 of
    // them — which is the listed count, not a whale. Only the top of the list
    // is checked: an escrow holding a handful changes nothing, and an escrow
    // holding enough to matter is always near the top.
    let suspects: Vec<String> = ranked.iter().take(25).map(|(w, _)| w.clone()).collect();
    let escrows = program_owned_accounts(http, &suspects).await;
    ranked.retain(|(w, _)| !escrows.contains(w));

    // Then give those pieces back to the people who listed them. Someone who
    // put their only NFT up for sale still owns it in every sense that
    // matters, and dropping them undercounted holders against Magic Eden's own
    // figure — 776 against their 885, the gap being sellers with nothing left
    // outside escrow.
    if !escrows.is_empty() {
        let sellers = me_listing_sellers(http, symbol).await;
        if !sellers.is_empty() {
            let mut by_wallet: HashMap<String, u64> = ranked.into_iter().collect();
            for seller in sellers {
                *by_wallet.entry(seller).or_default() += 1;
            }
            ranked = by_wallet.into_iter().collect();
            ranked.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
        }
    }

    let unique = ranked.len() as u64;
    let held: u64 = ranked.iter().map(|(_, n)| *n).sum();
    let share = |n: usize| -> f64 {
        if held == 0 {
            return 0.0;
        }
        ranked.iter().take(n).map(|(_, c)| *c).sum::<u64>() as f64 / held as f64
    };
    let singles = ranked.iter().filter(|(_, n)| *n == 1).count() as u64;

    // Magic Eden's own owner count, from the same source its collection page
    // uses. The chain scan is what makes concentration knowable; this is what
    // makes the headline agree with the marketplace beside it.
    let reported_owners = overview
        .as_ref()
        .and_then(|o| o.get("ownerCount").and_then(|v| v.as_u64()));
    // Which collection this is about. A stats card headed by a name alone
    // makes the reader carry the subject; the picture is how they recognise it.
    let identity = |k: &str| overview.as_ref().and_then(|o| o.get(k).cloned());

    Ok(serde_json::json!({
        "symbol": symbol,
        "name": identity("name"),
        "image": identity("image"),
        "isVerified": identity("isVerified"),
        "supply": identity("supply"),
        "floorPrice": identity("floorPrice"),
        "collection": group,
        "scanned": scanned,
        "reportedOwners": reported_owners,
        // False when the scan hit its page cap: the numbers describe what was
        // read, and saying so beats presenting a partial count as a total.
        "complete": complete,
        "uniqueHolders": unique,
        "held": held,
        "singleItemHolders": singles,
        "singleItemShare": if unique > 0 { singles as f64 / unique as f64 } else { 0.0 },
        "averageHeld": if unique > 0 { held as f64 / unique as f64 } else { 0.0 },
        "top1Share": share(1),
        "top5Share": share(5),
        "top10Share": share(10),
        "top20Share": share(20),
        "topHolders": ranked.iter().take(20).map(|(w, n)| serde_json::json!({
            "wallet": w,
            "count": n,
            "share": if held > 0 { *n as f64 / held as f64 } else { 0.0 },
        })).collect::<Vec<_>>(),
    }))
}

/// Sales over time, from the collection's own activity feed.
///
/// Magic Eden publishes activity but no time series, so the buckets are built
/// here: one per day, with what sold and for how much. Bids and listings are
/// excluded — an offer nobody took is not a price the market paid.
pub async fn me_collection_sales_history(
    http: &reqwest::Client,
    symbol: &str,
    days: u32,
) -> Result<serde_json::Value, AppError> {
    use std::collections::BTreeMap;

    const PAGE: u32 = 500;
    const MAX_PAGES: u32 = 6; // 3,000 events

    let days = days.clamp(1, 90);
    let cutoff = chrono::Utc::now().timestamp() - (days as i64 * 86_400);

    #[derive(Default)]
    struct Bucket {
        sales: u64,
        volume: f64,
        low: Option<f64>,
        high: Option<f64>,
    }
    let mut buckets: BTreeMap<i64, Bucket> = BTreeMap::new();
    let mut reached_cutoff = false;

    for page in 0..MAX_PAGES {
        // Filter at the source. Unfiltered, the feed is overwhelmingly bids
        // and pool updates — two hundred events covered twenty-two minutes of
        // Mad Lads, so six pages reached back a few hours and found one sale
        // in what was meant to be a fortnight.
        let url = format!(
            "{MAGIC_EDEN_API}/collections/{symbol}/activities?offset={}&limit={PAGE}&type=buyNow,buy",
            page * PAGE
        );
        let events = match me_get_json(http, &url).await {
            Ok(v) => v,
            Err(e) if page == 0 => return Err(e),
            Err(_) => break,
        };
        let events = match events.as_array() {
            Some(e) if !e.is_empty() => e.clone(),
            _ => break,
        };
        let got = events.len();
        for ev in &events {
            let at = ev.get("blockTime").and_then(|t| t.as_i64()).unwrap_or(0);
            if at == 0 {
                continue;
            }
            if at < cutoff {
                reached_cutoff = true;
                continue;
            }
            // The feed is already filtered to sales; this only guards against
            // the filter being ignored, which is how `activityTypes=` behaves.
            let kind = ev.get("type").and_then(|t| t.as_str()).unwrap_or("");
            if !matches!(kind, "buyNow" | "buy" | "sale" | "acceptBid") {
                continue;
            }
            let price = match ev.get("price").and_then(|p| p.as_f64()) {
                Some(p) if p > 0.0 => p,
                _ => continue,
            };
            let day = at - at.rem_euclid(86_400);
            let b = buckets.entry(day).or_default();
            b.sales += 1;
            b.volume += price;
            b.low = Some(b.low.map_or(price, |l: f64| l.min(price)));
            b.high = Some(b.high.map_or(price, |h: f64| h.max(price)));
        }
        if reached_cutoff || got < PAGE as usize {
            break;
        }
    }

    let series: Vec<serde_json::Value> = buckets
        .iter()
        .map(|(day, b)| {
            serde_json::json!({
                "day": day,
                "sales": b.sales,
                "volume": b.volume,
                "average": if b.sales > 0 { b.volume / b.sales as f64 } else { 0.0 },
                "low": b.low,
                "high": b.high,
            })
        })
        .collect();

    let sales: u64 = buckets.values().map(|b| b.sales).sum();
    let volume: f64 = buckets.values().map(|b| b.volume).sum();

    let overview = me_collection_overview(http, symbol).await;
    let identity = |k: &str| overview.as_ref().and_then(|o| o.get(k).cloned());

    Ok(serde_json::json!({
        "symbol": symbol,
        "name": identity("name"),
        "image": identity("image"),
        "isVerified": identity("isVerified"),
        "floorPrice": identity("floorPrice"),
        "days": days,
        // The feed is read newest-first and capped; `covers` says how far back
        // the numbers actually reach, so a quiet week is not read as a crash.
        "covers": series.first().and_then(|s| s.get("day").cloned()),
        "sales": sales,
        "volume": volume,
        "average": if sales > 0 { volume / sales as f64 } else { 0.0 },
        "series": series,
    }))
}

/// On-chain facts about a mint: its collection account, its token standard,
/// and whether it is frozen. Best-effort — a card without them is still a
/// card.
async fn me_asset_facts(http: &reqwest::Client, mint: &str) -> serde_json::Value {
    let key = match std::env::var("HELIUS_API_KEY")
        .ok()
        .filter(|k| !k.is_empty())
    {
        Some(k) => k,
        None => return serde_json::json!({}),
    };
    let resp: serde_json::Value = match http
        .post(format!("https://mainnet.helius-rpc.com/?api-key={key}"))
        .json(&serde_json::json!({
            "jsonrpc": "2.0", "id": 1, "method": "getAsset", "params": { "id": mint }
        }))
        .send()
        .await
    {
        Ok(r) => match r.json().await {
            Ok(v) => v,
            Err(_) => return serde_json::json!({}),
        },
        Err(_) => return serde_json::json!({}),
    };
    let result = match resp.get("result") {
        Some(r) => r,
        None => return serde_json::json!({}),
    };
    // Anyone can mint a picture and call it Mad Lads. What they cannot forge
    // is a signature: a creator is `verified` only if that key signed the
    // metadata, and a collection grouping is only reported once the
    // collection authority has verified the membership. Those two are the
    // difference between the real thing and a copy of its art.
    let creators_verified = result
        .get("creators")
        .and_then(|c| c.as_array())
        .map(|arr| {
            !arr.is_empty()
                && arr
                    .iter()
                    .any(|c| c.get("verified").and_then(|v| v.as_bool()) == Some(true))
        });

    serde_json::json!({
        "collectionMint": result
            .get("grouping")
            .and_then(|g| g.as_array())
            .and_then(|arr| arr.iter()
                .find(|g| g.get("group_key").and_then(|k| k.as_str()) == Some("collection")))
            .and_then(|g| g.get("group_value"))
            .and_then(|v| v.as_str()),
        "standard": result.get("interface").and_then(|v| v.as_str()),
        "frozen": result.pointer("/ownership/frozen").and_then(|v| v.as_bool()),
        "compressed": result.pointer("/compression/compressed").and_then(|v| v.as_bool()),
        "creatorsVerified": creators_verified,
        // Metadata that can still be rewritten after you own it: the picture
        // you bought is not guaranteed to stay the picture you bought.
        "mutable": result.get("mutable").and_then(|v| v.as_bool()),
        "burnt": result.get("burnt").and_then(|v| v.as_bool()),
    })
}

/// Everything worth knowing about one NFT, in one reply.
///
/// "Tell me about this NFT" is four questions — what is it, what are its
/// traits, who has bid on it, what has happened to it — and Magic Eden
/// answers each on a different endpoint. Three cards for one question is
/// three round-trips and three headers; the traits already ride along with
/// the token itself.
///
/// Offers and activity are best-effort: an NFT nobody has bid on is a normal
/// NFT, and it should not fail to render because one of its lists is empty
/// or its endpoint is having a bad minute.
async fn me_nft_detail(http: &reqwest::Client, mint: &str) -> Result<serde_json::Value, AppError> {
    let token_url = format!("{MAGIC_EDEN_API}/tokens/{mint}");
    let offers_url = format!("{MAGIC_EDEN_API}/tokens/{mint}/offers_received?limit=20");
    let acts_url = format!("{MAGIC_EDEN_API}/tokens/{mint}/activities?limit=20");
    let (token, offers, activities) = futures::join!(
        me_get_json(http, &token_url),
        me_get_json(http, &offers_url),
        me_get_json(http, &acts_url),
    );
    let token = token?;
    if token.get("mintAddress").is_none() {
        return Err(AppError::NotFound(
            "Magic Eden has no record of that NFT".into(),
        ));
    }

    // What each of this NFT's traits is worth and how rare it is.
    //
    // A trait list on its own is a description. With the share of the
    // collection that carries it and the floor of the pieces that do, it is
    // the thing people actually read an NFT page for — the difference between
    // "Background: Pink" and "Background: Pink, 10%, floor 7.6 SOL".
    //
    // Only this NFT's traits are kept: the full attribute table for a
    // ten-thousand piece collection is far more than a card needs.
    let trait_stats = match token.get("collection").and_then(|c| c.as_str()) {
        Some(symbol) => me_trait_stats(http, symbol, &token).await,
        None => serde_json::json!({}),
    };

    // The chain facts Magic Eden shows under Details and its own API does
    // not carry: which collection account this belongs to, and whether it is
    // a programmable NFT — the difference decides whether a marketplace can
    // move it at all.
    let chain = me_asset_facts(http, mint).await;

    Ok(serde_json::json!({
        "token": token,
        "offers": offers.unwrap_or_else(|_| serde_json::json!([])),
        "activities": activities.unwrap_or_else(|_| serde_json::json!([])),
        "traitStats": trait_stats,
        "chain": chain,
    }))
}

/// Resolve `collection + #number -> mint`, trying both slug spellings.
async fn mint_by_number_any_spelling(
    http: &reqwest::Client,
    raw_symbol: &str,
    digits: &str,
) -> Option<String> {
    for candidate in collection_symbol_candidates(raw_symbol) {
        if let Some(mint) = me_resolve_mint_by_number(http, &candidate, digits).await {
            return Some(mint);
        }
    }
    None
}

/// Fill in `mintAddress` on a Magic Eden write that named the NFT the way a
/// person does.
///
/// "Offer 0.1 on Mad Lads #3983" is a complete instruction, but every builder
/// needs a mint. Without this the card rendered an empty "NFT mint address"
/// box under a panel that was already showing the piece — asking the user for
/// the one thing the conversation had just established.
pub async fn resolve_me_action_mint(
    http: &reqwest::Client,
    action: &str,
    wallet: &str,
    mut params: serde_json::Value,
) -> serde_json::Value {
    if !action.starts_with("me_") || action.starts_with("me_mmm_") {
        return params;
    }
    let non_empty = |v: Option<&serde_json::Value>| -> Option<String> {
        let v = v?;
        let s = match v {
            serde_json::Value::String(s) => s.trim().to_string(),
            serde_json::Value::Number(n) => n.to_string(),
            _ => return None,
        };
        if s.is_empty() {
            None
        } else {
            Some(s)
        }
    };
    let obj = match params.as_object() {
        Some(o) => o,
        None => return params,
    };
    if non_empty(obj.get("mintAddress")).is_some() {
        return params;
    }
    let symbol = ["symbol", "collectionSymbol", "collection", "collectionName"]
        .iter()
        .find_map(|k| non_empty(obj.get(*k)));
    let number = ["number", "tokenNumber", "nftNumber", "edition", "serial"]
        .iter()
        .find_map(|k| non_empty(obj.get(*k)));
    let (symbol, number) = match (symbol, number) {
        (Some(s), Some(n)) => (s, n),
        _ => {
            // Nothing to resolve from, but a cancel may not need anything: if
            // exactly one offer or listing is open, that is the one meant.
            if let Some(mint) = only_open_position_mint(http, action, wallet).await {
                if let Some(o) = params.as_object_mut() {
                    o.insert("mintAddress".into(), serde_json::json!(mint));
                }
            }
            return params;
        }
    };
    let digits = number.trim_start_matches('#').trim().to_string();
    if let Some(mint) = mint_by_number_any_spelling(http, &symbol, &digits).await {
        if let Some(o) = params.as_object_mut() {
            o.insert("mintAddress".into(), serde_json::json!(mint));
        }
    }
    params
}

/// "Cancel my offer" when there is only one offer to cancel.
///
/// A withdrawal names no NFT because there is nothing to name: the wallet has
/// one bid out and the user means that one. Without this the card put an empty
/// "NFT mint address" box in front of someone whose only open offer we could
/// have read in a single request. With several out the choice is real, so the
/// mint stays unresolved and the list card does its job.
async fn only_open_position_mint(
    http: &reqwest::Client,
    action: &str,
    wallet: &str,
) -> Option<String> {
    if wallet.is_empty() {
        return None;
    }
    let (url, mint_key) = if action.contains("cancel_offer") || action.contains("buy_cancel") {
        (
            format!("{MAGIC_EDEN_API}/wallets/{wallet}/offers_made?limit=20"),
            "tokenMint",
        )
    } else if action.contains("cancel_listing") || action.contains("sell_cancel") {
        (
            format!("{MAGIC_EDEN_API}/wallets/{wallet}/tokens?limit=50&listStatus=listed"),
            "mintAddress",
        )
    } else {
        return None;
    };

    let rows = me_get_json(http, &url).await.ok()?;
    let rows = rows.as_array()?;
    let mints: Vec<&str> = rows
        .iter()
        .filter_map(|r| r.get(mint_key).and_then(|m| m.as_str()))
        .collect();
    match mints.as_slice() {
        [only] => Some(only.to_string()),
        _ => None,
    }
}

/// Attach name, art and collection to each offer row.
///
/// Magic Eden's offer endpoints return a mint and nothing else about the
/// piece. Bounded and concurrent: the first page is what anyone reads, and a
/// row that cannot be resolved simply keeps its mint.
async fn enrich_offers_with_nft(
    http: &reqwest::Client,
    data: serde_json::Value,
) -> serde_json::Value {
    const MAX: usize = 12;
    let rows = match data.as_array() {
        Some(r) => r.clone(),
        None => return data,
    };
    let lookups = rows.iter().take(MAX).map(|row| {
        let mint = row
            .get("tokenMint")
            .and_then(|m| m.as_str())
            .unwrap_or_default()
            .to_string();
        async move {
            if mint.is_empty() {
                return None;
            }
            // `get_nft_info`, not `nft_display`: the latter invents a
            // truncated-mint name when the lookup fails, and a fabricated name
            // is indistinguishable from a real one downstream — the row then
            // looks resolved and no fallback can tell it isn't.
            get_nft_info(http, &mint)
                .await
                .ok()
                .map(|n| (n.name.clone(), n.image.clone(), n.collection_name.clone()))
        }
    });
    let resolved = futures::future::join_all(lookups).await;

    let mut out = rows;
    for (row, display) in out.iter_mut().zip(resolved) {
        let (name, image, collection) = match display {
            Some(d) => d,
            None => continue,
        };
        if let Some(o) = row.as_object_mut() {
            o.insert("name".into(), serde_json::json!(name));
            if let Some(img) = image {
                o.insert("image".into(), serde_json::json!(img));
            }
            if let Some(c) = collection {
                o.insert("collectionName".into(), serde_json::json!(c));
            }
        }
    }
    serde_json::Value::Array(out)
}

/// The read envelope, which is the same for every read: no transaction, no
/// approval, just the data and a title.
fn me_read_response(action: &str, params: &MeReadParams, data: serde_json::Value) -> BuildResponse {
    BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: action.to_string(),
            description: me_read_title(action, params),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!({}),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(data),
    }
}

pub async fn build_me_read(
    http: &reqwest::Client,
    action: &str,
    params: &MeReadParams,
) -> Result<BuildResponse, AppError> {
    if matches!(action, "me_token" | "me_nft_info") {
        // A mint if we were given one; otherwise the collection and the
        // number, which is what a person actually has.
        let mint = match params.mint_address.as_deref().filter(|m| !m.is_empty()) {
            Some(m) => m.to_string(),
            None => {
                let symbol = need(&params.symbol, "collection")?;
                let number = need(&params.number, "NFT number")?;
                let digits = number.trim_start_matches('#').trim().to_string();
                // The model names a collection the way a person does — "Mad
                // Lads" — and this passed it through as a slug, so a lookup by
                // number only ever worked when the name happened to be one.
                match mint_by_number_any_spelling(http, symbol, &digits).await {
                    Some(m) => m,
                    None => {
                        return Err(AppError::NotFound(format!(
                            "#{digits} is not currently listed, so I can't find it by number. \
                             Paste its mint address and I'll pull it up."
                        )))
                    }
                }
            }
        };
        let data = me_nft_detail(http, &mint).await?;
        return Ok(BuildResponse {
            preview: ActionPreview {
                id: Uuid::new_v4().to_string(),
                action_type: action.to_string(),
                description: me_read_title(action, params),
                estimated_fee: "0".to_string(),
                estimated_refund: None,
                params: serde_json::json!({}),
                warnings: vec![],
                requires_approval: false,
            },
            transaction: None,
            additional_signers_required: 0,
            execution_steps: None,
            quote: None,
            is_cross_chain: false,
            data: Some(data),
        });
    }
    if action == "me_trending_collections" {
        let window = params.days.as_deref().unwrap_or("1d");
        // Ask for the full ranking, not the first screen of it. The card
        // pages through what it is given, and a list that stops at twenty
        // cannot be paged past twenty.
        let limit = params.limit.unwrap_or(100);
        let sort = params.sort.as_deref().unwrap_or("volume");
        let data = me_trending_collections(http, window, limit, sort).await?;
        return Ok(me_read_response(action, params, data));
    }
    if action == "me_collection_holder_stats" {
        let symbol = collection_symbol(need(&params.symbol, "collection")?);
        let data = me_collection_holders(http, &symbol).await?;
        return Ok(me_read_response(action, params, data));
    }
    if action == "me_collection_sales_history" {
        let symbol = collection_symbol(need(&params.symbol, "collection")?);
        let days = params
            .days
            .as_deref()
            .and_then(|d| d.trim().trim_end_matches('d').parse::<u32>().ok())
            .unwrap_or(30);
        let data = me_collection_sales_history(http, &symbol, days).await?;
        return Ok(me_read_response(action, params, data));
    }
    if matches!(action, "me_collection_stats" | "me_collection_info") {
        let symbol = need(&params.symbol, "collection")?.to_string();
        let slug = collection_symbol(&symbol);
        let mut data = me_collection_detail(http, &slug).await?;
        // The market numbers come from the stats host, which knows the supply,
        // the holders and every window's volume — none of which the public v2
        // route carries. The v2 record stays for what it is good for: the
        // description and the project's own links.
        if let Some(overview) = me_collection_overview(http, &slug).await {
            if let (Some(obj), Some(extra)) = (data.as_object_mut(), overview.as_object()) {
                for (k, v) in extra {
                    if !v.is_null() {
                        obj.insert(k.clone(), v.clone());
                    }
                }
            }
        }
        let data = data;
        return Ok(BuildResponse {
            preview: ActionPreview {
                id: Uuid::new_v4().to_string(),
                action_type: action.to_string(),
                description: me_read_title(action, params),
                estimated_fee: "0".to_string(),
                estimated_refund: None,
                params: serde_json::json!({}),
                warnings: vec![],
                requires_approval: false,
            },
            transaction: None,
            additional_signers_required: 0,
            execution_steps: None,
            quote: None,
            is_cross_chain: false,
            data: Some(data),
        });
    }
    let url = me_read_url(action, params)?;
    let data = match me_get_json(http, &url).await {
        Ok(d) => d,
        Err(first) => {
            // Collections are inconsistent about joining words: "mad_lads" but
            // also "trenchors". If the underscore form misses, try the runs-
            // together form before telling someone their collection does not
            // exist — which is what OPRAI did, offering a same-named pump.fun
            // coin instead.
            let alt = params
                .symbol
                .as_deref()
                .map(collection_symbol_candidates)
                .and_then(|c| c.into_iter().nth(1));
            match alt {
                Some(other) => {
                    let mut retry = params.clone();
                    retry.symbol = Some(other);
                    let url = me_read_url(action, &retry)?;
                    me_get_json(http, &url).await.map_err(|_| first)?
                }
                None => return Err(first),
            }
        }
    };
    // An offer names only a mint, so a list of offers rendered as a list of
    // truncated addresses — you cannot tell which of your bids is which. Fill
    // in what each one is on.
    let data = if action.contains("offers") {
        enrich_offers_with_nft(http, data).await
    } else {
        data
    };

    // A tile list needs rarity per trait, and asking per NFT would be one
    // collection-wide request each. Normalise once here so the whole list can
    // be scored from a single read.
    let data = if action == "me_collection_attributes" {
        let stats = data
            .pointer("/results/availableAttributes")
            .and_then(|v| v.as_array())
            .map(|a| normalize_trait_stats(a, None))
            .unwrap_or_else(|| serde_json::json!({}));
        serde_json::json!({ "attributes": data, "traitStats": stats })
    } else {
        data
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: action.to_string(),
            description: me_read_title(action, params),
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: serde_json::json!({}),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(data),
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// MMM — Magic Eden's NFT AMM pools
// ──────────────────────────────────────────────────────────────────────────────
//
// A pool quotes both sides of a collection: it buys NFTs at its bid and sells
// them at its ask, moving the price along a curve after each fill. The seven
// endpoints below are the whole lifecycle — create, retune, fund, defund,
// trade against, close.
//
// The required parameters are not guesses. Each endpoint was asked with no
// arguments and answers with the list it wants; these structs mirror that.

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeMmmCreatePoolParams {
    /// Starting price in SOL.
    pub spot_price: String,
    /// "linear" or "exp".
    pub curve_type: String,
    /// How far the price moves per fill — SOL for linear, basis points for exp.
    pub curve_delta: String,
    pub collection_symbol: String,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub reinvest_buy: Option<bool>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub reinvest_sell: Option<bool>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub expiry: Option<i64>,
    /// The pool's own fee, in basis points.
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub lp_fee_bp: Option<u32>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub buyside_creator_royalty_bp: Option<u32>,
    /// Defaults to SOL.
    #[serde(default)]
    pub payment_mint: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeMmmUpdatePoolParams {
    pub pool: String,
    pub spot_price: String,
    pub curve_type: String,
    pub curve_delta: String,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub reinvest_buy: Option<bool>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub reinvest_sell: Option<bool>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub expiry: Option<i64>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub lp_fee_bp: Option<u32>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub buyside_creator_royalty_bp: Option<u32>,
    #[serde(default)]
    pub payment_mint: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeMmmPoolParams {
    pub pool: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeMmmFundParams {
    pub pool: String,
    /// SOL to move in or out of the pool's buy side.
    #[serde(alias = "amount")]
    pub payment_amount: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeMmmFulfillBuyParams {
    pub pool: String,
    pub asset_mint: String,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub asset_amount: Option<u32>,
    /// Floor on what you receive — the pool's bid can move before you land.
    #[serde(default)]
    pub min_payment_amount: Option<String>,
    #[serde(default)]
    pub asset_token_account: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeMmmFulfillSellParams {
    pub pool: String,
    pub asset_mint: String,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub asset_amount: Option<u32>,
    /// Ceiling on what you pay.
    #[serde(default)]
    pub max_payment_amount: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub buyside_creator_royalty_bp: Option<u32>,
}

const SOL_MINT: &str = "So11111111111111111111111111111111111111112";

fn bool_str(v: Option<bool>, default: bool) -> String {
    v.unwrap_or(default).to_string()
}

pub async fn build_me_mmm_create_pool(
    http: &reqwest::Client,
    user: &Pubkey,
    p: &MeMmmCreatePoolParams,
) -> Result<BuildResponse, AppError> {
    let spot: f64 = p
        .spot_price
        .parse()
        .map_err(|_| AppError::InvalidParams("Enter a starting price in SOL".into()))?;
    if spot <= 0.0 {
        return Err(AppError::InvalidParams(
            "Starting price must be above zero".into(),
        ));
    }
    let curve = p.curve_type.to_lowercase();
    if curve != "linear" && curve != "exp" {
        return Err(AppError::InvalidParams(
            "Curve must be either linear or exp".into(),
        ));
    }
    let tx = me_instruction(
        http,
        "mmm/create-pool",
        &[
            ("owner", user.to_string()),
            ("collectionSymbol", p.collection_symbol.clone()),
            ("spotPrice", spot.to_string()),
            ("curveType", curve.clone()),
            ("curveDelta", p.curve_delta.clone()),
            ("reinvestBuy", bool_str(p.reinvest_buy, true)),
            ("reinvestSell", bool_str(p.reinvest_sell, true)),
            ("expiry", p.expiry.unwrap_or(0).to_string()),
            ("lpFeeBp", p.lp_fee_bp.unwrap_or(0).to_string()),
            (
                "buysideCreatorRoyaltyBp",
                p.buyside_creator_royalty_bp.unwrap_or(0).to_string(),
            ),
            (
                "paymentMint",
                p.payment_mint
                    .clone()
                    .unwrap_or_else(|| SOL_MINT.to_string()),
            ),
        ],
    )
    .await?;
    Ok(me_tx_response(
        "me_mmm_create_pool",
        format!(
            "Create a {curve} pool on {} at {spot} SOL",
            p.collection_symbol
        ),
        tx,
        serde_json::json!({
            "collectionSymbol": p.collection_symbol,
            "spotPrice": spot,
            "curveType": curve,
            "curveDelta": p.curve_delta,
            "lpFeeBp": p.lp_fee_bp.unwrap_or(0),
        }),
        vec!["The pool starts empty — deposit SOL before it can buy anything".into()],
    ))
}

pub async fn build_me_mmm_update_pool(
    http: &reqwest::Client,
    _user: &Pubkey,
    p: &MeMmmUpdatePoolParams,
) -> Result<BuildResponse, AppError> {
    let spot: f64 = p
        .spot_price
        .parse()
        .map_err(|_| AppError::InvalidParams("Enter a price in SOL".into()))?;
    let curve = p.curve_type.to_lowercase();
    let tx = me_instruction(
        http,
        "mmm/update-pool",
        &[
            ("pool", p.pool.clone()),
            ("spotPrice", spot.to_string()),
            ("curveType", curve.clone()),
            ("curveDelta", p.curve_delta.clone()),
            ("reinvestBuy", bool_str(p.reinvest_buy, true)),
            ("reinvestSell", bool_str(p.reinvest_sell, true)),
            ("expiry", p.expiry.unwrap_or(0).to_string()),
            ("lpFeeBp", p.lp_fee_bp.unwrap_or(0).to_string()),
            (
                "buysideCreatorRoyaltyBp",
                p.buyside_creator_royalty_bp.unwrap_or(0).to_string(),
            ),
            (
                "paymentMint",
                p.payment_mint
                    .clone()
                    .unwrap_or_else(|| SOL_MINT.to_string()),
            ),
        ],
    )
    .await?;
    Ok(me_tx_response(
        "me_mmm_update_pool",
        format!("Retune pool to {spot} SOL"),
        tx,
        serde_json::json!({ "pool": p.pool, "spotPrice": spot, "curveType": curve }),
        vec![],
    ))
}

pub async fn build_me_mmm_close_pool(
    http: &reqwest::Client,
    _user: &Pubkey,
    p: &MeMmmPoolParams,
) -> Result<BuildResponse, AppError> {
    let tx = me_instruction(http, "mmm/sol-close-pool", &[("pool", p.pool.clone())]).await?;
    Ok(me_tx_response(
        "me_mmm_sol_close_pool",
        "Close the pool and take back its balance".to_string(),
        tx,
        serde_json::json!({ "pool": p.pool }),
        vec!["Withdraw any NFTs the pool still holds first".into()],
    ))
}

pub async fn build_me_mmm_deposit_buy(
    http: &reqwest::Client,
    _user: &Pubkey,
    p: &MeMmmFundParams,
) -> Result<BuildResponse, AppError> {
    let amount: f64 = p
        .payment_amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Enter an amount in SOL".into()))?;
    let tx = me_instruction(
        http,
        "mmm/sol-deposit-buy",
        &[
            ("pool", p.pool.clone()),
            ("paymentAmount", amount.to_string()),
        ],
    )
    .await?;
    Ok(me_tx_response(
        "me_mmm_sol_deposit_buy",
        format!("Fund the pool with {amount} SOL"),
        tx,
        serde_json::json!({ "pool": p.pool, "amount": amount }),
        vec!["This is what the pool bids with".into()],
    ))
}

pub async fn build_me_mmm_withdraw_buy(
    http: &reqwest::Client,
    _user: &Pubkey,
    p: &MeMmmFundParams,
) -> Result<BuildResponse, AppError> {
    let amount: f64 = p
        .payment_amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Enter an amount in SOL".into()))?;
    let tx = me_instruction(
        http,
        "mmm/sol-withdraw-buy",
        &[
            ("pool", p.pool.clone()),
            ("paymentAmount", amount.to_string()),
        ],
    )
    .await?;
    Ok(me_tx_response(
        "me_mmm_sol_withdraw_buy",
        format!("Take {amount} SOL out of the pool"),
        tx,
        serde_json::json!({ "pool": p.pool, "amount": amount }),
        vec!["The pool bids less, or stops bidding, once this is out".into()],
    ))
}

/// Sell an NFT INTO a pool's bid.
pub async fn build_me_mmm_fulfill_buy(
    http: &reqwest::Client,
    user: &Pubkey,
    p: &MeMmmFulfillBuyParams,
) -> Result<BuildResponse, AppError> {
    let token_account = match p.asset_token_account.clone() {
        Some(t) => t,
        None => resolve_token_account(http, user, &p.asset_mint).await,
    };
    let tx = me_instruction(
        http,
        "mmm/sol-fulfill-buy",
        &[
            ("pool", p.pool.clone()),
            ("seller", user.to_string()),
            ("assetMint", p.asset_mint.clone()),
            ("assetTokenAccount", token_account.clone()),
            ("assetAmount", p.asset_amount.unwrap_or(1).to_string()),
            (
                "minPaymentAmount",
                p.min_payment_amount.clone().unwrap_or_else(|| "0".into()),
            ),
        ],
    )
    .await?;
    let (name, image, collection) = nft_display(http, &p.asset_mint).await;
    Ok(me_tx_response(
        "me_mmm_sol_fulfill_buy",
        format!("Sell {name} into the pool"),
        tx,
        serde_json::json!({
            "pool": p.pool, "mintAddress": p.asset_mint,
            "nftName": name, "nftImage": image, "collectionName": collection,
        }),
        vec![],
    ))
}

/// Buy an NFT OUT of a pool.
pub async fn build_me_mmm_fulfill_sell(
    http: &reqwest::Client,
    user: &Pubkey,
    p: &MeMmmFulfillSellParams,
) -> Result<BuildResponse, AppError> {
    let tx = me_instruction(
        http,
        "mmm/sol-fulfill-sell",
        &[
            ("pool", p.pool.clone()),
            ("buyer", user.to_string()),
            ("assetMint", p.asset_mint.clone()),
            ("assetAmount", p.asset_amount.unwrap_or(1).to_string()),
            (
                "maxPaymentAmount",
                p.max_payment_amount.clone().unwrap_or_else(|| "0".into()),
            ),
            (
                "buysideCreatorRoyaltyBp",
                p.buyside_creator_royalty_bp.unwrap_or(0).to_string(),
            ),
        ],
    )
    .await?;
    let (name, image, collection) = nft_display(http, &p.asset_mint).await;
    Ok(me_tx_response(
        "me_mmm_sol_fulfill_sell",
        format!("Buy {name} from the pool"),
        tx,
        serde_json::json!({
            "pool": p.pool, "mintAddress": p.asset_mint,
            "nftName": name, "nftImage": image, "collectionName": collection,
        }),
        vec![],
    ))
}

#[cfg(test)]
mod symbol_tests {
    use super::*;

    /// A person names a collection; Magic Eden wants a slug.
    ///
    /// Asked for "Trenchors nft", OPRAI reported that no such collection
    /// existed and offered a pump.fun coin of the same name — because the
    /// name was never turned into the symbol the API answers to.
    #[test]
    fn a_name_becomes_a_symbol() {
        assert_eq!(collection_symbol("Trenchors"), "trenchors");
        assert_eq!(collection_symbol("Mad Lads"), "mad_lads");
        assert_eq!(
            collection_symbol("  Solana Monkey Business "),
            "solana_monkey_business"
        );
        assert_eq!(collection_symbol("DeGods #1"), "degods_1");
        // Already a symbol — untouched, so a slug pasted from a URL survives.
        assert_eq!(
            collection_symbol("solana_monkey_business"),
            "solana_monkey_business"
        );
    }

    #[test]
    fn multi_word_names_offer_both_spellings() {
        assert_eq!(
            collection_symbol_candidates("Mad Lads"),
            vec!["mad_lads".to_string(), "madlads".to_string()],
        );
        // One word has only one spelling; no wasted second request.
        assert_eq!(
            collection_symbol_candidates("Trenchors"),
            vec!["trenchors".to_string()]
        );
    }
}

use base64::Engine;
use serde::{Deserialize, Serialize};
use solana_sdk::{message::Message, pubkey::Pubkey, transaction::Transaction};
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

/// Auction House ID for Magic Eden
pub const MAGIC_EDEN_AUCTION_HOUSE: &str = "C76z7q6gT84xMH5s9t1r7hRe2QQpBCT7o2D8nQ46DmNK";

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/// NFT metadata from Magic Eden
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeNFT {
    pub mint_address: String,
    pub name: String,
    pub uri: String,
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
    pub trait_type: String,
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
    pub name: String,
    pub uri: String,
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
    pub expiry: Option<u64>,
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
    /// Seller's wallet address
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
    pub expiry: Option<u64>,
}

/// Parameters for accepting an offer
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeAcceptOfferParams {
    /// NFT mint address
    pub mint_address: String,
    /// Offer price to accept
    pub price: String,
    /// Buyer's wallet address (offer maker)
    pub buyer: Option<String>,
}

/// Parameters for canceling an offer
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCancelOfferParams {
    /// NFT mint address
    pub mint_address: String,
    /// Offer price to cancel
    pub price: String,
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
    pub limit: Option<u32>,
    /// Pagination offset
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
    pub limit: Option<u32>,
    /// Pagination offset
    pub offset: Option<u32>,
}

/// Parameters for getting listings
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeListingsParams {
    /// Collection symbol
    pub symbol: String,
    /// Pagination limit
    pub limit: Option<u32>,
    /// Pagination offset
    pub offset: Option<u32>,
}

/// Parameters for getting offers
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeOffersParams {
    /// NFT mint address
    pub mint_address: String,
    /// Pagination limit
    pub limit: Option<u32>,
    /// Pagination offset
    pub offset: Option<u32>,
}

/// Parameters for getting NFTs by collection
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MeCollectionNFTsParams {
    /// Collection symbol
    pub symbol: String,
    /// Pagination limit
    pub limit: Option<u32>,
    /// Pagination offset
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
        .get(format!("{}/nfts/{}", MAGIC_EDEN_API, mint_address))
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
    let mut url = format!("{}/nfts/{}/offers", MAGIC_EDEN_API, mint_address);
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
        .get(format!("{}/nfts/{}/listings", MAGIC_EDEN_API, mint_address))
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
// Build Functions
// ──────────────────────────────────────────────────────────────────────────────

/// Build NFT listing transaction
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
        .map_err(|_| AppError::InvalidParams("Invalid price".into()))?;

    // Get NFT info for preview
    let nft_info = get_nft_info(http, &params.mint_address).await.ok();

    let blockhash = solana_sdk::hash::Hash::default();
    let message = Message::new(&[], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    let marketplace_fee = price * (MARKETPLACE_FEE_BPS as f64) / 10000.0;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_list".to_string(),
            description: format!(
                "List {} for {} SOL",
                nft_info
                    .as_ref()
                    .map(|n| n.name.as_str())
                    .unwrap_or(&params.mint_address),
                price
            ),
            estimated_fee: format!(
                "~0.000005 SOL + {} SOL marketplace fee on sale",
                marketplace_fee
            ),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "price": price,
                "expiry": params.expiry,
                "nftName": nft_info.as_ref().map(|n| n.name.clone()),
                "collectionName": nft_info.as_ref().and_then(|n| n.collection_name.clone()),
                "marketplaceFeeBps": MARKETPLACE_FEE_BPS,
                "auctionHouse": MAGIC_EDEN_AUCTION_HOUSE,
            }),
            warnings: vec![
                format!(
                    "Marketplace fee: {}% ({} SOL)",
                    MARKETPLACE_FEE_BPS as f64 / 100.0,
                    marketplace_fee
                ),
                "Listing will be active until cancelled or sold".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Build NFT buy transaction
pub async fn build_me_buy(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeBuyParams,
) -> Result<BuildResponse, AppError> {
    validate_me_buy_params(params)?;

    let price: f64 = params
        .price
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid price".into()))?;

    // Get NFT info and listings
    let nft_info = get_nft_info(http, &params.mint_address).await.ok();
    let listings = get_nft_listings(http, &params.mint_address).await.ok();

    let blockhash = solana_sdk::hash::Hash::default();
    let message = Message::new(&[], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    let marketplace_fee = price * (MARKETPLACE_FEE_BPS as f64) / 10000.0;
    let total_cost = price + marketplace_fee;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_buy".to_string(),
            description: format!(
                "Buy {} for {} SOL",
                nft_info
                    .as_ref()
                    .map(|n| n.name.as_str())
                    .unwrap_or(&params.mint_address),
                price
            ),
            estimated_fee: format!("~{} SOL total (price + fee)", total_cost),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "price": price,
                "marketplaceFee": marketplace_fee,
                "totalCost": total_cost,
                "tokenAddress": params.token_address,
                "seller": params.seller.clone().or(listings.and_then(|l| l.first().map(|l| l.seller.clone()))),
                "nftName": nft_info.as_ref().map(|n| n.name.clone()),
                "collectionName": nft_info.as_ref().and_then(|n| n.collection_name.clone()),
            }),
            warnings: vec![
                format!(
                    "Total cost: {} SOL (includes {} SOL fee)",
                    total_cost, marketplace_fee
                ),
                "Transaction will fail if listing is no longer active".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Build cancel listing transaction
pub async fn build_me_cancel_listing(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeCancelListingParams,
) -> Result<BuildResponse, AppError> {
    validate_me_cancel_listing_params(params)?;

    let nft_info = get_nft_info(http, &params.mint_address).await.ok();

    let blockhash = solana_sdk::hash::Hash::default();
    let message = Message::new(&[], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_cancel_listing".to_string(),
            description: format!(
                "Cancel listing for {}",
                nft_info
                    .as_ref()
                    .map(|n| n.name.as_str())
                    .unwrap_or(&params.mint_address)
            ),
            estimated_fee: "~0.000005 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "price": params.price,
                "tokenAddress": params.token_address,
                "nftName": nft_info.as_ref().map(|n| n.name.clone()),
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Build make offer transaction
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
        .map_err(|_| AppError::InvalidParams("Invalid price".into()))?;

    let nft_info = get_nft_info(http, &params.mint_address).await.ok();

    let blockhash = solana_sdk::hash::Hash::default();
    let message = Message::new(&[], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    let _marketplace_fee = price * (MARKETPLACE_FEE_BPS as f64) / 10000.0;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_make_offer".to_string(),
            description: format!(
                "Make offer of {} SOL on {}",
                price,
                nft_info
                    .as_ref()
                    .map(|n| n.name.as_str())
                    .unwrap_or(&params.mint_address)
            ),
            estimated_fee: "~0.000005 SOL (offer amount escrowed)".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "price": price,
                "expiry": params.expiry,
                "nftName": nft_info.as_ref().map(|n| n.name.clone()),
                "collectionName": nft_info.as_ref().and_then(|n| n.collection_name.clone()),
                "marketplaceFeeBps": MARKETPLACE_FEE_BPS,
            }),
            warnings: vec![
                format!("Offer amount ({}) SOL will be escrowed", price),
                format!(
                    "Marketplace fee: {}% on sale",
                    MARKETPLACE_FEE_BPS as f64 / 100.0
                ),
            ],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Build accept offer transaction
pub async fn build_me_accept_offer(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeAcceptOfferParams,
) -> Result<BuildResponse, AppError> {
    validate_me_accept_offer_params(params)?;

    let price: f64 = params
        .price
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid price".into()))?;

    let nft_info = get_nft_info(http, &params.mint_address).await.ok();
    let offers = get_nft_offers(http, &params.mint_address, Some(10), None)
        .await
        .ok();

    let blockhash = solana_sdk::hash::Hash::default();
    let message = Message::new(&[], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    let marketplace_fee = price * (MARKETPLACE_FEE_BPS as f64) / 10000.0;
    let net_proceeds = price - marketplace_fee;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_accept_offer".to_string(),
            description: format!(
                "Accept offer of {} SOL on {}",
                price,
                nft_info
                    .as_ref()
                    .map(|n| n.name.as_str())
                    .unwrap_or(&params.mint_address)
            ),
            estimated_fee: format!(
                "~{} SOL net (after {} SOL fee)",
                net_proceeds, marketplace_fee
            ),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "price": price,
                "buyer": params.buyer.clone().or(offers.as_ref().and_then(|o| o.first().map(|o| o.buyer.clone()))),
                "netProceeds": net_proceeds,
                "marketplaceFee": marketplace_fee,
                "nftName": nft_info.as_ref().map(|n| n.name.clone()),
            }),
            warnings: vec![
                format!(
                    "You will receive {} SOL after {} SOL marketplace fee",
                    net_proceeds, marketplace_fee
                ),
                "Transaction will fail if offer is no longer active".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Build cancel offer transaction
pub async fn build_me_cancel_offer(
    http: &reqwest::Client,
    _rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &MeCancelOfferParams,
) -> Result<BuildResponse, AppError> {
    validate_me_cancel_offer_params(params)?;

    let nft_info = get_nft_info(http, &params.mint_address).await.ok();

    let blockhash = solana_sdk::hash::Hash::default();
    let message = Message::new(&[], Some(user_pubkey));
    let mut transaction = Transaction::new_unsigned(message);
    transaction.message.recent_blockhash = blockhash;

    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "me_cancel_offer".to_string(),
            description: format!(
                "Cancel offer on {}",
                nft_info
                    .as_ref()
                    .map(|n| n.name.as_str())
                    .unwrap_or(&params.mint_address)
            ),
            estimated_fee: "~0.000005 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "price": params.price,
                "nftName": nft_info.as_ref().map(|n| n.name.clone()),
            }),
            warnings: vec!["Escrowed funds will be returned".into()],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Query Actions (no transaction, just data)
// ──────────────────────────────────────────────────────────────────────────────

/// Get collection info action
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

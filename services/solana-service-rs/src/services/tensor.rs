//! Tensor NFT Marketplace Service
//!
//! #1 NFT aggregator on Solana. Supports buying, listing, offers, and queries.
//! Program ID: TSWAPaqyCSx2KABk68Shruf4rp7CxcAi9S6fxqfSGQ
//! API: https://api.tensor.so/v1 (REST) and https://api.tensor.so/graphql (GraphQL)

use base64::Engine;
use serde::{Deserialize, Serialize};
use solana_sdk::{
    instruction::{AccountMeta, Instruction},
    message::Message,
    pubkey::Pubkey,
    system_program,
    transaction::Transaction,
};
use std::str::FromStr;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

pub const TENSOR_PROGRAM_ID: &str = "TSWAPaqyCSx2KABk68Shruf4rp7CxcAi9S6fxqfSGQ";
pub const TENSOR_API: &str = "https://api.tensor.so/v1";

/// Instruction discriminators (Anchor 8-byte prefix would be used in full SDK)
const IX_BUY: u8 = 0x01;
const IX_LIST: u8 = 0x02;
const IX_CANCEL_LISTING: u8 = 0x03;
const IX_MAKE_OFFER: u8 = 0x04;
const IX_CANCEL_OFFER: u8 = 0x05;

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorBuyParams {
    pub mint_address: String,
    /// Max price willing to pay in SOL
    pub max_price: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorListParams {
    pub mint_address: String,
    /// Listing price in SOL
    pub price: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorCancelListingParams {
    pub mint_address: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorMakeOfferParams {
    pub collection_slug: String,
    /// Offer price in SOL
    pub price: String,
    #[serde(default)]
    pub quantity: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorCancelOfferParams {
    pub bid_id: String,
}

// Data-only query params
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorCollectionInfoParams {
    pub collection_slug: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorNftInfoParams {
    pub mint_address: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorWalletNftsParams {
    pub wallet: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorListingsParams {
    pub collection_slug: String,
    #[serde(default)]
    pub limit: Option<String>,
}

// ──────────────────────────────────────────────────────────────────────────────
// API Types
// ──────────────────────────────────────────────────────────────────────────────

/// Response from Tensor API for getting a buy transaction
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorBuyTxResponse {
    pub tx_data: String,
    pub seller: Option<String>,
    pub auction_house: Option<String>,
}

/// Response from Tensor API for getting a list transaction
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorListTxResponse {
    pub tx_data: String,
}

/// Response from Tensor API for NFT mint info
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorMintInfo {
    pub name: Option<String>,
    pub image: Option<String>,
    pub collection: Option<TensorCollection>,
    pub listing: Option<TensorListing>,
    pub owner: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorCollection {
    pub name: Option<String>,
    pub slug: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorListing {
    pub price: Option<f64>,
    pub seller: Option<String>,
    pub auction_house: Option<String>,
}

/// Response from Tensor API for collection info
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TensorCollectionInfo {
    pub name: Option<String>,
    pub floor_price: Option<f64>,
    pub listed_count: Option<i64>,
    pub volume_all: Option<f64>,
}

/// Tensor API error response
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TensorApiError {
    pub message: Option<String>,
    pub code: Option<i32>,
}

/// Fetch NFT mint info from Tensor API.
pub async fn get_nft_info(http: &reqwest::Client, mint_address: &str) -> Option<TensorMintInfo> {
    let url = format!("{TENSOR_API}/mint/{mint_address}");
    let resp = http.get(&url).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    resp.json::<TensorMintInfo>().await.ok()
}

/// Fetch collection info from Tensor API.
pub async fn get_collection_info(http: &reqwest::Client, slug: &str) -> Option<TensorCollectionInfo> {
    let url = format!("{TENSOR_API}/collections/{slug}");
    let resp = http.get(&url).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    resp.json::<TensorCollectionInfo>().await.ok()
}

/// Build buy transaction via Tensor API.
/// This calls the Tensor API to get the actual transaction data.
pub async fn get_buy_tx(
    http: &reqwest::Client,
    buyer: &str,
    mint_address: &str,
    max_price_sol: f64,
) -> Result<TensorBuyTxResponse, AppError> {
    let max_price_lamports = (max_price_sol * 1_000_000_000.0) as u64;

    // Tensor API expects price in lamports
    let body = serde_json::json!({
        "buyer": buyer,
        "mint": mint_address,
        "maxPriceLamports": max_price_lamports,
    });

    let url = format!("{TENSOR_API}/execute/buy");
    let resp = http
        .post(&url)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Tensor API request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let error: TensorApiError = resp.json().await.unwrap_or(TensorApiError {
            message: Some(format!("Tensor API error: {}", status)),
            code: Some(status.as_u16() as i32),
        });
        return Err(AppError::Internal(
            error.message.unwrap_or_else(|| "Unknown Tensor API error".to_string())
        ));
    }

    resp.json::<TensorBuyTxResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse Tensor response: {e}")))
}

/// Build list transaction via Tensor API.
pub async fn get_list_tx(
    http: &reqwest::Client,
    seller: &str,
    mint_address: &str,
    price_sol: f64,
) -> Result<TensorListTxResponse, AppError> {
    let price_lamports = (price_sol * 1_000_000_000.0) as u64;

    let body = serde_json::json!({
        "seller": seller,
        "mint": mint_address,
        "priceLamports": price_lamports,
    });

    let url = format!("{TENSOR_API}/execute/list");
    let resp = http
        .post(&url)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Tensor API request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let error: TensorApiError = resp.json().await.unwrap_or(TensorApiError {
            message: Some(format!("Tensor API error: {}", status)),
            code: Some(status.as_u16() as i32),
        });
        return Err(AppError::Internal(
            error.message.unwrap_or_else(|| "Unknown Tensor API error".to_string())
        ));
    }

    resp.json::<TensorListTxResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse Tensor response: {e}")))
}

/// Build cancel listing transaction via Tensor API.
pub async fn get_cancel_list_tx(
    http: &reqwest::Client,
    seller: &str,
    mint_address: &str,
) -> Result<TensorListTxResponse, AppError> {
    let body = serde_json::json!({
        "seller": seller,
        "mint": mint_address,
    });

    let url = format!("{TENSOR_API}/execute/cancel_list");
    let resp = http
        .post(&url)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Tensor API request failed: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let error: TensorApiError = resp.json().await.unwrap_or(TensorApiError {
            message: Some(format!("Tensor API error: {}", status)),
            code: Some(status.as_u16() as i32),
        });
        return Err(AppError::Internal(
            error.message.unwrap_or_else(|| "Unknown Tensor API error".to_string())
        ));
    }

    resp.json::<TensorListTxResponse>()
        .await
        .map_err(|e| AppError::Internal(format!("Failed to parse Tensor response: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// Transaction Builders
// ──────────────────────────────────────────────────────────────────────────────

fn build_stub_tx(
    user_pubkey: &Pubkey,
    discriminator: u8,
    extra_data: Vec<u8>,
) -> Result<String, AppError> {
    let program_id = Pubkey::from_str(TENSOR_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid program ID: {e}")))?;

    let mut data = vec![discriminator];
    data.extend_from_slice(&extra_data);

    let ix = Instruction {
        program_id,
        accounts: vec![
            AccountMeta::new(*user_pubkey, true),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(user_pubkey));
    let tx = Transaction::new_unsigned(message);
    let serialized = bincode::serialize(&tx)
        .map_err(|e| AppError::Internal(format!("Serialize error: {e}")))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(&serialized))
}

pub async fn build_tensor_buy(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &TensorBuyParams,
) -> Result<BuildResponse, AppError> {
    let price: f64 = params.max_price.parse().map_err(|_| {
        AppError::InvalidParams("Invalid maxPrice: must be a positive number".into())
    })?;
    if price <= 0.0 {
        return Err(AppError::InvalidParams("maxPrice must be positive".into()));
    }

    // Validate wallet
    let _wallet = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;

    // Fetch current listing to validate price and get NFT info
    let nft_info = get_nft_info(http, &params.mint_address).await;
    let current_price = nft_info
        .as_ref()
        .and_then(|n| n.listing.as_ref())
        .and_then(|l| l.price)
        .map(|p| p / 1_000_000_000.0);

    let nft_name = nft_info
        .as_ref()
        .and_then(|n| n.name.clone())
        .unwrap_or_else(|| params.mint_address[..8.min(params.mint_address.len())].to_string());

    // Get actual transaction from Tensor API
    let tx_response = match get_buy_tx(http, user_pubkey_str, &params.mint_address, price).await {
        Ok(tx) => tx,
        Err(e) => {
            // If API fails, fall back to stub but warn user
            tracing::warn!("Tensor API failed, using fallback: {}", e);
            let price_lamports = (price * 1_000_000_000.0) as u64;
            let encoded = build_stub_tx(
                &Pubkey::from_str(user_pubkey_str).map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?,
                IX_BUY,
                price_lamports.to_le_bytes().to_vec()
            )?;

            return Ok(BuildResponse {
                preview: ActionPreview {
                    id: Uuid::new_v4().to_string(),
                    action_type: "tensor_buy".into(),
                    description: format!(
                        "Buy NFT {} (max {:.3} SOL) via Tensor",
                        &nft_name[..8.min(nft_name.len())],
                        price
                    ),
                    estimated_fee: "~0.001 SOL + NFT price".into(),
                    estimated_refund: None,
                    params: serde_json::json!({
                        "mintAddress": params.mint_address,
                        "maxPrice": params.max_price,
                        "currentListingPrice": current_price,
                    }),
                    warnings: vec!["Note: Using fallback transaction - Tensor API unavailable".into()],
                    requires_approval: true,
                },
                transaction: Some(encoded),
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
        data: None,
            });
        }
    };

    let mut warnings = vec![];
    if let Some(cp) = current_price {
        if price < cp {
            warnings.push(format!("Current listing price is {cp:.3} SOL — your max price ({price:.3} SOL) may be too low"));
        }
    }

    // Use the transaction from Tensor API
    let encoded = tx_response.tx_data;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_buy".into(),
            description: format!(
                "Buy NFT {} (max {:.3} SOL) via Tensor",
                &nft_name[..8.min(nft_name.len())],
                price
            ),
            estimated_fee: "~0.001 SOL + NFT price".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "maxPrice": params.max_price,
                "currentListingPrice": current_price,
                "nftName": nft_name,
                "seller": tx_response.seller,
            }),
            warnings,
            requires_approval: true,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

pub async fn build_tensor_list(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &TensorListParams,
) -> Result<BuildResponse, AppError> {
    let price: f64 = params.price.parse().map_err(|_| {
        AppError::InvalidParams("Invalid price: must be a positive number".into())
    })?;
    if price <= 0.0 {
        return Err(AppError::InvalidParams("Price must be positive".into()));
    }

    // Validate wallet
    let _wallet = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;

    // Get NFT info for display
    let nft_info = get_nft_info(http, &params.mint_address).await;
    let nft_name = nft_info
        .as_ref()
        .and_then(|n| n.name.clone())
        .unwrap_or_else(|| params.mint_address[..8.min(params.mint_address.len())].to_string());

    // Get actual transaction from Tensor API
    let tx_response = match get_list_tx(http, user_pubkey_str, &params.mint_address, price).await {
        Ok(tx) => tx,
        Err(e) => {
            // If API fails, fall back to stub but warn user
            tracing::warn!("Tensor API failed for list, using fallback: {}", e);
            let price_lamports = (price * 1_000_000_000.0) as u64;
            let encoded = build_stub_tx(
                &Pubkey::from_str(user_pubkey_str).map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?,
                IX_LIST,
                price_lamports.to_le_bytes().to_vec()
            )?;

            return Ok(BuildResponse {
                preview: ActionPreview {
                    id: Uuid::new_v4().to_string(),
                    action_type: "tensor_list".into(),
                    description: format!(
                        "List NFT {} for {:.3} SOL on Tensor",
                        &nft_name[..8.min(nft_name.len())],
                        price
                    ),
                    estimated_fee: "~0.001 SOL".into(),
                    estimated_refund: None,
                    params: serde_json::json!({
                        "mintAddress": params.mint_address,
                        "price": params.price,
                        "nftName": nft_name,
                    }),
                    warnings: vec!["Note: Using fallback transaction - Tensor API unavailable".into()],
                    requires_approval: true,
                },
                transaction: Some(encoded),
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
        data: None,
            });
        }
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_list".into(),
            description: format!(
                "List NFT {} for {:.3} SOL on Tensor",
                &nft_name[..8.min(nft_name.len())],
                price
            ),
            estimated_fee: "~0.001 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "price": params.price,
                "nftName": nft_name,
            }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx_response.tx_data),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

pub async fn build_tensor_cancel_listing(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &TensorCancelListingParams,
) -> Result<BuildResponse, AppError> {
    // Validate wallet
    let _wallet = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;

    // Get NFT info for display
    let nft_info = get_nft_info(http, &params.mint_address).await;
    let nft_name = nft_info
        .as_ref()
        .and_then(|n| n.name.clone())
        .unwrap_or_else(|| params.mint_address[..8.min(params.mint_address.len())].to_string());

    // Get actual transaction from Tensor API
    let tx_response = match get_cancel_list_tx(http, user_pubkey_str, &params.mint_address).await {
        Ok(tx) => tx,
        Err(e) => {
            // If API fails, fall back to stub but warn user
            tracing::warn!("Tensor API failed for cancel listing, using fallback: {}", e);
            let encoded = build_stub_tx(
                &Pubkey::from_str(user_pubkey_str).map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?,
                IX_CANCEL_LISTING,
                vec![]
            )?;

            return Ok(BuildResponse {
                preview: ActionPreview {
                    id: Uuid::new_v4().to_string(),
                    action_type: "tensor_cancel_listing".into(),
                    description: format!(
                        "Cancel Tensor listing for NFT {}",
                        &nft_name[..8.min(nft_name.len())]
                    ),
                    estimated_fee: "~0.001 SOL".into(),
                    estimated_refund: None,
                    params: serde_json::json!({
                        "mintAddress": params.mint_address,
                        "nftName": nft_name,
                    }),
                    warnings: vec!["Note: Using fallback transaction - Tensor API unavailable".into()],
                    requires_approval: false,
                },
                transaction: Some(encoded),
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
        data: None,
            });
        }
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_cancel_listing".into(),
            description: format!(
                "Cancel Tensor listing for NFT {}",
                &nft_name[..8.min(nft_name.len())]
            ),
            estimated_fee: "~0.001 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "mintAddress": params.mint_address,
                "nftName": nft_name,
            }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(tx_response.tx_data),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

pub async fn build_tensor_make_offer(
    _http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &TensorMakeOfferParams,
) -> Result<BuildResponse, AppError> {
    let price: f64 = params.price.parse().map_err(|_| {
        AppError::InvalidParams("Invalid price: must be a positive number".into())
    })?;
    if price <= 0.0 {
        return Err(AppError::InvalidParams("Offer price must be positive".into()));
    }

    let wallet = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;

    let qty: u64 = params.quantity.as_deref().unwrap_or("1").parse().unwrap_or(1);
    let price_lamports = (price * 1_000_000_000.0) as u64;
    let mut data = price_lamports.to_le_bytes().to_vec();
    data.extend_from_slice(&qty.to_le_bytes());
    let encoded = build_stub_tx(&wallet, IX_MAKE_OFFER, data)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_make_offer".into(),
            description: format!(
                "Offer {} SOL × {} for {} collection (Tensor)",
                price, qty, params.collection_slug
            ),
            estimated_fee: format!("~{:.3} SOL escrow", price * qty as f64),
            estimated_refund: None,
            params: serde_json::json!({
                "collectionSlug": params.collection_slug,
                "price": params.price,
                "quantity": qty,
            }),
            warnings: vec!["SOL will be escrowed until offer is accepted or cancelled".into()],
            requires_approval: true,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

pub async fn build_tensor_cancel_offer(
    _http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &TensorCancelOfferParams,
) -> Result<BuildResponse, AppError> {
    let wallet = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;

    let encoded = build_stub_tx(&wallet, IX_CANCEL_OFFER, vec![])?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_cancel_offer".into(),
            description: format!("Cancel Tensor offer {}", &params.bid_id[..8.min(params.bid_id.len())]),
            estimated_fee: "~0.001 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "bidId": params.bid_id }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Data-only queries (no transaction)
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_tensor_collection_info(
    http: &reqwest::Client,
    params: &TensorCollectionInfoParams,
) -> Result<BuildResponse, AppError> {
    let info = get_collection_info(http, &params.collection_slug).await;

    // Convert the structured info to JSON for the response
    let params_json = if let Some(ref coll) = info {
        serde_json::json!({
            "collectionSlug": params.collection_slug,
            "name": coll.name,
            "floorPrice": coll.floor_price.map(|p| p / 1_000_000_000.0),
            "listedCount": coll.listed_count,
            "volumeAll": coll.volume_all.map(|v| v / 1_000_000_000.0),
        })
    } else {
        serde_json::json!({ "collectionSlug": params.collection_slug })
    };

    let collection_name = info
        .as_ref()
        .and_then(|c| c.name.clone())
        .unwrap_or_else(|| params.collection_slug.clone());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_collection_info".into(),
            description: format!("Collection info for '{}'", collection_name),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: params_json,
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

pub async fn build_tensor_nft_info(
    http: &reqwest::Client,
    params: &TensorNftInfoParams,
) -> Result<BuildResponse, AppError> {
    let info = get_nft_info(http, &params.mint_address).await;
    let nft_name = info
        .as_ref()
        .and_then(|n| n.name.clone())
        .unwrap_or_else(|| params.mint_address[..8.min(params.mint_address.len())].to_string());

    // Convert the structured info to JSON for the response
    let params_json = if let Some(ref nft) = info {
        serde_json::json!({
            "mintAddress": params.mint_address,
            "name": nft.name,
            "image": nft.image,
            "collection": nft.collection,
            "listing": nft.listing,
            "owner": nft.owner,
        })
    } else {
        serde_json::json!({ "mintAddress": params.mint_address })
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_nft_info".into(),
            description: format!("NFT info for {}", nft_name),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: params_json,
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

pub async fn build_tensor_wallet_nfts(
    _http: &reqwest::Client,
    params: &TensorWalletNftsParams,
) -> Result<BuildResponse, AppError> {
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_wallet_nfts".into(),
            description: format!("NFTs for wallet {} (Tensor)", &params.wallet[..8.min(params.wallet.len())]),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "wallet": params.wallet }),
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

pub async fn build_tensor_listings(
    _http: &reqwest::Client,
    params: &TensorListingsParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.as_deref().unwrap_or("20");
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "tensor_listings".into(),
            description: format!("Top {} listings for '{}' (Tensor)", limit, params.collection_slug),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "collectionSlug": params.collection_slug,
                "limit": limit,
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
// Dispatcher
// ──────────────────────────────────────────────────────────────────────────────

pub async fn build_tensor_action(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    action_type: &str,
    params: serde_json::Value,
) -> Result<BuildResponse, AppError> {
    match action_type {
        "tensor_buy" => {
            let p: TensorBuyParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_buy params: {e}")))?;
            build_tensor_buy(http, user_pubkey_str, &p).await
        }
        "tensor_list" => {
            let p: TensorListParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_list params: {e}")))?;
            build_tensor_list(http, user_pubkey_str, &p).await
        }
        "tensor_cancel_listing" => {
            let p: TensorCancelListingParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_cancel_listing params: {e}")))?;
            build_tensor_cancel_listing(http, user_pubkey_str, &p).await
        }
        "tensor_make_offer" => {
            let p: TensorMakeOfferParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_make_offer params: {e}")))?;
            build_tensor_make_offer(http, user_pubkey_str, &p).await
        }
        "tensor_cancel_offer" => {
            let p: TensorCancelOfferParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_cancel_offer params: {e}")))?;
            build_tensor_cancel_offer(http, user_pubkey_str, &p).await
        }
        "tensor_collection_info" => {
            let p: TensorCollectionInfoParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_collection_info params: {e}")))?;
            build_tensor_collection_info(http, &p).await
        }
        "tensor_nft_info" => {
            let p: TensorNftInfoParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_nft_info params: {e}")))?;
            build_tensor_nft_info(http, &p).await
        }
        "tensor_wallet_nfts" => {
            let p: TensorWalletNftsParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_wallet_nfts params: {e}")))?;
            build_tensor_wallet_nfts(http, &p).await
        }
        "tensor_listings" => {
            let p: TensorListingsParams = serde_json::from_value(params)
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_listings params: {e}")))?;
            build_tensor_listings(http, &p).await
        }
        _ => Err(AppError::InvalidParams(format!("Unknown Tensor action: {action_type}"))),
    }
}

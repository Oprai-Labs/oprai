//! Helius API integrations.
//!
//! Covers:
//!
//! | Action | API | Endpoint |
//! |---|---|---|
//! | `helius_tx_history` | Enhanced Transactions | `GET /v0/addresses/{addr}/transactions` |
//! | `helius_parse_transactions` | Enhanced Transactions | `POST /v0/transactions` |
//! | `helius_get_assets` | DAS | `getAssetsByOwner` |
//! | `helius_get_asset` | DAS | `getAsset` |
//! | `helius_search_assets` | DAS | `searchAssets` |
//! | `helius_nft_editions` | DAS | `getNftEditions` |
//! | `helius_get_token_accounts` | DAS | `getTokenAccounts` |
//! | `helius_asset_signatures` | DAS | `getSignaturesForAsset` |
//! | `helius_priority_fee` | Priority Fee | `getPriorityFeeEstimate` |
//! | `helius_wallet_identity` | Wallet API | `GET /v1/wallet/{addr}/identity` |
//! | `helius_batch_identity` | Wallet API | `POST /v1/wallet/batch-identity` |

use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;

const HELIUS_ENHANCED_TX_API: &str = "https://api-mainnet.helius-rpc.com";
const HELIUS_RPC_API: &str = "https://mainnet.helius-rpc.com";
const HELIUS_WALLET_API: &str = "https://api.helius.xyz";

fn require_key(api_key: Option<&str>) -> Result<&str, AppError> {
    api_key.filter(|k| !k.is_empty()).ok_or_else(|| {
        AppError::InvalidParams("HELIUS_API_KEY is not configured".into())
    })
}

fn short_addr(addr: &str) -> String {
    if addr.len() > 8 {
        format!("{}…{}", &addr[..4], &addr[addr.len() - 4..])
    } else {
        addr.to_string()
    }
}

async fn helius_get(
    http: &reqwest::Client,
    url: &str,
    api_key: &str,
) -> Result<Value, AppError> {
    let resp = http
        .get(url)
        .header("Accept", "application/json")
        .header("Authorization", format!("Bearer {api_key}"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Helius GET error: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "Helius {status}: {body}"
        )));
    }

    resp.json::<Value>()
        .await
        .map_err(|e| AppError::Internal(format!("Helius parse error: {e}")))
}

async fn helius_post(
    http: &reqwest::Client,
    url: &str,
    body: &Value,
    api_key: &str,
) -> Result<Value, AppError> {
    let resp = http
        .post(url)
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {api_key}"))
        .json(body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Helius POST error: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body_text = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!(
            "Helius {status}: {body_text}"
        )));
    }

    resp.json::<Value>()
        .await
        .map_err(|e| AppError::Internal(format!("Helius parse error: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// 1. helius_tx_history — Enhanced Transactions GET
// ──────────────────────────────────────────────────────────────────────────────

/// Parsed transaction history for a wallet address.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusTxHistoryParams {
    /// Wallet address. Defaults to authenticated user.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Max transactions to return (1–100). Default 25.
    #[serde(default)]
    pub limit: Option<u32>,
    /// Pagination cursor — signature to start before.
    #[serde(default)]
    pub before: Option<String>,
    /// Pagination cursor — signature to start after.
    #[serde(default)]
    pub after: Option<String>,
    /// Filter by transaction type: SWAP, TRANSFER, NFT_SALE, STAKE, etc.
    #[serde(rename = "type", default)]
    pub tx_type: Option<String>,
    /// Filter by source protocol: JUPITER, MAGIC_EDEN, RAYDIUM, etc.
    #[serde(default)]
    pub source: Option<String>,
}

pub fn validate_helius_tx_history_params(_p: &HeliusTxHistoryParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_helius_tx_history(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusTxHistoryParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let limit = params.limit.unwrap_or(25).min(100).max(1);

    let mut url = format!(
        "{HELIUS_ENHANCED_TX_API}/v0/addresses/{target}/transactions?limit={limit}"
    );
    if let Some(before) = &params.before {
        url.push_str(&format!("&before={before}"));
    }
    if let Some(after) = &params.after {
        url.push_str(&format!("&after={after}"));
    }
    if let Some(tx_type) = &params.tx_type {
        url.push_str(&format!("&type={tx_type}"));
    }
    if let Some(source) = &params.source {
        url.push_str(&format!("&source={source}"));
    }

    let data = helius_get(http, &url, key).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_tx_history".into(),
            description: format!("{count} parsed transaction(s) for {}", short_addr(target)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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
// 2. helius_parse_transactions — Enhanced Transactions POST
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusParseTransactionsParams {
    /// Transaction signatures to parse (max 100).
    pub transactions: Vec<String>,
    /// Commitment level: finalized | confirmed. Default: finalized.
    #[serde(default)]
    pub commitment: Option<String>,
}

pub fn validate_helius_parse_transactions_params(p: &HeliusParseTransactionsParams) -> Result<(), AppError> {
    if p.transactions.is_empty() {
        return Err(AppError::InvalidParams("transactions list cannot be empty".into()));
    }
    if p.transactions.len() > 100 {
        return Err(AppError::InvalidParams("max 100 transactions per request".into()));
    }
    Ok(())
}

pub async fn build_helius_parse_transactions(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusParseTransactionsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_parse_transactions_params(params)?;

    let commitment = params.commitment.as_deref().unwrap_or("finalized");
    let url = format!("{HELIUS_ENHANCED_TX_API}/v0/transactions?commitment={commitment}");

    let body = json!({ "transactions": params.transactions });
    let data = helius_post(http, &url, &body, key).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_parse_transactions".into(),
            description: format!("{count} transaction(s) parsed"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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
// 3. helius_get_assets — DAS getAssetsByOwner
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetAssetsParams {
    /// Wallet address. Defaults to authenticated user.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Page number (1-based). Default 1.
    #[serde(default)]
    pub page: Option<u32>,
    /// Items per page (max 1000). Default 100.
    #[serde(default)]
    pub limit: Option<u32>,
    /// Sort by: "created", "updated", "recent_action", "id". Default "recent_action".
    #[serde(default)]
    pub sort_by: Option<String>,
    /// Token type filter: "all", "fungible", "nonFungible", "regularNft", "compressedNft".
    #[serde(default)]
    pub token_type: Option<String>,
    /// Include native SOL balance in response.
    #[serde(default)]
    pub show_native_balance: Option<bool>,
}

pub fn validate_helius_get_assets_params(_p: &HeliusGetAssetsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_helius_get_assets(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusGetAssetsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let page = params.page.unwrap_or(1).max(1);
    let limit = params.limit.unwrap_or(100).min(1000).max(1);
    let sort_by = params.sort_by.as_deref().unwrap_or("recent_action");
    let token_type = params.token_type.as_deref().unwrap_or("all");
    let show_native = params.show_native_balance.unwrap_or(false);

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getAssetsByOwner",
        "params": {
            "ownerAddress": target,
            "page": page,
            "limit": limit,
            "sortBy": { "sortBy": sort_by, "sortDirection": "desc" },
            "displayOptions": {
                "showFungible": true,
                "showNativeBalance": show_native,
                "tokenType": token_type,
            }
        }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let items = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let total = result.get("total").and_then(|v| v.as_u64()).unwrap_or(items as u64);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_assets".into(),
            description: format!("{items} asset(s) (of {total} total) for {}", short_addr(target)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// 4. helius_get_asset — DAS getAsset
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetAssetParams {
    /// Mint address of the asset.
    pub id: String,
}

pub fn validate_helius_get_asset_params(p: &HeliusGetAssetParams) -> Result<(), AppError> {
    if p.id.is_empty() {
        return Err(AppError::InvalidParams("id (mint address) is required".into()));
    }
    Ok(())
}

pub async fn build_helius_get_asset(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusGetAssetParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_asset_params(params)?;

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getAsset",
        "params": {
            "id": params.id,
            "displayOptions": { "showFungible": true }
        }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let name = result.get("content").and_then(|c| c.get("metadata")).and_then(|m| m.get("name")).and_then(|n| n.as_str()).unwrap_or("unknown");

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_asset".into(),
            description: format!("Asset: {name} ({})", short_addr(&params.id)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// 5. helius_search_assets — DAS searchAssets
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusSearchAssetsParams {
    /// Filter by owner address.
    #[serde(default)]
    pub owner: Option<String>,
    /// Filter by creator address.
    #[serde(default)]
    pub creator: Option<String>,
    /// Filter by collection mint address.
    #[serde(default)]
    pub collection: Option<String>,
    /// Filter only compressed NFTs.
    #[serde(default)]
    pub compressed: Option<bool>,
    /// Token type: "all", "fungible", "nonFungible", "regularNft", "compressedNft".
    #[serde(default)]
    pub token_type: Option<String>,
    /// Page (1-based). Default 1.
    #[serde(default)]
    pub page: Option<u32>,
    /// Items per page (max 1000). Default 100.
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_search_assets_params(p: &HeliusSearchAssetsParams) -> Result<(), AppError> {
    if p.owner.is_none() && p.creator.is_none() && p.collection.is_none() {
        return Err(AppError::InvalidParams(
            "at least one of owner, creator, or collection is required".into(),
        ));
    }
    Ok(())
}

pub async fn build_helius_search_assets(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusSearchAssetsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_search_assets_params(params)?;

    let page = params.page.unwrap_or(1).max(1);
    let limit = params.limit.unwrap_or(100).min(1000).max(1);

    let owner = params.owner.as_deref().unwrap_or(wallet);
    let mut search_params = json!({
        "ownerAddress": owner,
        "page": page,
        "limit": limit,
    });
    if let Some(creator) = &params.creator {
        search_params["creatorAddress"] = json!(creator);
    }
    if let Some(collection) = &params.collection {
        search_params["grouping"] = json!([["collection", collection]]);
    }
    if let Some(compressed) = params.compressed {
        search_params["compressed"] = json!(compressed);
    }
    if let Some(token_type) = &params.token_type {
        search_params["tokenType"] = json!(token_type);
    }

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "searchAssets",
        "params": search_params
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let items = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let total = result.get("total").and_then(|v| v.as_u64()).unwrap_or(items as u64);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_search_assets".into(),
            description: format!("{items} asset(s) found (of {total} total)"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// 6. helius_nft_editions — DAS getNftEditions
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusNftEditionsParams {
    /// Master edition mint address.
    pub mint: String,
    /// Page (1-based). Default 1.
    #[serde(default)]
    pub page: Option<u32>,
    /// Items per page (max 1000). Default 100.
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_nft_editions_params(p: &HeliusNftEditionsParams) -> Result<(), AppError> {
    if p.mint.is_empty() {
        return Err(AppError::InvalidParams("mint address is required".into()));
    }
    Ok(())
}

pub async fn build_helius_nft_editions(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusNftEditionsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_nft_editions_params(params)?;

    let page = params.page.unwrap_or(1).max(1);
    let limit = params.limit.unwrap_or(100).min(1000).max(1);

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getNftEditions",
        "params": {
            "masterEditionMint": params.mint,
            "page": page,
            "limit": limit,
        }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let editions = result.get("editions").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let total = result.get("total").and_then(|v| v.as_u64()).unwrap_or(editions as u64);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_nft_editions".into(),
            description: format!("{editions} edition(s) of {total} for {}", short_addr(&params.mint)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// 7. helius_get_token_accounts — DAS getTokenAccounts
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetTokenAccountsParams {
    /// Filter by token mint address.
    #[serde(default)]
    pub mint: Option<String>,
    /// Filter by owner address. Defaults to authenticated user.
    #[serde(default)]
    pub owner: Option<String>,
    /// Page (1-based). Default 1.
    #[serde(default)]
    pub page: Option<u32>,
    /// Items per page (max 1000). Default 100.
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_get_token_accounts_params(p: &HeliusGetTokenAccountsParams) -> Result<(), AppError> {
    if p.mint.is_none() && p.owner.is_none() {
        return Err(AppError::InvalidParams("at least one of mint or owner is required".into()));
    }
    Ok(())
}

pub async fn build_helius_get_token_accounts(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusGetTokenAccountsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_token_accounts_params(params)?;

    let page = params.page.unwrap_or(1).max(1);
    let limit = params.limit.unwrap_or(100).min(1000).max(1);
    let owner = params.owner.as_deref().unwrap_or(wallet);

    let mut das_params = json!({
        "page": page,
        "limit": limit,
        "owner": owner,
    });
    if let Some(mint) = &params.mint {
        das_params["mint"] = json!(mint);
    }

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getTokenAccounts",
        "params": das_params
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let items = result.get("token_accounts").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let total = result.get("total").and_then(|v| v.as_u64()).unwrap_or(items as u64);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_token_accounts".into(),
            description: format!("{items} token account(s) (of {total} total) for {}", short_addr(owner)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// 8. helius_asset_signatures — DAS getSignaturesForAsset
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusAssetSignaturesParams {
    /// Asset mint address.
    pub id: String,
    /// Page (1-based). Default 1.
    #[serde(default)]
    pub page: Option<u32>,
    /// Items per page (max 1000). Default 100.
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_asset_signatures_params(p: &HeliusAssetSignaturesParams) -> Result<(), AppError> {
    if p.id.is_empty() {
        return Err(AppError::InvalidParams("id (mint address) is required".into()));
    }
    Ok(())
}

pub async fn build_helius_asset_signatures(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusAssetSignaturesParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_asset_signatures_params(params)?;

    let page = params.page.unwrap_or(1).max(1);
    let limit = params.limit.unwrap_or(100).min(1000).max(1);

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getSignaturesForAsset",
        "params": {
            "id": params.id,
            "page": page,
            "limit": limit,
        }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let items = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let total = result.get("total").and_then(|v| v.as_u64()).unwrap_or(items as u64);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_asset_signatures".into(),
            description: format!("{items} signature(s) (of {total}) for asset {}", short_addr(&params.id)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// 9. helius_priority_fee — getPriorityFeeEstimate
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusPriorityFeeParams {
    /// Account keys to estimate fee for (alternative to transaction).
    #[serde(default)]
    pub account_keys: Option<Vec<String>>,
    /// Serialized transaction (base58/base64) for accurate estimation.
    #[serde(default)]
    pub transaction: Option<String>,
    /// Fee level: Min | Low | Medium | High | VeryHigh | UnsafeMax. Default Medium.
    #[serde(default)]
    pub level: Option<String>,
    /// Return all priority fee levels.
    #[serde(default)]
    pub include_all_levels: Option<bool>,
    /// Lookback slots (1–150). Default 150.
    #[serde(default)]
    pub lookback_slots: Option<u32>,
}

pub fn validate_helius_priority_fee_params(p: &HeliusPriorityFeeParams) -> Result<(), AppError> {
    if p.account_keys.is_none() && p.transaction.is_none() {
        return Err(AppError::InvalidParams(
            "at least one of account_keys or transaction is required".into(),
        ));
    }
    Ok(())
}

pub async fn build_helius_priority_fee(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusPriorityFeeParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_priority_fee_params(params)?;

    let level = params.level.as_deref().unwrap_or("Medium");
    let include_all = params.include_all_levels.unwrap_or(true);
    let lookback = params.lookback_slots.unwrap_or(150).min(150).max(1);

    let mut options = json!({
        "priorityLevel": level,
        "includeAllPriorityFeeLevels": include_all,
        "lookbackSlots": lookback,
        "evaluateEmptySlotAsZero": false,
    });

    let mut fee_params = json!({ "options": options });
    if let Some(keys) = &params.account_keys {
        fee_params["accountKeys"] = json!(keys);
    }
    if let Some(tx) = &params.transaction {
        fee_params["transaction"] = json!(tx);
        options["includeVote"] = json!(false);
        fee_params["options"] = options;
    }

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getPriorityFeeEstimate",
        "params": [fee_params]
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let fee = result.get("priorityFeeEstimate").and_then(|v| v.as_f64()).unwrap_or(0.0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_priority_fee".into(),
            description: format!("Estimated priority fee ({level}): {fee:.0} microlamports"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// 10. helius_wallet_identity — Wallet API identity lookup
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusWalletIdentityParams {
    /// Wallet address to identify. Defaults to authenticated user.
    #[serde(default)]
    pub wallet: Option<String>,
}

pub fn validate_helius_wallet_identity_params(_p: &HeliusWalletIdentityParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_helius_wallet_identity(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusWalletIdentityParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);

    let url = format!("{HELIUS_WALLET_API}/v1/wallet/{target}/identity");
    let data = helius_get(http, &url, key).await?;
    let name = data.get("name").and_then(|v| v.as_str()).unwrap_or("Unknown");

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_wallet_identity".into(),
            description: format!("Wallet {} identified as: {name}", short_addr(target)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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
// 11. helius_batch_identity — Wallet API batch identity lookup
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusBatchIdentityParams {
    /// Wallet addresses to identify (max 100).
    pub addresses: Vec<String>,
}

pub fn validate_helius_batch_identity_params(p: &HeliusBatchIdentityParams) -> Result<(), AppError> {
    if p.addresses.is_empty() {
        return Err(AppError::InvalidParams("addresses cannot be empty".into()));
    }
    if p.addresses.len() > 100 {
        return Err(AppError::InvalidParams("max 100 addresses per request".into()));
    }
    Ok(())
}

pub async fn build_helius_batch_identity(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusBatchIdentityParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_batch_identity_params(params)?;

    let url = format!("{HELIUS_WALLET_API}/v1/wallet/batch-identity");
    let body = json!({ "addresses": params.addresses });
    let data = helius_post(http, &url, &body, key).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_batch_identity".into(),
            description: format!("{count} wallet identities resolved"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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
// 12. helius_wallet_balances — Wallet API /v1/wallet/{wallet}/balances
// ──────────────────────────────────────────────────────────────────────────────

/// Full token + NFT balances with USD values for a wallet.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusWalletBalancesParams {
    #[serde(default)]
    pub wallet: Option<String>,
}

pub fn validate_helius_wallet_balances_params(_p: &HeliusWalletBalancesParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_helius_wallet_balances(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusWalletBalancesParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let url = format!("{HELIUS_WALLET_API}/v1/wallet/{target}/balances");
    let data = helius_get(http, &url, key).await?;

    let native_sol = data
        .get("nativeBalance")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0)
        / 1_000_000_000.0;
    let token_count = data
        .get("tokens")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_wallet_balances".into(),
            description: format!("{} — {native_sol:.4} SOL + {token_count} token(s)", short_addr(target)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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
// 13. helius_wallet_history — Wallet API /v1/wallet/{wallet}/history
// ──────────────────────────────────────────────────────────────────────────────

/// Complete transaction history with balance changes for a wallet.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusWalletHistoryParams {
    #[serde(default)]
    pub wallet: Option<String>,
    #[serde(default)]
    pub before: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_wallet_history_params(_p: &HeliusWalletHistoryParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_helius_wallet_history(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusWalletHistoryParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let limit = params.limit.unwrap_or(25).min(100).max(1);

    let mut url = format!("{HELIUS_WALLET_API}/v1/wallet/{target}/history?limit={limit}");
    if let Some(before) = &params.before {
        url.push_str(&format!("&before={before}"));
    }

    let data = helius_get(http, &url, key).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_wallet_history".into(),
            description: format!("{count} transaction(s) for {}", short_addr(target)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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
// 14. helius_wallet_transfers — Wallet API /v1/wallet/{wallet}/transfers
// ──────────────────────────────────────────────────────────────────────────────

/// Incoming and outgoing token transfers for a wallet.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusWalletTransfersParams {
    #[serde(default)]
    pub wallet: Option<String>,
    #[serde(default)]
    pub before: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_wallet_transfers_params(_p: &HeliusWalletTransfersParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_helius_wallet_transfers(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusWalletTransfersParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let limit = params.limit.unwrap_or(25).min(100).max(1);

    let mut url = format!("{HELIUS_WALLET_API}/v1/wallet/{target}/transfers?limit={limit}");
    if let Some(before) = &params.before {
        url.push_str(&format!("&before={before}"));
    }

    let data = helius_get(http, &url, key).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_wallet_transfers".into(),
            description: format!("{count} transfer(s) for {}", short_addr(target)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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
// 15. helius_wallet_funded_by — Wallet API /v1/wallet/{wallet}/funded-by
// ──────────────────────────────────────────────────────────────────────────────

/// Discover the original wallet that funded this address.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusWalletFundedByParams {
    #[serde(default)]
    pub wallet: Option<String>,
}

pub fn validate_helius_wallet_funded_by_params(_p: &HeliusWalletFundedByParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_helius_wallet_funded_by(
    http: &reqwest::Client,
    wallet: &str,
    params: &HeliusWalletFundedByParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);

    let url = format!("{HELIUS_WALLET_API}/v1/wallet/{target}/funded-by");
    let data = helius_get(http, &url, key).await?;

    let funder = data
        .get("wallet")
        .and_then(|v| v.as_str())
        .map(|w| short_addr(w))
        .unwrap_or_else(|| "unknown".into());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_wallet_funded_by".into(),
            description: format!("{} originally funded by {funder}", short_addr(target)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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
// DAS: getAssetBatch
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetAssetBatchParams {
    /// Mint addresses (max 1000).
    pub ids: Vec<String>,
}

pub fn validate_helius_get_asset_batch_params(p: &HeliusGetAssetBatchParams) -> Result<(), AppError> {
    if p.ids.is_empty() {
        return Err(AppError::InvalidParams("ids cannot be empty".into()));
    }
    if p.ids.len() > 1000 {
        return Err(AppError::InvalidParams("max 1000 ids per request".into()));
    }
    Ok(())
}

pub async fn build_helius_get_asset_batch(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusGetAssetBatchParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_asset_batch_params(params)?;

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getAssetBatch",
        "params": { "ids": params.ids, "displayOptions": { "showFungible": true } }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let count = result.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_asset_batch".into(),
            description: format!("{count} asset(s) fetched"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// DAS: getAssetsByCreator
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetAssetsByCreatorParams {
    pub creator_address: String,
    #[serde(default)]
    pub only_verified: Option<bool>,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub limit: Option<u32>,
    #[serde(default)]
    pub sort_by: Option<String>,
}

pub fn validate_helius_get_assets_by_creator_params(p: &HeliusGetAssetsByCreatorParams) -> Result<(), AppError> {
    if p.creator_address.is_empty() {
        return Err(AppError::InvalidParams("creator_address is required".into()));
    }
    Ok(())
}

pub async fn build_helius_get_assets_by_creator(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusGetAssetsByCreatorParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_assets_by_creator_params(params)?;

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getAssetsByCreator",
        "params": {
            "creatorAddress": params.creator_address,
            "onlyVerified": params.only_verified.unwrap_or(false),
            "page": params.page.unwrap_or(1),
            "limit": params.limit.unwrap_or(100).min(1000),
            "sortBy": { "sortBy": params.sort_by.as_deref().unwrap_or("recent_action"), "sortDirection": "desc" },
        }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let items = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let total = result.get("total").and_then(|v| v.as_u64()).unwrap_or(items as u64);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_assets_by_creator".into(),
            description: format!("{items} asset(s) (of {total}) by creator {}", short_addr(&params.creator_address)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// DAS: getAssetsByAuthority
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetAssetsByAuthorityParams {
    pub authority_address: String,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub limit: Option<u32>,
    #[serde(default)]
    pub sort_by: Option<String>,
}

pub fn validate_helius_get_assets_by_authority_params(p: &HeliusGetAssetsByAuthorityParams) -> Result<(), AppError> {
    if p.authority_address.is_empty() {
        return Err(AppError::InvalidParams("authority_address is required".into()));
    }
    Ok(())
}

pub async fn build_helius_get_assets_by_authority(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusGetAssetsByAuthorityParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_assets_by_authority_params(params)?;

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getAssetsByAuthority",
        "params": {
            "authorityAddress": params.authority_address,
            "page": params.page.unwrap_or(1),
            "limit": params.limit.unwrap_or(100).min(1000),
            "sortBy": { "sortBy": params.sort_by.as_deref().unwrap_or("recent_action"), "sortDirection": "desc" },
        }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let items = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let total = result.get("total").and_then(|v| v.as_u64()).unwrap_or(items as u64);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_assets_by_authority".into(),
            description: format!("{items} asset(s) (of {total}) for authority {}", short_addr(&params.authority_address)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// DAS: getAssetsByGroup
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetAssetsByGroupParams {
    /// Group key: "collection", "parent", "family".
    pub group_key: String,
    /// Group value (e.g. collection mint address).
    pub group_value: String,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub limit: Option<u32>,
    #[serde(default)]
    pub sort_by: Option<String>,
}

pub fn validate_helius_get_assets_by_group_params(p: &HeliusGetAssetsByGroupParams) -> Result<(), AppError> {
    if p.group_value.is_empty() {
        return Err(AppError::InvalidParams("group_value is required".into()));
    }
    Ok(())
}

pub async fn build_helius_get_assets_by_group(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusGetAssetsByGroupParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_assets_by_group_params(params)?;

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getAssetsByGroup",
        "params": {
            "groupKey": params.group_key,
            "groupValue": params.group_value,
            "page": params.page.unwrap_or(1),
            "limit": params.limit.unwrap_or(100).min(1000),
            "sortBy": { "sortBy": params.sort_by.as_deref().unwrap_or("recent_action"), "sortDirection": "desc" },
        }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);
    let items = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    let total = result.get("total").and_then(|v| v.as_u64()).unwrap_or(items as u64);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_assets_by_group".into(),
            description: format!("{items} asset(s) (of {total}) in {} {}", params.group_key, short_addr(&params.group_value)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// DAS: getAssetProof
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetAssetProofParams {
    /// Compressed NFT mint address.
    pub id: String,
}

pub fn validate_helius_get_asset_proof_params(p: &HeliusGetAssetProofParams) -> Result<(), AppError> {
    if p.id.is_empty() {
        return Err(AppError::InvalidParams("id is required".into()));
    }
    Ok(())
}

pub async fn build_helius_get_asset_proof(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusGetAssetProofParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_asset_proof_params(params)?;

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getAssetProof",
        "params": { "id": params.id }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_asset_proof".into(),
            description: format!("Merkle proof for asset {}", short_addr(&params.id)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// DAS: getAssetProofBatch
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetAssetProofBatchParams {
    /// Compressed NFT mint addresses (max 1000).
    pub ids: Vec<String>,
}

pub fn validate_helius_get_asset_proof_batch_params(p: &HeliusGetAssetProofBatchParams) -> Result<(), AppError> {
    if p.ids.is_empty() {
        return Err(AppError::InvalidParams("ids cannot be empty".into()));
    }
    if p.ids.len() > 1000 {
        return Err(AppError::InvalidParams("max 1000 ids per request".into()));
    }
    Ok(())
}

pub async fn build_helius_get_asset_proof_batch(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusGetAssetProofBatchParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_asset_proof_batch_params(params)?;

    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "getAssetProofBatch",
        "params": { "ids": params.ids }
    });

    let resp = helius_post(http, &url, &body, key).await?;
    let result = resp.get("result").cloned().unwrap_or(resp);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_asset_proof_batch".into(),
            description: format!("Merkle proofs for {} asset(s)", params.ids.len()),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: result,
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
// Webhooks API
// ──────────────────────────────────────────────────────────────────────────────

const HELIUS_WEBHOOKS_API: &str = "https://api-mainnet.helius-rpc.com/v0/webhooks";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusCreateWebhookParams {
    /// HTTPS URL to receive webhook events.
    pub url: String,
    /// Wallet addresses to monitor.
    pub addresses: Vec<String>,
    /// Transaction types to filter: SWAP, NFT_SALE, TRANSFER, etc. Empty = all.
    #[serde(default)]
    pub transaction_types: Option<Vec<String>>,
    /// Encoding: jsonParsed | base58 | base64. Default jsonParsed.
    #[serde(default)]
    pub encoding: Option<String>,
}

pub fn validate_helius_create_webhook_params(p: &HeliusCreateWebhookParams) -> Result<(), AppError> {
    if p.url.is_empty() {
        return Err(AppError::InvalidParams("url is required".into()));
    }
    if p.addresses.is_empty() {
        return Err(AppError::InvalidParams("at least one address is required".into()));
    }
    Ok(())
}

pub async fn build_helius_create_webhook(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusCreateWebhookParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_create_webhook_params(params)?;

    let url = format!("{HELIUS_WEBHOOKS_API}");
    let mut body = json!({
        "webhookURL": params.url,
        "accountAddresses": params.addresses,
        "webhookType": "enhanced",
        "encoding": params.encoding.as_deref().unwrap_or("jsonParsed"),
    });
    if let Some(types) = &params.transaction_types {
        body["transactionTypes"] = json!(types);
    }

    let data = helius_post(http, &url, &body, key).await?;
    let webhook_id = data.get("webhookID").and_then(|v| v.as_str()).unwrap_or("unknown");

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_create_webhook".into(),
            description: format!("Webhook created: {webhook_id} → {}", params.url),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusListWebhooksParams {}

pub fn validate_helius_list_webhooks_params(_p: &HeliusListWebhooksParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_helius_list_webhooks(
    http: &reqwest::Client,
    _wallet: &str,
    _params: &HeliusListWebhooksParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let url = format!("{HELIUS_WEBHOOKS_API}");
    let data = helius_get(http, &url, key).await?;
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_list_webhooks".into(),
            description: format!("{count} webhook(s) configured"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusGetWebhookParams {
    pub webhook_id: String,
}

pub fn validate_helius_get_webhook_params(p: &HeliusGetWebhookParams) -> Result<(), AppError> {
    if p.webhook_id.is_empty() {
        return Err(AppError::InvalidParams("webhook_id is required".into()));
    }
    Ok(())
}

pub async fn build_helius_get_webhook(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusGetWebhookParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_get_webhook_params(params)?;

    let url = format!("{HELIUS_WEBHOOKS_API}/{}", params.webhook_id);
    let data = helius_get(http, &url, key).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_get_webhook".into(),
            description: format!("Webhook {}", params.webhook_id),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusUpdateWebhookParams {
    pub webhook_id: String,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub addresses: Option<Vec<String>>,
    #[serde(default)]
    pub transaction_types: Option<Vec<String>>,
}

pub fn validate_helius_update_webhook_params(p: &HeliusUpdateWebhookParams) -> Result<(), AppError> {
    if p.webhook_id.is_empty() {
        return Err(AppError::InvalidParams("webhook_id is required".into()));
    }
    Ok(())
}

pub async fn build_helius_update_webhook(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusUpdateWebhookParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_update_webhook_params(params)?;

    let endpoint = format!("{HELIUS_WEBHOOKS_API}/{}", params.webhook_id);
    let mut body = json!({});
    if let Some(url) = &params.url { body["webhookURL"] = json!(url); }
    if let Some(addrs) = &params.addresses { body["accountAddresses"] = json!(addrs); }
    if let Some(types) = &params.transaction_types { body["transactionTypes"] = json!(types); }

    let resp = http
        .put(&endpoint)
        .header("Authorization", format!("Bearer {key}"))
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Helius PUT error: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!("Helius {status}: {text}")));
    }
    let data = resp.json::<Value>().await
        .map_err(|e| AppError::Internal(format!("Helius parse error: {e}")))?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_update_webhook".into(),
            description: format!("Webhook {} updated", params.webhook_id),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusToggleWebhookParams {
    pub webhook_id: String,
    pub enabled: bool,
}

pub fn validate_helius_toggle_webhook_params(p: &HeliusToggleWebhookParams) -> Result<(), AppError> {
    if p.webhook_id.is_empty() {
        return Err(AppError::InvalidParams("webhook_id is required".into()));
    }
    Ok(())
}

pub async fn build_helius_toggle_webhook(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusToggleWebhookParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_toggle_webhook_params(params)?;

    let endpoint = format!("{HELIUS_WEBHOOKS_API}/{}", params.webhook_id);
    let body = json!({ "enabled": params.enabled });

    let resp = http
        .patch(&endpoint)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Helius PATCH error: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!("Helius {status}: {text}")));
    }
    let data = resp.json::<Value>().await
        .map_err(|e| AppError::Internal(format!("Helius parse error: {e}")))?;

    let state = if params.enabled { "enabled" } else { "disabled" };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_toggle_webhook".into(),
            description: format!("Webhook {} {state}", params.webhook_id),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusDeleteWebhookParams {
    pub webhook_id: String,
}

pub fn validate_helius_delete_webhook_params(p: &HeliusDeleteWebhookParams) -> Result<(), AppError> {
    if p.webhook_id.is_empty() {
        return Err(AppError::InvalidParams("webhook_id is required".into()));
    }
    Ok(())
}

pub async fn build_helius_delete_webhook(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusDeleteWebhookParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_delete_webhook_params(params)?;

    let endpoint = format!("{HELIUS_WEBHOOKS_API}/{}", params.webhook_id);
    let resp = http
        .delete(&endpoint)
        .header("Authorization", format!("Bearer {key}"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Helius DELETE error: {e}")))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!("Helius {status}: {text}")));
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_delete_webhook".into(),
            description: format!("Webhook {} deleted", params.webhook_id),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: json!({ "webhookID": params.webhook_id, "deleted": true }),
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
// Helius Sender API
// ──────────────────────────────────────────────────────────────────────────────

const HELIUS_SENDER_API: &str = "https://sender.helius-rpc.com";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusSendTransactionParams {
    /// Signed transaction (base64 encoded).
    pub transaction: String,
    /// Skip preflight checks. Must be true for Sender API.
    #[serde(default)]
    pub skip_preflight: Option<bool>,
    /// Max retries. Must be 0 for Sender API.
    #[serde(default)]
    pub max_retries: Option<u32>,
}

pub fn validate_helius_send_transaction_params(p: &HeliusSendTransactionParams) -> Result<(), AppError> {
    if p.transaction.is_empty() {
        return Err(AppError::InvalidParams("transaction is required".into()));
    }
    Ok(())
}

pub async fn build_helius_send_transaction(
    http: &reqwest::Client,
    _wallet: &str,
    params: &HeliusSendTransactionParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_send_transaction_params(params)?;

    let url = format!("{HELIUS_SENDER_API}/fast");
    let body = json!({
        "jsonrpc": "2.0",
        "id": Uuid::new_v4().to_string(),
        "method": "sendTransaction",
        "params": [
            params.transaction,
            {
                "encoding": "base64",
                "skipPreflight": params.skip_preflight.unwrap_or(true),
                "maxRetries": params.max_retries.unwrap_or(0),
                "preflightCommitment": "confirmed"
            }
        ]
    });

    let data = helius_post(http, &url, &body, key).await?;
    let sig = data.get("result").and_then(|v| v.as_str()).unwrap_or("unknown");

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_send_transaction".into(),
            description: format!("Transaction submitted via Helius Sender: {}", short_addr(sig)),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
            warnings: vec!["Transaction submitted with skipPreflight=true via Helius Sender for optimal landing rate.".into()],
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
// ZK Compression API
// ──────────────────────────────────────────────────────────────────────────────

async fn zk_call(http: &reqwest::Client, api_key: &str, method: &str, params: Value) -> Result<Value, AppError> {
    let url = format!("{HELIUS_RPC_API}/");
    let body = json!({ "jsonrpc": "2.0", "id": Uuid::new_v4().to_string(), "method": method, "params": params });
    let resp = helius_post(http, &url, &body, api_key).await?;
    Ok(resp.get("result").cloned().unwrap_or(resp))
}

macro_rules! zk_simple {
    ($fn_name:ident, $validate_name:ident, $param_type:ident, $method:expr, $desc_fn:expr) => {
        pub fn $validate_name(_p: &$param_type) -> Result<(), AppError> { Ok(()) }
        pub async fn $fn_name(
            http: &reqwest::Client, _wallet: &str, params: &$param_type, api_key: Option<&str>,
        ) -> Result<BuildResponse, AppError> {
            let key = require_key(api_key)?;
            let result = zk_call(http, key, $method, serde_json::to_value(params).unwrap_or(json!({}))).await?;
            let description: String = ($desc_fn)(params, &result);
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: Uuid::new_v4().to_string(),
                    action_type: stringify!($fn_name).trim_start_matches("build_").to_string().replace('_', "_"),
                    description,
                    estimated_fee: "0".into(),
                    estimated_refund: None,
                    params: result,
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
    };
}

// Individual ZK Compression structs and implementations

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusZkAccountParams {
    #[serde(default)]
    pub address: Option<String>,
    #[serde(default)]
    pub hash: Option<String>,
}

pub fn validate_helius_zk_account_params(p: &HeliusZkAccountParams) -> Result<(), AppError> {
    if p.address.is_none() && p.hash.is_none() {
        return Err(AppError::InvalidParams("address or hash is required".into()));
    }
    Ok(())
}

pub async fn build_helius_zk_compressed_account(
    http: &reqwest::Client, _wallet: &str, params: &HeliusZkAccountParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_zk_account_params(params)?;
    let hash = params.hash.as_deref().or(params.address.as_deref()).unwrap_or("");
    let result = zk_call(http, key, "getCompressedAccount", json!([hash])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_compressed_account".into(),
            description: "Compressed account info".into(),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusZkMultipleAccountsParams {
    pub addresses: Option<Vec<String>>,
    pub hashes: Option<Vec<String>>,
}

pub fn validate_helius_zk_multiple_accounts_params(p: &HeliusZkMultipleAccountsParams) -> Result<(), AppError> {
    if p.addresses.as_ref().map(|a| a.is_empty()).unwrap_or(true)
        && p.hashes.as_ref().map(|h| h.is_empty()).unwrap_or(true) {
        return Err(AppError::InvalidParams("addresses or hashes is required".into()));
    }
    Ok(())
}

pub async fn build_helius_zk_multiple_compressed_accounts(
    http: &reqwest::Client, _wallet: &str, params: &HeliusZkMultipleAccountsParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_zk_multiple_accounts_params(params)?;
    let hashes = params.hashes.as_deref().unwrap_or(&[]);
    let addrs = params.addresses.as_deref().unwrap_or(&[]);
    let list: Vec<&str> = if !hashes.is_empty() { hashes.iter().map(|s| s.as_str()).collect() } else { addrs.iter().map(|s| s.as_str()).collect() };
    let result = zk_call(http, key, "getMultipleCompressedAccounts", json!([list])).await?;
    let count = result.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_multiple_compressed_accounts".into(),
            description: format!("{count} compressed account(s)"),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusZkOwnerParams {
    #[serde(default)]
    pub wallet: Option<String>,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_zk_owner_params(_p: &HeliusZkOwnerParams) -> Result<(), AppError> { Ok(()) }

pub async fn build_helius_zk_compressed_balance_by_owner(
    http: &reqwest::Client, wallet: &str, params: &HeliusZkOwnerParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let result = zk_call(http, key, "getCompressedBalanceByOwner", json!([target])).await?;
    let balance = result.get("balance").and_then(|v| v.as_u64()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_compressed_balance_by_owner".into(),
            description: format!("Compressed balance for {}: {} lamports", short_addr(target), balance),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

pub async fn build_helius_zk_token_accounts_by_owner(
    http: &reqwest::Client, wallet: &str, params: &HeliusZkOwnerParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let result = zk_call(http, key, "getCompressedTokenAccountsByOwner", json!([target])).await?;
    let count = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_token_accounts_by_owner".into(),
            description: format!("{count} compressed token account(s) for {}", short_addr(target)),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

pub async fn build_helius_zk_token_balances_by_owner(
    http: &reqwest::Client, wallet: &str, params: &HeliusZkOwnerParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let result = zk_call(http, key, "getCompressedTokenBalancesByOwnerV2", json!([target, null, null, null])).await?;
    let count = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_token_balances_by_owner".into(),
            description: format!("{count} compressed token balance(s) for {}", short_addr(target)),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusZkMintParams {
    pub mint: String,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_zk_mint_params(p: &HeliusZkMintParams) -> Result<(), AppError> {
    if p.mint.is_empty() { return Err(AppError::InvalidParams("mint is required".into())); }
    Ok(())
}

pub async fn build_helius_zk_mint_token_holders(
    http: &reqwest::Client, _wallet: &str, params: &HeliusZkMintParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_zk_mint_params(params)?;
    let result = zk_call(http, key, "getCompressedMintTokenHolders", json!([params.mint, null, null])).await?;
    let count = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_mint_token_holders".into(),
            description: format!("{count} holder(s) for compressed mint {}", short_addr(&params.mint)),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusZkSignaturesParams {
    #[serde(default)]
    pub address: Option<String>,
    #[serde(default)]
    pub hash: Option<String>,
    #[serde(default)]
    pub wallet: Option<String>,
    #[serde(default)]
    pub page: Option<u32>,
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_helius_zk_signatures_params(_p: &HeliusZkSignaturesParams) -> Result<(), AppError> { Ok(()) }

pub async fn build_helius_zk_compression_signatures_for_owner(
    http: &reqwest::Client, wallet: &str, params: &HeliusZkSignaturesParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let result = zk_call(http, key, "getCompressionSignaturesForOwner", json!([target])).await?;
    let count = result.get("items").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_compression_signatures_for_owner".into(),
            description: format!("{count} compression signature(s) for {}", short_addr(target)),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

pub async fn build_helius_zk_transaction_with_compression(
    http: &reqwest::Client, _wallet: &str, params: &HeliusZkSignaturesParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let sig = params.hash.as_deref().or(params.address.as_deref()).unwrap_or("");
    if sig.is_empty() { return Err(AppError::InvalidParams("hash (signature) is required".into())); }
    let result = zk_call(http, key, "getTransactionWithCompressionInfo", json!([sig])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_transaction_with_compression".into(),
            description: format!("Compression info for tx {}", short_addr(sig)),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct HeliusZkIndexerParams {}

pub fn validate_helius_zk_indexer_params(_p: &HeliusZkIndexerParams) -> Result<(), AppError> { Ok(()) }

pub async fn build_helius_zk_indexer_health(
    http: &reqwest::Client, _wallet: &str, _params: &HeliusZkIndexerParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let result = zk_call(http, key, "getIndexerHealth", json!([])).await?;
    let status = result.get("status").and_then(|v| v.as_str()).unwrap_or("unknown");
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_indexer_health".into(),
            description: format!("ZK indexer status: {status}"),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

pub async fn build_helius_zk_indexer_slot(
    http: &reqwest::Client, _wallet: &str, _params: &HeliusZkIndexerParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    let result = zk_call(http, key, "getIndexerSlot", json!([])).await?;
    let slot = result.as_u64().unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_indexer_slot".into(),
            description: format!("ZK indexer current slot: {slot}"),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

pub async fn build_helius_zk_validity_proof(
    http: &reqwest::Client, _wallet: &str, params: &HeliusZkAccountParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_zk_account_params(params)?;
    let hash = params.hash.as_deref().or(params.address.as_deref()).unwrap_or("");
    let result = zk_call(http, key, "getValidityProof", json!([[hash], []])).await?;
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_validity_proof".into(),
            description: "ZK validity proof".into(),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusZkNewAddressEntry {
    pub address: String,
    pub tree: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusZkNewAddressProofsParams {
    pub addresses: Vec<HeliusZkNewAddressEntry>,
}

pub fn validate_helius_zk_new_address_proofs_params(p: &HeliusZkNewAddressProofsParams) -> Result<(), AppError> {
    if p.addresses.is_empty() { return Err(AppError::InvalidParams("addresses is required".into())); }
    Ok(())
}

pub async fn build_helius_zk_new_address_proofs(
    http: &reqwest::Client, _wallet: &str, params: &HeliusZkNewAddressProofsParams, api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    let key = require_key(api_key)?;
    validate_helius_zk_new_address_proofs_params(params)?;
    let entries: Vec<serde_json::Value> = params.addresses.iter()
        .map(|e| json!({ "address": e.address, "tree": e.tree }))
        .collect();
    let result = zk_call(http, key, "getMultipleNewAddressProofsV2", json!(entries)).await?;
    let count = result.as_array().map(|a| a.len()).unwrap_or(0);
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_zk_new_address_proofs".into(),
            description: format!("{count} new address proof(s)"),
            estimated_fee: "0".into(), estimated_refund: None,
            params: result, warnings: vec![], requires_approval: false,
        },
        transaction: None, additional_signers_required: 0, execution_steps: None,
        quote: None, is_cross_chain: false, data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// 42. helius_smart_send — Smart Transaction (optimal priority fee + CU limit)
// ──────────────────────────────────────────────────────────────────────────────
//
// Wraps any pre-built unsigned transaction with:
//   1. Helius getPriorityFeeEstimate → dynamic microlamports/CU
//   2. ComputeBudgetInstruction::set_compute_unit_price(microlamports)
//   3. ComputeBudgetInstruction::set_compute_unit_limit(cu_limit)
//   4. Fresh blockhash
//
// Flow: /actions/build → raw tx → helius_smart_send → optimized tx → wallet sign → submit

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HeliusSmartSendParams {
    /// Base64-encoded unsigned transaction returned by any /actions/build call.
    pub transaction: String,
    /// "Min" | "Low" | "Medium" | "High" | "VeryHigh" | "UnsafeMax" (default: "Medium")
    pub priority_level: Option<String>,
    /// Override compute unit limit (default: 200_000)
    pub compute_unit_limit: Option<u32>,
}

pub fn validate_helius_smart_send_params(p: &HeliusSmartSendParams) -> Result<(), AppError> {
    if p.transaction.is_empty() {
        return Err(AppError::InvalidParams("transaction is required".into()));
    }
    if let Some(lvl) = &p.priority_level {
        match lvl.as_str() {
            "Min" | "Low" | "Medium" | "High" | "VeryHigh" | "UnsafeMax" => {}
            other => return Err(AppError::InvalidParams(
                format!("Invalid priority_level '{other}'. Use: Min/Low/Medium/High/VeryHigh/UnsafeMax")
            )),
        }
    }
    Ok(())
}

pub async fn build_helius_smart_send(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    params: &HeliusSmartSendParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    use solana_sdk::{
        compute_budget::ComputeBudgetInstruction,
        instruction::{AccountMeta, Instruction},
        message::Message,
        transaction::Transaction,
    };

    let key = require_key(api_key)?;

    // 1. Decode the base64 transaction
    let tx_bytes = base64::engine::general_purpose::STANDARD
        .decode(&params.transaction)
        .map_err(|e| AppError::InvalidParams(format!("Invalid base64: {e}")))?;

    let tx: Transaction = bincode::deserialize(&tx_bytes)
        .map_err(|e| AppError::InvalidParams(format!("Invalid transaction: {e}")))?;

    let msg = &tx.message;
    let account_keys = &msg.account_keys;

    let fee_payer = account_keys
        .first()
        .cloned()
        .ok_or_else(|| AppError::InvalidParams("Empty transaction message".into()))?;

    // 2. Decompile compiled instructions back to Instruction objects
    let original_ixs: Vec<Instruction> = msg.instructions.iter().map(|ci| {
        let program_id = account_keys[ci.program_id_index as usize];
        let accounts: Vec<AccountMeta> = ci.accounts.iter().map(|&idx| AccountMeta {
            pubkey: account_keys[idx as usize],
            is_signer: msg.is_signer(idx as usize),
            is_writable: msg.is_writable(idx as usize),
        }).collect();
        Instruction { program_id, accounts, data: ci.data.clone() }
    }).collect();

    // 3. Strip any existing compute budget instructions (we replace them with optimal values)
    let compute_budget_id = solana_sdk::compute_budget::id();
    let non_budget_ixs: Vec<Instruction> = original_ixs
        .into_iter()
        .filter(|ix| ix.program_id != compute_budget_id)
        .collect();

    // 4. Get dynamic priority fee from Helius
    let priority_level = params.priority_level.as_deref().unwrap_or("Medium");
    let key_strings: Vec<String> = account_keys.iter().map(|pk| pk.to_string()).collect();
    let rpc_url = format!("{HELIUS_RPC_API}/");

    let fee_resp = helius_post(http, &rpc_url, &json!({
        "jsonrpc": "2.0",
        "id": "smart-tx",
        "method": "getPriorityFeeEstimate",
        "params": [{ "accountKeys": key_strings, "options": { "priorityLevel": priority_level } }]
    }), key).await?;

    let microlamports: u64 = fee_resp["result"]["priorityFeeEstimate"]
        .as_f64()
        .map(|f| f as u64)
        .unwrap_or(1_000)
        .max(1);

    // 5. Pick CU limit. If the caller fixed it, honour the override; otherwise
    //    measure via simulation: build a probe tx with the wide-open 1.4M
    //    ceiling, simulate, take `units_consumed × 1.2` as the actual limit.
    //    The 1.2× margin absorbs legitimate variance (oracle-account state,
    //    associated-token-account creation) without overpaying for blockspace.
    let cu_limit: u32 = if let Some(explicit) = params.compute_unit_limit {
        explicit
    } else {
        let blockhash_for_probe = rpc
            .get_latest_blockhash_with_retry()
            .map_err(|e| AppError::Internal(format!("Blockhash error: {e}")))?;
        let mut probe_ixs: Vec<Instruction> = vec![
            ComputeBudgetInstruction::set_compute_unit_limit(1_400_000u32),
            ComputeBudgetInstruction::set_compute_unit_price(microlamports),
        ];
        probe_ixs.extend(non_budget_ixs.clone());
        let probe_msg = Message::new_with_blockhash(&probe_ixs, Some(&fee_payer), &blockhash_for_probe);
        let probe_tx = Transaction::new_unsigned(probe_msg);
        let probe_versioned: solana_sdk::transaction::VersionedTransaction = probe_tx.into();

        match rpc.client().simulate_transaction_with_config(
            &probe_versioned,
            solana_client::rpc_config::RpcSimulateTransactionConfig {
                sig_verify: false,
                replace_recent_blockhash: true,
                commitment: Some(solana_sdk::commitment_config::CommitmentConfig::confirmed()),
                encoding: None,
                accounts: None,
                min_context_slot: None,
                inner_instructions: false,
            },
        ) {
            Ok(sim) => match sim.value.units_consumed {
                // 1.2× margin, clamp to [50_000, 1_400_000] so a wildly cheap
                // tx still gets a usable floor and we never exceed Solana's
                // per-tx ceiling.
                Some(units) if units > 0 => {
                    let scaled = ((units as f64) * 1.2).ceil() as u64;
                    scaled.clamp(50_000, 1_400_000) as u32
                }
                _ => 200_000u32,
            },
            Err(_) => 200_000u32, // Sim failed — fall back to a safe default.
        }
    };

    // 6. Build optimized instruction list: [CU limit, CU price, ...original]
    let mut new_ixs: Vec<Instruction> = vec![
        ComputeBudgetInstruction::set_compute_unit_limit(cu_limit),
        ComputeBudgetInstruction::set_compute_unit_price(microlamports),
    ];
    new_ixs.extend(non_budget_ixs);

    // 7. Fresh blockhash for the final tx (probe blockhash is no longer used)
    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Blockhash error: {e}")))?;

    // 8. Rebuild unsigned transaction
    let new_msg = Message::new_with_blockhash(&new_ixs, Some(&fee_payer), &blockhash);
    let new_tx = Transaction::new_unsigned(new_msg);

    let new_bytes = bincode::serialize(&new_tx)
        .map_err(|e| AppError::Internal(format!("Serialize error: {e}")))?;
    let optimized_b64 = base64::engine::general_purpose::STANDARD.encode(&new_bytes);

    // priority fee in SOL: (microlamports/CU × CU_limit) / 1e6 lamports / 1e9 SOL
    let fee_sol = (microlamports as f64 * cu_limit as f64) / 1_000_000.0 / 1_000_000_000.0;

    let warnings = if microlamports > 500_000 {
        vec!["High network congestion — priority fee is elevated".into()]
    } else {
        vec![]
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "helius_smart_send".into(),
            description: format!(
                "Smart transaction ({priority_level} priority): {microlamports} microlamports/CU, {cu_limit} CU limit"
            ),
            estimated_fee: format!("~{fee_sol:.6} SOL priority + ~0.000005 SOL base"),
            estimated_refund: None,
            params: json!({
                "priority_level": priority_level,
                "microlamports_per_cu": microlamports,
                "cu_limit": cu_limit,
                "fee_sol": fee_sol,
            }),
            warnings,
            requires_approval: true,
        },
        transaction: Some(optimized_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

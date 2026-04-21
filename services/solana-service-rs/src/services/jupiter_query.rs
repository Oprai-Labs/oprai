//! Jupiter read-only query actions.
//!
//! Covers the following Jupiter APIs (no transaction required):
//!
//! | Action | API | Endpoint |
//! |---|---|---|
//! | `jup_price` | Price v3 | `GET /price/v3?ids=` |
//! | `jup_token_search` | Tokens v2 | `GET /tokens/v2/search?query=` |
//! | `jup_tokens_tag` | Tokens v2 | `GET /tokens/v2/tag?tag=` |
//! | `jup_tokens_recent` | Tokens v2 | `GET /tokens/v2/recent` |
//! | `jup_tokens_trending` | Tokens v2 | `GET /tokens/v2/category/{category}/{interval}` |
//! | `jup_portfolio_positions` | Portfolio v1 | `GET /portfolio/v1/positions/{address}` |
//! | `jup_staked_jup` | Portfolio v1 | `GET /portfolio/v1/staked-jup/{address}` |
//! | `jup_lend_positions` | Lend v1 Earn | `GET /lend/v1/earn/positions?userPubkey=` |
//! | `jup_lend_earnings` | Lend v1 Earn | `GET /lend/v1/earn/earnings?userPubkey=` |
//! | `jup_pending_invites` | Send v1 | `GET /send/v1/pending-invites?address=` |
//! | `jup_platforms` | Portfolio v1 | `GET /portfolio/v1/platforms` |

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::tokens::resolve_token_address;

// ──────────────────────────────────────────────────────────────────────────────
// API base URLs
// ──────────────────────────────────────────────────────────────────────────────

const JUP_PRICE_API: &str = "https://api.jup.ag/price/v3";
const JUP_TOKENS_API: &str = "https://api.jup.ag/tokens/v2";
const JUP_PORTFOLIO_API: &str = "https://api.jup.ag/portfolio/v1";
const JUP_LEND_EARN_API: &str = "https://api.jup.ag/lend/v1/earn";
const JUP_SEND_API: &str = "https://api.jup.ag/send/v1";

// ──────────────────────────────────────────────────────────────────────────────
// Internal HTTP helper
// ──────────────────────────────────────────────────────────────────────────────

async fn jup_get(
    http: &reqwest::Client,
    url: &str,
    api_key: Option<&str>,
) -> Result<serde_json::Value, AppError> {
    let mut req = http.get(url);
    if let Some(key) = api_key {
        req = req.header("x-api-key", key);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Jupiter GET {url}: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        return Err(AppError::JupiterApiError(format!(
            "Jupiter GET {url} returned {status}: {body}"
        )));
    }
    resp.json()
        .await
        .map_err(|e| AppError::JupiterApiError(format!("Jupiter GET {url} parse error: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// 1. jup_price — Price API v3
// ──────────────────────────────────────────────────────────────────────────────

/// Get USD prices for up to 50 tokens.
///
/// Accepts token symbols ("SOL", "USDC") or mint addresses.
/// Example: `tokens: "SOL,USDC,JUP"`.
///
/// Response always includes: `usdPrice`, `priceChange24h`, `decimals`, `blockId`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupPriceParams {
    /// Comma-separated token symbols or mint addresses. Max 50.
    /// Example: "SOL,USDC,JUP" or "So11111111111111111111111111112,EPjFWdd..."
    pub tokens: String,
}

pub fn validate_jup_price_params(p: &JupPriceParams) -> Result<(), AppError> {
    if p.tokens.trim().is_empty() {
        return Err(AppError::InvalidParams(
            "tokens is required (e.g. \"SOL,USDC,JUP\")".into(),
        ));
    }
    let count = p.tokens.split(',').filter(|t| !t.trim().is_empty()).count();
    if count > 50 {
        return Err(AppError::InvalidParams("maximum 50 tokens per request".into()));
    }
    Ok(())
}

pub async fn build_jup_price(
    http: &reqwest::Client,
    _wallet: &str,
    params: &JupPriceParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_price_params(params)?;

    // Resolve symbols → mint addresses
    let token_entries: Vec<(&str, String)> = params
        .tokens
        .split(',')
        .map(|t| t.trim())
        .filter(|t| !t.is_empty())
        .map(|sym| (sym, resolve_token_address(sym)))
        .collect();

    let ids = token_entries
        .iter()
        .map(|(_, mint)| mint.as_str())
        .collect::<Vec<_>>()
        .join(",");

    let url = format!("{JUP_PRICE_API}?ids={ids}");
    let raw = jup_get(http, &url, api_key).await?;

    // Jupiter Price v3 returns `{ data: { "<mint>": { usdPrice, ... } } }` or
    // directly `{ "<mint>": { usdPrice, ... } }` depending on version.
    let price_map = raw
        .get("data")
        .unwrap_or(&raw);

    // Build human-readable summary
    let summaries: Vec<String> = token_entries
        .iter()
        .map(|(sym, mint)| {
            let entry = price_map.get(mint.as_str())
                .or_else(|| price_map.get(*sym));
            match entry {
                Some(obj) => {
                    let price = obj.get("usdPrice")
                        .or_else(|| obj.get("price"))
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0);
                    let change = obj.get("priceChange24h")
                        .and_then(|v| v.as_f64());
                    match change {
                        Some(c) => format!("{sym}=${price:.4} ({c:+.2}% 24h)"),
                        None => format!("{sym}=${price:.4}"),
                    }
                }
                None => format!("{sym}=N/A"),
            }
        })
        .collect();

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_price".into(),
            description: summaries.join("  |  "),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: raw,
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
// 2. jup_token_search — Tokens API v2 search
// ──────────────────────────────────────────────────────────────────────────────

/// Search for tokens by name, symbol, or mint address.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupTokenSearchParams {
    /// Search term: token name, symbol, or mint address.
    pub query: String,
    /// Max results to return (1–50). Default: 10.
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_jup_token_search_params(p: &JupTokenSearchParams) -> Result<(), AppError> {
    if p.query.trim().is_empty() {
        return Err(AppError::InvalidParams("query is required".into()));
    }
    if let Some(l) = p.limit {
        if l == 0 || l > 50 {
            return Err(AppError::InvalidParams("limit must be between 1 and 50".into()));
        }
    }
    Ok(())
}

pub async fn build_jup_token_search(
    http: &reqwest::Client,
    _wallet: &str,
    params: &JupTokenSearchParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_token_search_params(params)?;
    let encoded = urlencoding_simple(&params.query);
    let url = format!("{JUP_TOKENS_API}/search?query={encoded}");
    let mut data = jup_get(http, &url, api_key).await?;

    // Apply limit on client side if API doesn't support it
    if let (Some(limit), Some(arr)) = (params.limit, data.as_array_mut()) {
        arr.truncate(limit as usize);
    }
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_token_search".into(),
            description: format!("{count} token(s) found for \"{}\"", params.query),
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
// 3. jup_tokens_tag — Tokens API v2 by tag
// ──────────────────────────────────────────────────────────────────────────────

/// List tokens filtered by a tag.
///
/// Common tags: `verified`, `lst`, `strict`, `clone`, `community`,
/// `pump`, `unknown`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupTokensTagParams {
    /// Tag to filter by. E.g. "verified", "lst", "strict", "community".
    pub tag: String,
    /// Max results (1–100). Default: 20.
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_jup_tokens_tag_params(p: &JupTokensTagParams) -> Result<(), AppError> {
    if p.tag.trim().is_empty() {
        return Err(AppError::InvalidParams(
            "tag is required (e.g. \"verified\", \"lst\", \"strict\")".into(),
        ));
    }
    if let Some(l) = p.limit {
        if l == 0 || l > 100 {
            return Err(AppError::InvalidParams("limit must be between 1 and 100".into()));
        }
    }
    Ok(())
}

pub async fn build_jup_tokens_tag(
    http: &reqwest::Client,
    _wallet: &str,
    params: &JupTokensTagParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_tokens_tag_params(params)?;
    // Jupiter Tokens v2 uses `query=` not `tag=` for the tag filter
    let url = format!("{JUP_TOKENS_API}/tag?query={}", params.tag.trim());
    let mut data = jup_get(http, &url, api_key).await?;

    if let (Some(limit), Some(arr)) = (params.limit, data.as_array_mut()) {
        arr.truncate(limit as usize);
    }
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_tokens_tag".into(),
            description: format!("{count} token(s) with tag \"{}\"", params.tag),
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
// 4. jup_tokens_recent — Tokens API v2 recently created
// ──────────────────────────────────────────────────────────────────────────────

/// List recently created tokens with their initial pools.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupTokensRecentParams {
    /// Max results (1–50). Default: 20.
    #[serde(default)]
    pub limit: Option<u32>,
}

pub fn validate_jup_tokens_recent_params(p: &JupTokensRecentParams) -> Result<(), AppError> {
    if let Some(l) = p.limit {
        if l == 0 || l > 50 {
            return Err(AppError::InvalidParams("limit must be between 1 and 50".into()));
        }
    }
    Ok(())
}

pub async fn build_jup_tokens_recent(
    http: &reqwest::Client,
    _wallet: &str,
    params: &JupTokensRecentParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_tokens_recent_params(params)?;
    let url = format!("{JUP_TOKENS_API}/recent");
    let mut data = jup_get(http, &url, api_key).await?;

    if let (Some(limit), Some(arr)) = (params.limit, data.as_array_mut()) {
        arr.truncate(limit as usize);
    }
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_tokens_recent".into(),
            description: format!("{count} recently created token(s)"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
            warnings: vec!["New tokens carry high risk — verify before trading.".into()],
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
// 5. jup_tokens_trending — Tokens API v2 top by category
// ──────────────────────────────────────────────────────────────────────────────

/// Get top tokens ranked by a category metric.
///
/// `category`: `toptrending` | `toptraded` | `toporganicscore`
/// `interval`: `5m` | `1h` | `6h` | `24h`
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupTokensTrendingParams {
    /// Ranking metric.
    /// Options: "toptrending" (trending), "toptraded" (by volume), "toporganicscore" (organic quality score).
    /// Default: "toptrending".
    #[serde(default = "default_trending_category")]
    pub category: String,
    /// Time interval. Options: "5m", "1h", "6h", "24h". Default: "24h".
    #[serde(default = "default_trending_interval")]
    pub interval: String,
    /// Max results (1–50). Default: 20.
    #[serde(default)]
    pub limit: Option<u32>,
}

fn default_trending_category() -> String {
    "toptrending".to_string()
}
fn default_trending_interval() -> String {
    "24h".to_string()
}

pub fn validate_jup_tokens_trending_params(p: &JupTokensTrendingParams) -> Result<(), AppError> {
    const VALID_CATEGORIES: &[&str] = &["toptrending", "toptraded", "toporganicscore"];
    const VALID_INTERVALS: &[&str] = &["5m", "1h", "6h", "24h"];

    if !VALID_CATEGORIES.contains(&p.category.as_str()) {
        return Err(AppError::InvalidParams(format!(
            "category must be one of: {} (e.g. \"toptrending\" for trending, \"toptraded\" for volume)",
            VALID_CATEGORIES.join(", ")
        )));
    }
    if !VALID_INTERVALS.contains(&p.interval.as_str()) {
        return Err(AppError::InvalidParams(format!(
            "interval must be one of: {}",
            VALID_INTERVALS.join(", ")
        )));
    }
    if let Some(l) = p.limit {
        if l == 0 || l > 50 {
            return Err(AppError::InvalidParams("limit must be between 1 and 50".into()));
        }
    }
    Ok(())
}

pub async fn build_jup_tokens_trending(
    http: &reqwest::Client,
    _wallet: &str,
    params: &JupTokensTrendingParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_tokens_trending_params(params)?;
    let url = format!(
        "{JUP_TOKENS_API}/category/{}/{}",
        params.category, params.interval
    );
    let mut data = jup_get(http, &url, api_key).await?;

    if let (Some(limit), Some(arr)) = (params.limit, data.as_array_mut()) {
        arr.truncate(limit as usize);
    }
    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_tokens_trending".into(),
            description: format!(
                "Top {count} tokens by {} ({})",
                params.category, params.interval
            ),
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
// 6. jup_portfolio_positions — Portfolio API v1
// ──────────────────────────────────────────────────────────────────────────────

/// Get a wallet's cross-protocol DeFi positions (Jupiter Portfolio).
///
/// Returns positions across all protocols tracked by Jupiter Portfolio.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupPortfolioPositionsParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Comma-separated platform IDs to filter results (optional).
    /// E.g. "jupiter,kamino,marginfi". Omit to return all platforms.
    #[serde(default)]
    pub platforms: Option<String>,
}

pub fn validate_jup_portfolio_positions_params(
    _p: &JupPortfolioPositionsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_jup_portfolio_positions(
    http: &reqwest::Client,
    wallet: &str,
    params: &JupPortfolioPositionsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_portfolio_positions_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let mut url = format!("{JUP_PORTFOLIO_API}/positions/{target}");
    if let Some(ref platforms) = params.platforms {
        url.push_str(&format!("?platforms={}", urlencoding_simple(platforms)));
    }
    let data = jup_get(http, &url, api_key).await?;

    let platform_count = data
        .get("platforms")
        .or_else(|| data.get("positions"))
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);

    let wallet_short = if target.len() > 8 {
        format!("{}...{}", &target[..4], &target[target.len() - 4..])
    } else {
        target.to_string()
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_portfolio_positions".into(),
            description: format!(
                "Portfolio positions for {wallet_short}: {platform_count} protocol(s)"
            ),
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
// 7. jup_staked_jup — Portfolio API v1 staked JUP
// ──────────────────────────────────────────────────────────────────────────────

/// Get a wallet's staked JUP information (voting power, unlock date, rewards).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupStakedJupParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
}

pub fn validate_jup_staked_jup_params(_p: &JupStakedJupParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_jup_staked_jup(
    http: &reqwest::Client,
    wallet: &str,
    params: &JupStakedJupParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_staked_jup_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let url = format!("{JUP_PORTFOLIO_API}/staked-jup/{target}");
    let data = jup_get(http, &url, api_key).await?;

    let staked = data
        .get("stakedAmount")
        .or_else(|| data.get("amount"))
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);

    let wallet_short = if target.len() > 8 {
        format!("{}...{}", &target[..4], &target[target.len() - 4..])
    } else {
        target.to_string()
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_staked_jup".into(),
            description: format!("Staked JUP for {wallet_short}: {staked:.4} JUP"),
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
// 8. jup_lend_positions — Lend API v1 Earn
// ──────────────────────────────────────────────────────────────────────────────

/// Get a wallet's active Jupiter Lend (Earn) deposit positions.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupLendPositionsParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
}

pub fn validate_jup_lend_positions_params(_p: &JupLendPositionsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_jup_lend_positions(
    http: &reqwest::Client,
    wallet: &str,
    params: &JupLendPositionsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_lend_positions_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    // Jupiter Lend Earn uses `users=` (plural, comma-separated) not `userPubkey=`
    let url = format!("{JUP_LEND_EARN_API}/positions?users={target}");
    let data = jup_get(http, &url, api_key).await?;

    let count = data
        .as_array()
        .map(|a| a.len())
        .or_else(|| {
            data.get("positions")
                .and_then(|v| v.as_array())
                .map(|a| a.len())
        })
        .unwrap_or(0);

    let wallet_short = if target.len() > 8 {
        format!("{}...{}", &target[..4], &target[target.len() - 4..])
    } else {
        target.to_string()
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_lend_positions".into(),
            description: format!(
                "{count} active Jupiter Lend position(s) for {wallet_short}"
            ),
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
// 9. jup_lend_earnings — Lend API v1 Earn
// ──────────────────────────────────────────────────────────────────────────────

/// Get a wallet's accumulated interest earnings from Jupiter Lend.
///
/// This is a two-step call:
/// 1. Fetch `/lend/v1/earn/positions?users={wallet}` to get jlToken mint addresses.
/// 2. Fetch `/lend/v1/earn/earnings?user={wallet}&positions={mints}` for earnings.
///
/// If you already know the jlToken mint addresses you can pass them directly
/// via `positions` to skip step 1.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupLendEarningsParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// jlToken mint addresses (comma-separated). If omitted, positions are
    /// fetched automatically first.
    #[serde(default)]
    pub positions: Option<String>,
}

pub fn validate_jup_lend_earnings_params(_p: &JupLendEarningsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_jup_lend_earnings(
    http: &reqwest::Client,
    wallet: &str,
    params: &JupLendEarningsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_lend_earnings_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);

    // Step 1: resolve jlToken mints if not provided
    let position_mints = if let Some(ref p) = params.positions {
        p.clone()
    } else {
        // Fetch positions to extract jlToken mint addresses
        let positions_url = format!("{JUP_LEND_EARN_API}/positions?users={target}");
        let positions_data = jup_get(http, &positions_url, api_key).await?;
        let mints: Vec<String> = positions_data
            .as_array()
            .unwrap_or(&vec![])
            .iter()
            .filter_map(|pos| {
                pos.get("token")
                    .and_then(|t| t.get("mint"))
                    .and_then(|m| m.as_str())
                    .map(String::from)
            })
            .collect();
        if mints.is_empty() {
            return Ok(BuildResponse {
                preview: ActionPreview {
                    id: Uuid::new_v4().to_string(),
                    action_type: "jup_lend_earnings".into(),
                    description: "No active Jupiter Lend positions found".to_string(),
                    estimated_fee: "0".into(),
                    estimated_refund: None,
                    params: serde_json::json!({ "earnings": [] }),
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
        data: None,
            });
        }
        mints.join(",")
    };

    // Step 2: fetch earnings for those positions
    // Jupiter Lend Earn earnings endpoint: GET /earn/earnings?user={addr}&positions={mints}
    let url = format!(
        "{JUP_LEND_EARN_API}/earnings?user={target}&positions={}",
        urlencoding_simple(&position_mints)
    );
    let data = jup_get(http, &url, api_key).await?;

    let count = data.as_array().map(|a| a.len()).unwrap_or(0);
    let wallet_short = if target.len() > 8 {
        format!("{}...{}", &target[..4], &target[target.len() - 4..])
    } else {
        target.to_string()
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_lend_earnings".into(),
            description: format!(
                "Jupiter Lend earnings for {wallet_short}: {count} position(s)"
            ),
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
// 10. jup_pending_invites — Send API v1
// ──────────────────────────────────────────────────────────────────────────────

/// Get pending Jupiter Send invite links for a wallet.
///
/// Jupiter Send allows sending tokens via invite links to walletless recipients.
/// Pending invites are tokens sent but not yet claimed by the recipient.
/// Response includes `hasMoreData: true` when additional pages are available.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupPendingInvitesParams {
    /// Wallet address. Defaults to the authenticated user's wallet.
    #[serde(default)]
    pub wallet: Option<String>,
    /// Page number for pagination (minimum 1). Default: 1.
    #[serde(default)]
    pub page: Option<u32>,
}

pub fn validate_jup_pending_invites_params(_p: &JupPendingInvitesParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_jup_pending_invites(
    http: &reqwest::Client,
    wallet: &str,
    params: &JupPendingInvitesParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_pending_invites_params(params)?;
    let target = params.wallet.as_deref().unwrap_or(wallet);
    let page = params.page.unwrap_or(1).max(1);
    let url = format!("{JUP_SEND_API}/pending-invites?address={target}&page={page}");
    let data = jup_get(http, &url, api_key).await?;

    let count = data
        .as_array()
        .map(|a| a.len())
        .or_else(|| {
            data.get("invites")
                .and_then(|v| v.as_array())
                .map(|a| a.len())
        })
        .unwrap_or(0);
    let has_more = data.get("hasMoreData").and_then(|v| v.as_bool()).unwrap_or(false);

    let wallet_short = if target.len() > 8 {
        format!("{}...{}", &target[..4], &target[target.len() - 4..])
    } else {
        target.to_string()
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_pending_invites".into(),
            description: format!(
                "{count} pending Jupiter Send invite(s) for {wallet_short}"
            ),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: data,
            warnings: {
                let mut w = vec![];
                if count > 0 {
                    w.push("Unclaimed tokens can be reclaimed via the Jupiter Send clawback.".into());
                }
                if has_more {
                    w.push(format!("More results available — use page: {} to see the next page.", page + 1));
                }
                w
            },
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
// 11. jup_platforms — Portfolio API v1
// ──────────────────────────────────────────────────────────────────────────────

/// List all platforms supported by the Jupiter Portfolio API.
///
/// Returns platform metadata (id, name, logo, url) for all DeFi protocols
/// whose positions can be tracked via `/portfolio/v1/positions`.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct JupPlatformsParams {}

pub fn validate_jup_platforms_params(_p: &JupPlatformsParams) -> Result<(), AppError> {
    Ok(())
}

pub async fn build_jup_platforms(
    http: &reqwest::Client,
    _wallet: &str,
    params: &JupPlatformsParams,
    api_key: Option<&str>,
) -> Result<BuildResponse, AppError> {
    validate_jup_platforms_params(params)?;
    let url = format!("{JUP_PORTFOLIO_API}/platforms");
    let data = jup_get(http, &url, api_key).await?;

    let count = data.as_array().map(|a| a.len()).unwrap_or(0);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "jup_platforms".into(),
            description: format!("{count} platforms supported by Jupiter Portfolio"),
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
// Internal: simple URL-encode (no external dep needed for basic ASCII)
// ──────────────────────────────────────────────────────────────────────────────

fn urlencoding_simple(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for byte in s.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9'
            | b'-' | b'_' | b'.' | b'~' => out.push(byte as char),
            b' ' => out.push('+'),
            _ => {
                out.push('%');
                out.push_str(&format!("{byte:02X}"));
            }
        }
    }
    out
}

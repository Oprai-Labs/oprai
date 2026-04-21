use actix_web::{get, web, HttpResponse};
use serde::Serialize;
use std::collections::HashMap;
use std::sync::LazyLock;

use crate::error::AppError;
use crate::solana::tokens::COMMON_TOKENS;

// ──────────────────────────────────────────────────────────────────────────────
// Protocol registry (mirrors services/solana-service/src/services/protocols.service.ts)
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct ProtocolInfo {
    pub id: &'static str,
    pub name: &'static str,
    pub description: &'static str,
    pub category: &'static str,
    pub website: &'static str,
    pub actions: &'static [&'static str],
}

static PROTOCOLS: LazyLock<Vec<ProtocolInfo>> = LazyLock::new(|| {
    vec![
        ProtocolInfo {
            id: "jupiter",
            name: "Jupiter",
            description: "Solana's leading swap, lending and perpetuals aggregator",
            category: "DEX Aggregator",
            website: "https://jup.ag",
            actions: &["swap", "limit_order", "dca", "lend", "withdraw_lend", "borrow", "repay", "perp_open", "perp_close", "jlp_add", "jlp_remove", "jupsol_stake", "jupsol_unstake"],
        },
        ProtocolInfo {
            id: "jupiter_lend",
            name: "Jupiter Lend",
            description: "Earn yield on deposits or borrow against collateral",
            category: "Lending",
            website: "https://jup.ag/earn",
            actions: &["lend", "withdraw_lend", "borrow", "repay"],
        },
        ProtocolInfo {
            id: "jupiter_perp",
            name: "Jupiter Perps",
            description: "Trade perpetual futures with up to 50x leverage",
            category: "Perpetuals",
            website: "https://jup.ag/perps",
            actions: &["perp_open", "perp_close", "jlp_add", "jlp_remove"],
        },
        ProtocolInfo {
            id: "pump_fun",
            name: "Pump.fun",
            description: "Fair launch token platform with bonding curves",
            category: "Token Launchpad",
            website: "https://pump.fun",
            actions: &["launch_token", "buy", "sell"],
        },
        ProtocolInfo {
            id: "raydium",
            name: "Raydium",
            description: "AMM and concentrated liquidity protocol",
            category: "DEX / AMM",
            website: "https://raydium.io",
            actions: &["swap", "provide_liquidity", "remove_liquidity", "farm"],
        },
        ProtocolInfo {
            id: "marinade",
            name: "Marinade Finance",
            description: "Liquid staking protocol for SOL",
            category: "Liquid Staking",
            website: "https://marinade.finance",
            actions: &[
                // Liquid Staking
                "stake",
                "unstake",
                "delayed_unstake",
            ],
        },
        ProtocolInfo {
            id: "jito",
            name: "Jito",
            description: "MEV-boosted liquid staking and transaction bundles",
            category: "Liquid Staking + MEV",
            website: "https://www.jito.wtf",
            actions: &[
                // Liquid Staking
                "stake",
                "unstake",
                // MEV Tips
                "tip",
                // Transaction Bundles
                "bundle",
                "bundle_status",
            ],
        },
        ProtocolInfo {
            id: "orca",
            name: "Orca",
            description: "User-friendly DEX with concentrated liquidity (Whirlpools)",
            category: "DEX / CLMM",
            website: "https://www.orca.so",
            actions: &[
                "swap",
                "add_liquidity",
                "remove_liquidity",
                "open_position",
                "close_position",
                "increase_position",
                "decrease_position",
                "collect_fees",
                "collect_rewards",
            ],
        },
        ProtocolInfo {
            id: "meteora",
            name: "Meteora",
            description: "Dynamic Liquidity Market Maker (DLMM) with concentrated liquidity",
            category: "DEX / DLMM",
            website: "https://www.meteora.ag",
            actions: &[
                // DLMM Swap
                "swap",
                // Liquidity Management
                "add_liquidity",
                "remove_liquidity",
                "create_pool",
                // Position Management
                "open_position",
                "close_position",
                "add_to_position",
                "claim_fees",
                "claim_rewards",
                // Farming
                "stake",
                "unstake",
                "harvest",
            ],
        },
        ProtocolInfo {
            id: "marginfi",
            name: "marginfi",
            description: "Decentralized lending protocol",
            category: "Lending",
            website: "https://www.marginfi.com",
            actions: &["deposit", "withdraw", "borrow", "repay"],
        },
        ProtocolInfo {
            id: "kamino",
            name: "Kamino Finance",
            description: "Automated liquidity strategies and lending",
            category: "Yield Optimizer / Lending",
            website: "https://kamino.finance",
            actions: &[
                // K-Lend
                "deposit",
                "withdraw",
                "borrow",
                "repay",
                "add_collateral",
                "withdraw_collateral",
                // Multiply Vaults
                "multiply_open",
                "multiply_add",
                "multiply_withdraw",
                "multiply_close",
                // Long/Short Vaults
                "long_open",
                "short_open",
                "position_close",
                // Liquidity Vaults
                "vault_deposit",
                "vault_withdraw",
                // Staking
                "stake",
                "unstake",
            ],
        },
    ]
});

static PROTOCOL_MAP: LazyLock<HashMap<&'static str, &'static ProtocolInfo>> = LazyLock::new(|| {
    PROTOCOLS.iter().map(|p| (p.id, p)).collect()
});

// ──────────────────────────────────────────────────────────────────────────────
// GET /protocols
// ──────────────────────────────────────────────────────────────────────────────

#[get("")]
pub async fn list_protocols() -> HttpResponse {
    HttpResponse::Ok().json(serde_json::json!({ "protocols": *PROTOCOLS }))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /protocols/{id}/stats
// ──────────────────────────────────────────────────────────────────────────────

#[get("/{id}/stats")]
pub async fn get_protocol_stats(path: web::Path<String>) -> Result<HttpResponse, AppError> {
    let id = path.into_inner();
    match PROTOCOL_MAP.get(id.as_str()) {
        Some(info) => Ok(HttpResponse::Ok().json(serde_json::json!({
            "protocol": info,
            "actions": info.actions,
        }))),
        None => Err(AppError::NotFound("Protocol not found".into())),
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /protocols/tokens  (also aliased as /tokens)
// ──────────────────────────────────────────────────────────────────────────────

#[get("/tokens")]
pub async fn list_tokens() -> HttpResponse {
    let tokens: Vec<_> = COMMON_TOKENS.values().collect();
    HttpResponse::Ok().json(serde_json::json!({ "tokens": tokens }))
}

/// Alias: GET /tokens (mounted under /tokens scope).
#[get("")]
pub async fn list_tokens_alias() -> HttpResponse {
    let tokens: Vec<_> = COMMON_TOKENS.values().collect();
    HttpResponse::Ok().json(serde_json::json!({ "tokens": tokens }))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /protocols/tokens/{symbol}  (also aliased as /tokens/{symbol})
// ──────────────────────────────────────────────────────────────────────────────

#[get("/tokens/{symbol}")]
pub async fn get_token_by_symbol(path: web::Path<String>) -> Result<HttpResponse, AppError> {
    let symbol = path.into_inner().to_uppercase();
    match COMMON_TOKENS.get(symbol.as_str()) {
        Some(info) => Ok(HttpResponse::Ok().json(serde_json::json!({ "token": info }))),
        None => Err(AppError::NotFound("Token not found".into())),
    }
}

/// Alias: GET /tokens/{symbol}.
#[get("/{symbol}")]
pub async fn get_token_by_symbol_alias(
    path: web::Path<String>,
) -> Result<HttpResponse, AppError> {
    let symbol = path.into_inner().to_uppercase();
    match COMMON_TOKENS.get(symbol.as_str()) {
        Some(info) => Ok(HttpResponse::Ok().json(serde_json::json!({ "token": info }))),
        None => Err(AppError::NotFound("Token not found".into())),
    }
}

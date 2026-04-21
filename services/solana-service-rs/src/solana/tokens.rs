use serde::Serialize;
use std::collections::HashMap;
use std::sync::LazyLock;

/// Token metadata mirroring `@oprai/solana-common` / `@oprai/types` TokenInfo.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenInfo {
    pub address: &'static str,
    pub symbol: &'static str,
    pub name: &'static str,
    pub decimals: u8,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub logo_uri: Option<&'static str>,
}

/// The native SOL wrapped-mint address.
pub const SOL_MINT: &str = "So11111111111111111111111111111111111111112";

/// Common tokens registry -- matches packages/solana-common/src/tokens.ts exactly.
pub static COMMON_TOKENS: LazyLock<HashMap<&'static str, TokenInfo>> = LazyLock::new(|| {
    let mut m = HashMap::new();
    m.insert("SOL", TokenInfo {
        address: SOL_MINT,
        symbol: "SOL",
        name: "Solana",
        decimals: 9,
        logo_uri: Some("https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png"),
    });
    m.insert("USDC", TokenInfo {
        address: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        symbol: "USDC",
        name: "USD Coin",
        decimals: 6,
        logo_uri: Some("https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v/logo.png"),
    });
    m.insert("USDT", TokenInfo {
        address: "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        symbol: "USDT",
        name: "Tether USD",
        decimals: 6,
        logo_uri: Some("https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB/logo.svg"),
    });
    m.insert("BONK", TokenInfo {
        address: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        symbol: "BONK",
        name: "Bonk",
        decimals: 5,
        logo_uri: Some("https://arweave.net/hQiPZOsRZXGXBJd_82PhVdlM_hACsT_q6wqwf5cSY7I"),
    });
    m.insert("JUP", TokenInfo {
        address: "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        symbol: "JUP",
        name: "Jupiter",
        decimals: 6,
        logo_uri: Some("https://static.jup.ag/jup/icon.png"),
    });
    m.insert("RAY", TokenInfo {
        address: "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
        symbol: "RAY",
        name: "Raydium",
        decimals: 6,
        logo_uri: Some("https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R/logo.png"),
    });
    m.insert("ORCA", TokenInfo {
        address: "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
        symbol: "ORCA",
        name: "Orca",
        decimals: 6,
        logo_uri: Some("https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE/logo.png"),
    });
    m.insert("MSOL", TokenInfo {
        address: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
        symbol: "mSOL",
        name: "Marinade staked SOL",
        decimals: 9,
        logo_uri: Some("https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So/logo.png"),
    });
    m.insert("JITOSOL", TokenInfo {
        address: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kongC",
        symbol: "jitoSOL",
        name: "Jito Staked SOL",
        decimals: 9,
        logo_uri: Some("https://storage.googleapis.com/token-metadata/JitoSOL-256.png"),
    });
    m.insert("JUPSOL", TokenInfo {
        address: "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v",
        symbol: "jupSOL",
        name: "Jupiter Staked SOL",
        decimals: 9,
        logo_uri: Some("https://static.jup.ag/jupSOL/icon.png"),
    });
    m
});

/// Resolve a token symbol (e.g. "SOL") or mint address to its canonical mint address.
pub fn resolve_token_address(symbol_or_address: &str) -> String {
    let upper = symbol_or_address.to_uppercase();
    if let Some(info) = COMMON_TOKENS.get(upper.as_str()) {
        info.address.to_string()
    } else {
        symbol_or_address.to_string()
    }
}

/// Look up token info by symbol or mint address.
pub fn get_token_info(symbol_or_address: &str) -> Option<&TokenInfo> {
    let upper = symbol_or_address.to_uppercase();
    if let Some(info) = COMMON_TOKENS.get(upper.as_str()) {
        return Some(info);
    }
    // Fallback: search by address
    COMMON_TOKENS
        .values()
        .find(|t| t.address == symbol_or_address)
}

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::LazyLock;

/// Token metadata mirroring `@oprai/solana-common` / `@oprai/types` TokenInfo.
///
/// All fields are owned `String`s because the source-of-truth registry is loaded
/// from `shared/tokens.json` at compile time (see `COMMON_TOKENS` below). The JSON
/// is verified against the Jupiter API in CI (`scripts/verify-tokens.mjs`), so a
/// typo or vanity-prefix collision (e.g. the historic JitoSOL `kongC` vs `kGCPn`
/// bug) is caught before it ships.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenInfo {
    pub address: String,
    pub symbol: String,
    pub name: String,
    pub decimals: u8,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub logo_uri: Option<String>,
}

/// On-disk schema of `shared/tokens.json`.
#[derive(Debug, Deserialize)]
struct TokensFile {
    tokens: Vec<TokenEntry>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TokenEntry {
    address: String,
    symbol: String,
    name: String,
    decimals: u8,
    #[serde(default, rename = "logoURI")]
    logo_uri: Option<String>,
}

/// The native SOL wrapped-mint address.
pub const SOL_MINT: &str = "So11111111111111111111111111111111111111112";

/// Compile-time-embedded canonical token registry.
///
/// `include_str!` reads `../../../../shared/tokens.json` at build time, so the
/// service binary always carries the exact registry that was committed alongside
/// the source. Out-of-tree edits cannot bypass it; CI verification cannot be
/// skipped; runtime parse failure is a panic (fail-closed).
static TOKENS_JSON: &str = include_str!("../../../../shared/tokens.json");

/// Common tokens registry — loaded from `shared/tokens.json`.
///
/// Indexed by uppercase symbol. `resolve_token_address` and `get_token_info`
/// also accept raw mint addresses.
pub static COMMON_TOKENS: LazyLock<HashMap<String, TokenInfo>> = LazyLock::new(|| {
    let parsed: TokensFile = serde_json::from_str(TOKENS_JSON)
        .expect("shared/tokens.json must be valid JSON conforming to TokensFile schema");
    let mut m = HashMap::with_capacity(parsed.tokens.len());
    for t in parsed.tokens {
        let key = t.symbol.to_uppercase();
        m.insert(
            key,
            TokenInfo {
                address: t.address,
                symbol: t.symbol,
                name: t.name,
                decimals: t.decimals,
                logo_uri: t.logo_uri,
            },
        );
    }
    m
});

/// Resolve a token symbol (e.g. "SOL") or mint address to its canonical mint address.
pub fn resolve_token_address(symbol_or_address: &str) -> String {
    let upper = symbol_or_address.to_uppercase();
    if let Some(info) = COMMON_TOKENS.get(&upper) {
        info.address.clone()
    } else {
        symbol_or_address.to_string()
    }
}

/// Look up token info by symbol or mint address.
pub fn get_token_info(symbol_or_address: &str) -> Option<&'static TokenInfo> {
    let upper = symbol_or_address.to_uppercase();
    if let Some(info) = COMMON_TOKENS.get(&upper) {
        return Some(info);
    }
    // Fallback: search by address
    COMMON_TOKENS
        .values()
        .find(|t| t.address == symbol_or_address)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_loads_and_contains_core_tokens() {
        // If shared/tokens.json is malformed or missing, this fails at first access.
        let sol = COMMON_TOKENS.get("SOL").expect("SOL must be in registry");
        assert_eq!(sol.address, SOL_MINT);
        assert_eq!(sol.decimals, 9);

        // Full-mint regression guards. Vanity grinders can produce arbitrarily
        // long matching prefixes (or suffixes) cheaply, so we pin the entire
        // 44-char address — the only string that defends against a swap.
        const PINS: &[(&str, &str, u8)] = &[
            ("SOL",     "So11111111111111111111111111111111111111112", 9),
            ("USDC",    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
            ("USDT",    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
            ("JITOSOL", "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", 9),
            ("MSOL",    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  9),
            ("JUPSOL",  "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v",  9),
            ("BSOL",    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",  9),
            ("JUP",     "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  6),
        ];
        for (sym, expected_addr, expected_decimals) in PINS {
            let info = COMMON_TOKENS
                .get(*sym)
                .unwrap_or_else(|| panic!("{} must be in registry", sym));
            assert_eq!(
                info.address, *expected_addr,
                "{} mint mismatch — registry tampering or typo? expected {}, got {}",
                sym, expected_addr, info.address
            );
            assert_eq!(
                info.decimals, *expected_decimals,
                "{} decimals mismatch — expected {}, got {}",
                sym, expected_decimals, info.decimals
            );
        }
    }

    #[test]
    fn every_address_passes_base58_format() {
        // Solana base58 alphabet excludes I O l 0. Hand-rolled to avoid pulling
        // a regex dep — the CI verifier (scripts/verify-tokens.mjs) is the
        // primary enforcement layer; this just guards against load-time corruption.
        const VALID: &[u8] = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
        for (sym, info) in COMMON_TOKENS.iter() {
            assert!(
                (32..=44).contains(&info.address.len()),
                "token {} has wrong mint length: {} ({} chars)",
                sym,
                info.address,
                info.address.len()
            );
            for b in info.address.bytes() {
                assert!(
                    VALID.contains(&b),
                    "token {} mint {} contains invalid base58 byte 0x{:02x}",
                    sym,
                    info.address,
                    b
                );
            }
        }
    }
}

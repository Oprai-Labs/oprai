use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;

use crate::error::AppError;
use crate::solana::tokens::{get_token_info, resolve_token_address, COMMON_TOKENS};

/// Provenance of a mint address — what we actually know about a token the LLM
/// (or a third-party UI) just asked us to swap/transfer.
///
/// Vanity-prefix attacks are cheap on Solana (`solana-keygen grind --starts-with`
/// produces matching prefixes in seconds). The defence is to refuse to act on
/// a mint we cannot trace back to either our compile-time registry or a live
/// authority (Jupiter), and to surface the actual symbol/name to the user so
/// they don't approve "JitoSOL" when the address is a look-alike.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "trust", rename_all = "snake_case")]
pub enum MintProvenance {
    /// Mint matches an entry in our compile-time `shared/tokens.json` registry.
    /// The CI verifier (`scripts/verify-tokens.mjs`) cross-checks every entry
    /// against Jupiter + Birdeye, so this is the strongest signal.
    Verified {
        symbol: String,
        name: String,
        decimals: u8,
    },
    /// Mint is not in the local registry but Jupiter recognises it (it is
    /// tradeable through their aggregator). Symbol/name come from Jupiter and
    /// MUST be surfaced to the user — a vanity-prefix mint impersonating
    /// JITOSOL will return its real (random) symbol here.
    JupiterKnown {
        symbol: String,
        name: String,
        decimals: u8,
    },
    /// Mint is base58-valid but neither in our registry nor known to Jupiter.
    /// Refuse to build the transaction — there's nothing safe to show the user.
    Unknown,
}

impl MintProvenance {
    pub fn is_trusted(&self) -> bool {
        matches!(self, MintProvenance::Verified { .. })
    }

    pub fn requires_user_warning(&self) -> bool {
        matches!(self, MintProvenance::JupiterKnown { .. })
    }
}

#[derive(Clone)]
struct CacheEntry {
    provenance: MintProvenance,
    fetched_at: Instant,
}

/// In-process cache of mint lookups so we don't hit Jupiter on every quote.
/// 10-minute TTL — long enough to absorb a quote → build → submit cycle, short
/// enough that a token getting blacklisted at Jupiter propagates within minutes.
pub struct MintSecurityCache {
    entries: RwLock<std::collections::HashMap<String, CacheEntry>>,
    ttl: Duration,
}

impl MintSecurityCache {
    pub fn new() -> Self {
        Self {
            entries: RwLock::new(std::collections::HashMap::new()),
            ttl: Duration::from_secs(600),
        }
    }

    async fn get(&self, mint: &str) -> Option<MintProvenance> {
        let entries = self.entries.read().await;
        let entry = entries.get(mint)?;
        if entry.fetched_at.elapsed() < self.ttl {
            Some(entry.provenance.clone())
        } else {
            None
        }
    }

    async fn put(&self, mint: String, provenance: MintProvenance) {
        let mut entries = self.entries.write().await;
        entries.insert(
            mint,
            CacheEntry {
                provenance,
                fetched_at: Instant::now(),
            },
        );
    }
}

impl Default for MintSecurityCache {
    fn default() -> Self {
        Self::new()
    }
}

/// Resolve a mint to its provenance. Prefers the local registry; falls back to
/// a live Jupiter lookup for unknowns; caches the result for 10 minutes.
///
/// `mint_or_symbol` accepts the same inputs as [`crate::solana::tokens::get_token_info`]:
/// either a raw mint address or a known symbol like `"JITOSOL"`.
pub async fn classify_mint(
    cache: &MintSecurityCache,
    http: &reqwest::Client,
    mint_or_symbol: &str,
) -> Result<MintProvenance, AppError> {
    // Registry hit — fastest path, no I/O.
    if let Some(info) = get_token_info(mint_or_symbol) {
        return Ok(MintProvenance::Verified {
            symbol: info.symbol.clone(),
            name: info.name.clone(),
            decimals: info.decimals,
        });
    }

    // We need a base58 mint address to query Jupiter. Reject anything that
    // isn't either a known symbol (handled above) or a syntactically-valid
    // pubkey — this also catches malformed input the LLM might emit.
    let mint = mint_or_symbol;
    if mint.parse::<solana_sdk::pubkey::Pubkey>().is_err() {
        return Err(AppError::InvalidParams(format!(
            "Mint '{mint}' is not a valid Solana address and is not in the registry"
        )));
    }

    if let Some(cached) = cache.get(mint).await {
        return Ok(cached);
    }

    let provenance = jupiter_lookup(http, mint)
        .await
        .unwrap_or(MintProvenance::Unknown);
    cache.put(mint.to_string(), provenance.clone()).await;
    Ok(provenance)
}

/// Hit Jupiter's token-info endpoint to check whether a mint is tradeable.
/// We treat any failure (timeout, 404, bad payload) as "unknown" rather than
/// surfacing the network error — the caller decides whether to reject.
async fn jupiter_lookup(http: &reqwest::Client, mint: &str) -> Result<MintProvenance, AppError> {
    // Jupiter RETIRED `tokens.jup.ag/token/<mint>` (it now NXDOMAINs), which made
    // this classify every non-registry mint as Unknown and reject legitimate,
    // liquid tokens (e.g. PYUSD — a Token-2022 mint). The current metadata lookup
    // is the v2 search endpoint, which returns an ARRAY of matches. Tight timeout
    // so a slow Jupiter doesn't stall a quote.
    let url = format!("https://api.jup.ag/tokens/v2/search?query={mint}");
    let resp = http
        .get(&url)
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter token lookup failed: {e}")))?;

    if !resp.status().is_success() {
        return Ok(MintProvenance::Unknown);
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct TokenMeta {
        id: Option<String>,
        symbol: Option<String>,
        name: Option<String>,
        decimals: Option<u8>,
    }
    let body: Vec<TokenMeta> = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter token decode failed: {e}")))?;

    // Exact-mint match only — a search query can return near matches by symbol.
    let Some(hit) = body.into_iter().find(|t| t.id.as_deref() == Some(mint)) else {
        return Ok(MintProvenance::Unknown);
    };
    let symbol = hit.symbol.unwrap_or_default();
    if symbol.is_empty() {
        return Ok(MintProvenance::Unknown);
    }
    Ok(MintProvenance::JupiterKnown {
        symbol,
        name: hit.name.unwrap_or_default(),
        decimals: hit.decimals.unwrap_or(0),
    })
}

/// Resolve a swap/transfer token argument to a canonical mint ADDRESS, accepting
/// either a raw mint, a compile-time-registry symbol (`SOL`, `USDC`, …), or a
/// Jupiter-verified symbol (`PYUSD`, …) not in our registry.
///
/// The whole difficulty is that symbols collide: a Jupiter search for `PYUSD`
/// returns ~20 mints, only ONE of which is the real PayPal token — the rest are
/// `banned`/`unknown` vanity impersonators (some literally ending in `…pump`).
/// So for the live path we accept only an `isVerified` token whose ticker matches
/// exactly, preferring the most-liquid one when several verified tokens genuinely
/// share a symbol. Anything ambiguous is refused rather than guessed — the same
/// stance [`classify_mint`] takes.
pub async fn resolve_action_mint(
    http: &reqwest::Client,
    mint_or_symbol: &str,
) -> Result<String, AppError> {
    // Already a valid mint address — nothing to resolve.
    if mint_or_symbol
        .parse::<solana_sdk::pubkey::Pubkey>()
        .is_ok()
    {
        return Ok(mint_or_symbol.to_string());
    }
    // Compile-time registry symbol — no network hit.
    let resolved = resolve_token_address(mint_or_symbol);
    if resolved != mint_or_symbol {
        return Ok(resolved);
    }
    // Non-registry ticker — resolve against Jupiter's live list, verified-only.
    if let Some(mint) = resolve_verified_symbol(http, mint_or_symbol).await {
        return Ok(mint);
    }
    Err(AppError::InvalidParams(format!(
        "'{mint_or_symbol}' is not a valid Solana address, is not in the verified \
         registry, and no verified token with that symbol was found on Jupiter."
    )))
}

/// Jupiter live symbol → mint, verified-only. `None` when there is no single
/// trustworthy match (nothing verified, or the query wasn't a real ticker).
async fn resolve_verified_symbol(http: &reqwest::Client, symbol: &str) -> Option<String> {
    let url = format!("https://api.jup.ag/tokens/v2/search?query={symbol}");
    let resp = http
        .get(&url)
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .ok()?;
    if !resp.status().is_success() {
        return None;
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct SearchHit {
        id: Option<String>,
        symbol: Option<String>,
        is_verified: Option<bool>,
        liquidity: Option<f64>,
    }
    let hits: Vec<SearchHit> = resp.json().await.ok()?;

    hits.into_iter()
        .filter(|h| h.is_verified == Some(true))
        .filter(|h| {
            h.symbol
                .as_deref()
                .is_some_and(|s| s.eq_ignore_ascii_case(symbol))
        })
        .filter(|h| h.id.is_some())
        .max_by(|a, b| {
            a.liquidity
                .unwrap_or(0.0)
                .partial_cmp(&b.liquidity.unwrap_or(0.0))
                .unwrap_or(std::cmp::Ordering::Equal)
        })
        .and_then(|h| h.id)
}

/// Convenience: classify a mint and reject outright if it's `Unknown`.
/// Use this from action handlers that should never proceed with an unverifiable
/// mint (swap, transfer, burn, lend, perps, etc).
pub async fn require_known_mint(
    cache: &MintSecurityCache,
    http: &reqwest::Client,
    mint_or_symbol: &str,
) -> Result<MintProvenance, AppError> {
    let provenance = classify_mint(cache, http, mint_or_symbol).await?;
    if let MintProvenance::Unknown = provenance {
        return Err(AppError::InvalidParams(format!(
            "Mint '{mint_or_symbol}' is not in the verified registry and Jupiter does not recognise it. \
             Refusing to build a transaction against an unverifiable token."
        )));
    }
    Ok(provenance)
}

/// Sanity check used at startup: every registry entry parses as a Solana pubkey
/// and has a non-empty symbol. If the JSON file gets corrupted (or a bad PR
/// lands), the service refuses to boot.
pub fn assert_registry_self_consistent() {
    for (sym, info) in COMMON_TOKENS.iter() {
        assert!(!sym.is_empty(), "registry has an entry with empty symbol");
        info.address
            .parse::<solana_sdk::pubkey::Pubkey>()
            .unwrap_or_else(|_| {
                panic!("registry entry {} has malformed mint {}", sym, info.address)
            });
    }
}

/// Type-alias for an Arc-shared cache placed into the Actix `AppState`.
pub type SharedMintSecurityCache = Arc<MintSecurityCache>;

use base64::Engine as _;
use reqwest::Client;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use solana_sdk::{
    instruction::{AccountMeta, Instruction},
    pubkey::Pubkey,
    system_program, sysvar,
    transaction::Transaction,
};
use std::str::FromStr;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const SNS_PROXY: &str = "https://sns-sdk-proxy.bonfida.workers.dev";
const HASH_PREFIX: &[u8] = b"SPL Name Service";

const NAME_PROGRAM_ID_STR: &str = "namesLPaSamrXa4y5Uv72vtnQiqRVhHtfnLt8FroKHf";
const REGISTRAR_PROGRAM_STR: &str = "jCebN34bUfdeUYJT13J1yG16XWQpt5PDx6Mse9GUqhR";
const ROOT_DOMAIN_STR: &str = "3mDqiU7grAHocTLG9m1vGFu1ZLZkTqzMPbqHtbRAcfko";
const CENTRAL_STATE_STR: &str = "2j4hkAaWKDwCMa5LiGpMQwTyMpPoC2bq3ER7B6xkiPr8";
const REVERSE_LOOKUP_CLASS_STR: &str = "33m47vH6Eav6jr5Ry86XjhRft2jRBLDnDgPSHoquXi2Z";
const NAME_OFFERS_PROGRAM_STR: &str = "85iDfUvr3HJyLM2zcq5BXSiDvUWfw6cSE1FfNBo8Ap29";
// BONFIDA_USDC_VAULT is kept for reference; vault is derived via ATA of central_state
const BONFIDA_USDC_VAULT_STR: &str = "DmF1JCEFpRCHMjCXEnJCgqANLvZCT7BGWM9JTQ9Xb1eW";
const USDC_MINT_STR: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const FIDA_MINT_STR: &str = "EchesyfXePKdLtoiZSL8pBe8Myagyy8ZRqsACNCFGnvp";
const SPL_TOKEN_STR: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";

// SPL Name Service instruction tags (opcode byte at position 0 of instruction data)
const IX_CREATE: u8 = 0;
const IX_UPDATE: u8 = 1;
const IX_TRANSFER: u8 = 2;
const IX_DELETE: u8 = 3;
const IX_REALLOC: u8 = 4;

// Name-Offers / registerFavorite instruction tag (Borsh, single u8)
const IX_FAVOURITE: u8 = 6;

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

fn p(s: &str) -> Pubkey {
    Pubkey::from_str(s).expect("const pubkey")
}

fn name_program_id() -> Pubkey {
    p(NAME_PROGRAM_ID_STR)
}
fn registrar_program() -> Pubkey {
    p(REGISTRAR_PROGRAM_STR)
}
fn root_domain() -> Pubkey {
    p(ROOT_DOMAIN_STR)
}
fn central_state() -> Pubkey {
    p(CENTRAL_STATE_STR)
}
fn reverse_lookup_class() -> Pubkey {
    p(REVERSE_LOOKUP_CLASS_STR)
}
fn name_offers_program() -> Pubkey {
    p(NAME_OFFERS_PROGRAM_STR)
}
fn usdc_mint() -> Pubkey {
    p(USDC_MINT_STR)
}
#[allow(dead_code)]
fn bonfida_usdc_vault() -> Pubkey {
    p(BONFIDA_USDC_VAULT_STR)
}
fn fida_mint() -> Pubkey {
    p(FIDA_MINT_STR)
}
fn spl_token_program() -> Pubkey {
    p(SPL_TOKEN_STR)
}

/// sha256(HASH_PREFIX + name) — used for all PDA derivations
fn hashed_name(name: &str) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(HASH_PREFIX);
    h.update(name.as_bytes());
    h.finalize().into()
}

/// Derive name account PDA:
/// seeds = [hashed_name, name_class (32 bytes), parent_name (32 bytes)]
pub fn name_account_key(
    name: &str,
    name_class: Option<&Pubkey>,
    parent: Option<&Pubkey>,
) -> Pubkey {
    let hash = hashed_name(name);
    let class_bytes = name_class.map(|k| k.to_bytes()).unwrap_or([0u8; 32]);
    let parent_bytes = parent.map(|k| k.to_bytes()).unwrap_or([0u8; 32]);
    let (key, _) =
        Pubkey::find_program_address(&[&hash, &class_bytes, &parent_bytes], &name_program_id());
    key
}

/// Top-level .sol domain key: parent = ROOT_DOMAIN_ACCOUNT, class = default
pub fn domain_key(name: &str) -> Pubkey {
    let clean = name.trim_end_matches(".sol").to_lowercase();
    let root = root_domain();
    name_account_key(&clean, None, Some(&root))
}

/// Subdomain key: parent = parent domain key
/// SNS spec: subdomain label must be prefixed with \0 (null character) before hashing
pub fn subdomain_key(subdomain: &str, parent_key: &Pubkey) -> Pubkey {
    let with_prefix = format!("\0{}", subdomain);
    name_account_key(&with_prefix, None, Some(parent_key))
}

/// Record V1 key: uses "\x01" prefix in the name hash
/// Record name format: "\x01{RecordType}.{domain}"
pub fn record_key_v1(record_type: &str, domain: &str) -> Pubkey {
    let clean_domain = domain.trim_end_matches(".sol").to_lowercase();
    let record_name = format!("\x01{}.{}", record_type, clean_domain);
    let parent = domain_key(&clean_domain);
    // Record class = REVERSE_LOOKUP_CLASS for most records
    let class = reverse_lookup_class();
    name_account_key(&record_name, Some(&class), Some(&parent))
}

/// 8-byte Anchor discriminator: sha256("global:{name}")[..8]
fn anchor_discriminator(name: &str) -> [u8; 8] {
    let mut h = Sha256::new();
    h.update(format!("global:{}", name).as_bytes());
    let hash = h.finalize();
    let mut disc = [0u8; 8];
    disc.copy_from_slice(&hash[..8]);
    disc
}

/// Resolve payment token: "USDC" | "FIDA" | base58 address
fn resolve_payment_token(token: &str) -> Pubkey {
    match token.to_uppercase().as_str() {
        "USDC" => usdc_mint(),
        "FIDA" => fida_mint(),
        _ => Pubkey::from_str(token).unwrap_or_else(|_| usdc_mint()),
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Proxy response wrapper
// ─────────────────────────────────────────────────────────────────────────────

/// Raw proxy response — result is untyped Value so we can extract error messages
/// from either the `e` field or the `result` field (proxy uses both patterns).
#[derive(Debug, Deserialize)]
struct ProxyRaw {
    s: String,
    result: Option<serde_json::Value>,
    #[serde(rename = "e")]
    error: Option<String>,
}

fn proxy_error_msg(raw: &ProxyRaw) -> String {
    raw.error
        .clone()
        .or_else(|| {
            raw.result
                .as_ref()
                .and_then(|v| v.as_str().map(|s| s.to_string()))
        })
        .unwrap_or_else(|| "unknown".into())
}

async fn proxy_get<T: serde::de::DeserializeOwned>(
    http: &Client,
    path: &str,
) -> Result<T, AppError> {
    let url = format!("{SNS_PROXY}/{path}");
    let raw: ProxyRaw = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("SNS proxy request failed: {e}")))?
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("SNS proxy parse failed: {e}")))?;

    if raw.s != "ok" {
        return Err(AppError::Internal(format!(
            "SNS proxy error: {}",
            proxy_error_msg(&raw)
        )));
    }
    let val = raw
        .result
        .ok_or_else(|| AppError::Internal("SNS proxy returned no result".into()))?;
    serde_json::from_value(val)
        .map_err(|e| AppError::Internal(format!("SNS proxy type mismatch: {e}")))
}

/// Like `proxy_get` but returns `Ok(None)` when the domain/key is not found,
/// and `Err` only on real network or parse failures.
async fn proxy_get_optional<T: serde::de::DeserializeOwned>(
    http: &Client,
    path: &str,
) -> Result<Option<T>, AppError> {
    let url = format!("{SNS_PROXY}/{path}");
    let raw: ProxyRaw = http
        .get(&url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("SNS proxy request failed: {e}")))?
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("SNS proxy parse failed: {e}")))?;

    if raw.s != "ok" {
        let msg = proxy_error_msg(&raw).to_lowercase();
        // Treat "not found" / "invalid domain" / "invalid input" as "does not exist" (not an error)
        if msg.contains("not found")
            || msg.contains("invalid domain")
            || msg.contains("unregistered")
            || msg.contains("invalid input")
        {
            return Ok(None);
        }
        return Err(AppError::Internal(format!("SNS proxy error: {msg}")));
    }
    match raw.result {
        None => Ok(None),
        Some(v) => serde_json::from_value::<T>(v)
            .map(Some)
            .map_err(|e| AppError::Internal(format!("SNS proxy type mismatch: {e}"))),
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Param / result types
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsResolveParams {
    pub domain: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsReverseParams {
    /// Domain account pubkey (not the owner wallet)
    pub pubkey: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsDomainsParams {
    pub owner: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsRecordParams {
    pub domain: String,
    /// Record type: "ETH" | "SOL" | "IPFS" | "twitter" | "discord" | etc.
    pub record: String,
    #[serde(default, deserialize_with = "crate::services::params::lenient")]
    pub v2: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsDomainInfoParams {
    pub domain: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsCheckAvailableParams {
    pub domain: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsPrimaryDomainParams {
    pub owner: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsRegisterParams {
    pub domain: String,
    /// Buyer token account (USDC ATA by default)
    #[serde(default)]
    pub buyer_token_account: String,
    /// Storage bytes 0–10240 (default 0)
    #[serde(default, deserialize_with = "crate::services::params::lenient")]
    pub space: u32,
    /// "USDC" | "FIDA" (default USDC)
    #[serde(default)]
    pub token: String,
    pub referrer: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsTransferParams {
    pub domain: String,
    pub new_owner: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsSetRecordParams {
    pub domain: String,
    /// "ETH" | "SOL" | "IPFS" | "twitter" | "discord" | "url" | "github" | etc.
    pub record: String,
    pub value: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsDeleteParams {
    pub domain: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsCreateSubdomainParams {
    /// Full subdomain e.g. "sub.parent" (without .sol)
    pub subdomain: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsListParams {
    pub domain: String,
    /// Price in token units (e.g. 100.0 for 100 USDC)
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub price: f64,
    /// "USDC" | "FIDA" | "SOL" (default USDC)
    #[serde(default)]
    pub token: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsBuyParams {
    pub domain: String,
    /// "USDC" | "FIDA" — must match what the seller listed with (default USDC)
    #[serde(default)]
    pub token: String,
    pub referrer: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsMakeOfferParams {
    pub domain: String,
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub amount: f64,
    #[serde(default)]
    pub token: String,
    /// Buyer's token account
    #[serde(default)]
    pub token_account: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsAcceptOfferParams {
    pub domain: String,
    /// Offer account pubkey
    pub offer_key: String,
    /// "USDC" | "FIDA" — must match the token the buyer's offer was made in (default USDC)
    #[serde(default)]
    pub token: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsCancelOfferParams {
    pub domain: String,
    pub offer_key: String,
    #[serde(default)]
    pub token: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsP2pCreateParams {
    /// Domains you are selling (base58 domain account pubkeys)
    pub base_domains: Vec<String>,
    /// Domains you want in return
    pub quote_domains: Vec<String>,
    /// SOL amount you also want (can be 0)
    #[serde(default, deserialize_with = "crate::services::params::lenient")]
    pub amount_sol: f64,
    /// The specific buyer you are offering to
    pub counter_party: String,
    /// Unix timestamp expiry (0 = no expiry)
    #[serde(default, deserialize_with = "crate::services::params::lenient")]
    pub expiry_ts: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsP2pAcceptParams {
    pub offer_key: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsP2pCancelParams {
    pub offer_key: String,
}

// ─────────────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
// Utility: transparent domain resolution (used by transfer auto-resolve)
// ─────────────────────────────────────────────────────────────────────────────

/// Resolve a `.sol` domain to its owner pubkey string.
///
/// Read from the chain, not from a proxy. Bonfida's worker — the single
/// dependency every SNS read in this service went through — now answers
/// Cloudflare error 1042 on every path, so `toly.sol` came back "not
/// registered". A domain that has existed for years reported as unregistered
/// is worse than an outage: it is a wrong answer delivered confidently, and
/// the transfer card would have sent the user looking for a different address.
///
/// The registry is three lines of arithmetic. A domain's account is a PDA of
/// sha256("SPL Name Service" + label) under the `.sol` root, and the owner is
/// the second 32-byte field of its header. No third party is involved, so
/// there is nothing left to go down.
pub async fn resolve_domain(_http: &Client, domain: &str) -> Result<String, AppError> {
    Err(AppError::Internal(format!(
        "resolve_domain must be called with an RPC: {domain}"
    )))
}

/// The SPL Name Service program, and the account of the `.sol` top-level domain.
const NAME_PROGRAM_ID: &str = "namesLPneVptA9Z5rqUDD9tMTWEJwofgaYwp8cawRkX";
const SOL_TLD_AUTHORITY: &str = "58PwtjSDuFHuUkYjH9BYnnQKHfwo9reZhC2zMJv9JPkx";
/// parent(32) + owner(32) + class(32); the owner starts right after the parent.
const NAME_OWNER_OFFSET: usize = 32;

/// The account that holds a `.sol` domain's registry entry.
pub fn domain_account(label: &str) -> Result<Pubkey, AppError> {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(format!("SPL Name Service{}", label.trim().to_lowercase()).as_bytes());
    let hashed: [u8; 32] = hasher.finalize().into();

    let program = Pubkey::from_str(NAME_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("name program id: {e}")))?;
    let parent = Pubkey::from_str(SOL_TLD_AUTHORITY)
        .map_err(|e| AppError::Internal(format!("sol tld: {e}")))?;

    let (key, _) = Pubkey::find_program_address(
        &[&hashed, &[0u8; 32], parent.as_ref()],
        &program,
    );
    Ok(key)
}

/// Resolve `<label>.sol` to the wallet that owns it, straight from the chain.
pub async fn resolve_domain_onchain(
    rpc: &crate::solana::connection::SolanaRpc,
    domain: &str,
) -> Result<String, AppError> {
    let label = domain.trim().to_lowercase();
    let label = label.strip_suffix(".sol").unwrap_or(&label).to_string();
    if label.is_empty() {
        return Err(AppError::InvalidParams("No domain given.".into()));
    }
    let key = domain_account(&label)?;
    let rpc = rpc.clone();
    let account = actix_web::web::block(move || rpc.client().get_account(&key))
        .await
        .map_err(|e| AppError::Internal(format!("thread pool error: {e}")))?
        .map_err(|_| {
            AppError::NotFound(format!("{label}.sol is not registered."))
        })?;

    if account.data.len() < NAME_OWNER_OFFSET + 32 {
        return Err(AppError::NotFound(format!(
            "{label}.sol has no owner recorded."
        )));
    }
    let owner = Pubkey::try_from(&account.data[NAME_OWNER_OFFSET..NAME_OWNER_OFFSET + 32])
        .map_err(|e| AppError::Internal(format!("owner decode: {e}")))?;
    Ok(owner.to_string())
}

// ─────────────────────────────────────────────────────────────────────────────
// Query: Resolve domain → owner pubkey
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_resolve(
    rpc: &crate::solana::connection::SolanaRpc,
    params: SnsResolveParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let owner: String = resolve_domain_onchain(rpc, &domain).await?;
    let domain_pubkey = domain_key(&domain);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_resolve_{}", Uuid::new_v4()),
            action_type: "sns_resolve".into(),
            description: format!("{domain}.sol → {owner}"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "owner": owner,
            "domainKey": domain_pubkey.to_string(),
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query: Reverse lookup — domain account pubkey → domain name
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_reverse_lookup(
    http: &Client,
    params: SnsReverseParams,
) -> Result<BuildResponse, AppError> {
    let pubkey = &params.pubkey;
    let name: String = proxy_get(http, &format!("reverse-lookup/{pubkey}")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_rev_{}", Uuid::new_v4()),
            action_type: "sns_reverse_lookup".into(),
            description: format!("{} → {name}.sol", &pubkey[..8]),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "pubkey": pubkey }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "pubkey": pubkey,
            "domain": format!("{name}.sol"),
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query: All domains for a wallet
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_domains(
    http: &Client,
    params: SnsDomainsParams,
) -> Result<BuildResponse, AppError> {
    let owner = &params.owner;
    let domains: Vec<String> = proxy_get(http, &format!("domains/{owner}")).await?;

    let count = domains.len();
    let domain_list: Vec<serde_json::Value> = domains
        .iter()
        .map(|d| {
            serde_json::json!({
                "name": format!("{d}.sol"),
                "key": domain_key(d).to_string(),
            })
        })
        .collect();

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_domains_{}", Uuid::new_v4()),
            action_type: "sns_domains".into(),
            description: format!(
                "{} owns {} .sol domain{}",
                &owner[..8],
                count,
                if count == 1 { "" } else { "s" }
            ),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "owner": owner }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({ "owner": owner, "domains": domain_list, "count": count })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query: Record value for a domain
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_record(
    http: &Client,
    params: SnsRecordParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let record = &params.record;

    let endpoint = if params.v2 {
        format!("v2/record/{domain}/{record}")
    } else {
        format!("record/{domain}/{record}")
    };

    let value: String = proxy_get(http, &endpoint).await?;

    let version = if params.v2 { "V2" } else { "V1" };
    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_rec_{}", Uuid::new_v4()),
            action_type: "sns_record".into(),
            description: format!(
                "{domain}.sol [{record} {version}]: {}",
                &value[..value.len().min(40)]
            ),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "record": record }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "record": record,
            "value": value,
            "version": version,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query: Full domain info (owner + key + batch records)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_domain_info(
    http: &Client,
    params: SnsDomainInfoParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let dkey = domain_key(&domain);

    // Resolve owner via proxy — Ok(None) = unregistered, Err = real network failure
    let (owner, available) =
        match proxy_get_optional::<String>(http, &format!("resolve/{domain}")).await? {
            Some(o) => (Some(o), false),
            None => (None, true),
        };

    // Batch-fetch common records
    let record_types = [
        "SOL", "ETH", "IPFS", "url", "twitter", "discord", "telegram", "github",
    ];
    let mut records: serde_json::Map<String, serde_json::Value> = serde_json::Map::new();
    for rt in &record_types {
        if let Ok(val) = proxy_get::<String>(http, &format!("record/{domain}/{rt}")).await {
            records.insert(rt.to_string(), serde_json::Value::String(val));
        }
    }

    let description = if available {
        format!("{domain}.sol is available for registration (~20 USDC)")
    } else {
        format!(
            "{domain}.sol — owner: {}",
            owner.as_deref().unwrap_or("unknown")
        )
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_info_{}", Uuid::new_v4()),
            action_type: "sns_domain_info".into(),
            description,
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "domainKey": dkey.to_string(),
            "owner": owner,
            "available": available,
            "records": records,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query: Check domain availability
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_check_available(
    http: &Client,
    params: SnsCheckAvailableParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let available = proxy_get_optional::<String>(http, &format!("resolve/{domain}"))
        .await?
        .is_none();

    let msg = if available {
        format!("{domain}.sol is available — register for ~20 USDC")
    } else {
        format!("{domain}.sol is already registered")
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_avail_{}", Uuid::new_v4()),
            action_type: "sns_check_available".into(),
            description: msg.clone(),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "available": available,
            "message": msg,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query: Primary domain for a wallet
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_primary_domain(
    http: &Client,
    params: SnsPrimaryDomainParams,
) -> Result<BuildResponse, AppError> {
    let owner = &params.owner;
    // The proxy /reverse-key endpoint returns the primary domain for a wallet pubkey
    // /favorite-domain/:owner returns the wallet's set primary domain
    let domain_result =
        proxy_get_optional::<String>(http, &format!("favorite-domain/{owner}")).await;

    let (domain, found) = match domain_result? {
        Some(d) => (format!("{d}.sol"), true),
        None => (String::new(), false),
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_primary_{}", Uuid::new_v4()),
            action_type: "sns_primary_domain".into(),
            description: if found {
                format!("{}'s primary domain: {domain}", &owner[..8])
            } else {
                format!("{} has no primary domain set", &owner[..8])
            },
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "owner": owner }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "owner": owner,
            "primaryDomain": if found { Some(domain) } else { None::<String> },
            "found": found,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Register domain — uses SNS proxy to build the instruction (avoids
//     hardcoding registrar program tags/accounts which change across versions)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_register(
    http: &Client,
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsRegisterParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();

    // Reject already-registered domains
    if proxy_get_optional::<String>(http, &format!("resolve/{domain}"))
        .await?
        .is_some()
    {
        return Err(AppError::InvalidParams(format!(
            "{domain}.sol is already registered"
        )));
    }

    // Validate buyer pubkey
    Pubkey::from_str(user).map_err(|_| AppError::InvalidParams("Invalid buyer pubkey".into()))?;

    let token_mint = resolve_payment_token(if params.token.is_empty() {
        "USDC"
    } else {
        &params.token
    });
    let mint_str = if token_mint == fida_mint() {
        FIDA_MINT_STR
    } else {
        USDC_MINT_STR
    };
    let space = params.space.min(10_240);
    let cost_label = if token_mint == fida_mint() {
        "15 FIDA"
    } else {
        "20 USDC"
    };

    // Build registration transaction via proxy (handles versioning, account layout, pricing oracle)
    let mut path = format!(
        "register?buyer={user}&domain={domain}&space={space}&serialize=true&mint={mint_str}"
    );
    let mut referrer_warning: Option<String> = None;
    if let Some(ref referrer_str) = params.referrer {
        if Pubkey::from_str(referrer_str).is_ok() {
            path.push_str(&format!("&referrer={referrer_str}"));
        } else {
            referrer_warning = Some(format!(
                "Referrer address '{referrer_str}' is invalid and was ignored"
            ));
        }
    }

    let serialized: String = proxy_get(http, &path).await?;

    let name_acct = domain_key(&domain);

    let mut warnings = vec![
        "Registration is permanent — no renewal fees".into(),
        format!("Storage: {} bytes", space),
    ];
    if space == 0 {
        warnings.push(
            "Space is 0 — no data can be stored. Consider requesting at least 1000 bytes.".into(),
        );
    }
    if let Some(w) = referrer_warning {
        warnings.push(w);
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_reg_{}", Uuid::new_v4()),
            action_type: "sns_register".into(),
            description: format!("Register {domain}.sol — {cost_label} + ~0.001 SOL gas"),
            estimated_fee: cost_label.into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "space": space }),
            warnings,
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "nameAccount": name_acct.to_string(),
            "space": space,
            "token": if token_mint == fida_mint() { "FIDA" } else { "USDC" },
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Transfer domain ownership (SPL Name Service Transfer ix)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_transfer(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsTransferParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;
    let new_owner = Pubkey::from_str(&params.new_owner)
        .map_err(|_| AppError::InvalidParams("Invalid new_owner pubkey".into()))?;

    let name_acct = domain_key(&domain);

    // Instruction data: [2(tag)] [new_owner 32 bytes]
    let mut data = vec![IX_TRANSFER];
    data.extend_from_slice(&new_owner.to_bytes());

    let ix = Instruction {
        program_id: name_program_id(),
        accounts: vec![
            AccountMeta::new(name_acct, false),
            AccountMeta::new_readonly(owner, true),
        ],
        data,
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_transfer_{}", Uuid::new_v4()),
            action_type: "sns_transfer".into(),
            description: format!("Transfer {domain}.sol → {}", &params.new_owner[..8]),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "newOwner": params.new_owner }),
            warnings: vec![
                "Domain transfer is irreversible without the new owner's cooperation".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "nameAccount": name_acct.to_string(),
            "newOwner": params.new_owner,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Set / update a record (SPL Name Service Update ix on record account)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_set_record(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsSetRecordParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;

    let record_acct = record_key_v1(&params.record, &domain);
    let value_bytes = params.value.as_bytes();

    // Update instruction: [1(tag)] [offset(u32 LE)] [len(u32 LE)] [data bytes]
    let mut data = vec![IX_UPDATE];
    data.extend_from_slice(&0u32.to_le_bytes()); // offset = 0
    data.extend_from_slice(&(value_bytes.len() as u32).to_le_bytes());
    data.extend_from_slice(value_bytes);

    // The signer for update is the domain owner (or the name class if a class is set)
    let ix = Instruction {
        program_id: name_program_id(),
        accounts: vec![
            AccountMeta::new(record_acct, false),
            AccountMeta::new_readonly(owner, true),
        ],
        data,
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_setrec_{}", Uuid::new_v4()),
            action_type: "sns_set_record".into(),
            description: format!(
                "Set {}.sol [{}] = {}",
                domain,
                params.record,
                &params.value[..params.value.len().min(30)]
            ),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "record": params.record, "value": params.value }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "record": params.record,
            "recordAccount": record_acct.to_string(),
            "value": params.value,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Delete domain (SPL Name Service Delete ix)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_delete(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsDeleteParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;

    let name_acct = domain_key(&domain);

    // Delete instruction: [3(tag)] — refund to owner
    let ix = Instruction {
        program_id: name_program_id(),
        accounts: vec![
            AccountMeta::new(name_acct, false),
            AccountMeta::new(owner, true),
            AccountMeta::new(owner, false), // refund target = owner
        ],
        data: vec![IX_DELETE],
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_del_{}", Uuid::new_v4()),
            action_type: "sns_delete".into(),
            description: format!("Delete {domain}.sol (reclaim rent)"),
            estimated_fee: "0 (rent refunded)".into(),
            estimated_refund: Some("~0.001–0.005 SOL".into()),
            params: serde_json::json!({ "domain": domain }),
            warnings: vec!["Deletion frees the domain for re-registration by anyone".into()],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "nameAccount": name_acct.to_string(),
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Create subdomain
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_create_subdomain(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsCreateSubdomainParams,
) -> Result<BuildResponse, AppError> {
    let parts: Vec<&str> = params
        .subdomain
        .trim_end_matches(".sol")
        .splitn(2, '.')
        .collect();
    if parts.len() != 2 {
        return Err(AppError::InvalidParams(
            "subdomain must be in format 'sub.parent'".into(),
        ));
    }
    let sub_label = parts[0];
    let parent_label = parts[1];

    let owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;

    let parent_key = domain_key(parent_label);
    let sub_key = subdomain_key(sub_label, &parent_key);
    let sub_hash = hashed_name(&format!("\0{}", sub_label));

    // Create instruction: [0(tag)] [hash_len(u32 LE)] [32 hash bytes] [lamports(u64 LE)] [space(u32 LE)]
    // lamports = 0 (minimum rent-exempt calculated by runtime for default space)
    // space = 2048 (default 2KB for subdomains)
    let space: u32 = 2048;
    let lamports: u64 = 0; // runtime calculates minimum

    let mut data = vec![IX_CREATE];
    data.extend_from_slice(&32u32.to_le_bytes()); // hashed_name length (always 32)
    data.extend_from_slice(&sub_hash);
    data.extend_from_slice(&lamports.to_le_bytes());
    data.extend_from_slice(&space.to_le_bytes());

    let create_ix = Instruction {
        program_id: name_program_id(),
        accounts: vec![
            AccountMeta::new(owner, true),
            AccountMeta::new(sub_key, false),
            AccountMeta::new_readonly(owner, false), // initial owner
            AccountMeta::new_readonly(sysvar::rent::id(), false),
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new(parent_key, false),
            AccountMeta::new(owner, true), // parent owner must sign
        ],
        data,
    };

    let tx = Transaction::new_with_payer(&[create_ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_sub_{}", Uuid::new_v4()),
            action_type: "sns_create_subdomain".into(),
            description: format!("Create subdomain {sub_label}.{parent_label}.sol"),
            estimated_fee: "~gas only (no registration fee)".into(),
            estimated_refund: None,
            params: serde_json::json!({ "subdomain": params.subdomain }),
            warnings: vec![
                "Parent domain owner can transfer subdomain without subdomain owner signature"
                    .into(),
            ],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "subdomain": format!("{sub_label}.{parent_label}.sol"),
            "subdomainKey": sub_key.to_string(),
            "parentKey": parent_key.to_string(),
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Fixed-price listing (Name-Offers make_fixed_price_offer)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_list(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsListParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let seller = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid seller pubkey".into()))?;

    if params.price <= 0.0 {
        return Err(AppError::InvalidParams("Price must be positive".into()));
    }

    let token_mint = resolve_payment_token(if params.token.is_empty() {
        "USDC"
    } else {
        &params.token
    });
    let name_acct = domain_key(&domain);
    let price_lamports = (params.price * 1_000_000.0) as u64; // USDC = 6 decimals

    // PDA for fixed-price offer account
    let (offer_acct, nonce) = Pubkey::find_program_address(
        &[b"fixed-price-offer", name_acct.as_ref(), seller.as_ref()],
        &name_offers_program(),
    );

    // Seller's token ATA (where they receive payment)
    let seller_token_acct =
        spl_associated_token_account::get_associated_token_address(&seller, &token_mint);

    // Anchor discriminator for make_fixed_price_offer
    let disc = anchor_discriminator("make_fixed_price_offer");

    // Instruction data: [disc(8)] [nonce(u8)] [price(u64 LE)]
    let mut data = disc.to_vec();
    data.push(nonce);
    data.extend_from_slice(&price_lamports.to_le_bytes());

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts: vec![
            AccountMeta::new(offer_acct, false),
            AccountMeta::new(name_acct, false),
            AccountMeta::new(seller, true),
            AccountMeta::new_readonly(token_mint, false),
            AccountMeta::new_readonly(seller_token_acct, false),
            AccountMeta::new_readonly(name_program_id(), false),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&seller));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());
    let token_label = if token_mint == fida_mint() {
        "FIDA"
    } else if token_mint == usdc_mint() {
        "USDC"
    } else {
        "tokens"
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_list_{}", Uuid::new_v4()),
            action_type: "sns_list".into(),
            description: format!("List {domain}.sol for {} {token_label}", params.price),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "price": params.price, "token": token_label }),
            warnings: vec![
                "Domain custody transfers to the offer escrow until sold or cancelled".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "offerAccount": offer_acct.to_string(),
            "price": params.price,
            "token": token_label,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Buy at fixed price (Name-Offers buy_fixed_price)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_buy(
    http: &Client,
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsBuyParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let buyer = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid buyer pubkey".into()))?;

    // Resolve current owner (who made the fixed-price listing) from proxy
    let seller_str = proxy_get::<String>(http, &format!("resolve/{domain}"))
        .await
        .map_err(|_| AppError::InvalidParams(format!("{domain}.sol not found or not listed")))?;
    let seller = Pubkey::from_str(&seller_str)
        .map_err(|_| AppError::Internal("Invalid seller pubkey from proxy".into()))?;

    let name_acct = domain_key(&domain);
    let (offer_acct, _) = Pubkey::find_program_address(
        &[b"fixed-price-offer", name_acct.as_ref(), seller.as_ref()],
        &name_offers_program(),
    );

    // Use token specified by caller — must match what the seller listed with
    let token_mint = resolve_payment_token(if params.token.is_empty() {
        "USDC"
    } else {
        &params.token
    });
    let buyer_token =
        spl_associated_token_account::get_associated_token_address(&buyer, &token_mint);
    let seller_token =
        spl_associated_token_account::get_associated_token_address(&seller, &token_mint);

    let disc = anchor_discriminator("buy_fixed_price");

    let mut accounts = vec![
        AccountMeta::new(buyer, true),
        AccountMeta::new(offer_acct, false),
        AccountMeta::new(name_acct, false),
        AccountMeta::new_readonly(name_program_id(), false),
        AccountMeta::new(buyer_token, false),
        AccountMeta::new(seller_token, false),
        AccountMeta::new_readonly(spl_token_program(), false),
        AccountMeta::new_readonly(system_program::id(), false),
    ];

    if let Some(ref referrer_str) = params.referrer {
        if let Ok(ref_key) = Pubkey::from_str(referrer_str) {
            let ref_ata =
                spl_associated_token_account::get_associated_token_address(&ref_key, &token_mint);
            accounts.push(AccountMeta::new(ref_ata, false));
        }
    }

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts,
        data: disc.to_vec(),
    };

    let token_label = if token_mint == fida_mint() {
        "FIDA"
    } else if token_mint == usdc_mint() {
        "USDC"
    } else {
        "tokens"
    };
    let tx = Transaction::new_with_payer(&[ix], Some(&buyer));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_buy_{}", Uuid::new_v4()),
            action_type: "sns_buy".into(),
            description: format!(
                "Buy {domain}.sol from {} (pay in {token_label})",
                &seller_str[..8]
            ),
            estimated_fee: format!("listing price in {token_label} + ~0.000005 SOL gas"),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "token": token_label }),
            warnings: vec![
                "Price is set by the seller's fixed-price listing".into(),
                format!("Paying with {token_label} — must match the token the seller listed with"),
            ],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "offerAccount": offer_acct.to_string(),
            "seller": seller_str,
            "token": token_label,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Make unsolicited offer (Name-Offers make_offer)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_make_offer(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsMakeOfferParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let buyer = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid buyer pubkey".into()))?;

    if params.amount <= 0.0 {
        return Err(AppError::InvalidParams(
            "Offer amount must be positive".into(),
        ));
    }

    let token_mint = resolve_payment_token(if params.token.is_empty() {
        "USDC"
    } else {
        &params.token
    });
    let name_acct = domain_key(&domain);
    let offer_amount = (params.amount * 1_000_000.0) as u64;

    let (offer_acct, nonce) = Pubkey::find_program_address(
        &[b"offer", name_acct.as_ref(), buyer.as_ref()],
        &name_offers_program(),
    );

    // Escrow account where buyer's funds are held
    let (escrow_acct, _) =
        Pubkey::find_program_address(&[b"escrow", offer_acct.as_ref()], &name_offers_program());

    let buyer_token = if params.token_account.is_empty() {
        spl_associated_token_account::get_associated_token_address(&buyer, &token_mint)
    } else {
        Pubkey::from_str(&params.token_account)
            .map_err(|_| AppError::InvalidParams("Invalid token_account".into()))?
    };

    let disc = anchor_discriminator("make_offer");
    let mut data = disc.to_vec();
    data.push(nonce);
    data.extend_from_slice(&offer_amount.to_le_bytes());

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts: vec![
            AccountMeta::new(offer_acct, false),
            AccountMeta::new(name_acct, false),
            AccountMeta::new(buyer, true),
            AccountMeta::new_readonly(token_mint, false),
            AccountMeta::new(buyer_token, false),
            AccountMeta::new(escrow_acct, false),
            AccountMeta::new_readonly(spl_token_program(), false),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let token_label = if token_mint == fida_mint() {
        "FIDA"
    } else if token_mint == usdc_mint() {
        "USDC"
    } else {
        "tokens"
    };
    let tx = Transaction::new_with_payer(&[ix], Some(&buyer));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_offer_{}", Uuid::new_v4()),
            action_type: "sns_make_offer".into(),
            description: format!("Offer {} {token_label} for {domain}.sol", params.amount),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "amount": params.amount, "token": token_label }),
            warnings: vec![
                "Funds are held in escrow until domain owner accepts or you cancel".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "offerAccount": offer_acct.to_string(),
            "escrowAccount": escrow_acct.to_string(),
            "amount": params.amount,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Accept unsolicited offer (Name-Offers accept_offer)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_accept_offer(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsAcceptOfferParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let domain_owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;
    let offer_acct = Pubkey::from_str(&params.offer_key)
        .map_err(|_| AppError::InvalidParams("Invalid offer_key".into()))?;

    let name_acct = domain_key(&domain);
    let (escrow_acct, _) =
        Pubkey::find_program_address(&[b"escrow", offer_acct.as_ref()], &name_offers_program());

    // Use token specified by caller — must match the token the buyer's offer was made in
    let token_mint = resolve_payment_token(if params.token.is_empty() {
        "USDC"
    } else {
        &params.token
    });
    let seller_token =
        spl_associated_token_account::get_associated_token_address(&domain_owner, &token_mint);

    let disc = anchor_discriminator("accept_offer");

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts: vec![
            AccountMeta::new(domain_owner, true),
            AccountMeta::new(offer_acct, false),
            AccountMeta::new(name_acct, false),
            AccountMeta::new(escrow_acct, false),
            AccountMeta::new(seller_token, false),
            AccountMeta::new_readonly(name_program_id(), false),
            AccountMeta::new_readonly(spl_token_program(), false),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data: disc.to_vec(),
    };

    let token_label = if token_mint == fida_mint() {
        "FIDA"
    } else if token_mint == usdc_mint() {
        "USDC"
    } else {
        "tokens"
    };
    let tx = Transaction::new_with_payer(&[ix], Some(&domain_owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_accept_{}", Uuid::new_v4()),
            action_type: "sns_accept_offer".into(),
            description: format!(
                "Accept {token_label} offer for {domain}.sol — receive payment from escrow"
            ),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "offerKey": params.offer_key, "token": token_label }),
            warnings: vec![
                "Domain transfers to buyer immediately upon acceptance".into(),
                format!(
                    "You will receive {token_label} from escrow — ensure this matches the offer"
                ),
            ],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "offerAccount": params.offer_key,
            "token": token_label,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Cancel unsolicited offer (Name-Offers cancel_offer)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_cancel_offer(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsCancelOfferParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let buyer = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid buyer pubkey".into()))?;
    let offer_acct = Pubkey::from_str(&params.offer_key)
        .map_err(|_| AppError::InvalidParams("Invalid offer_key".into()))?;

    let token_mint = resolve_payment_token(if params.token.is_empty() {
        "USDC"
    } else {
        &params.token
    });
    let (escrow_acct, _) =
        Pubkey::find_program_address(&[b"escrow", offer_acct.as_ref()], &name_offers_program());
    let buyer_token =
        spl_associated_token_account::get_associated_token_address(&buyer, &token_mint);

    let disc = anchor_discriminator("cancel_offer");

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts: vec![
            AccountMeta::new(buyer, true),
            AccountMeta::new(offer_acct, false),
            AccountMeta::new(escrow_acct, false),
            AccountMeta::new(buyer_token, false),
            AccountMeta::new_readonly(spl_token_program(), false),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data: disc.to_vec(),
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&buyer));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_cancel_offer_{}", Uuid::new_v4()),
            action_type: "sns_cancel_offer".into(),
            description: format!("Cancel offer for {domain}.sol — refund escrow"),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "offerKey": params.offer_key }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "offerAccount": params.offer_key,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Create P2P offer (Name-Offers make_p2p)
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_p2p_create(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsP2pCreateParams,
) -> Result<BuildResponse, AppError> {
    let owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;
    let counter_party = Pubkey::from_str(&params.counter_party)
        .map_err(|_| AppError::InvalidParams("Invalid counter_party pubkey".into()))?;

    if params.base_domains.is_empty() {
        return Err(AppError::InvalidParams(
            "At least one base domain is required".into(),
        ));
    }

    let (offer_acct, nonce) = Pubkey::find_program_address(
        &[b"p2p-offer", owner.as_ref(), counter_party.as_ref()],
        &name_offers_program(),
    );

    let amount_lamports = (params.amount_sol * 1_000_000_000.0) as u64;
    let disc = anchor_discriminator("make_p2p");

    // Instruction data: [disc(8)] [nonce(u8)] [n_base(u32)] [base_keys] [n_quote(u32)] [quote_keys] [amount(u64)] [expiry(u64)]
    let mut data = disc.to_vec();
    data.push(nonce);
    data.extend_from_slice(&(params.base_domains.len() as u32).to_le_bytes());
    for dk in &params.base_domains {
        let key = Pubkey::from_str(dk)
            .map_err(|_| AppError::InvalidParams(format!("Invalid base domain key: {dk}")))?;
        data.extend_from_slice(&key.to_bytes());
    }
    data.extend_from_slice(&(params.quote_domains.len() as u32).to_le_bytes());
    for dk in &params.quote_domains {
        let key = Pubkey::from_str(dk)
            .map_err(|_| AppError::InvalidParams(format!("Invalid quote domain key: {dk}")))?;
        data.extend_from_slice(&key.to_bytes());
    }
    data.extend_from_slice(&amount_lamports.to_le_bytes());
    data.extend_from_slice(&params.expiry_ts.to_le_bytes());

    // Accounts: offer PDA + all base domain name accounts + owner + counter_party + system_program
    let mut accounts = vec![
        AccountMeta::new(offer_acct, false),
        AccountMeta::new(owner, true),
        AccountMeta::new_readonly(counter_party, false),
        AccountMeta::new_readonly(name_program_id(), false),
        AccountMeta::new_readonly(system_program::id(), false),
    ];
    for dk in &params.base_domains {
        let key = Pubkey::from_str(dk).unwrap();
        accounts.push(AccountMeta::new(key, false));
    }
    for dk in &params.quote_domains {
        let key = Pubkey::from_str(dk).unwrap();
        accounts.push(AccountMeta::new_readonly(key, false));
    }

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts,
        data,
    };
    let tx = Transaction::new_with_payer(&[ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_p2p_{}", Uuid::new_v4()),
            action_type: "sns_p2p_create".into(),
            description: format!(
                "P2P offer: {} domain{} → {} domain{} + {} SOL to {}",
                params.base_domains.len(),
                if params.base_domains.len() == 1 {
                    ""
                } else {
                    "s"
                },
                params.quote_domains.len(),
                if params.quote_domains.len() == 1 {
                    ""
                } else {
                    "s"
                },
                params.amount_sol,
                &params.counter_party[..8]
            ),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "baseDomains": params.base_domains,
                "quoteDomains": params.quote_domains,
                "counterParty": params.counter_party,
            }),
            warnings: vec![
                format!("Only {} can accept this offer", &params.counter_party[..8]),
                if params.expiry_ts > 0 {
                    format!("Expires at unix ts {}", params.expiry_ts)
                } else {
                    "No expiry set".into()
                },
            ],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "offerAccount": offer_acct.to_string(),
            "baseDomains": params.base_domains,
            "quoteDomains": params.quote_domains,
            "amountSol": params.amount_sol,
            "counterParty": params.counter_party,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Accept P2P offer
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_p2p_accept(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsP2pAcceptParams,
) -> Result<BuildResponse, AppError> {
    let counter_party =
        Pubkey::from_str(user).map_err(|_| AppError::InvalidParams("Invalid pubkey".into()))?;
    let offer_acct = Pubkey::from_str(&params.offer_key)
        .map_err(|_| AppError::InvalidParams("Invalid offer_key".into()))?;

    let disc = anchor_discriminator("accept_p2p");

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts: vec![
            AccountMeta::new(counter_party, true),
            AccountMeta::new(offer_acct, false),
            AccountMeta::new_readonly(name_program_id(), false),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data: disc.to_vec(),
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&counter_party));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_p2p_accept_{}", Uuid::new_v4()),
            action_type: "sns_p2p_accept".into(),
            description: format!("Accept P2P offer {}", &params.offer_key[..8]),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "offerKey": params.offer_key }),
            warnings: vec!["Domain exchange is atomic — both sides transfer simultaneously".into()],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({ "offerAccount": params.offer_key })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Cancel P2P offer
// ─────────────────────────────────────────────────────────────────────────────

pub async fn build_sns_p2p_cancel(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsP2pCancelParams,
) -> Result<BuildResponse, AppError> {
    let owner =
        Pubkey::from_str(user).map_err(|_| AppError::InvalidParams("Invalid pubkey".into()))?;
    let offer_acct = Pubkey::from_str(&params.offer_key)
        .map_err(|_| AppError::InvalidParams("Invalid offer_key".into()))?;

    let disc = anchor_discriminator("cancel_p2p");

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts: vec![
            AccountMeta::new(owner, true),
            AccountMeta::new(offer_acct, false),
            AccountMeta::new_readonly(name_program_id(), false),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data: disc.to_vec(),
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_p2p_cancel_{}", Uuid::new_v4()),
            action_type: "sns_p2p_cancel".into(),
            description: format!(
                "Cancel P2P offer {} — reclaim domains",
                &params.offer_key[..8]
            ),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "offerKey": params.offer_key }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({ "offerAccount": params.offer_key })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Set primary / favourite domain (Name-Offers registerFavourite ix, tag=6)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsSetFavoriteParams {
    pub domain: String,
}

pub async fn build_sns_set_favorite(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsSetFavoriteParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;

    let name_acct = domain_key(&domain);

    // PDA seeds: ["favourite_domain", owner]
    let (favourite_acct, _) = Pubkey::find_program_address(
        &[b"favourite_domain", owner.as_ref()],
        &name_offers_program(),
    );

    // Borsh-serialised instruction: single tag byte
    let data = vec![IX_FAVOURITE];

    let ix = Instruction {
        program_id: name_offers_program(),
        accounts: vec![
            AccountMeta::new_readonly(name_acct, false),
            AccountMeta::new(favourite_acct, false),
            AccountMeta::new(owner, true),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_setfav_{}", Uuid::new_v4()),
            action_type: "sns_set_favorite".into(),
            description: format!("Set {domain}.sol as your primary domain"),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain }),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "favouriteAccount": favourite_acct.to_string(),
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// Query: List all subdomains of a parent domain
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsSubdomainsParams {
    pub parent_domain: String,
}

pub async fn build_sns_subdomains(
    http: &Client,
    params: SnsSubdomainsParams,
) -> Result<BuildResponse, AppError> {
    let parent = params.parent_domain.trim_end_matches(".sol").to_lowercase();
    // Proxy returns array of subdomain pubkeys/names for a parent domain
    let subdomains: Vec<String> = proxy_get(http, &format!("subdomains/{parent}"))
        .await
        .unwrap_or_default();

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_subs_{}", Uuid::new_v4()),
            action_type: "sns_subdomains".into(),
            description: format!("{parent}.sol has {} subdomain(s)", subdomains.len()),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "parentDomain": parent }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "parentDomain": format!("{parent}.sol"),
            "subdomains": subdomains,
            "count": subdomains.len(),
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Reallocate domain storage space (SPL Name Service Realloc ix, opcode=4)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsReallocParams {
    pub domain: String,
    /// New total space in bytes (max 10240)
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub new_space: u32,
}

pub async fn build_sns_realloc(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsReallocParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;

    if params.new_space > 10_240 {
        return Err(AppError::InvalidParams(
            "new_space cannot exceed 10240 bytes".into(),
        ));
    }

    let name_acct = domain_key(&domain);

    // Realloc instruction: [4(tag)] [new_space(u32 LE)]
    let mut data = vec![IX_REALLOC];
    data.extend_from_slice(&params.new_space.to_le_bytes());

    let ix = Instruction {
        program_id: name_program_id(),
        accounts: vec![
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new(owner, true), // payer
            AccountMeta::new(name_acct, false),
            AccountMeta::new_readonly(owner, false), // name owner
        ],
        data,
    };

    let tx = Transaction::new_with_payer(&[ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_realloc_{}", Uuid::new_v4()),
            action_type: "sns_realloc".into(),
            description: format!(
                "Reallocate {domain}.sol storage to {} bytes",
                params.new_space
            ),
            estimated_fee: "~rent difference in SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "newSpace": params.new_space }),
            warnings: vec!["Increasing space requires extra rent-exempt SOL deposit".into()],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "nameAccount": name_acct.to_string(),
            "newSpace": params.new_space,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Transfer subdomain ownership (SPL Name Service Transfer ix)
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsTransferSubdomainParams {
    /// Full subdomain e.g. "sub.parent" or "sub.parent.sol"
    pub subdomain: String,
    pub new_owner: String,
    /// true = parent domain owner is the signer (not the current subdomain owner)
    #[serde(default, deserialize_with = "crate::services::params::lenient")]
    pub parent_owner_signs: bool,
}

pub async fn build_sns_transfer_subdomain(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsTransferSubdomainParams,
) -> Result<BuildResponse, AppError> {
    let full = params.subdomain.trim_end_matches(".sol");
    let parts: Vec<&str> = full.splitn(2, '.').collect();
    if parts.len() != 2 {
        return Err(AppError::InvalidParams(
            "subdomain must be in format 'sub.parent'".into(),
        ));
    }
    let sub_label = parts[0];
    let parent_label = parts[1];

    let signer = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid signer pubkey".into()))?;
    let new_owner = Pubkey::from_str(&params.new_owner)
        .map_err(|_| AppError::InvalidParams("Invalid new_owner pubkey".into()))?;

    let parent_key = domain_key(parent_label);
    let sub_key = subdomain_key(sub_label, &parent_key);

    // Transfer instruction: [2(tag)] [new_owner 32 bytes]
    let mut data = vec![IX_TRANSFER];
    data.extend_from_slice(&new_owner.to_bytes());

    // Accounts: name_acct, current_owner (signer), optional parent
    let mut accounts = vec![
        AccountMeta::new(sub_key, false),
        AccountMeta::new_readonly(signer, true),
        AccountMeta::new_readonly(parent_key, false),
    ];
    if params.parent_owner_signs {
        // Parent owner can transfer subdomains without the subdomain owner's signature
        accounts.push(AccountMeta::new_readonly(signer, true));
    }

    let ix = Instruction {
        program_id: name_program_id(),
        accounts,
        data,
    };
    let tx = Transaction::new_with_payer(&[ix], Some(&signer));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_tsub_{}", Uuid::new_v4()),
            action_type: "sns_transfer_subdomain".into(),
            description: format!(
                "Transfer {sub_label}.{parent_label}.sol → {}",
                &params.new_owner[..8]
            ),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "subdomain": params.subdomain, "newOwner": params.new_owner }),
            warnings: vec![
                "Subdomain transfer is irreversible without the new owner's cooperation".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "subdomain": format!("{sub_label}.{parent_label}.sol"),
            "subdomainKey": sub_key.to_string(),
            "newOwner": params.new_owner,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// TX: Create a V1 record account (SPL Name Service Create ix, opcode=0)
//     Use this to initialise a record for the first time; use sns_set_record
//     to update an already-existing record account.
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsCreateRecordParams {
    pub domain: String,
    /// Record type: "ETH" | "SOL" | "IPFS" | "A" | "AAAA" | "twitter" | "discord" | etc.
    pub record: String,
    /// Initial value for the record
    pub value: String,
    /// Storage space for the record in bytes (default = value length + 32 padding)
    #[serde(default, deserialize_with = "crate::services::params::lenient")]
    pub space: u32,
}

pub async fn build_sns_create_record(
    _rpc: &SolanaRpc,
    user: &str,
    params: SnsCreateRecordParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol").to_lowercase();
    let owner = Pubkey::from_str(user)
        .map_err(|_| AppError::InvalidParams("Invalid owner pubkey".into()))?;

    let record_acct = record_key_v1(&params.record, &domain);
    let domain_acct = domain_key(&domain);
    let rev_class = reverse_lookup_class();

    // Compute hash of the record name (with \x01 prefix, already done in record_key_v1)
    let record_name = format!("\x01{}.{}", params.record, domain);
    let record_hash = hashed_name(&record_name);

    // Space: use provided or auto-size from value + 32-byte padding
    let value_bytes = params.value.as_bytes();
    let space = if params.space > 0 {
        params.space
    } else {
        (value_bytes.len() as u32).saturating_add(32)
    };

    // Create instruction: [0(tag)] [32(u32 LE)] [hashed_name 32 bytes] [lamports(u64 LE)] [space(u32 LE)]
    let mut data = vec![IX_CREATE];
    data.extend_from_slice(&32u32.to_le_bytes());
    data.extend_from_slice(&record_hash);
    data.extend_from_slice(&0u64.to_le_bytes()); // runtime calculates minimum rent
    data.extend_from_slice(&space.to_le_bytes());

    // Instruction 1: Create the record account
    let create_ix = Instruction {
        program_id: name_program_id(),
        accounts: vec![
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new(owner, true),               // payer
            AccountMeta::new(record_acct, false),        // new record account
            AccountMeta::new_readonly(owner, false),     // initial owner
            AccountMeta::new_readonly(rev_class, false), // name class = REVERSE_LOOKUP_CLASS
            AccountMeta::new(domain_acct, false),        // parent = domain account
            AccountMeta::new_readonly(owner, true),      // parent owner must sign
        ],
        data,
    };

    // Instruction 2: Write initial value via Update
    let mut update_data = vec![IX_UPDATE];
    update_data.extend_from_slice(&0u32.to_le_bytes());
    update_data.extend_from_slice(&(value_bytes.len() as u32).to_le_bytes());
    update_data.extend_from_slice(value_bytes);

    let update_ix = Instruction {
        program_id: name_program_id(),
        accounts: vec![
            AccountMeta::new(record_acct, false),
            AccountMeta::new_readonly(owner, true),
        ],
        data: update_data,
    };

    let tx = Transaction::new_with_payer(&[create_ix, update_ix], Some(&owner));
    let serialized =
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(&tx).unwrap());

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_crec_{}", Uuid::new_v4()),
            action_type: "sns_create_record".into(),
            description: format!("Create {}.sol [{}] record = {}", domain, params.record, &params.value[..params.value.len().min(30)]),
            estimated_fee: "~rent-exempt SOL + ~0.000005 gas".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": domain, "record": params.record, "value": params.value, "space": space }),
            warnings: vec!["Creates the record account for the first time; use sns_set_record to update an existing one".into()],
            requires_approval: true,
        },
        transaction: Some(serialized),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "record": params.record,
            "recordAccount": record_acct.to_string(),
            "value": params.value,
            "space": space,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// QUERY: Domain key lookup
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsDomainKeyParams {
    pub domain: String,
}

pub async fn build_sns_domain_key(
    http: &reqwest::Client,
    params: SnsDomainKeyParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol");
    let key: String = proxy_get(http, &format!("domain-key/{domain}")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_dk_{}", Uuid::new_v4()),
            action_type: "sns_domain_key".into(),
            description: format!("Domain key for {domain}.sol"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": params.domain }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "key": key,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// QUERY: Record key lookup
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsRecordKeyParams {
    pub domain: String,
    pub record: String,
}

pub async fn build_sns_record_key(
    http: &reqwest::Client,
    params: SnsRecordKeyParams,
) -> Result<BuildResponse, AppError> {
    let domain = params.domain.trim_end_matches(".sol");
    let record = &params.record;
    let key: String = proxy_get(http, &format!("record-key/{domain}/{record}")).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_rk_{}", Uuid::new_v4()),
            action_type: "sns_record_key".into(),
            description: format!("Record key for {record} on {domain}.sol"),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "domain": params.domain, "record": params.record }),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "domain": format!("{domain}.sol"),
            "record": record,
            "key": key,
        })),
    })
}

// ─────────────────────────────────────────────────────────────────────────────
// QUERY: Twitter handle ↔ key lookups
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnsTwitterHandleParams {
    /// Twitter handle (without @) OR a Solana pubkey — function detects which
    pub input: String,
}

pub async fn build_sns_twitter_handle(
    http: &reqwest::Client,
    params: SnsTwitterHandleParams,
) -> Result<BuildResponse, AppError> {
    let input = params.input.trim_start_matches('@');

    // If it looks like a base58 pubkey (32–44 chars, alphanumeric), resolve key → handle
    let (description, data) = if input.len() >= 32
        && input.chars().all(|c| c.is_ascii_alphanumeric())
    {
        let handle: String = proxy_get(http, &format!("twitter/get-handle-by-key/{input}")).await?;
        (
            format!("Twitter handle for key {}", &input[..8]),
            serde_json::json!({ "key": input, "handle": handle }),
        )
    } else {
        let key: String = proxy_get(http, &format!("twitter/get-key-by-handle/{input}")).await?;
        (
            format!("Solana key for @{input}"),
            serde_json::json!({ "handle": input, "key": key }),
        )
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: format!("sns_tw_{}", Uuid::new_v4()),
            action_type: "sns_twitter_handle".into(),
            description,
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::json!({ "input": params.input }),
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

// ─────────────────────────────────────────────────────────────────────────────
// Validation helpers (called from builder.rs)
// ─────────────────────────────────────────────────────────────────────────────

pub fn validate_domain_name(domain: &str) -> Result<(), AppError> {
    let clean = domain.trim_end_matches(".sol");
    if clean.is_empty() {
        return Err(AppError::InvalidParams("Domain name is empty".into()));
    }
    if clean.len() > 63 {
        return Err(AppError::InvalidParams(
            "Domain name too long (max 63 chars)".into(),
        ));
    }
    if !clean.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return Err(AppError::InvalidParams(
            "Domain name may only contain lowercase letters, digits, and hyphens".into(),
        ));
    }
    Ok(())
}

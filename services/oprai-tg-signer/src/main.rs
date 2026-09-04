//! OPRAI Telegram bot custodial signer.
//!
//! This is the ONLY component in the Telegram stack that touches private keys.
//! Isolation is the point: it runs as its own service/container, its only
//! external dependency is Vault (Transit engine) for envelope encryption, and
//! it exposes a tiny surface. It is fail-closed: no Vault, no signing.
//!
//! The signer is STATELESS. A key's ciphertext (`enc_key_ref`, a Vault
//! `vault:v1:…` string) is stored by the bot and passed back on every sign
//! call; only this service's Vault token can decrypt it, so the bot DB alone
//! can never recover a key.

mod crypto;
mod vault;

use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer};
use serde::{Deserialize, Serialize};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

use crate::crypto::Chain;
use crate::vault::Vault;

struct AppState {
    vault: Option<Vault>,
    /// Shared secret the bot must present (X-Internal-Api-Key). None = signing
    /// is refused (fail-closed): an unauthenticated signer must never sign.
    internal_key: Option<String>,
}

/// Constant-time header check. Every mutating endpoint is gated: only the bot,
/// holding OPRAI_INTERNAL_API_KEY, may create/import/sign. Loopback binding is
/// defence-in-depth, not the control.
fn require_auth(req: &HttpRequest, state: &AppState) -> Result<(), HttpResponse> {
    let key = state.internal_key.as_deref().ok_or_else(|| {
        unavailable("signer internal API key not configured — refusing (fail-closed)")
    })?;
    let provided = req
        .headers()
        .get("x-internal-api-key")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if ct_eq(provided.as_bytes(), key.as_bytes()) {
        Ok(())
    } else {
        Err(HttpResponse::Unauthorized().json(ErrResp {
            error: "unauthorized".into(),
        }))
    }
}

/// Constant-time byte comparison (length may differ; content compare is CT).
fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

#[derive(Serialize)]
struct Health {
    status: &'static str,
    service: &'static str,
    vault: &'static str,
}

async fn health(state: web::Data<AppState>) -> HttpResponse {
    let vault = match &state.vault {
        None => "not-configured",
        Some(v) => {
            if v.healthy().await {
                "connected"
            } else {
                "unreachable"
            }
        }
    };
    HttpResponse::Ok().json(Health {
        status: "ok",
        service: "oprai-tg-signer",
        vault,
    })
}

// ── request/response shapes ─────────────────────────────────────────────────
#[derive(Deserialize)]
struct CreateReq {
    chain: String,
}
#[derive(Deserialize)]
struct ImportReq {
    chain: String,
    secret: String,
}
#[derive(Deserialize)]
struct SignReq {
    chain: String,
    enc_key_ref: String,
    message: String, // UTF-8 message (SIWS/SIWE auth text)
}
#[derive(Deserialize)]
struct ExportReq {
    chain: String,
    enc_key_ref: String,
}
#[derive(Serialize)]
struct ExportResp {
    address: String,
    secret: String,
}
#[derive(Serialize)]
struct WalletResp {
    address: String,
    enc_key_ref: String,
}
#[derive(Serialize)]
struct SignResp {
    address: String,
    signature: String,
}

/// EIP-1559 transaction fields, amounts as strings (decimal or 0x-hex) so no
/// precision is lost through JSON numbers.
#[derive(Deserialize, Default)]
#[serde(default)]
struct TxFields {
    chain_id: String,
    nonce: String,
    to: String,
    value: String,
    data: String,
    gas: String,
    max_fee_per_gas: String,
    max_priority_fee_per_gas: String,
}
#[derive(Deserialize)]
struct SignTxReq {
    #[allow(dead_code)]
    chain: Option<String>, // always "evm" today; accepted for symmetry
    enc_key_ref: String,
    tx: TxFields,
}
#[derive(Serialize)]
struct SignTxResp {
    address: String,
    raw: String,
    hash: String,
}

/// EIP-712 typed data (Permit2 for Uniswap ERC-20 swaps). The payload is passed
/// through as-is; the crypto layer normalises Uniswap's `values`/no-primaryType
/// variant before hashing.
#[derive(Deserialize)]
struct SignTypedReq {
    #[allow(dead_code)]
    chain: Option<String>,
    enc_key_ref: String,
    typed_data: serde_json::Value,
}
#[derive(Serialize)]
struct ErrResp {
    error: String,
}

fn bad(msg: impl Into<String>) -> HttpResponse {
    HttpResponse::BadRequest().json(ErrResp { error: msg.into() })
}
fn unavailable(msg: impl Into<String>) -> HttpResponse {
    HttpResponse::ServiceUnavailable().json(ErrResp { error: msg.into() })
}
fn upstream(msg: impl Into<String>) -> HttpResponse {
    HttpResponse::BadGateway().json(ErrResp { error: msg.into() })
}

fn require_vault(state: &AppState) -> Result<&Vault, HttpResponse> {
    state
        .vault
        .as_ref()
        .ok_or_else(|| unavailable("signer is not configured with Vault — signing disabled"))
}

async fn wallet_create(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<CreateReq>,
) -> HttpResponse {
    if let Err(e) = require_auth(&req, &state) {
        return e;
    }
    let vault = match require_vault(&state) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let chain = match Chain::parse(&body.chain) {
        Ok(c) => c,
        Err(e) => return bad(e.to_string()),
    };
    let km = match crypto::generate(chain) {
        Ok(k) => k,
        Err(e) => return bad(e.to_string()),
    };
    match vault.encrypt(&km.secret).await {
        Ok(ct) => HttpResponse::Ok().json(WalletResp {
            address: km.address,
            enc_key_ref: ct,
        }),
        Err(e) => upstream(e.to_string()),
    }
}

async fn wallet_import(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<ImportReq>,
) -> HttpResponse {
    if let Err(e) = require_auth(&req, &state) {
        return e;
    }
    let vault = match require_vault(&state) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let chain = match Chain::parse(&body.chain) {
        Ok(c) => c,
        Err(e) => return bad(e.to_string()),
    };
    let km = match crypto::import(chain, &body.secret) {
        Ok(k) => k,
        Err(e) => return bad(e.to_string()),
    };
    match vault.encrypt(&km.secret).await {
        Ok(ct) => HttpResponse::Ok().json(WalletResp {
            address: km.address,
            enc_key_ref: ct,
        }),
        Err(e) => upstream(e.to_string()),
    }
}

/// Hand the user back their own private key.
///
/// Custody that cannot be left is not custody, it is a trap: without this a
/// person's funds are only ever reachable through us. So the key can come out
/// — but this is the one endpoint that returns plaintext key material, so it
/// is gated like the others and the caller is expected to warn, confirm and
/// audit before asking.
async fn wallet_export(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<ExportReq>,
) -> HttpResponse {
    if let Err(e) = require_auth(&req, &state) {
        return e;
    }
    let vault = match require_vault(&state) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let chain = match Chain::parse(&body.chain) {
        Ok(c) => c,
        Err(e) => return bad(e.to_string()),
    };
    let secret = match vault.decrypt(&body.enc_key_ref).await {
        Ok(s) => s,
        Err(e) => return upstream(e.to_string()),
    };
    // Derive the address from the key rather than trusting the caller's: an
    // exported key that doesn't match the wallet we showed would send someone
    // to the wrong place with the right-looking confirmation.
    let address = match crypto::address_from_secret(chain, &secret) {
        Ok(a) => a,
        Err(e) => return bad(e.to_string()),
    };
    let encoded = crypto::encode_secret(chain, &secret);
    tracing::warn!(%address, "wallet key exported");
    HttpResponse::Ok().json(ExportResp {
        address,
        secret: encoded,
    })
}

async fn sign(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<SignReq>,
) -> HttpResponse {
    if let Err(e) = require_auth(&req, &state) {
        return e;
    }
    let vault = match require_vault(&state) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let chain = match Chain::parse(&body.chain) {
        Ok(c) => c,
        Err(e) => return bad(e.to_string()),
    };
    // decrypt → derive address → sign → secret is Zeroizing (wiped on drop)
    let secret = match vault.decrypt(&body.enc_key_ref).await {
        Ok(s) => zeroize::Zeroizing::new(s),
        Err(e) => return upstream(e.to_string()),
    };
    let address = match crypto::address_from_secret(chain, &secret) {
        Ok(a) => a,
        Err(e) => return bad(e.to_string()),
    };
    match crypto::sign_message(chain, &secret, body.message.as_bytes()) {
        Ok(signature) => HttpResponse::Ok().json(SignResp { address, signature }),
        Err(e) => bad(e.to_string()),
    }
}

/// POST /sign-tx — sign an EIP-1559 transaction; the caller submits `raw` via
/// eth_sendRawTransaction. Money-moving: gated by the internal API key, and the
/// CALLER owns policy (caps, confirmations). The key is decrypted, used, and
/// wiped (Zeroizing) within this call.
async fn sign_tx(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<SignTxReq>,
) -> HttpResponse {
    if let Err(e) = require_auth(&req, &state) {
        return e;
    }
    let vault = match require_vault(&state) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let secret = match vault.decrypt(&body.enc_key_ref).await {
        Ok(s) => zeroize::Zeroizing::new(s),
        Err(e) => return upstream(e.to_string()),
    };
    let t = &body.tx;
    let tx = crypto::EvmTx {
        chain_id: t.chain_id.clone(),
        nonce: t.nonce.clone(),
        to: t.to.clone(),
        value: t.value.clone(),
        data: t.data.clone(),
        gas: t.gas.clone(),
        max_fee_per_gas: t.max_fee_per_gas.clone(),
        max_priority_fee_per_gas: t.max_priority_fee_per_gas.clone(),
    };
    match crypto::sign_evm_tx(&secret, &tx) {
        Ok(s) => HttpResponse::Ok().json(SignTxResp {
            address: s.address,
            raw: s.raw,
            hash: s.hash,
        }),
        Err(e) => bad(e.to_string()),
    }
}

/// POST /sign-typed-data — sign EIP-712 typed data (Permit2). Same custody
/// rules as every other signing route: internal-key gated, key decrypted for
/// the call and wiped after.
async fn sign_typed_data(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<SignTypedReq>,
) -> HttpResponse {
    if let Err(e) = require_auth(&req, &state) {
        return e;
    }
    let vault = match require_vault(&state) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let secret = match vault.decrypt(&body.enc_key_ref).await {
        Ok(s) => zeroize::Zeroizing::new(s),
        Err(e) => return upstream(e.to_string()),
    };
    let address = match crypto::address_from_secret(Chain::Evm, &secret) {
        Ok(a) => a,
        Err(e) => return bad(e.to_string()),
    };
    match crypto::sign_typed_data(&secret, &body.typed_data) {
        Ok(signature) => HttpResponse::Ok().json(SignResp { address, signature }),
        Err(e) => bad(e.to_string()),
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let _ = dotenvy::from_filename(".env");
    let _ = dotenvy::from_filename("../../.env");

    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with(tracing_subscriber::fmt::layer())
        .init();

    let bind_host = std::env::var("BIND_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(3060);

    let vault = Vault::from_env();
    if vault.is_none() {
        tracing::warn!("VAULT_ADDR/VAULT_TOKEN not set — signing endpoints are DISABLED (fail-closed)");
    }

    let internal_key = std::env::var("OPRAI_INTERNAL_API_KEY")
        .ok()
        .filter(|s| !s.is_empty());
    if internal_key.is_none() {
        tracing::warn!("OPRAI_INTERNAL_API_KEY not set — signing endpoints are DISABLED (fail-closed)");
    }

    tracing::info!(
        %bind_host, port,
        vault = vault.is_some(),
        auth = internal_key.is_some(),
        "oprai-tg-signer starting"
    );

    let state = web::Data::new(AppState {
        vault,
        internal_key,
    });

    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .app_data(web::JsonConfig::default().limit(64 * 1024))
            .route("/health", web::get().to(health))
            .route("/wallet/create", web::post().to(wallet_create))
            .route("/wallet/import", web::post().to(wallet_import))
            .route("/wallet/export", web::post().to(wallet_export))
            // SIWS/SIWE auth signing + (later) tx signing all go through /sign,
            // with `chain` in the body.
            .route("/sign", web::post().to(sign))
            .route("/sign-tx", web::post().to(sign_tx))
            .route("/sign-typed-data", web::post().to(sign_typed_data))
    })
    .bind((bind_host, port))?
    .run()
    .await?;

    Ok(())
}

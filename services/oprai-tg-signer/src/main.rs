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

use actix_web::{web, App, HttpResponse, HttpServer};
use serde::{Deserialize, Serialize};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

use crate::crypto::Chain;
use crate::vault::Vault;

struct AppState {
    vault: Option<Vault>,
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
    state: web::Data<AppState>,
    body: web::Json<CreateReq>,
) -> HttpResponse {
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
    state: web::Data<AppState>,
    body: web::Json<ImportReq>,
) -> HttpResponse {
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

async fn sign(state: web::Data<AppState>, body: web::Json<SignReq>) -> HttpResponse {
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

    tracing::info!(%bind_host, port, vault = vault.is_some(), "oprai-tg-signer starting");

    let state = web::Data::new(AppState { vault });

    HttpServer::new(move || {
        App::new()
            .app_data(state.clone())
            .app_data(web::JsonConfig::default().limit(64 * 1024))
            .route("/health", web::get().to(health))
            .route("/wallet/create", web::post().to(wallet_create))
            .route("/wallet/import", web::post().to(wallet_import))
            // SIWS/SIWE auth signing + (later) tx signing all go through /sign,
            // with `chain` in the body.
            .route("/sign", web::post().to(sign))
    })
    .bind((bind_host, port))?
    .run()
    .await?;

    Ok(())
}

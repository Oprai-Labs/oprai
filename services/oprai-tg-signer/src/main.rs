//! OPRAI Telegram bot custodial signer.
//!
//! This is the ONLY component in the Telegram stack that touches private keys.
//! Isolation is the point: it runs as its own service/container, its only
//! external dependency is Vault (Transit engine) for envelope encryption, and
//! it exposes a tiny surface. Faz 0 ships the scaffold + /health; custody +
//! signing (wallet/create, wallet/import, sign, siws-sign, siwe-sign) land in
//! 0.3, at which point it is fail-closed: no Vault, no signing.

use actix_web::{web, App, HttpResponse, HttpServer};
use serde::Serialize;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

#[derive(Serialize)]
struct Health {
    status: &'static str,
    service: &'static str,
    // 0.3 flips this to reflect a live Vault Transit connection.
    vault: &'static str,
}

async fn health() -> HttpResponse {
    HttpResponse::Ok().json(Health {
        status: "ok",
        service: "oprai-tg-signer",
        vault: "not-wired", // 0.3
    })
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // ── Load .env (service-local, then monorepo root fallback) ───────────────
    let _ = dotenvy::from_filename(".env");
    let _ = dotenvy::from_filename("../../.env");

    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with(tracing_subscriber::fmt::layer())
        .init();

    let bind_host =
        std::env::var("BIND_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(3060);

    tracing::info!(%bind_host, port, "oprai-tg-signer starting");

    HttpServer::new(|| App::new().route("/health", web::get().to(health)))
        .bind((bind_host, port))?
        .run()
        .await?;

    Ok(())
}

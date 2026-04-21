// solana_sdk::system_program and system_instruction are deprecated in 2.x in favour of
// solana-system-interface, but that crate's Address type is not yet ABI-compatible with
// AccountMeta's Pubkey expectation across all sub-crate version combinations.
// Suppressed until the solana ecosystem fully stabilises the split-crate types.
#![allow(deprecated)]

mod config;
mod db;
mod error;
mod grpc;
mod middleware;
mod routes;
mod services;
mod solana;

use std::sync::Arc;

use actix_governor::{Governor, GovernorConfigBuilder};
use actix_web::{web, App, HttpResponse, HttpServer};
use tracing_actix_web::TracingLogger;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

use crate::config::Config;
use crate::db::connection::create_pool;
use crate::grpc::GrpcState;
use crate::middleware::auth::InternalAuth;
use crate::middleware::security_headers::SecurityHeaders;
use crate::routes::actions::AppState;
use crate::solana::connection::SolanaRpc;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // ── Load .env files ──────────────────────────────────────────────────
    let _ = dotenvy::from_filename(".env");
    let _ = dotenvy::from_filename("../../.env"); // monorepo root fallback

    // ── Tracing / logging ────────────────────────────────────────────────
    // Use JSON output when LOG_FORMAT=json (default in production),
    // pretty human-readable output otherwise.
    let log_format = std::env::var("LOG_FORMAT").unwrap_or_default();
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let registry = tracing_subscriber::registry().with(filter);
    if log_format == "json" {
        registry
            .with(tracing_subscriber::fmt::layer().json())
            .init();
    } else {
        registry
            .with(tracing_subscriber::fmt::layer())
            .init();
    }

    // ── Config ───────────────────────────────────────────────────────────
    let cfg = Config::from_env();
    cfg.validate();

    tracing::info!(
        port = cfg.port,
        grpc_port = cfg.grpc_port,
        network = %cfg.solana_network,
        "Starting oprai-solana-service-rs"
    );

    // ── Database pool + schema migration ────────────────────────────────
    let pool = create_pool(&cfg.database_url);
    tracing::info!("Database connection pool created");

    db::run_migrations(&pool).await
        .expect("Failed to run solana_schema migrations");

    // ── Solana RPC ───────────────────────────────────────────────────────
    let rpc = if !cfg.solana_rpc_fallback.is_empty() && cfg.solana_rpc_fallback != cfg.solana_rpc {
        let fallbacks: Vec<String> = cfg.solana_rpc_fallback.split(',').map(|s| s.trim().to_string()).collect();
        let rpc = SolanaRpc::new_multi(&cfg.solana_rpc, fallbacks);
        tracing::info!(
            primary = %cfg.solana_rpc,
            fallbacks = %cfg.solana_rpc_fallback,
            "Solana Multi-RPC client initialized"
        );
        rpc
    } else {
        let rpc = SolanaRpc::new(&cfg.solana_rpc);
        tracing::info!(endpoint = %cfg.solana_rpc, "Solana RPC client initialized");
        rpc
    };

    // ── HTTP client (reused for Jupiter, Marinade, etc.) ─────────────────
    let http_client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;

    // ── Shared state for Actix-Web ───────────────────────────────────────
    let app_state = web::Data::new(AppState {
        pool: pool.clone(),
        rpc: rpc.clone(),
        http: http_client.clone(),
        jupiter_api_key: cfg.jupiter_api_key.clone(),
        helius_api_key: cfg.helius_api_key.clone(),
        relay_fee_recipient: cfg.relay_fee_recipient.clone(),
        relay_api_key: cfg.relay_api_key.clone(),
    });

    let internal_key = cfg.internal_api_key.clone();

    // ── gRPC server (background task) ────────────────────────────────────
    let grpc_port = cfg.grpc_port;
    let grpc_bind_host = cfg.grpc_bind_host.clone();
    let grpc_state = GrpcState {
        pool: pool.clone(),
        rpc: Arc::new(rpc.clone()),
        http: http_client.clone(),
        jupiter_api_key: cfg.jupiter_api_key.clone(),
        helius_api_key: cfg.helius_api_key.clone(),
        relay_fee_recipient: cfg.relay_fee_recipient.clone(),
        relay_api_key: cfg.relay_api_key.clone(),
    };

    tokio::spawn(async move {
        if let Err(e) = start_grpc_server(&grpc_bind_host, grpc_port, grpc_state).await {
            tracing::error!(error = %e, "gRPC server failed");
        }
    });

    // ── Actix-Web HTTP server ────────────────────────────────────────────
    let http_port = cfg.port;

    let bind_addr = format!("{}:{}", cfg.bind_host, http_port);

    // 10 req/sec per IP with 50 request burst (SEC-021)
    let governor_conf = GovernorConfigBuilder::default()
        .per_second(10)
        .burst_size(50)
        .finish()
        .unwrap();

    HttpServer::new(move || {
        let auth = InternalAuth::new(internal_key.clone());

        // Return JSON errors when the JSON extractor fails (instead of actix's plain-text default).
        let json_cfg = web::JsonConfig::default()
            .error_handler(|err, _req| {
                let msg = err.to_string();
                let response = HttpResponse::BadRequest()
                    .json(serde_json::json!({ "error": msg }));
                actix_web::error::InternalError::from_response(err, response).into()
            });

        App::new()
            .wrap(Governor::new(&governor_conf))
            .wrap(TracingLogger::default())
            .wrap(SecurityHeaders)
            .app_data(app_state.clone())
            .app_data(json_cfg)
            // Health check is unauthenticated.
            .service(routes::health::health_check)
            // Authenticated routes under /actions.
            .service(
                web::scope("/actions")
                    .wrap(InternalAuth::new(internal_key.clone()))
                    .service(routes::actions::post_quote)
                    .service(routes::actions::post_cross_chain_quote)
                    .service(routes::actions::post_build)
                    .service(routes::actions::post_simulate)
                    .service(routes::actions::post_advanced_simulate)
                    .service(routes::actions::get_limit_orders)
                    .service(routes::actions::get_dca_orders)
                    .service(routes::actions::get_supported_chains)
                    .service(routes::actions::get_chain_tokens)
                    .service(routes::actions::get_action_by_id)
                    .service(routes::actions::post_relay_execute_permits)
                    .service(routes::actions::get_relay_intent_status)
                    .service(routes::actions::post_relay_index_transaction)
                    .service(routes::actions::post_relay_single_transaction)
                    .service(routes::actions::post_relay_deposit_address_reindex)
                    .service(routes::actions::get_relay_app_fee_balances)
                    .service(routes::actions::post_relay_claim_app_fees)
                    .service(routes::actions::post_relay_fast_fill)
                    .service(routes::actions::get_relay_swap_sources)
                    .service(routes::actions::post_relay_execute),
            )
            // Authenticated routes under /protocols.
            .service(
                web::scope("/protocols")
                    .wrap(InternalAuth::new(internal_key.clone()))
                    .service(routes::protocols::list_protocols)
                    .service(routes::protocols::get_protocol_stats)
                    .service(routes::protocols::list_tokens)
                    .service(routes::protocols::get_token_by_symbol),
            )
            // Authenticated routes under /tokens (alias).
            .service(
                web::scope("/tokens")
                    .wrap(InternalAuth::new(internal_key.clone()))
                    .service(routes::protocols::list_tokens_alias)
                    .service(routes::protocols::get_token_by_symbol_alias),
            )
            // Authenticated routes under /transactions.
            .service(
                web::scope("/transactions")
                    .wrap(auth)
                    .service(routes::actions::list_transactions)
                    .service(routes::actions::create_transaction)
                    .service(routes::actions::get_transaction_by_id)
                    .service(routes::actions::patch_transaction_status),
            )
    })
    .bind(&bind_addr)?
    .run()
    .await?;

    Ok(())
}

/// Start the Tonic gRPC server on the given port.
async fn start_grpc_server(
    bind_host: &str,
    port: u16,
    state: GrpcState,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use crate::grpc::health::pb::health_service_server::HealthServiceServer;
    use crate::grpc::health::HealthServiceImpl;
    use std::time::Duration;

    let addr = format!("{bind_host}:{port}").parse()?;
    tracing::info!(%addr, "gRPC server listening");

    let health_service = HealthServiceImpl {
        state: state.clone(),
        started_at: std::time::Instant::now(),
    };

    // Note: The SolanaActionService, SolanaQuoteService, SolanaProtocolService
    // are hand-defined traits (no .proto yet). They are available for future
    // proto integration. For now, only the HealthService from the compiled
    // proto is served over gRPC. The action/quote/protocol gRPC endpoints
    // can be added once a solana.proto is committed to the repository.

    tonic::transport::Server::builder()
        // HTTP/2 keepalive settings to prevent "too_many_pings" errors
        .http2_keepalive_interval(Some(Duration::from_secs(30)))
        .http2_keepalive_timeout(Some(Duration::from_secs(10)))
        .add_service(HealthServiceServer::new(health_service))
        .serve(addr)
        .await?;

    Ok(())
}

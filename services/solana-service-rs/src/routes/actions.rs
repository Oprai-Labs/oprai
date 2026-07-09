use actix_web::{get, patch, post, web, HttpMessage, HttpRequest, HttpResponse};
use base64::Engine as _;
use chrono::Utc;
use diesel::prelude::*;
use diesel_async::RunQueryDsl;
use serde::Deserialize;
use solana_sdk::signature::Signer;
use solana_sdk::transaction::VersionedTransaction;
use uuid::Uuid;

use crate::db::connection::DbPool;
use crate::db::models::{NewTransaction, NewTransactionEvent, Transaction as TxModel};
use crate::db::schema::transactions;
use crate::db::tx_events;
use crate::error::AppError;
use crate::middleware::auth::UserWallet;
use crate::services::builder::{self, BuildRequest};
use crate::services::{dca, jupiter_perp, limit_order, simulation};
use crate::services::relay::{self, CrossChainSwapParams, RelayExecutePermitsRequest, RelayIndexTransactionRequest, RelaySingleTransactionRequest, RelayDepositAddressReindexRequest, RelayClaimAppFeesRequest, RelayFastFillRequest, RelayExecuteRequest};
use crate::services::mint_security::SharedMintSecurityCache;
use crate::services::spending_client::SpendingClient;
use crate::services::swap::{self, QuoteRequest, MAX_SLIPPAGE_BPS};
use crate::solana::connection::SolanaRpc;

// ──────────────────────────────────────────────────────────────────────────────
// Shared extractors
// ──────────────────────────────────────────────────────────────────────────────

/// Application state shared via Actix-Web Data.
pub struct AppState {
    pub pool: DbPool,
    pub rpc: SolanaRpc,
    pub http: reqwest::Client,
    pub jupiter_api_key: Option<String>,
    pub helius_api_key: Option<String>,
    pub relay_fee_recipient: Option<String>,
    pub relay_api_key: Option<String>,
    /// 10-minute TTL cache of registry/Jupiter mint lookups so we don't pay a
    /// round-trip on every swap quote. See [`mint_security`].
    pub mint_security: SharedMintSecurityCache,
    /// Internal HTTP client to auth-service's `/internal/spending/*`. The
    /// authoritative cap-enforcement happens via this client; the frontend
    /// equivalent is informational only.
    pub spending: SpendingClient,
}

fn wallet_from_req(req: &HttpRequest) -> Result<String, AppError> {
    req.extensions()
        .get::<UserWallet>()
        .map(|w| w.0.clone())
        .ok_or_else(|| AppError::Unauthorized("Missing wallet".into()))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/quote
// ──────────────────────────────────────────────────────────────────────────────

/// Get a swap quote from Jupiter.
#[post("/quote")]
pub async fn post_quote(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<QuoteRequest>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    let slippage_bps = body.slippage_bps.unwrap_or(50);
    if slippage_bps > MAX_SLIPPAGE_BPS {
        return Err(AppError::InvalidParams(format!(
            "slippage_bps {} exceeds maximum allowed value of {} (30%)",
            slippage_bps, MAX_SLIPPAGE_BPS
        )));
    }

    // Mint provenance check — refuse to quote against a mint we cannot trace
    // back to either the compile-time registry or Jupiter's live token list.
    // This is the primary defence against vanity-prefix grinding (an attacker
    // produces an address starting with `J1toso1u…` that the LLM mistakes for
    // JitoSOL). See `services::mint_security`.
    let input_provenance = crate::services::mint_security::require_known_mint(
        &state.mint_security,
        &state.http,
        &body.input_mint,
    )
    .await?;
    let output_provenance = crate::services::mint_security::require_known_mint(
        &state.mint_security,
        &state.http,
        &body.output_mint,
    )
    .await?;

    let params = swap::SwapParams {
        input_mint: body.input_mint.clone(),
        output_mint: body.output_mint.clone(),
        amount: body.amount.clone(),
        slippage_bps: Some(slippage_bps),
        only_direct_routes: body.only_direct_routes,
        swap_mode: body.swap_mode.clone(),
        priority_fee: None,
        restrict_intermediate_tokens: body.restrict_intermediate_tokens,
    };

    let quote = swap::get_swap_quote(&state.http, state.jupiter_api_key.as_deref(), &params).await
        .map_err(|e| {
            tracing::error!(error = %e, input_mint = %body.input_mint, output_mint = %body.output_mint, "Failed to get swap quote");
            e
        })?;

    // Server-side spending-cap enforcement. The frontend has its own check
    // for UX, but this is the line a malicious client cannot bypass. We use
    // the Jupiter quote (atomic in/out amounts) so the USD estimate is an
    // accurate post-routing number rather than a pre-routing guess.
    let est_usd = crate::services::spending_client::estimate_swap_usd(
        &state.http,
        &quote.input_mint,
        &quote.output_mint,
        &quote.in_amount,
        &quote.out_amount,
    )
    .await;
    crate::services::spending_client::enforce_spending_cap(&state.spending, &wallet, est_usd)
        .await?;

    tracing::info!(
        action = "swap_quote",
        wallet = %wallet,
        input_mint = %body.input_mint,
        output_mint = %body.output_mint,
        amount = %body.amount,
        est_usd = %est_usd,
        input_trust = ?std::mem::discriminant(&input_provenance),
        output_trust = ?std::mem::discriminant(&output_provenance),
        "Generated swap quote successfully"
    );

    let warn_user = input_provenance.requires_user_warning()
        || output_provenance.requires_user_warning();

    Ok(HttpResponse::Ok().json(serde_json::json!({
        "quote": quote,
        "mintProvenance": {
            "input": input_provenance,
            "output": output_provenance,
            "warnUser": warn_user,
        },
        "estUsd": est_usd,
    })))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/cross-chain-quote
// ──────────────────────────────────────────────────────────────────────────────

/// Get a cross-chain swap quote from Relay.
#[post("/cross-chain-quote")]
pub async fn post_cross_chain_quote(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<CrossChainSwapParams>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    relay::validate_cross_chain_params(&body)?;

    let quote = relay::get_cross_chain_quote(&state.http, &body, &wallet, state.relay_fee_recipient.as_deref()).await
        .map_err(|e| {
            tracing::error!(error = %e, user_wallet = %wallet, origin = %body.origin_chain_id, dest = %body.destination_chain_id, "Failed to get cross-chain quote");
            e
        })?;

    tracing::info!(
        action = "cross_chain_quote",
        user_wallet = %wallet,
        origin_chain = %body.origin_chain_id,
        destination_chain = %body.destination_chain_id,
        "Generated cross-chain quote successfully"
    );

    Ok(HttpResponse::Ok().json(serde_json::json!({ "quote": quote })))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/chains
// ──────────────────────────────────────────────────────────────────────────────

/// Get supported chains for cross-chain swaps.
/// Optional query param `includeChains`: comma-separated chain ID filter.
#[derive(Debug, Deserialize)]
pub struct GetChainsQuery {
    pub include_chains: Option<String>,
}

#[get("/chains")]
pub async fn get_supported_chains(
    _req: HttpRequest,
    state: web::Data<AppState>,
    query: web::Query<GetChainsQuery>,
) -> Result<HttpResponse, AppError> {
    let chains = relay::get_supported_chains(&state.http, query.include_chains.as_deref()).await?;
    Ok(HttpResponse::Ok().json(serde_json::json!({ "chains": chains })))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/chains/{chainId}/tokens
// ──────────────────────────────────────────────────────────────────────────────

/// Get supported tokens for a specific chain.
#[get("/chains/{chainId}/tokens")]
pub async fn get_chain_tokens(
    _req: HttpRequest,
    state: web::Data<AppState>,
    path: web::Path<u64>,
) -> Result<HttpResponse, AppError> {
    let chain_id = path.into_inner();
    let tokens = relay::get_chain_tokens(&state.http, chain_id).await?;
    Ok(HttpResponse::Ok().json(serde_json::json!({ "tokens": tokens })))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/build
// ──────────────────────────────────────────────────────────────────────────────

/// Build a transaction (transfer, swap, stake, etc.).
#[post("/build")]
pub async fn post_build(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<BuildRequest>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    // Validate action type.
    builder::validate_action(&body.action_type, &body.params)?;

    let user_pubkey: solana_sdk::pubkey::Pubkey = wallet
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid wallet address".into()))?;

    let result = builder::build_action(
        &state.http,
        &state.rpc,
        state.jupiter_api_key.as_deref(),
        state.helius_api_key.as_deref(),
        state.relay_fee_recipient.as_deref(),
        state.relay_api_key.as_deref(),
        &user_pubkey,
        &body.action_type,
        body.params.clone(),
    )
    .await
    .map_err(|e| {
        tracing::error!(error = %e, user_wallet = %wallet, action_type = %body.action_type, "Failed to build transaction");
        e
    })?;

    tracing::info!(
        action = "build_transaction",
        action_type = %body.action_type,
        user_wallet = %wallet,
        "Built transaction successfully"
    );

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/perp-execute
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PerpExecuteBody {
    /// Jupiter action tag: "increase-position" | "decrease-position".
    pub action: String,
    /// The user-signed transaction, base64-encoded.
    pub serialized_tx_base64: String,
}

/// Hand a user-signed Jupiter Perps transaction to Jupiter's execute endpoint,
/// which adds the keeper signatures and submits it. Returns the on-chain txid.
/// Perp txs cannot be submitted via plain RPC — they are multi-signer and only
/// Jupiter's backend holds the remaining keys.
#[post("/perp-execute")]
pub async fn post_perp_execute(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<PerpExecuteBody>,
) -> Result<HttpResponse, AppError> {
    // Require an authenticated wallet (gateway injects it) so this can't be
    // used as an open relay to Jupiter.
    let _wallet = wallet_from_req(&req)?;

    let action = body.action.as_str();
    if action != "increase-position" && action != "decrease-position" {
        return Err(AppError::InvalidParams(
            "action must be 'increase-position' or 'decrease-position'".into(),
        ));
    }

    let result = jupiter_perp::execute_perp_transaction(
        &state.http,
        state.jupiter_api_key.as_deref(),
        action,
        &body.serialized_tx_base64,
    )
    .await?;

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/vanity-mint
// ──────────────────────────────────────────────────────────────────────────────

/// Hand the client a mint keypair for a pump.fun launch whose address ends in
/// the `pump` vanity suffix. Pops a pre-ground keypair from the background pool
/// for an instant response; if the pool is cold/empty it returns a plain random
/// keypair (`vanity: false`) so a launch never blocks.
///
/// Returning the secret is safe: a pump.fun mint keypair is a throwaway that
/// controls nothing after `create` (mint authority is a program PDA), and it is
/// the same secret the client would otherwise have generated locally.
#[post("/vanity-mint")]
pub async fn post_vanity_mint(
    req: HttpRequest,
    _state: web::Data<AppState>,
) -> Result<HttpResponse, AppError> {
    // Require an authenticated wallet, matching every other /actions endpoint.
    let _wallet = wallet_from_req(&req)?;

    let (keypair, vanity) = match crate::services::vanity::take_vanity_mint() {
        Some(kp) => (kp, true),
        None => (solana_sdk::signature::Keypair::new(), false),
    };
    // 64-byte [secret32 || public32] layout — identical to web3.js
    // Keypair.fromSecretKey, so the client can reconstruct it directly.
    let secret_key = keypair.to_bytes().to_vec();
    Ok(HttpResponse::Ok().json(serde_json::json!({
        "publicKey": keypair.pubkey().to_string(),
        "secretKey": secret_key,
        "vanity": vanity,
    })))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/limit-orders
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, serde::Deserialize)]
pub struct OrderStatusQuery {
    #[serde(default = "default_order_status")]
    pub status: String,
}

fn default_order_status() -> String {
    "open".to_string()
}

/// List open (or historical) limit orders for the authenticated wallet.
#[get("/limit-orders")]
pub async fn get_limit_orders(
    req: HttpRequest,
    state: web::Data<AppState>,
    query: web::Query<OrderStatusQuery>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let orders =
        limit_order::get_limit_orders(&state.http, state.jupiter_api_key.as_deref(), &wallet, &query.status).await?;
    Ok(HttpResponse::Ok().json(orders))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/dca-orders
// ──────────────────────────────────────────────────────────────────────────────

/// List active (or historical) DCA orders for the authenticated wallet.
#[get("/dca-orders")]
pub async fn get_dca_orders(
    req: HttpRequest,
    state: web::Data<AppState>,
    query: web::Query<OrderStatusQuery>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let orders = dca::get_dca_orders(&state.http, state.jupiter_api_key.as_deref(), &wallet, &query.status).await?;
    Ok(HttpResponse::Ok().json(orders))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/{id}
// ──────────────────────────────────────────────────────────────────────────────

/// Get a transaction by ID.
#[get("/{id}")]
pub async fn get_action_by_id(
    req: HttpRequest,
    state: web::Data<AppState>,
    path: web::Path<String>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let tx_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid transaction ID format".into()))?;

    let wallet_uuid: Uuid = wallet
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid wallet UUID".into()))?;

    let mut conn = state
        .pool
        .get()
        .await
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    let tx: Option<TxModel> = transactions::table
        .filter(transactions::id.eq(tx_id))
        .filter(transactions::user_id.eq(wallet_uuid))
        .first(&mut conn)
        .await
        .optional()
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    match tx {
        Some(t) => Ok(HttpResponse::Ok().json(serde_json::json!({ "transaction": t }))),
        None => Err(AppError::NotFound("Transaction not found".into())),
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /transactions
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CreateTransactionBody {
    pub action: String,
    #[serde(default)]
    pub tx_hash: Option<String>,
    #[serde(default)]
    pub protocol: Option<String>,
    #[serde(default)]
    pub parameters: Option<serde_json::Value>,
    #[serde(default)]
    pub chain: Option<String>,
    #[serde(default)]
    pub chat_session_id: Option<String>,
    #[serde(default)]
    pub chat_message_id: Option<String>,
    /// USD value at quote time. Frontend forwards the `estUsd` it received
    /// from `/actions/quote`. The handler atomically increments the wallet's
    /// daily-spending counter via auth-service so the cap is enforced on
    /// actually-broadcast transactions, not on quotes that the user backed
    /// out of.
    #[serde(default)]
    pub est_usd: Option<f64>,
}

#[post("")]
pub async fn create_transaction(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<CreateTransactionBody>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let wallet_uuid: Uuid = wallet
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid wallet UUID".into()))?;

    let mut new_tx = NewTransaction::new(
        wallet_uuid,
        wallet.clone(),
        &body.action,
        body.parameters.clone().unwrap_or(serde_json::json!({})),
    );
    new_tx.tx_hash = body.tx_hash.clone();
    new_tx.protocol = body.protocol.clone();
    new_tx.chain = body.chain.clone().unwrap_or_else(|| "solana".to_string());
    new_tx.chat_session_id = body
        .chat_session_id
        .as_ref()
        .and_then(|s| s.parse().ok());
    new_tx.chat_message_id = body
        .chat_message_id
        .as_ref()
        .and_then(|s| s.parse().ok());

    let mut conn = state
        .pool
        .get()
        .await
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    let inserted: TxModel = diesel::insert_into(transactions::table)
        .values(&new_tx)
        .get_result(&mut conn)
        .await
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    // Log creation event (fire-and-forget).
    tx_events::log_event(
        &state.pool,
        NewTransactionEvent::status_change(inserted.id, "created", None, "pending"),
    )
    .await;

    // Server-side spending counter — increments only on the *create* path
    // (i.e. the moment the frontend tells us "I broadcast this"). The
    // pre-quote check at /actions/quote already gated the upper bound; this
    // call is the post-broadcast accounting that powers the daily cap.
    if let Some(amount_usd) = body.est_usd {
        if amount_usd > 0.0 {
            if let Err(e) = state.spending.commit(&wallet, amount_usd).await {
                tracing::warn!(
                    error = %e, wallet = %wallet, est_usd = %amount_usd,
                    "spending commit failed (cap counter not updated)"
                );
            }
        }
    }

    Ok(HttpResponse::Ok().json(serde_json::json!({ "transaction": inserted })))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /transactions
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct ListTransactionsQuery {
    #[serde(default = "default_limit")]
    pub limit: i64,
    #[serde(default)]
    pub offset: i64,
}

fn default_limit() -> i64 {
    50
}

#[get("")]
pub async fn list_transactions(
    req: HttpRequest,
    state: web::Data<AppState>,
    query: web::Query<ListTransactionsQuery>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let wallet_uuid: Uuid = wallet
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid wallet UUID".into()))?;

    let limit = query.limit.min(100).max(1);

    let mut conn = state
        .pool
        .get()
        .await
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    let txs: Vec<TxModel> = transactions::table
        .filter(transactions::user_id.eq(wallet_uuid))
        .order(transactions::created_at.desc())
        .limit(limit)
        .offset(query.offset)
        .load(&mut conn)
        .await
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    Ok(HttpResponse::Ok().json(serde_json::json!({ "transactions": txs })))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /transactions/{id}
// ──────────────────────────────────────────────────────────────────────────────

#[get("/{id}")]
pub async fn get_transaction_by_id(
    req: HttpRequest,
    state: web::Data<AppState>,
    path: web::Path<String>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let tx_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid transaction ID format".into()))?;

    let wallet_uuid: Uuid = wallet
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid wallet UUID".into()))?;

    let mut conn = state
        .pool
        .get()
        .await
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    let tx: Option<TxModel> = transactions::table
        .filter(transactions::id.eq(tx_id))
        .filter(transactions::user_id.eq(wallet_uuid))
        .first(&mut conn)
        .await
        .optional()
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    match tx {
        Some(t) => Ok(HttpResponse::Ok().json(serde_json::json!({ "transaction": t }))),
        None => Err(AppError::NotFound("Transaction not found".into())),
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// PATCH /transactions/{id}/status
// ──────────────────────────────────────────────────────────────────────────────
// Called by the frontend after submitting or confirming a transaction
// to keep the DB in sync with the on-chain state.

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateTransactionStatusBody {
    /// New status: "submitted" | "confirmed" | "failed" | "cancelled"
    pub status: String,
    #[serde(default)]
    pub tx_hash: Option<String>,
    #[serde(default)]
    pub actual_fee: Option<String>,
    #[serde(default)]
    pub error_message: Option<String>,
    // est_usd intentionally not on this path: the daily spending counter
    // is committed in POST /transactions (the actual broadcast point), so
    // the PATCH path doesn't need to know about it. Status changes here
    // include retries that must not double-count.
}

#[patch("/{id}/status")]
pub async fn patch_transaction_status(
    req: HttpRequest,
    state: web::Data<AppState>,
    path: web::Path<String>,
    body: web::Json<UpdateTransactionStatusBody>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let tx_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid transaction ID".into()))?;

    let wallet_uuid: Uuid = wallet
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid wallet UUID".into()))?;

    let allowed = ["submitted", "confirmed", "failed", "cancelled"];
    if !allowed.contains(&body.status.as_str()) {
        return Err(AppError::InvalidParams(format!(
            "Invalid status '{}'. Allowed: submitted, confirmed, failed, cancelled",
            body.status
        )));
    }

    let mut conn = state
        .pool
        .get()
        .await
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    // Read current status (also verifies ownership).
    let current_tx: Option<TxModel> = transactions::table
        .filter(transactions::id.eq(tx_id))
        .filter(transactions::user_id.eq(wallet_uuid))
        .first(&mut conn)
        .await
        .optional()
        .map_err(|e| AppError::DatabaseError(e.to_string()))?;

    let current_tx = match current_tx {
        Some(t) => t,
        None => return Err(AppError::NotFound("Transaction not found".into())),
    };
    let from_status = current_tx.status.clone();

    let now = Utc::now();

    match body.status.as_str() {
        "submitted" => {
            diesel::update(transactions::table)
                .filter(transactions::id.eq(tx_id))
                .set((
                    transactions::status.eq(&body.status),
                    transactions::tx_hash.eq(body.tx_hash.as_deref()),
                    transactions::submitted_at.eq(Some(now)),
                    transactions::updated_at.eq(now),
                ))
                .execute(&mut conn)
                .await
                .map_err(|e| AppError::DatabaseError(e.to_string()))?;
            // Spending counter is committed in POST /transactions (the actual
            // broadcast point), not here. PATCH submitted is also used for
            // retries; double-incrementing would over-report the daily total.
        }
        "confirmed" => {
            diesel::update(transactions::table)
                .filter(transactions::id.eq(tx_id))
                .set((
                    transactions::status.eq(&body.status),
                    transactions::tx_hash.eq(body.tx_hash.as_deref()),
                    transactions::actual_fee.eq(body.actual_fee.as_deref()),
                    transactions::confirmed_at.eq(Some(now)),
                    transactions::updated_at.eq(now),
                ))
                .execute(&mut conn)
                .await
                .map_err(|e| AppError::DatabaseError(e.to_string()))?;
        }
        "failed" | "cancelled" => {
            diesel::update(transactions::table)
                .filter(transactions::id.eq(tx_id))
                .set((
                    transactions::status.eq(&body.status),
                    transactions::error_message.eq(body.error_message.as_deref()),
                    transactions::updated_at.eq(now),
                ))
                .execute(&mut conn)
                .await
                .map_err(|e| AppError::DatabaseError(e.to_string()))?;
        }
        _ => unreachable!(),
    }

    // Log state-transition event (fire-and-forget).
    {
        let mut evt = NewTransactionEvent::status_change(
            tx_id,
            &body.status,
            Some(&from_status),
            &body.status,
        );
        evt.tx_hash = body.tx_hash.clone();
        evt.error_message = body.error_message.clone();
        if body.error_message.is_some() {
            evt.error_category = Some("unknown".to_string());
        }
        tx_events::log_event(&state.pool, evt).await;
    }

    tracing::info!(
        action = "update_transaction_status",
        transaction_id = %tx_id,
        user_wallet = %wallet,
        from_status = %from_status,
        new_status = %body.status,
        "Transaction status updated"
    );

    Ok(HttpResponse::Ok().json(serde_json::json!({
        "id": tx_id,
        "status": body.status,
        "updatedAt": now,
    })))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/simulate
// ──────────────────────────────────────────────────────────────────────────────
// Simulates a base64-encoded transaction via RPC (no broadcast).
// Returns: success, logs, units consumed, error details.

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SimulateRequest {
    /// Base64-encoded serialized transaction.
    pub transaction: String,
}

#[derive(Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SimulateResponse {
    pub success: bool,
    pub units_consumed: Option<u64>,
    pub logs: Vec<String>,
    pub error: Option<serde_json::Value>,
    pub error_message: Option<String>,
}

#[post("/simulate")]
pub async fn post_simulate(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<SimulateRequest>,
) -> Result<HttpResponse, AppError> {
    let _wallet = wallet_from_req(&req)?;

    let tx_bytes = base64::engine::general_purpose::STANDARD
        .decode(&body.transaction)
        .map_err(|_| AppError::InvalidParams("Invalid base64 transaction".into()))?;

    let versioned_tx: VersionedTransaction = bincode::deserialize(&tx_bytes)
        .map_err(|e| AppError::InvalidParams(format!("Cannot deserialize transaction: {e}")))?;

    // The shared `state.rpc.client()` is the SYNC `solana_client::rpc_client::
    // RpcClient`. Calling its `simulate_transaction_with_config` from an async
    // handler dispatches through `tokio::task::block_in_place`, which panics
    // ("can call blocking only when running on the multi-threaded runtime")
    // because actix-web spawns one current-thread runtime per worker. We
    // construct a fresh nonblocking client from the same endpoint here so the
    // call stays cooperative — same behaviour as `meteora.rs` /
    // `build_vtx_b64` and the simulation step in `helius.rs`.
    let async_rpc = solana_rpc_client::nonblocking::rpc_client::RpcClient::new_with_commitment(
        state.rpc.endpoint().to_string(),
        solana_sdk::commitment_config::CommitmentConfig::confirmed(),
    );
    // `VersionedTransaction` MUST be sent as base64; the RPC server rejects the
    // default base58 encoding with -32602 ("base64 encoded VersionedTransaction").
    let sim_result = async_rpc
        .simulate_transaction_with_config(
            &versioned_tx,
            solana_client::rpc_config::RpcSimulateTransactionConfig {
                sig_verify: false,
                replace_recent_blockhash: true,
                commitment: Some(solana_sdk::commitment_config::CommitmentConfig::confirmed()),
                encoding: Some(solana_transaction_status::UiTransactionEncoding::Base64),
                accounts: None,
                min_context_slot: None,
                inner_instructions: false,
            },
        )
        .await
        .map_err(|e| {
            // Use Debug instead of Display — the JSON-RPC inner error message
            // (which carries the actual reason like "too large: 1450 bytes")
            // is truncated by Display but preserved by Debug.
            let tx_size = tx_bytes.len();
            AppError::SolanaRpcError(format!("Simulation RPC error (tx_size={tx_size}B): {e:?}"))
        })?;

    let value = sim_result.value;
    let success = value.err.is_none();
    let logs = value.logs.unwrap_or_default();
    let error_message = value
        .err
        .as_ref()
        .map(|e| format!("{e:?}"));

    Ok(HttpResponse::Ok().json(SimulateResponse {
        success,
        units_consumed: value.units_consumed,
        logs,
        error: value.err.map(|e| serde_json::to_value(e).unwrap_or(serde_json::Value::Null)),
        error_message,
    }))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/simulate-advanced
// ──────────────────────────────────────────────────────────────────────────────

/// Advanced simulation with balance change analysis, risk assessment, and price impact
#[derive(Debug, Deserialize)]
pub struct AdvancedSimulateRequest {
    #[allow(dead_code)]
    pub simulation_type: simulation::SimulationType,
    #[allow(dead_code)]
    pub user_wallet: String,
    #[allow(dead_code)]
    pub skip_token_security: Option<bool>,
    #[allow(dead_code)]
    pub skip_liquidation_check: Option<bool>,
}

#[post("/simulate-advanced")]
pub async fn post_advanced_simulate(
    req: HttpRequest,
    _state: web::Data<AppState>,
    _body: web::Json<AdvancedSimulateRequest>,
) -> Result<HttpResponse, AppError> {
    let _wallet = wallet_from_req(&req)?;
    Err(AppError::Internal("Advanced simulation not yet available".into()))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/relay/intent-status
// ──────────────────────────────────────────────────────────────────────────────

/// Poll the execution status of a Relay cross-chain intent.
/// Frontend uses this to show real-time progress: waiting → depositing → pending → success/failure.
#[derive(Debug, Deserialize)]
pub struct IntentStatusQuery {
    pub request_id: String,
}

#[get("/relay/intent-status")]
pub async fn get_relay_intent_status(
    req: HttpRequest,
    state: web::Data<AppState>,
    query: web::Query<IntentStatusQuery>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    if query.request_id.trim().is_empty() {
        return Err(AppError::InvalidParams("requestId is required".into()));
    }

    let status = relay::get_relay_intent_status(&state.http, &query.request_id)
        .await
        .map_err(|e| {
            tracing::error!(
                error = %e,
                user_wallet = %wallet,
                request_id = %query.request_id,
                "Relay intent status check failed"
            );
            e
        })?;

    tracing::info!(
        action = "relay_intent_status",
        user_wallet = %wallet,
        request_id = %query.request_id,
        status = %status.status,
        "Relay intent status checked"
    );

    Ok(HttpResponse::Ok().json(status))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/relay/execute-permits
// ──────────────────────────────────────────────────────────────────────────────

/// Submit an EIP-3009 (or similar) permit signature to Relay and receive
/// updated execution steps.
///
/// Flow:
///   1. Frontend calls relay_bridge → receives quote with steps
///   2. A step with kind="eip3009" (or similar) appears — user signs it
///   3. Frontend calls this endpoint: { signature (query), kind, requestId, api (optional) }
///   4. Returns updated steps that the frontend executes via wagmi/viem
#[derive(Debug, Deserialize)]
pub struct ExecutePermitsQuery {
    pub signature: String,
}

#[post("/relay/execute-permits")]
pub async fn post_relay_execute_permits(
    req: HttpRequest,
    state: web::Data<AppState>,
    query: web::Query<ExecutePermitsQuery>,
    body: web::Json<RelayExecutePermitsRequest>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    if query.signature.trim().is_empty() {
        return Err(AppError::InvalidParams("signature query parameter is required".into()));
    }
    if body.kind.trim().is_empty() {
        return Err(AppError::InvalidParams("kind is required".into()));
    }
    if body.request_id.trim().is_empty() {
        return Err(AppError::InvalidParams("requestId is required".into()));
    }

    let result = relay::execute_relay_permits(
        &state.http,
        &query.signature,
        &body,
    )
    .await
    .map_err(|e| {
        tracing::error!(
            error = %e,
            user_wallet = %wallet,
            request_id = %body.request_id,
            "Relay execute/permits failed"
        );
        e
    })?;

    tracing::info!(
        action = "relay_execute_permits",
        user_wallet = %wallet,
        request_id = %body.request_id,
        kind = %body.kind,
        steps_returned = result.steps.len(),
        "Relay permit submitted"
    );

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/relay/transactions/index
// ──────────────────────────────────────────────────────────────────────────────

/// Notify Relay to index a submitted deposit transaction.
#[post("/relay/transactions/index")]
pub async fn post_relay_index_transaction(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<RelayIndexTransactionRequest>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    if body.chain_id.trim().is_empty() {
        return Err(AppError::InvalidParams("chainId is required".into()));
    }
    if body.tx_hash.trim().is_empty() {
        return Err(AppError::InvalidParams("txHash is required".into()));
    }

    let result = relay::index_relay_transaction(&state.http, &body)
        .await
        .map_err(|e| {
            tracing::error!(
                error = %e,
                user_wallet = %wallet,
                tx_hash = %body.tx_hash,
                "Relay index transaction failed"
            );
            e
        })?;

    tracing::info!(
        action = "relay_index_transaction",
        user_wallet = %wallet,
        chain_id = %body.chain_id,
        tx_hash = %body.tx_hash,
        "Relay transaction indexed"
    );

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/relay/transactions/single
// ──────────────────────────────────────────────────────────────────────────────

/// Notify Relay to index transfers, wraps, and unwraps for a specific request.
#[post("/relay/transactions/single")]
pub async fn post_relay_single_transaction(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<RelaySingleTransactionRequest>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    if body.request_id.trim().is_empty() {
        return Err(AppError::InvalidParams("requestId is required".into()));
    }
    if body.chain_id.trim().is_empty() {
        return Err(AppError::InvalidParams("chainId is required".into()));
    }
    if body.tx.trim().is_empty() {
        return Err(AppError::InvalidParams("tx is required".into()));
    }

    let result = relay::single_relay_transaction(&state.http, &body)
        .await
        .map_err(|e| {
            tracing::error!(
                error = %e,
                user_wallet = %wallet,
                tx = %body.tx,
                "Relay single transaction failed"
            );
            e
        })?;

    tracing::info!(
        action = "relay_single_transaction",
        user_wallet = %wallet,
        request_id = %body.request_id,
        chain_id = %body.chain_id,
        tx = %body.tx,
        "Relay single transaction indexed"
    );

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/relay/transactions/deposit-address/reindex
// ──────────────────────────────────────────────────────────────────────────────

/// Reindex a Relay deposit address — scans all currencies for activity.
#[post("/relay/transactions/deposit-address/reindex")]
pub async fn post_relay_deposit_address_reindex(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<RelayDepositAddressReindexRequest>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    if body.deposit_address.trim().is_empty() {
        return Err(AppError::InvalidParams("depositAddress is required".into()));
    }

    let result = relay::reindex_deposit_address(&state.http, &body)
        .await
        .map_err(|e| {
            tracing::error!(
                error = %e,
                user_wallet = %wallet,
                deposit_address = %body.deposit_address,
                "Relay deposit address reindex failed"
            );
            e
        })?;

    tracing::info!(
        action = "relay_deposit_address_reindex",
        user_wallet = %wallet,
        chain_id = body.chain_id,
        deposit_address = %body.deposit_address,
        triggered = result.triggered_currencies.len(),
        checked = result.checked_currencies.unwrap_or(0),
        "Relay deposit address reindexed"
    );

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/relay/app-fees/{wallet}/balances
// ──────────────────────────────────────────────────────────────────────────────

/// Fetch accumulated app fee balances for a fee-recipient wallet.
#[get("/relay/app-fees/{wallet}/balances")]
pub async fn get_relay_app_fee_balances(
    req: HttpRequest,
    state: web::Data<AppState>,
    path: web::Path<String>,
) -> Result<HttpResponse, AppError> {
    let caller_wallet = wallet_from_req(&req)?;
    let target_wallet = path.into_inner();

    let result = relay::get_app_fee_balances(&state.http, &target_wallet)
        .await
        .map_err(|e| {
            tracing::error!(
                error = %e,
                user_wallet = %caller_wallet,
                target_wallet = %target_wallet,
                "Relay app fee balances fetch failed"
            );
            e
        })?;

    tracing::info!(
        action = "relay_get_app_fee_balances",
        user_wallet = %caller_wallet,
        target_wallet = %target_wallet,
        total_usd = result.total_balance_usd.unwrap_or(0.0),
        "Relay app fee balances fetched"
    );

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/relay/app-fees/{wallet}/claim
// ──────────────────────────────────────────────────────────────────────────────

/// Initiate a Relay app-fee claim — returns signature steps for the frontend.
#[post("/relay/app-fees/{wallet}/claim")]
pub async fn post_relay_claim_app_fees(
    req: HttpRequest,
    state: web::Data<AppState>,
    path: web::Path<String>,
    body: web::Json<RelayClaimAppFeesRequest>,
) -> Result<HttpResponse, AppError> {
    let caller_wallet = wallet_from_req(&req)?;
    let wallet = path.into_inner();

    if body.currency.trim().is_empty() {
        return Err(AppError::InvalidParams("currency is required".into()));
    }
    if body.recipient.trim().is_empty() {
        return Err(AppError::InvalidParams("recipient is required".into()));
    }

    let result = relay::claim_app_fees(&state.http, &wallet, &body)
        .await
        .map_err(|e| {
            tracing::error!(
                error = %e,
                user_wallet = %caller_wallet,
                wallet = %wallet,
                currency = %body.currency,
                "Relay app fee claim failed"
            );
            e
        })?;

    tracing::info!(
        action = "relay_claim_app_fees",
        user_wallet = %caller_wallet,
        wallet = %wallet,
        currency = %body.currency,
        chain_id = body.chain_id,
        steps = result.steps.len(),
        "Relay app fee claim initiated"
    );

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/relay/fast-fill
// ──────────────────────────────────────────────────────────────────────────────

/// Queue a Relay request for fast fill (requires RELAY_API_KEY).
#[post("/relay/fast-fill")]
pub async fn post_relay_fast_fill(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<RelayFastFillRequest>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    let api_key = state
        .relay_api_key
        .as_deref()
        .ok_or_else(|| AppError::InvalidParams("RELAY_API_KEY is not configured".into()))?;

    if body.request_id.trim().is_empty() {
        return Err(AppError::InvalidParams("requestId is required".into()));
    }

    let result = relay::fast_fill(&state.http, api_key, &body)
        .await
        .map_err(|e| {
            tracing::error!(
                error = %e,
                user_wallet = %wallet,
                request_id = %body.request_id,
                "Relay fast-fill failed"
            );
            e
        })?;

    tracing::info!(
        action = "relay_fast_fill",
        user_wallet = %wallet,
        request_id = %body.request_id,
        "Relay fast-fill queued"
    );

    Ok(HttpResponse::Ok().json(result))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/relay/swap-sources
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct SwapSourcesQuery {
    pub chain_id: Option<u64>,
}

// ──────────────────────────────────────────────────────────────────────────────
// POST /actions/relay/execute
// ──────────────────────────────────────────────────────────────────────────────

/// Submit a gasless EVM transaction via Relay (requires RELAY_API_KEY).
#[post("/relay/execute")]
pub async fn post_relay_execute(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<RelayExecuteRequest>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;

    let api_key = state
        .relay_api_key
        .as_deref()
        .ok_or_else(|| AppError::InvalidParams("RELAY_API_KEY is not configured".into()))?;

    let result = relay::relay_execute(&state.http, api_key, &body)
        .await
        .map_err(|e| {
            tracing::error!(
                error = %e,
                user_wallet = %wallet,
                chain_id = body.data.chain_id,
                to = %body.data.to,
                "Relay execute failed"
            );
            e
        })?;

    tracing::info!(
        action = "relay_execute",
        user_wallet = %wallet,
        chain_id = body.data.chain_id,
        to = %body.data.to,
        request_id = ?result.request_id,
        "Relay execute submitted"
    );

    Ok(HttpResponse::Ok().json(result))
}

/// Get available Relay swap sources, optionally filtered by chain ID.
#[get("/relay/swap-sources")]
pub async fn get_relay_swap_sources(
    _req: HttpRequest,
    state: web::Data<AppState>,
    query: web::Query<SwapSourcesQuery>,
) -> Result<HttpResponse, AppError> {
    let result = relay::get_swap_sources(&state.http, query.chain_id).await?;
    Ok(HttpResponse::Ok().json(result))
}

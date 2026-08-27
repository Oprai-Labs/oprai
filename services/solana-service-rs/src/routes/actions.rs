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
use crate::services::mint_security::SharedMintSecurityCache;
use crate::services::relay::{
    self, CrossChainSwapParams, RelayClaimAppFeesRequest, RelayDepositAddressReindexRequest,
    RelayExecutePermitsRequest, RelayExecuteRequest, RelayFastFillRequest,
    RelayIndexTransactionRequest, RelaySingleTransactionRequest,
};
use crate::services::spending_client::SpendingClient;
use crate::services::swap::{self, QuoteRequest, MAX_SLIPPAGE_BPS};
use crate::services::{dca, jupiter_perp, limit_order, simulation};
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

/// The OPRAI account id the gateway resolved from the JWT (`X-User-Account`), if
/// present. It ties a wallet's economics to its account so tiers/rewards pool
/// across all the account's wallets. Absent for legacy/unauthenticated paths.
fn account_from_req(req: &HttpRequest) -> Option<String> {
    req.headers()
        .get("X-User-Account")
        .and_then(|h| h.to_str().ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// The trader's cashback tier percent, from their POOLED (fee-paying) volume
/// across all their wallets/chains. Used to split a Relay app fee at collection
/// into the cashback pool vs the profit wallet, so the pool self-funds with
/// exactly what it owes. A DB miss reads as tier 1 (the lowest cashback %).
async fn trader_cashback_pct(state: &AppState, req: &HttpRequest) -> u16 {
    let wallet = wallet_from_req(req).unwrap_or_default();
    let account = account_from_req(req);
    let volume =
        crate::db::economics::account_tier_volume_usd(&state.pool, account.as_deref(), &wallet)
            .await;
    crate::services::fees::cashback_pct_for_volume(volume)
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
    // Resolve tickers to canonical mint addresses FIRST. The LLM (and third-party
    // callers) may pass a bare symbol that isn't in our compile-time registry
    // (e.g. PYUSD, a Token-2022 mint). Without this, the provenance check below
    // rejects it as "not a valid Solana address", and Jupiter's quote API — which
    // only speaks mint addresses — would fail too. The resolver is verified-only,
    // so it can't be used to smuggle in a vanity-prefix impersonator.
    let input_mint =
        crate::services::mint_security::resolve_action_mint(&state.http, &body.input_mint).await?;
    let output_mint =
        crate::services::mint_security::resolve_action_mint(&state.http, &body.output_mint).await?;

    let input_provenance = crate::services::mint_security::require_known_mint(
        &state.mint_security,
        &state.http,
        &input_mint,
    )
    .await?;
    let output_provenance = crate::services::mint_security::require_known_mint(
        &state.mint_security,
        &state.http,
        &output_mint,
    )
    .await?;

    // Tier no longer discounts the fee — the reward model is CASHBACK (user pays
    // full commission, earns a % back by tier; see the cashback ledger). The
    // discount plumbing stays wired at 0 (a no-op) rather than being torn out.
    let fee_discount_pct: u16 = 0;

    let params = swap::SwapParams {
        // Forward the caller's venue filter so a venue-scoped preview is
        // quoted through the same DEX the action will execute on.
        dexes: body.dexes.clone(),
        input_mint: input_mint.clone(),
        output_mint: output_mint.clone(),
        amount: body.amount.clone(),
        slippage_bps: Some(slippage_bps),
        only_direct_routes: body.only_direct_routes,
        swap_mode: body.swap_mode.clone(),
        priority_fee: None,
        restrict_intermediate_tokens: body.restrict_intermediate_tokens,
        fee_discount_pct,
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

    let warn_user =
        input_provenance.requires_user_warning() || output_provenance.requires_user_warning();

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

    let cashback_pct = trader_cashback_pct(&state, &req).await;
    let quote = relay::get_cross_chain_quote(&state.http, &body, &wallet, state.relay_fee_recipient.as_deref(), cashback_pct).await
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
/// Actions whose transaction is built by the TypeScript solana-service via the
/// @kamino-finance SDKs (klend/farms). These have no REST endpoint and can't be
/// built from Rust (no Kamino SDK crate), so the Rust service — the gateway's
/// upstream — proxies them to the TS service.
fn is_ts_delegated_action(action_type: &str) -> bool {
    matches!(
        action_type,
        "kamino_stake"
            | "kamino_unstake"
            | "kamino_claim_rewards"
            // Multiply (leveraged looping) — klend-sdk leverage ixs + Jupiter swapper.
            | "kamino_multiply_open"
            | "kamino_multiply_add"
            | "kamino_multiply_withdraw"
            | "kamino_multiply_close"
            // Read-only: list Multiply pools with metrics (needs the klend SDK
            // for exact per-pair max leverage).
            | "kamino_multiply_markets"
            // Concentrated liquidity (kLiquidity CLMM strategies) — kliquidity-sdk.
            | "kamino_liquidity_deposit"
            | "kamino_liquidity_withdraw"
            | "kamino_liquidity_strategies"
            // Raydium liquidity + positions — Raydium's REST API is swap-only, so
            // these must be built with @raydium-io/raydium-sdk-v2 in the TS service.
            | "raydium_add_liquidity"
            | "raydium_remove_liquidity"
            | "raydium_open_position"
            | "raydium_increase_position"
            | "raydium_decrease_position"
            | "raydium_close_position"
            | "raydium_create_pool"
            // Read: the user's CLMM positions straight from chain via the SDK
            // (raydium.clmm.getOwnerPositionInfo), not the owner-v1 farm API.
            | "raydium_get_user_positions"
            | "raydium_get_clmm_positions"
            // Meteora DAMM v2 (cp-amm) — a SEPARATE on-chain program from DAMM
            // v1 with NFT-backed positions, and a data API that exposes no
            // positions endpoint at all. Built with @meteora-ag/cp-amm-sdk so
            // quotes, deposit ratios and account derivation come from Meteora
            // rather than from our guesses about a program layout.
            | "meteora_dammv2_get_user_positions"
            // Read-only: the SDK's own deposit quote for one pool. The data
            // API cannot answer it — a bounded pool's split follows the band,
            // not the reserves or the price.
            | "meteora_dammv2_pool_quote"
            | "meteora_dammv2_add_liquidity"
            | "meteora_dammv2_remove_liquidity"
            | "meteora_dammv2_claim_fee"
            | "meteora_dammv2_close_position"
            | "meteora_dammv2_swap"
            // Marinade — @marinade.finance/marinade-ts-sdk.
            //
            // The Rust implementation is a placeholder and always was: a
            // one-byte discriminator where an Anchor program wants an eight-byte
            // sighash, and four accounts where deposit needs eleven. Its own
            // comment said "simplified — actual Marinade SDK would have more
            // accounts". It quoted a real exchange rate in the preview, so the
            // card looked correct right up to the signature, and the chain
            // answered InstructionFallbackNotFound. The SDK build has been
            // sitting in the TS service, unreachable, the whole time.
            | "marinade_stake"
            | "marinade_unstake"
            | "marinade_delayed_unstake"
            | "marinade_claim"
            | "marinade_claim_ticket"
            // Also written, also SDK-built, and unreachable until now: the
            // mSOL/SOL liquidity pool, and converting an existing native stake
            // account straight into mSOL without waiting to deactivate.
            | "marinade_add_liquidity"
            | "marinade_remove_liquidity"
            | "marinade_deposit_stake"
            // Reads, same route: the rate + APY, and the caller's tickets.
            | "marinade_exchange_rate"
            | "marinade_list_tickets"
    )
}

/// Forward a build request to the TypeScript solana-service and return its
/// response verbatim (same /actions/build JSON contract). The TS service does
/// its own validation and emits user-safe errors, which we pass through.
async fn delegate_build_to_ts(
    http: &reqwest::Client,
    action_type: &str,
    wallet: &str,
    params: &serde_json::Value,
) -> Result<HttpResponse, AppError> {
    let base = std::env::var("SOLANA_TS_SERVICE_URL")
        .unwrap_or_else(|_| "http://localhost:3031".to_string());
    let key = std::env::var("OPRAI_INTERNAL_API_KEY").unwrap_or_default();

    let resp = http
        .post(format!("{base}/actions/build"))
        .header("X-Internal-Api-Key", key)
        .header("X-User-Wallet", wallet)
        .json(&serde_json::json!({ "type": action_type, "params": params }))
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
        .map_err(|e| {
            tracing::error!(error = %e, %action_type, "Kamino SDK service (TS) unreachable");
            AppError::Internal("Kamino staking service is temporarily unavailable".into())
        })?;

    let status = actix_web::http::StatusCode::from_u16(resp.status().as_u16())
        .unwrap_or(actix_web::http::StatusCode::BAD_GATEWAY);
    let payload = resp
        .bytes()
        .await
        .map_err(|e| AppError::Internal(format!("Kamino SDK service read failed: {e}")))?;

    Ok(HttpResponse::build(status)
        .content_type("application/json")
        .body(payload))
}

/// SOL an action would spend, for the cap. Only actions that name an amount
/// in SOL — anything else is priced elsewhere or moves no value, and guessing
/// a number here would cap the wrong thing.
fn sol_amount_spent(action_type: &str, params: &serde_json::Value) -> Option<f64> {
    let num = |k: &str| {
        params
            .get(k)
            .and_then(|v| {
                v.as_str()
                    .map(|s| s.to_string())
                    .or_else(|| v.as_f64().map(|f| f.to_string()))
            })
            .and_then(|s| s.parse::<f64>().ok())
            .filter(|n| *n > 0.0)
    };
    match action_type {
        // Buying an NFT, bidding on one, funding the account bids are paid
        // from: all denominated in SOL and all able to empty a wallet.
        "me_buy"
        | "me_buy_now"
        | "me_buy_instruction"
        | "me_buy_now_transfer_nft"
        | "tensor_buy" => num("price"),
        "me_make_offer" => num("price"),
        "me_mmm_sol_deposit_buy" => num("amount").or_else(|| num("paymentAmount")),
        _ => None,
    }
}

#[post("/build")]
pub async fn post_build(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<BuildRequest>,
) -> Result<HttpResponse, AppError> {
    // Read-only, wallet-independent EVM data query: listing Uniswap pools needs
    // no signer. Serve it BEFORE the Solana-wallet gate — an EVM-session user's
    // X-User-Wallet is a 0x address that wallet_from_req would reject, and the
    // pool list is the same for everyone anyway.
    if body.action_type == "uniswap_pools" {
        let p: crate::services::uniswap::UniswapGetPoolsParams =
            serde_json::from_value(body.params.clone()).map_err(|e| {
                AppError::InvalidParams(format!("Invalid uniswap_pools params: {e}"))
            })?;
        crate::services::uniswap::validate_uniswap_get_pools_params(&p)?;
        let resp = crate::services::uniswap::build_uniswap_get_pools(&state.http, &p).await?;
        return Ok(HttpResponse::Ok().json(resp));
    }

    // Same rationale as uniswap_pools: the pools.trade launch feed is read-only,
    // wallet-independent EVM data — serve it before the Solana-wallet gate.
    if body.action_type == "uniswap_launches" {
        let p: crate::services::uniswap::UniswapLaunchesParams =
            serde_json::from_value(body.params.clone()).map_err(|e| {
                AppError::InvalidParams(format!("Invalid uniswap_launches params: {e}"))
            })?;
        let resp = crate::services::uniswap::build_uniswap_launches(&state.http, &p).await?;
        return Ok(HttpResponse::Ok().json(resp));
    }

    let wallet = wallet_from_req(&req)?;
    let mut body = body.into_inner();

    // A swap's token args can be non-registry tickers (e.g. PYUSD). Resolve them
    // to canonical, Jupiter-verified mint addresses BEFORE validation and the
    // Jupiter build — otherwise `validate_swap_params` rejects the ticker as an
    // "Invalid output token" and the quote step never runs. Mirrors the same
    // resolution done in `post_quote`; verified-only, so no impersonator can slip
    // through. Cross-chain swaps use chain-specific token fields and are left as-is.
    if body.action_type == "swap" {
        for key in ["inputMint", "input_mint", "outputMint", "output_mint"] {
            if let Some(sym) = body.params.get(key).and_then(|v| v.as_str()) {
                let resolved =
                    crate::services::mint_security::resolve_action_mint(&state.http, sym).await?;
                body.params[key] = serde_json::Value::String(resolved);
            }
        }

        // Enforce the SAME mint-provenance gate as /quote: refuse to BUILD a swap
        // against a token that is neither in the verified registry nor known to
        // Jupiter — i.e. a vanity-prefix impersonator (e.g. an address grinding
        // "J1toso1u…" to look like JitoSOL). /quote's require_known_mint rejected
        // these, but a caller could POST straight to /build (or a prompt-injection
        // could emit `[ACTION:swap] outputMint=<fake>`), skipping /quote and
        // getting a signable swap into the fake token — opaque in the wallet UI.
        // Mints are already resolved to canonical addresses above.
        let mut checked = std::collections::HashSet::new();
        for key in ["inputMint", "input_mint", "outputMint", "output_mint"] {
            if let Some(mint) = body.params.get(key).and_then(|v| v.as_str()) {
                if checked.insert(mint.to_string()) {
                    crate::services::mint_security::require_known_mint(
                        &state.mint_security,
                        &state.http,
                        mint,
                    )
                    .await?;
                }
            }
        }
    }

    // Kamino farm staking (and, later, leverage) are built with the
    // @kamino-finance SDKs, which only the TypeScript solana-service carries.
    // The gateway's upstream is this Rust service, so we forward those actions
    // to the TS service and return its response verbatim (identical
    // /actions/build contract). Everything else builds locally below.
    if is_ts_delegated_action(&body.action_type) {
        return delegate_build_to_ts(&state.http, &body.action_type, &wallet, &body.params).await;
    }

    // "Mad Lads #3983" is how a person names an NFT; every Magic Eden builder
    // needs a mint. Resolve it here — BEFORE validation, which is what would
    // otherwise reject the request for the very field this fills in.
    let params = crate::services::magic_eden::resolve_me_action_mint(
        &state.http,
        &body.action_type,
        &wallet,
        body.params.clone(),
    )
    .await;

    // Validate action type.
    builder::validate_action(&body.action_type, &params)?;

    // Spending cap. It lived on the swap-quote path only, so an NFT purchase,
    // a bid or an escrow deposit of any size went through uncapped — the one
    // limit a user sets to bound their own mistakes did not apply to the
    // actions most likely to be one. SOL-denominated actions price off the
    // amount they name; a swap is capped at its quote, where the number is
    // exact.
    if let Some(sol) = sol_amount_spent(&body.action_type, &params) {
        let usd = crate::services::spending_client::estimate_swap_usd(
            &state.http,
            "So11111111111111111111111111111111111111112",
            "So11111111111111111111111111111111111111112",
            &((sol * 1e9) as u64).to_string(),
            &((sol * 1e9) as u64).to_string(),
        )
        .await;
        crate::services::spending_client::enforce_spending_cap(&state.spending, &wallet, usd)
            .await?;
    }

    let user_pubkey: solana_sdk::pubkey::Pubkey = wallet
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid wallet address".into()))?;

    // Reward model is cashback, not a fee discount — full commission is charged
    // and a tier % is credited to the cashback ledger afterwards. Discount = 0.
    let fee_discount_pct: u16 = 0;
    let cashback_pct = trader_cashback_pct(&state, &req).await;

    let result = builder::build_action(
        &state.http,
        &state.rpc,
        state.jupiter_api_key.as_deref(),
        state.helius_api_key.as_deref(),
        state.relay_fee_recipient.as_deref(),
        state.relay_api_key.as_deref(),
        &user_pubkey,
        &body.action_type,
        params,
        fee_discount_pct,
        cashback_pct,
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
/// What each candidate price range would actually COST to open, and whether
/// this wallet can pay it.
///
/// A CLMM position is not just the deposit. Every position creates accounts
/// that must be rent-exempt, and if the chosen range reaches into a stretch of
/// the curve no LP has used yet, it also creates that stretch's tick arrays —
/// 10 KB accounts at roughly 0.072 SOL EACH, an order of magnitude more than
/// everything else combined, and the one part that is never refunded because
/// the array outlives the position.
///
/// Nothing in the UI knew this. The card defaulted to ±20%, which for a wallet
/// holding 0.066 SOL was arithmetically impossible, and the failure surfaced as
/// "not enough balance" pointing at a deposit of 0.0036 SOL — the one number
/// that was never the problem. The user picked a healthy pool from a list we
/// showed them and could not have known why it failed.
///
/// So the cost is computed BEFORE anything is offered: the caller sends the
/// ranges it would put on screen, and each comes back priced, so it can select
/// a default the wallet can actually fund and say what the others would cost
/// in SOL rather than in protocol vocabulary.
#[post("/clmm-range-costs")]
pub async fn post_clmm_range_costs(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    use crate::services::raydium_clmm::ix::tick_array_pda;
    use crate::services::raydium_clmm::state::{tick_array_start_index, PoolStateView};
    use std::str::FromStr;

    let wallet = wallet_from_req(&req)?;
    let user_pk = solana_sdk::pubkey::Pubkey::from_str(&wallet)
        .map_err(|e| AppError::InvalidParams(format!("bad wallet: {e}")))?;

    let pool_id = body
        .get("poolId")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::InvalidParams("poolId is required".into()))?;
    let pool_pk = solana_sdk::pubkey::Pubkey::from_str(pool_id)
        .map_err(|e| AppError::InvalidParams(format!("bad poolId: {e}")))?;

    // The caller sends the ranges it is about to render, as the same
    // minPrice/maxPrice pair it would send to /actions/build. Converting them
    // here with the builder's own helpers is what keeps the price the user was
    // quoted and the price the transaction uses from ever drifting apart.
    let ranges = body
        .get("ranges")
        .and_then(|v| v.as_array())
        .ok_or_else(|| AppError::InvalidParams("ranges is required".into()))?;

    let rpc = &state.rpc;
    let pool_data = {
        let rpc_clone = rpc.clone();
        tokio::task::spawn_blocking(move || rpc_clone.client().get_account_data(&pool_pk))
            .await
            .map_err(|e| AppError::Internal(format!("spawn_blocking: {e}")))?
            .map_err(|e| AppError::ProtocolError(format!("fetch pool: {e}")))?
    };
    let pool = PoolStateView::parse(&pool_data)?;

    // Rent asked of the chain, never hardcoded — the numbers move with the
    // cluster's rent parameters and a stale constant here would quietly
    // under-quote the very cost this endpoint exists to surface.
    let (tick_array_rent, base_rent, lamports) = {
        let rpc_clone = rpc.clone();
        tokio::task::spawn_blocking(move || {
            let c = rpc_clone.client();
            let ta = c
                .get_minimum_balance_for_rent_exemption(10_240)
                .unwrap_or(72_161_280);
            // position state + NFT mint + NFT token account + metadata
            let base = c
                .get_minimum_balance_for_rent_exemption(281)
                .unwrap_or(2_616_960)
                + c.get_minimum_balance_for_rent_exemption(82)
                    .unwrap_or(1_461_600)
                + c.get_minimum_balance_for_rent_exemption(165)
                    .unwrap_or(2_039_280)
                + c.get_minimum_balance_for_rent_exemption(607)
                    .unwrap_or(5_616_720);
            let bal = c.get_balance(&user_pk).unwrap_or(0);
            (ta, base, bal)
        })
        .await
        .map_err(|e| AppError::Internal(format!("spawn_blocking: {e}")))?
    };

    let mut out = Vec::new();
    for r in ranges {
        let min_p = r.get("minPrice").and_then(|v| v.as_f64());
        let max_p = r.get("maxPrice").and_then(|v| v.as_f64());
        let (min_p, max_p) = match (min_p, max_p) {
            (Some(a), Some(b)) if a > 0.0 && b > a => (a, b),
            _ => continue,
        };

        let tick_lower = crate::services::raydium::align_tick_lower(
            crate::services::raydium::price_to_tick(min_p),
            pool.tick_spacing as i32,
        );
        let tick_upper = crate::services::raydium::align_tick_upper(
            crate::services::raydium::price_to_tick(max_p),
            pool.tick_spacing as i32,
        );
        let lo_start = tick_array_start_index(tick_lower, pool.tick_spacing);
        let hi_start = tick_array_start_index(tick_upper, pool.tick_spacing);

        let mut pdas = vec![tick_array_pda(&pool_pk, lo_start)];
        if hi_start != lo_start {
            pdas.push(tick_array_pda(&pool_pk, hi_start));
        }
        let missing = {
            let rpc_clone = rpc.clone();
            let pdas_c = pdas.clone();
            tokio::task::spawn_blocking(move || {
                match rpc_clone.client().get_multiple_accounts(&pdas_c) {
                    // A missing account is one this range would have to create.
                    Ok(accs) => accs.iter().filter(|a| a.is_none()).count(),
                    // Unknown is not the same as missing: quoting a cost we did
                    // not verify would be worse than quoting none.
                    Err(_) => usize::MAX,
                }
            })
            .await
            .map_err(|e| AppError::Internal(format!("spawn_blocking: {e}")))?
        };

        if missing == usize::MAX {
            out.push(serde_json::json!({
                "minPrice": min_p, "maxPrice": max_p, "known": false,
            }));
            continue;
        }

        let extra = tick_array_rent.saturating_mul(missing as u64);
        let total = base_rent.saturating_add(extra);
        out.push(serde_json::json!({
            "minPrice": min_p,
            "maxPrice": max_p,
            "known": true,
            "newTickArrays": missing,
            "setupLamports": total,
            "setupSol": total as f64 / 1_000_000_000.0,
            // Deliberately excludes the deposit: this is what opening COSTS
            // before a single token goes in. The caller adds the deposit.
            "affordable": lamports >= total,
        }));
    }

    Ok(HttpResponse::Ok().json(serde_json::json!({
        "walletLamports": lamports,
        "walletSol": lamports as f64 / 1_000_000_000.0,
        "tickArrayRentSol": tick_array_rent as f64 / 1_000_000_000.0,
        "ranges": out,
    })))
}

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
    let orders = limit_order::get_limit_orders(
        &state.http,
        state.jupiter_api_key.as_deref(),
        &wallet,
        &query.status,
    )
    .await?;
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
    let orders = dca::get_dca_orders(
        &state.http,
        state.jupiter_api_key.as_deref(),
        &wallet,
        &query.status,
    )
    .await?;
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

/// A sensible protocol tag when the client didn't send one, so revenue analytics
/// (`v_revenue_by_protocol`) don't collapse everything into "unknown". Swaps are
/// routed and fee'd through Jupiter regardless of the underlying DEX, so that is
/// the protocol that earned the commission. Everything else the client already
/// tags explicitly; return None and let it stay untagged rather than guess.
fn default_protocol_for_action(action: &str) -> Option<String> {
    match action.trim().to_ascii_lowercase().as_str() {
        "swap" | "buy" | "sell" => Some("jupiter".to_string()),
        _ => None,
    }
}

#[derive(Debug, Deserialize)]
pub struct CashbackPayoutBody {
    #[serde(rename = "amountUsd")]
    pub amount_usd: f64,
}

/// Pay cashback out to the caller's wallet from the treasury. INTERNAL ONLY —
/// this route is not exposed through the gateway; chat-service calls it directly
/// (X-Internal-Api-Key) after it has verified the claimable amount server-side.
/// solana-service is the only service that can sign the treasury transfer.
#[post("/cashback-payout")]
pub async fn post_cashback_payout(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<CashbackPayoutBody>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    if !(body.amount_usd >= crate::services::cashback::MIN_CLAIM_USD) {
        return Err(AppError::InvalidParams(format!(
            "Minimum claim is ${:.2}.",
            crate::services::cashback::MIN_CLAIM_USD
        )));
    }
    let signature =
        crate::services::cashback::payout_sol(&state.http, &wallet, body.amount_usd).await?;
    Ok(HttpResponse::Ok().json(serde_json::json!({ "signature": signature })))
}

#[post("")]
pub async fn create_transaction(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<CreateTransactionBody>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    // Protocol tag, defaulted for swaps so revenue analytics can attribute them.
    let resolved_protocol = body
        .protocol
        .clone()
        .filter(|p| !p.trim().is_empty())
        .or_else(|| default_protocol_for_action(&body.action));
    // Resolve the real users.id via auth-service (X-User-Wallet is the Solana
    // address, not a UUID). Kept as an internal HTTP call so solana-service
    // never reads auth_schema directly.
    let wallet_uuid: Uuid = state.spending.resolve_user_id(&wallet).await?;

    let mut new_tx = NewTransaction::new(
        wallet_uuid,
        wallet.clone(),
        &body.action,
        body.parameters.clone().unwrap_or(serde_json::json!({})),
    );
    new_tx.tx_hash = body.tx_hash.clone();
    new_tx.protocol = resolved_protocol.clone();
    new_tx.chain = body.chain.clone().unwrap_or_else(|| "solana".to_string());
    new_tx.chat_session_id = body.chat_session_id.as_ref().and_then(|s| s.parse().ok());
    new_tx.chat_message_id = body.chat_message_id.as_ref().and_then(|s| s.parse().ok());

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

    // Economics ledger (fee + volume) — pending row. The OPRAI commission is
    // recomputed server-side from the mints, never taken from the client.
    // Fire-and-forget: a ledger miss must not fail the transaction record.
    {
        let params = body
            .parameters
            .clone()
            .unwrap_or_else(|| serde_json::json!({}));
        let getstr = |keys: &[&str]| -> Option<String> {
            for k in keys {
                if let Some(v) = params.get(k) {
                    if let Some(s) = v.as_str() {
                        if !s.is_empty() {
                            return Some(s.to_string());
                        }
                    } else if v.is_number() {
                        return Some(v.to_string());
                    }
                }
            }
            None
        };
        let input_mint = getstr(&["inputMint", "input_mint", "fromMint"]);
        let output_mint = getstr(&["outputMint", "output_mint", "toMint"]);
        let input_amount = getstr(&["amount", "inputAmount", "input_amount"]);
        let output_amount = getstr(&["outputAmount", "output_amount", "outAmount"]);

        let (fee_bps, fee_mint): (i32, Option<String>) = match (&input_mint, &output_mint) {
            (Some(i), Some(o)) => (
                crate::services::fees::swap_fee_bps(i, o) as i32,
                crate::services::fees::swap_fee_mints(i, o, false)
                    .into_iter()
                    .next()
                    .map(|s| s.to_string()),
            ),
            _ => (0, None),
        };
        // Volume (notional_usd) is intentionally left NULL at create time. It is
        // recomputed from the confirmed on-chain transaction in
        // `finalize_confirmed`, never from the client's `est_usd` — the tier /
        // points / fee-discount system depends on it and must not be forgeable.
        // fee_bps is still stored now (server-computed) so the confirm step can
        // derive fee_usd from the real on-chain notional.
        crate::db::economics::record_pending(
            &state.pool,
            inserted.id,
            inserted.user_wallet.clone(),
            resolved_protocol.clone(),
            body.action.clone(),
            input_mint,
            output_mint,
            input_amount,
            output_amount,
            None, // notional_usd — set at confirm from chain
            fee_bps,
            fee_mint,
            None, // fee_usd — derived from on-chain notional at confirm
            Some("pending".to_string()),
            inserted.chain.clone(),
            account_from_req(&req),
        )
        .await;
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

    let limit = query.limit.clamp(1, 100);

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
        .filter(transactions::user_wallet.eq(&wallet))
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

    // Finalize the economics ledger row (fire-and-forget): confirmed folds into
    // the wallet + daily rollups (idempotent); failed/cancelled is kept as an
    // attempted row for funnel/conversion.
    match body.status.as_str() {
        "confirmed" => {
            // Spawned, not awaited: the confirm step reads the transaction back
            // from the chain (RPC + price lookup) to compute server-authoritative
            // volume, and that must not add seconds to the PATCH response.
            let pool = state.pool.clone();
            let http = state.http.clone();
            let sig = body.tx_hash.clone();
            let owner = wallet.clone();
            tokio::spawn(async move {
                crate::db::economics::finalize_confirmed(&pool, &http, tx_id, sig, owner).await;
            });
        }
        "failed" => crate::db::economics::finalize_other(&state.pool, tx_id, "failed").await,
        "cancelled" => crate::db::economics::finalize_other(&state.pool, tx_id, "cancelled").await,
        _ => {}
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
    let error_message = value.err.as_ref().map(|e| format!("{e:?}"));

    Ok(HttpResponse::Ok().json(SimulateResponse {
        success,
        units_consumed: value.units_consumed,
        logs,
        error: value
            .err
            .map(|e| serde_json::to_value(e).unwrap_or(serde_json::Value::Null)),
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
    Err(AppError::Internal(
        "Advanced simulation not yet available".into(),
    ))
}

// ──────────────────────────────────────────────────────────────────────────────
// GET /actions/relay/intent-status
// ──────────────────────────────────────────────────────────────────────────────

/// Poll the execution status of a Relay cross-chain intent.
/// Frontend uses this to show real-time progress: waiting → depositing → pending → success/failure.
/// `requestId`, the way a browser writes it. Without the rename this route
/// answered "missing field `request_id`" to every caller — including the only
/// caller there is.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
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
// POST /actions/relay/record — book economics for a settled EVM (Relay) swap
// ──────────────────────────────────────────────────────────────────────────────

/// The frontend calls this once a Relay EVM swap settles, so the trade feeds the
/// per-chain rewards. Everything is re-derived server-side and never trusted from
/// the client: the intent must be `success` per Relay, and the volume + token
/// symbols come from Relay's own request record. The commission is recomputed
/// here (same tiering as Solana) and the cashback booked at the account's pooled
/// tier. Idempotent on the EVM tx hash. Solana-origin swaps are booked by the
/// `/transactions` flow instead and are ignored here.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayRecordBody {
    pub request_id: String,
}

#[post("/relay/record")]
pub async fn post_relay_record(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<RelayRecordBody>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let account = account_from_req(&req);
    let request_id = body.request_id.trim().to_string();
    if request_id.is_empty() {
        return Err(AppError::InvalidParams("requestId is required".into()));
    }

    let not_recorded = |reason: &str| {
        Ok(HttpResponse::Ok().json(serde_json::json!({ "recorded": false, "reason": reason })))
    };

    // 1. Must be a settled success. The server asks Relay; the client cannot assert it.
    let status = relay::get_relay_intent_status(&state.http, &request_id).await?;
    if status.status != "success" {
        return not_recorded(&format!("intent status is '{}'", status.status));
    }

    // 2. Authoritative amounts + symbols from Relay's own request record.
    let q = relay::RelayRequestsQuery {
        id: Some(request_id.clone()),
        limit: Some(1),
        ..Default::default()
    };
    let reqs = relay::get_relay_requests(&state.http, &q).await?;
    let Some(r) = reqs.requests.into_iter().next() else {
        return not_recorded("request not found");
    };

    let notional_usd = r
        .pointer("/data/metadata/currencyIn/amountUsd")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);
    let origin_symbol = r
        .pointer("/data/metadata/currencyIn/currency/symbol")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let dest_symbol = r
        .pointer("/data/metadata/currencyOut/currency/symbol")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let origin_chain = r
        .pointer("/data/metadata/currencyIn/currency/chainId")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);

    let Some(chain_key) = relay::chain_key_for_id(origin_chain) else {
        return not_recorded("unsupported chain");
    };
    // Solana-origin economics are booked by the /transactions confirm flow.
    if chain_key == "solana" {
        return not_recorded("solana booked via /transactions");
    }
    if notional_usd <= 0.0 {
        return not_recorded("no usd notional");
    }

    let fee_bps = relay::evm_fee_bps_from_symbols(&origin_symbol, &dest_symbol);
    let fee_usd = notional_usd * (fee_bps as f64) / 10_000.0;
    // Prefer the origin-chain deposit hash; fall back to the requestId so the row
    // is still idempotent and traceable.
    // `.first()` here resolves to diesel's QueryDsl::first, which is in scope
    // and does not compile against a Vec — hence the explicit iterator.
    #[allow(clippy::iter_next_slice)]
    let tx_hash = status
        .in_tx_hashes
        .iter()
        .next()
        .cloned()
        .unwrap_or_else(|| request_id.clone());

    match crate::db::economics::record_evm_confirmed(
        &state.pool,
        wallet.clone(),
        account,
        chain_key.to_string(),
        tx_hash,
        "relay".to_string(),
        "swap".to_string(),
        Some(origin_symbol),
        Some(dest_symbol),
        notional_usd,
        fee_bps as i32,
        fee_usd,
    )
    .await
    {
        Ok(id) => {
            tracing::info!(
                action = "relay_record", user_wallet = %wallet, chain = %chain_key,
                request_id = %request_id, notional_usd, fee_usd, fee_bps,
                "EVM swap economics recorded"
            );
            Ok(HttpResponse::Ok().json(serde_json::json!({
                "recorded": true, "transactionId": id, "chain": chain_key,
                "notionalUsd": notional_usd, "feeUsd": fee_usd, "feeBps": fee_bps
            })))
        }
        Err(e) => {
            tracing::warn!(error = %e, request_id = %request_id, "relay economics record failed");
            not_recorded("record failed")
        }
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// Uniswap (Phase 1: same-chain EVM swap via the Trading API)
// ──────────────────────────────────────────────────────────────────────────────

/// POST /actions/uniswap/quote — price a same-chain EVM swap and return the
/// preview + the material the frontend needs to finish it (opaque quote, EIP-712
/// permit, Permit2 approval tx). The wallet (swapper) comes from the trusted
/// header, never the body.
#[post("/uniswap/quote")]
pub async fn post_uniswap_quote(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<crate::services::relay::CrossChainSwapParams>,
) -> Result<HttpResponse, AppError> {
    // Swapper: prefer a valid EVM address the client passes (a Solana-native
    // OPRAI session can't be the EVM swapper, but the user has a connected EVM
    // wallet). Pricing only — the built swap is signed by the user's own wallet.
    let wallet = match body.sender.as_deref().filter(|s| s.len() == 42 && s.starts_with("0x")) {
        Some(s) => s.to_string(),
        None => wallet_from_req(&req)?,
    };
    if body.origin_chain_id != body.destination_chain_id {
        return Err(AppError::InvalidParams(
            "Uniswap swaps are same-chain only — use a bridge for cross-chain.".into(),
        ));
    }
    if !crate::services::uniswap::is_uniswap_chain(body.origin_chain_id) {
        return Err(AppError::InvalidParams(format!(
            "Uniswap isn't available on chain {}.",
            body.origin_chain_id
        )));
    }
    let result = crate::services::uniswap::uniswap_quote(&state.http, &wallet, &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/uniswap/swap — turn the frontend-signed permit into the final
/// EVM transaction. The Uniswap API key stays server-side; the client only ever
/// reaches Uniswap through this hop.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UniswapSwapBody {
    pub quote: serde_json::Value,
    #[serde(default)]
    pub permit_data: Option<serde_json::Value>,
    #[serde(default)]
    pub signature: Option<String>,
}

#[post("/uniswap/swap")]
pub async fn post_uniswap_swap(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<UniswapSwapBody>,
) -> Result<HttpResponse, AppError> {
    let _wallet = wallet_from_req(&req)?;
    let tx = crate::services::uniswap::uniswap_swap(
        &state.http,
        &body.quote,
        body.permit_data.as_ref(),
        body.signature.as_deref(),
    )
    .await?;
    Ok(HttpResponse::Ok().json(serde_json::json!({ "transaction": tx })))
}

/// POST /actions/uniswap/lp/build — build the approval + create transactions for
/// opening a Uniswap V3 liquidity position. Reads the pool on-chain, computes the
/// tick range, and returns the ready EVM txs for the frontend to sign in order.
#[post("/uniswap/lp/build")]
pub async fn post_uniswap_lp_build(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<crate::services::uniswap::UniswapAddLiquidityParams>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let result =
        crate::services::uniswap::build_uniswap_add_liquidity(&state.http, &wallet, &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/uniswap/lp/balances — the connected wallet's balance of a
/// pool's two tokens on the pool's chain (read via Alchemy, so it's correct
/// regardless of which network the wallet is currently on). Powers the card's
/// "Balance: X · Max".
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UniswapLpBalancesBody {
    pub chain: String,
    pub token0: String,
    pub token1: String,
}

#[post("/uniswap/lp/balances")]
pub async fn post_uniswap_lp_balances(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<UniswapLpBalancesBody>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let by_slug = crate::services::uniswap::dexscreener_slug_to_chain_id(&body.chain.trim().to_lowercase());
    let chain_id = if by_slug != 0 { by_slug } else { body.chain.trim().parse::<u64>().unwrap_or(0) };
    if chain_id == 0 {
        return Err(AppError::InvalidParams("A valid EVM chain is required.".into()));
    }
    let (b0, r0, d0) = crate::services::uniswap::token_balance_of(&state.http, chain_id, &wallet, &body.token0).await;
    let (b1, r1, d1) = crate::services::uniswap::token_balance_of(&state.http, chain_id, &wallet, &body.token1).await;
    Ok(HttpResponse::Ok().json(serde_json::json!({
        "token0": { "balance": b0, "balanceRaw": r0, "decimals": d0 },
        "token1": { "balance": b1, "balanceRaw": r1, "decimals": d1 },
    })))
}

/// POST /actions/uniswap/lp/positions — the wallet's Uniswap LP positions across
/// V2/V3/V4 and every chain (proxied from Uniswap's interface gateway, decoded).
#[post("/uniswap/lp/positions")]
pub async fn post_uniswap_lp_positions(
    req: HttpRequest,
    state: web::Data<AppState>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let result = crate::services::uniswap::uniswap_positions(&state.http, &wallet).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/uniswap/launch/buy — native pools.trade buy (bonding curve /
/// CCA). Proxies trade.prepareBuy; returns `{transactions:[{to,data,value}], …}`
/// for the wallet to sign on Robinhood Chain. Body: {tokenAddress, walletAddress,
/// amountUsd, slippagePct}. Public API, no signer here — the user signs.
#[post("/uniswap/launch/buy")]
pub async fn post_pools_launch_buy(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    let result =
        crate::services::uniswap::pools_trade_mutation(&state.http, "trade.prepareBuy", &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/uniswap/launch/sell — native pools.trade sell. Proxies
/// trade.prepareSell. Body: {tokenAddress, walletAddress, amountInWei, slippagePct}.
#[post("/uniswap/launch/sell")]
pub async fn post_pools_launch_sell(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    let result =
        crate::services::uniswap::pools_trade_mutation(&state.http, "trade.prepareSell", &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/uniswap/launch/x-auth-url — start pools.trade's REAL X OAuth.
/// Proxies xVerification.getAuthUrl; returns `{authUrl}` — a live x.com OAuth 2.0
/// URL whose redirect lands on pools.trade's OWN callback, so the X account is
/// verified ON pools.trade for the wallet (their verified-creator badge).
#[post("/uniswap/launch/x-auth-url")]
pub async fn post_pools_x_auth_url(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    let result =
        crate::services::uniswap::pools_trade_mutation(&state.http, "xVerification.getAuthUrl", &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/uniswap/launch/bid — commit (bid) into a pools.trade Crowd
/// Launch (CCA) auction. A crowd launch isn't a swap: you bid ETH during the
/// auction and claim tokens after it graduates. Proxies cca.prepareBid. Body:
/// {auctionAddress, walletAddress, amountUsd, maxPriceQ96}.
#[post("/uniswap/launch/bid")]
pub async fn post_pools_launch_bid(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    let result =
        crate::services::uniswap::pools_trade_mutation(&state.http, "cca.prepareBid", &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/pons/token-meta — resolve a Pons token's curve/pair/status by
/// address (chat buy/sell by 0x address). Body: {tokenAddress}.
#[post("/pons/token-meta")]
pub async fn post_pons_token_meta(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    let token = body.get("tokenAddress").and_then(|v| v.as_str()).unwrap_or("");
    let meta = crate::services::pons::pons_token_meta(&state.http, token).await?;
    Ok(HttpResponse::Ok().json(serde_json::json!({ "token": meta })))
}

/// POST /actions/pons/buy — Pons bonding-curve buy (ABI-encoded curve.buy tx).
#[post("/pons/buy")]
pub async fn post_pons_buy(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    let result = crate::services::pons::build_pons_buy(&state.http, &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/pons/sell — Pons bonding-curve sell (approve + curve.sell tx).
#[post("/pons/sell")]
pub async fn post_pons_sell(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    let result = crate::services::pons::build_pons_sell(&state.http, &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/pons/launch — create a Pons V2 token (factory.launchToken tx).
#[post("/pons/launch")]
pub async fn post_pons_launch(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    let result = crate::services::pons::build_pons_launch(&state.http, &body).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/uniswap/launch/token-meta — resolve a pools.trade token's
/// symbol / name / image / price from its 0x address, so a chat buy/sell by raw
/// address shows the coin instead of "?". Body: {tokenAddress}. Returns the
/// launch row (or null if it isn't a pools.trade launch).
#[derive(Debug, Deserialize)]
pub struct PoolsTokenMetaBody {
    #[serde(rename = "tokenAddress", alias = "token", alias = "address")]
    pub token_address: String,
}

#[post("/uniswap/launch/token-meta")]
pub async fn post_pools_token_meta(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<PoolsTokenMetaBody>,
) -> Result<HttpResponse, AppError> {
    let meta = crate::services::uniswap::pools_token_meta(&state.http, &body.token_address).await?;
    Ok(HttpResponse::Ok().json(serde_json::json!({ "token": meta })))
}

/// POST /actions/uniswap/eth-balance — a wallet's balance on Robinhood Chain
/// (4663). Native ETH by default; pass `token` (an ERC-20 mint) for a token
/// balance (used to size a Sell as a % of holdings). Read via our RPC so it's
/// the RIGHT chain regardless of what the browser wallet is switched to. Body:
/// {address, token?}.
#[derive(Debug, Deserialize)]
pub struct EthBalanceBody {
    pub address: String,
    #[serde(default)]
    pub token: Option<String>,
}

#[post("/uniswap/eth-balance")]
pub async fn post_pools_eth_balance(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<EthBalanceBody>,
) -> Result<HttpResponse, AppError> {
    let token = body
        .token
        .as_deref()
        .filter(|t| !t.is_empty())
        .unwrap_or(crate::services::relay::NATIVE_TOKEN_ADDRESS);
    let (display, raw, decimals) =
        crate::services::uniswap::token_balance_of(&state.http, 4663, &body.address, token).await;
    Ok(HttpResponse::Ok().json(serde_json::json!({
        "balanceEth": display,   // native (kept for the buy-size caller)
        "balance": display,      // generic label
        "balanceWei": raw,
        "decimals": decimals,
    })))
}

/// POST /actions/uniswap/launch/create — prepare a pools.trade token launch.
/// Proxies curve.prepareLaunch; returns the tx(s) to sign. Body carries the token
/// metadata (name/symbol/image/…) the creator entered.
#[post("/uniswap/launch/create")]
pub async fn post_pools_launch_create(
    _req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<serde_json::Value>,
) -> Result<HttpResponse, AppError> {
    // Two launch modes share the same core fields but different tRPC methods:
    //   "crowd"  → cca.prepareAuctionLaunch (Continuous Clearing Auction, 4h)
    //   "instant"→ curve.prepareLaunch (bonding curve, live immediately, default)
    let mut payload = body.into_inner();
    let mode = payload
        .get("mode")
        .and_then(|v| v.as_str())
        .unwrap_or("instant")
        .to_lowercase();
    let method = if matches!(mode.as_str(), "crowd" | "cca" | "auction") {
        "cca.prepareAuctionLaunch"
    } else {
        "curve.prepareLaunch"
    };
    // `mode` is our own routing field — drop it before forwarding.
    if let Some(obj) = payload.as_object_mut() {
        obj.remove("mode");
    }
    let result = crate::services::uniswap::pools_trade_mutation(&state.http, method, &payload).await?;
    Ok(HttpResponse::Ok().json(result))
}

/// POST /actions/uniswap/record — book economics after the swap settles on-chain.
/// Uniswap has no authoritative post-fill record (unlike Relay's /requests), so
/// the USD notional is derived SERVER-SIDE by pricing the swapped token into USDC
/// — the client only supplies the tx hash + token addresses/amounts, never a USD
/// figure. Fee is the flat 0.50% configured on the API key; cashback is booked at
/// the account's pooled tier. Idempotent on (txHash, chain).
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UniswapRecordBody {
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub chain_id: u64,
    pub tx_hash: String,
    pub input_token: String,
    pub output_token: String,
    pub input_amount: String,
    pub output_amount: String,
    #[serde(default)]
    pub input_symbol: Option<String>,
    #[serde(default)]
    pub output_symbol: Option<String>,
}

#[post("/uniswap/record")]
pub async fn post_uniswap_record(
    req: HttpRequest,
    state: web::Data<AppState>,
    body: web::Json<UniswapRecordBody>,
) -> Result<HttpResponse, AppError> {
    let wallet = wallet_from_req(&req)?;
    let account = account_from_req(&req);
    let tx_hash = body.tx_hash.trim().to_string();
    if tx_hash.is_empty() {
        return Err(AppError::InvalidParams("txHash is required".into()));
    }
    let not_recorded = |reason: &str| {
        Ok(HttpResponse::Ok().json(serde_json::json!({ "recorded": false, "reason": reason })))
    };
    let Some(chain_key) = relay::chain_key_for_id(body.chain_id) else {
        return not_recorded("unsupported chain");
    };

    // USD notional, server-derived (never the client's word): price the output
    // into USDC, falling back to the input side.
    let mut notional_usd = crate::services::uniswap::uniswap_price_usd(
        &state.http,
        body.chain_id,
        &body.output_token,
        &body.output_amount,
    )
    .await
    .unwrap_or(0.0);
    if notional_usd <= 0.0 {
        notional_usd = crate::services::uniswap::uniswap_price_usd(
            &state.http,
            body.chain_id,
            &body.input_token,
            &body.input_amount,
        )
        .await
        .unwrap_or(0.0);
    }

    // Only book a fee once Uniswap has actually enabled fee-taking for our
    // recipient. Until then the integratorFees field is accepted but nothing is
    // deducted, so recording a fee (and owing cashback on it) would be wrong.
    let fee_bps: i32 = if crate::services::uniswap::fee_active() {
        crate::services::uniswap::fee_bps() as i32
    } else {
        0
    };
    let fee_usd = notional_usd * (fee_bps as f64) / 10_000.0;

    match crate::db::economics::record_evm_confirmed(
        &state.pool,
        wallet.clone(),
        account,
        chain_key.to_string(),
        tx_hash.clone(),
        "uniswap".to_string(),
        "swap".to_string(),
        body.input_symbol.clone(),
        body.output_symbol.clone(),
        notional_usd,
        fee_bps,
        fee_usd,
    )
    .await
    {
        Ok(id) => {
            tracing::info!(action = "uniswap_record", user_wallet = %wallet, chain = %chain_key, tx = %tx_hash, notional_usd, fee_usd, "Uniswap swap economics recorded");
            Ok(HttpResponse::Ok().json(serde_json::json!({
                "recorded": true, "transactionId": id, "chain": chain_key,
                "notionalUsd": notional_usd, "feeUsd": fee_usd, "feeBps": fee_bps
            })))
        }
        Err(e) => {
            tracing::warn!(error = %e, tx = %tx_hash, "uniswap economics record failed");
            not_recorded("record failed")
        }
    }
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
        return Err(AppError::InvalidParams(
            "signature query parameter is required".into(),
        ));
    }
    if body.kind.trim().is_empty() {
        return Err(AppError::InvalidParams("kind is required".into()));
    }
    if body.request_id.trim().is_empty() {
        return Err(AppError::InvalidParams("requestId is required".into()));
    }

    let result = relay::execute_relay_permits(&state.http, &query.signature, &body)
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

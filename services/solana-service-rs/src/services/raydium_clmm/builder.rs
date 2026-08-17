//! Top-level builder for `raydium_open_position`.
//!
//! Flow:
//!   1. Fetch the pool account, parse `PoolStateView`.
//!   2. Resolve which side (`token_0` or `token_1`) the user is depositing.
//!   3. Align tick bounds to the pool's `tick_spacing`.
//!   4. Compute liquidity from the input amount via `solana-clmm-raydium`
//!      math (`get_liquidity_from_single_amount_0/1` for single-sided,
//!      `get_liquidity_from_amounts` for dual).
//!   5. Apply slippage to derive `amount_0_max` / `amount_1_max` envelopes.
//!   6. Generate a fresh keypair for the position NFT mint.
//!   7. Derive the user's ATAs for both pool tokens.
//!   8. Encode `open_position_v2` via `ix::open_position_v2(...)`.
//!   9. Prepend a compute-budget hint and assemble a versioned transaction.
//!
//! **First-cut implementation — needs devnet validation.** Likely points
//! of failure:
//!   - Tick-array auto-init: if either `tick_array_lower` or `_upper`
//!     doesn't exist on-chain yet, Raydium errors out. Real flow needs
//!     a preceding `open_tick_array` instruction. TODO before mainnet.
//!   - Position NFT mint must be a fresh keypair signer (we generate one
//!     here, embed it in the TX, and surface its pubkey to the caller for
//!     UX). The frontend must sign with the user's wallet — the NFT mint
//!     signs via the additional-signer list.
//!   - With-metadata path: we omit Metaplex by default (lower TX size).
//!     If a user wants the NFT to render in wallets, enable it via a
//!     param flag — leaves a comment hook below.

use base64::prelude::*;
use solana_sdk::{
    compute_budget::ComputeBudgetInstruction,
    instruction::Instruction,
    message::{v0::Message, VersionedMessage},
    pubkey::Pubkey,
    signature::{Keypair, Signer},
    transaction::VersionedTransaction,
};
use spl_associated_token_account::get_associated_token_address;

use crate::error::AppError;
use crate::services::builder::BuildResponse;
use crate::services::raydium_clmm::ix::{
    create_tick_array, open_position_v2, tick_array_pda, OpenPositionV2Inputs,
};
use crate::services::raydium_clmm::state::{tick_array_start_index, PoolStateView};
use crate::solana::connection::SolanaRpc;

/// Slippage envelope: turns the exact `amount_X` needed for `liquidity`
/// into an `amount_X_max` upper bound the on-chain program may consume.
/// `slippage_bps = 100` → +1% headroom.
fn apply_slippage_upper(amount: u64, slippage_bps: u32) -> u64 {
    // (amount * (10000 + bps)) / 10000, saturating to u64
    let numer = (amount as u128).saturating_mul((10_000u128).saturating_add(slippage_bps as u128));
    let result = numer / 10_000u128;
    if result > u64::MAX as u128 {
        u64::MAX
    } else {
        result as u64
    }
}

/// Align a tick to `tick_spacing` — rounding for the lower bound goes
/// down, for the upper bound goes up. Mirrors `align_tick_lower` /
/// `align_tick_upper` in `raydium.rs`.
fn align_lower(tick: i32, spacing: u16) -> i32 {
    let s = spacing as i32;
    let mut aligned = (tick / s) * s;
    if tick < 0 && tick % s != 0 {
        aligned -= s;
    }
    aligned
}
fn align_upper(tick: i32, spacing: u16) -> i32 {
    let s = spacing as i32;
    let mut aligned = (tick / s) * s;
    if tick > 0 && tick % s != 0 {
        aligned += s;
    } else if tick < 0 && tick % s != 0 {
        // already covered by floor div for negatives
    }
    aligned
}

/// All the user-supplied data the open-position builder needs.
pub struct OpenPositionRequest {
    pub user_pubkey: Pubkey,
    pub pool_id: Pubkey,
    /// Which pool token the user wants to deposit (the side they sized).
    /// Must equal either `pool.token_mint_0` or `pool.token_mint_1`.
    pub input_mint: Pubkey,
    /// Amount of `input_mint` in base units (lamports / smallest unit).
    pub input_amount: u64,
    /// Tick bounds — caller already aligned, but we re-align defensively.
    pub tick_lower: i32,
    pub tick_upper: i32,
    pub slippage_bps: u32,
    /// Mint NFT with Metaplex metadata (heavier TX). Default `false`.
    pub with_metadata: bool,
    /// The position NFT mint, when the CLIENT generated it.
    ///
    /// A position is NFT-backed, so opening one mints a fresh NFT that has to
    /// sign. Whoever holds that key signs it, and where the key lives decides
    /// the order the transaction gets signed in:
    ///
    /// - `Some`: the browser holds it. We build with the user's signer slot
    ///   AND the mint's slot both empty, the wallet signs first, and the
    ///   browser adds the mint signature afterwards. This is the order
    ///   Phantom documents for multi-signer transactions.
    /// - `None`: nobody told us, so we fall back to generating the key here
    ///   and pre-signing. Kept so a client that predates this still works —
    ///   it is the old behaviour, not the intended one.
    pub position_nft_mint: Option<Pubkey>,
}

/// Build a versioned transaction that opens a Raydium CLMM position.
/// Returns the base64-encoded transaction plus the position NFT mint
/// (so the caller can surface "your position will be NFT XYZ" to the user).
pub async fn build_open_position(
    rpc: &SolanaRpc,
    req: OpenPositionRequest,
) -> Result<(BuildResponse, Pubkey), AppError> {
    // 1. Fetch + parse the pool. `SolanaRpc::client()` is the BLOCKING
    //    `solana_client::rpc_client::RpcClient` — it internally uses
    //    `tokio::task::block_in_place`, which panics on actix-web's
    //    single-threaded worker runtime. Move the call to a blocking
    //    thread pool via `spawn_blocking`. Same pattern as pumpfun.rs.
    let pool_id_for_fetch = req.pool_id;
    let rpc_clone = rpc.clone();
    let pool_data = tokio::task::spawn_blocking(move || {
        rpc_clone.client().get_account_data(&pool_id_for_fetch)
    })
    .await
    .map_err(|e| AppError::Internal(format!("spawn_blocking join error: {e}")))?
    .map_err(|e| AppError::ProtocolError(format!("fetch pool {}: {e}", req.pool_id)))?;
    let pool = PoolStateView::parse(&pool_data)?;

    // 2. Identify which side the input is.
    let input_is_token_0 = req.input_mint == pool.token_mint_0;
    let input_is_token_1 = req.input_mint == pool.token_mint_1;
    if !input_is_token_0 && !input_is_token_1 {
        return Err(AppError::InvalidParams(format!(
            "inputMint {} is neither pool side ({} / {})",
            req.input_mint, pool.token_mint_0, pool.token_mint_1
        )));
    }

    // 3. Defensive re-alignment of tick bounds. UI may have passed
    //    pre-aligned values, but if the pool uses an unusual spacing
    //    (Raydium uses 1, 10, 60, 120, 200) we want to be sure.
    let tick_lower = align_lower(req.tick_lower, pool.tick_spacing);
    let tick_upper = align_upper(req.tick_upper, pool.tick_spacing);
    if tick_lower >= tick_upper {
        return Err(AppError::InvalidParams(format!(
            "tick_lower ({}) must be strictly less than tick_upper ({})",
            tick_lower, tick_upper
        )));
    }
    let tick_array_lower_start_index = tick_array_start_index(tick_lower, pool.tick_spacing);
    let tick_array_upper_start_index = tick_array_start_index(tick_upper, pool.tick_spacing);

    // 4. Compute liquidity from the user's single-sided deposit.
    let sqrt_price_lower = solana_clmm_raydium::tick_math::get_sqrt_price_at_tick(tick_lower)
        .map_err(|e| AppError::ProtocolError(format!("sqrt_price_lower: {:?}", e)))?;
    let sqrt_price_upper = solana_clmm_raydium::tick_math::get_sqrt_price_at_tick(tick_upper)
        .map_err(|e| AppError::ProtocolError(format!("sqrt_price_upper: {:?}", e)))?;

    let liquidity = if input_is_token_0 {
        solana_clmm_raydium::liquidity_math::get_liquidity_from_single_amount_0(
            pool.sqrt_price_x64,
            sqrt_price_lower,
            sqrt_price_upper,
            req.input_amount,
        )
    } else {
        solana_clmm_raydium::liquidity_math::get_liquidity_from_single_amount_1(
            pool.sqrt_price_x64,
            sqrt_price_lower,
            sqrt_price_upper,
            req.input_amount,
        )
    };
    if liquidity == 0 {
        return Err(AppError::InvalidParams(
            "computed liquidity is 0 — input amount too small for this range".into(),
        ));
    }

    // 5. Compute the OTHER side's required amount, then add slippage envelopes.
    //    `get_delta_amount_{0,1}_unsigned` returns the exact amount needed
    //    given the (lower, upper, liquidity, round_up) tuple.
    let amount_0_exact = solana_clmm_raydium::liquidity_math::get_delta_amount_0_unsigned(
        sqrt_price_lower,
        sqrt_price_upper.min(pool.sqrt_price_x64.max(sqrt_price_lower)),
        liquidity,
        true,
    )
    .unwrap_or(0);
    let amount_1_exact = solana_clmm_raydium::liquidity_math::get_delta_amount_1_unsigned(
        sqrt_price_lower.max(pool.sqrt_price_x64.min(sqrt_price_upper)),
        sqrt_price_upper,
        liquidity,
        true,
    )
    .unwrap_or(0);
    let amount_0_max = apply_slippage_upper(amount_0_exact, req.slippage_bps);
    let amount_1_max = apply_slippage_upper(amount_1_exact, req.slippage_bps);

    // 6. The position NFT mint. Client-supplied when the browser holds the
    //    key (the intended path); generated here only as the legacy fallback.
    let position_nft_mint_kp: Option<Keypair> = match req.position_nft_mint {
        Some(_) => None,
        None => Some(Keypair::new()),
    };
    let position_nft_mint = req.position_nft_mint.unwrap_or_else(|| {
        position_nft_mint_kp
            .as_ref()
            .expect("generated above")
            .pubkey()
    });
    let position_nft_account = get_associated_token_address(&req.user_pubkey, &position_nft_mint);

    // 7. User token ATAs for the pool's two mints.
    let user_token_account_0 = get_associated_token_address(&req.user_pubkey, &pool.token_mint_0);
    let user_token_account_1 = get_associated_token_address(&req.user_pubkey, &pool.token_mint_1);

    // 8. Encode the instruction.
    let inputs = OpenPositionV2Inputs {
        payer: req.user_pubkey,
        position_nft_owner: req.user_pubkey,
        position_nft_mint,
        position_nft_account,
        pool_id: req.pool_id,
        user_token_account_0,
        user_token_account_1,
        token_vault_0: pool.token_vault_0,
        token_vault_1: pool.token_vault_1,
        tick_lower_index: tick_lower,
        tick_upper_index: tick_upper,
        tick_array_lower_start_index,
        tick_array_upper_start_index,
        liquidity,
        amount_0_max,
        amount_1_max,
        with_metadata: req.with_metadata,
        // single-sided deposit: tell Raydium which side carries the bound.
        base_flag: Some(input_is_token_0),
    };
    let open_ix = open_position_v2(&inputs);

    // 9. Check whether the two tick_array PDAs already exist on-chain.
    //    Raydium's `open_position_v2` does NOT init them — if the user's
    //    range falls into a tick array that no prior LP has touched,
    //    Anchor fails to deserialise the zero-filled PDA and the whole
    //    sim aborts with error 3007 (`AccountDidNotDeserialize`).
    //    Fix: prepend a `create_tick_array` instruction for each missing
    //    array. The pool's `signer` field for create_tick_array is the
    //    payer (= user_pubkey), which then becomes the rent contributor.
    let tick_array_lower_pda = tick_array_pda(&req.pool_id, tick_array_lower_start_index);
    let tick_array_upper_pda = tick_array_pda(&req.pool_id, tick_array_upper_start_index);
    let rpc_clone = rpc.clone();
    let arrays_to_probe = vec![tick_array_lower_pda, tick_array_upper_pda];
    let probe_results: Vec<Option<solana_sdk::account::Account>> =
        tokio::task::spawn_blocking(move || {
            arrays_to_probe
                .into_iter()
                .map(|pk| rpc_clone.client().get_account(&pk).ok())
                .collect()
        })
        .await
        .map_err(|e| AppError::Internal(format!("spawn_blocking join error: {e}")))?;

    let mut init_ixs: Vec<Instruction> = Vec::new();
    let lower_exists = probe_results[0].is_some();
    let upper_exists =
        // If the start indices collide (single-tick-array position) we
        // only need to init once.
        if tick_array_lower_start_index == tick_array_upper_start_index {
            lower_exists
        } else {
            probe_results[1].is_some()
        };
    if !lower_exists {
        init_ixs.push(create_tick_array(
            req.user_pubkey,
            req.pool_id,
            tick_array_lower_start_index,
        ));
    }
    if !upper_exists && tick_array_lower_start_index != tick_array_upper_start_index {
        init_ixs.push(create_tick_array(
            req.user_pubkey,
            req.pool_id,
            tick_array_upper_start_index,
        ));
    }

    // 10. Compute-budget bump — heavier when we also need to init tick
    //     arrays (each `create_tick_array` allocates ~10 KiB rent-exempt
    //     account data and writes the initial zero state).
    let cu_limit: u32 = match init_ixs.len() {
        0 => 400_000,
        1 => 500_000,
        _ => 600_000,
    };
    let cu_ix = ComputeBudgetInstruction::set_compute_unit_limit(cu_limit);
    // Priority fee (compute-unit PRICE). Without it the open-position tx carries
    // a zero tip and gets deprioritized/dropped under any congestion — it never
    // lands and the frontend sits on "Confirming on-chain…" until the blockhash
    // expires (~60s). 100k microLamports/CU matches the other Raydium paths
    // (raydium.rs SDK builds); at ≤600k CU that's ≤0.00006 SOL.
    let cu_price_ix = ComputeBudgetInstruction::set_compute_unit_price(100_000);

    let mut instructions: Vec<Instruction> = Vec::with_capacity(3 + init_ixs.len());
    instructions.push(cu_price_ix);
    instructions.push(cu_ix);
    instructions.extend(init_ixs);
    instructions.push(open_ix);
    // `get_latest_blockhash_with_retry` is also blocking — same actix
    // panic guard as above.
    let rpc_clone = rpc.clone();
    let blockhash =
        tokio::task::spawn_blocking(move || rpc_clone.get_latest_blockhash_with_retry())
            .await
            .map_err(|e| AppError::Internal(format!("spawn_blocking join error: {e}")))?
            .map_err(|e| AppError::ProtocolError(format!("blockhash: {e}")))?;
    let msg = Message::try_compile(&req.user_pubkey, &instructions, &[], blockhash)
        .map_err(|e| AppError::ProtocolError(format!("compile msg: {e}")))?;

    // The mint is a required signer (its slot in `account_keys` sits below
    // `header.num_required_signatures`). Who fills that slot, and when,
    // depends on where the key lives — see `position_nft_mint` on the request.
    let num_required = msg.header.num_required_signatures as usize;
    let static_keys = msg.account_keys.clone();
    let versioned_msg = VersionedMessage::V0(msg);
    let mut tx = VersionedTransaction {
        signatures: vec![solana_sdk::signature::Signature::default(); num_required],
        message: versioned_msg,
    };
    let mint_idx = static_keys
        .iter()
        .position(|k| *k == position_nft_mint)
        .ok_or_else(|| {
            AppError::ProtocolError(
                "position_nft_mint not in tx static keys — message compile bug".into(),
            )
        })?;
    if mint_idx >= num_required {
        return Err(AppError::ProtocolError(format!(
            "position_nft_mint at index {} but only {} required signatures — not in signer set",
            mint_idx, num_required
        )));
    }
    // Only sign here on the legacy path. When the browser holds the key we
    // leave BOTH signer slots empty so the wallet is the first signature on
    // the transaction, which is what Phantom's multi-signer guidance asks for
    // — a transaction that already carries a stranger's signature is exactly
    // the shape its scanner cannot vouch for.
    if let Some(kp) = position_nft_mint_kp.as_ref() {
        tx.signatures[mint_idx] = kp.sign_message(&tx.message.serialize());
    }

    let tx_bytes = bincode::serialize(&tx)
        .map_err(|e| AppError::ProtocolError(format!("serialize tx: {e}")))?;
    let tx_b64 = BASE64_STANDARD.encode(&tx_bytes);

    // Diagnostic pre-simulation: ask the validator to run the TX with
    // `sig_verify=false` (the user's signer slot is still empty) and
    // `replace_recent_blockhash=true`. We DO NOT fail the build on a
    // sim error — the wallet will simulate again before signing — but
    // we DO log the program-log lines so when the action fails with a
    // bare error code, we have the actual on-chain context here in the
    // server log. Uses the nonblocking RpcClient because actix workers
    // run single-threaded current-thread tokio runtimes.
    let endpoint = rpc.endpoint().to_string();
    let async_rpc = solana_rpc_client::nonblocking::rpc_client::RpcClient::new_with_commitment(
        endpoint,
        solana_sdk::commitment_config::CommitmentConfig::confirmed(),
    );
    match async_rpc
        .simulate_transaction_with_config(
            &tx,
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
    {
        Ok(sim) => {
            let v = sim.value;
            let logs_joined = v.logs.as_ref().map(|l| l.join("\n")).unwrap_or_default();
            match v.err {
                Some(err) => {
                    // The full program log stays here, in the server log, where
                    // it is useful. It is not what the caller gets back.
                    tracing::warn!(
                        pool = %req.pool_id,
                        err = ?err,
                        tick_lower,
                        tick_upper,
                        tick_array_lower_start_index,
                        tick_array_upper_start_index,
                        liquidity,
                        "raydium_open_position SIM FAILED — program log follows:\n{}",
                        logs_joined
                    );

                    // An InstructionError is the program rejecting this exact
                    // transaction: deterministic, and it will reject it again
                    // when the user signs. Handing it over anyway meant asking
                    // someone to approve a transaction we already knew would
                    // revert — and the wallet, running the same simulation,
                    // says so in its own words before the prompt.
                    //
                    // Every other variant (blockhash not found, node lagging,
                    // an account not yet visible to this RPC) is about the
                    // moment rather than the transaction, so those still fall
                    // through and let the wallet decide. Failing on those
                    // would break valid positions whenever an RPC hiccuped.
                    //
                    // The error debug stays out of the returned message on
                    // purpose. `InstructionError(0, Custom(6021))` means
                    // nothing to whoever is opening a position, and the
                    // frontend's technical-text gate would recognise it and
                    // replace the WHOLE sentence with a generic apology —
                    // taking the one useful half, "try a different range",
                    // down with it. It is in the server log above.
                    if matches!(
                        err,
                        solana_sdk::transaction::TransactionError::InstructionError(_, _)
                    ) {
                        return Err(AppError::ProtocolError(
                            "This position can't be opened as set up — the pool rejected it. \
                             Try a different price range or a smaller amount."
                                .to_string(),
                        ));
                    }
                }
                None => {
                    tracing::info!(
                        pool = %req.pool_id,
                        units = ?v.units_consumed,
                        "raydium_open_position SIM OK"
                    );
                }
            }
        }
        Err(e) => {
            // Could not reach the simulator at all — no verdict either way, so
            // this is not evidence against the transaction.
            tracing::warn!(error = ?e, "raydium_open_position sim RPC error (build still returned)");
        }
    }

    let response = BuildResponse {
        preview: crate::services::builder::ActionPreview {
            id: uuid::Uuid::new_v4().to_string(),
            action_type: "raydium_open_position".to_string(),
            description: format!(
                "Open Raydium CLMM position: {} of {} in pool {}… (ticks {}→{}, liquidity {})",
                req.input_amount,
                req.input_mint,
                &req.pool_id.to_string()[..8],
                tick_lower,
                tick_upper,
                liquidity,
            ),
            estimated_fee: "~0.01 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::json!({
                "pool_id": req.pool_id.to_string(),
                "position_nft_mint": position_nft_mint.to_string(),
                "tick_lower": tick_lower,
                "tick_upper": tick_upper,
                "tick_array_lower_start": tick_array_lower_start_index,
                "tick_array_upper_start": tick_array_upper_start_index,
                "liquidity": liquidity.to_string(),
                "amount_0_max": amount_0_max,
                "amount_1_max": amount_1_max,
                "input_is_token_0": input_is_token_0,
            }),
            warnings: vec![
                "Concentrated liquidity earns fees only while price stays within your range".into(),
                "First-cut Raydium CLMM SDK integration — please test on devnet first".into(),
            ],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0, // pre-signed
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    };

    Ok((response, position_nft_mint))
}

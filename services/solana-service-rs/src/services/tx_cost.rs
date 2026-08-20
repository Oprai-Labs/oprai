//! What a transaction actually takes out of the wallet.
//!
//! Every builder used to quote a hand-written constant, and the constants were
//! wrong in the direction that hurts: a DAMM v2 open quoted "~0.0003 SOL"
//! against a measured 0.0099, and a DLMM open quotes "~0.005 SOL" for a
//! position account the chain charges 0.0574 to create. A wallet holding
//! enough for the deposit and the quoted fee then cannot submit, and nobody is
//! told that most of the difference comes back on close.
//!
//! The costs are not guessable by inspection. They are rent on accounts the
//! instruction decides to create — a position, its NFT mint, a token account,
//! sometimes a tick or bin array that exists only if some other LP happened to
//! use that stretch of the curve first. Enumerating them by hand is how the
//! wrong constants got written in the first place.
//!
//! So ask the chain: simulate the built transaction and read the fee payer's
//! balance before and after. That covers every account the instruction
//! touches, including the ones nobody remembered.

use solana_sdk::pubkey::Pubkey;
use solana_sdk::transaction::VersionedTransaction;

use solana_rpc_client::nonblocking::rpc_client::RpcClient as AsyncRpc;

/// The fee payer's SOL change if this transaction lands, in SOL.
///
/// Negative means SOL leaves the wallet. `None` when the simulation could not
/// be run or the account was not returned — callers must then say nothing
/// rather than fall back to a number they made up.
pub async fn simulated_sol_delta(
    rpc: &AsyncRpc,
    tx: &VersionedTransaction,
    payer: &Pubkey,
) -> Option<f64> {
    let before = rpc.get_balance(payer).await.ok()?;
    let sim = rpc
        .simulate_transaction_with_config(
            tx,
            solana_client::rpc_config::RpcSimulateTransactionConfig {
                sig_verify: false,
                replace_recent_blockhash: true,
                commitment: Some(solana_sdk::commitment_config::CommitmentConfig::confirmed()),
                // VersionedTransaction needs base64; the default base58 trips
                // -32602 and the simulation is silently lost.
                encoding: Some(solana_transaction_status::UiTransactionEncoding::Base64),
                accounts: Some(
                    solana_client::rpc_config::RpcSimulateTransactionAccountsConfig {
                        encoding: Some(solana_account_decoder::UiAccountEncoding::Base64),
                        addresses: vec![payer.to_string()],
                    },
                ),
                min_context_slot: None,
                inner_instructions: false,
            },
        )
        .await
        .ok()?;

    if sim.value.err.is_some() {
        return None;
    }
    let after = sim.value.accounts?.first()?.as_ref()?.lamports;
    Some((after as f64 - before as f64) / 1e9)
}

/// Render a measured cost the way a card should show it, or fall back to the
/// builder's own wording when nothing could be measured.
///
/// `spent_elsewhere` is the part of the outflow that is not a cost at all —
/// the SOL side of a deposit, or an amount being staked. Subtracting it leaves
/// what the action itself charges, which is the number a person needs in order
/// to know whether they can afford to do this.
pub fn cost_label(delta: Option<f64>, spent_elsewhere: f64, fallback: &str) -> String {
    match delta {
        Some(d) => {
            let cost = (-d - spent_elsewhere).max(0.0);
            format!("~{cost:.4} SOL")
        }
        None => fallback.to_string(),
    }
}

/// Same measurement, for builders that already hold the transaction as base64.
pub async fn simulated_sol_delta_b64(rpc_url: &str, tx_b64: &str, payer: &Pubkey) -> Option<f64> {
    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(tx_b64)
        .ok()?;
    let tx: VersionedTransaction = bincode::deserialize(&bytes).ok()?;
    let rpc = AsyncRpc::new_with_commitment(
        rpc_url.to_string(),
        solana_sdk::commitment_config::CommitmentConfig::confirmed(),
    );
    simulated_sol_delta(&rpc, &tx, payer).await
}

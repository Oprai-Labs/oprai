//! Cashback treasury payout.
//!
//! Cashback accrues in USD (a tier % of every commission the user paid, plus a
//! referral % of their referees' commissions) on `wallet_economics_rollup`. A
//! claim pays the claimable balance out **in SOL** from a treasury wallet whose
//! secret this service holds — the one place solana-service signs with a key of
//! its own.
//!
//! Safety, mirroring the fee model: no treasury configured means claims are
//! simply unavailable, never a broken action. The treasury is a hot wallet, so
//! it should hold only what is needed to cover outstanding cashback.

use std::str::FromStr;
use std::sync::OnceLock;
use std::time::Duration;

use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::{Keypair, Signer};
use solana_sdk::system_instruction;
use solana_sdk::transaction::Transaction;

use crate::error::AppError;

/// Minimum claim, in USD — matches the market (Based/Trojan-style $5 floor).
pub const MIN_CLAIM_USD: f64 = 5.0;

/// The treasury keypair, from `OPRAI_CASHBACK_TREASURY_SECRET` (a base58-encoded
/// 64-byte secret key, the format wallets export). Read once; an unparseable or
/// absent value disables cashback claims rather than crashing the service.
fn treasury_keypair() -> Option<&'static Keypair> {
    static KP: OnceLock<Option<Keypair>> = OnceLock::new();
    KP.get_or_init(|| {
        let raw = std::env::var("OPRAI_CASHBACK_TREASURY_SECRET").ok()?;
        let raw = raw.trim();
        if raw.is_empty() {
            return None;
        }
        let bytes = bs58::decode(raw).into_vec().ok()?;
        match Keypair::from_bytes(&bytes) {
            Ok(kp) => {
                tracing::info!(pubkey = %kp.pubkey(), "cashback treasury configured");
                Some(kp)
            }
            Err(e) => {
                tracing::error!(error = %e, "OPRAI_CASHBACK_TREASURY_SECRET is not a valid keypair — cashback claims disabled");
                None
            }
        }
    })
    .as_ref()
}

/// Whether cashback claims can be paid out at all.
pub fn treasury_configured() -> bool {
    treasury_keypair().is_some()
}

/// The current SOL price in USD (Jupiter lite price API). `None` on any failure,
/// which the caller turns into a retriable error rather than paying a wrong
/// amount.
async fn sol_price_usd(http: &reqwest::Client) -> Option<f64> {
    const WSOL: &str = "So11111111111111111111111111111111111111112";
    let url = format!("https://lite-api.jup.ag/price/v3?ids={WSOL}");
    let resp = http
        .get(&url)
        .timeout(Duration::from_secs(3))
        .send()
        .await
        .ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let v: serde_json::Value = resp.json().await.ok()?;
    let p = v
        .get(WSOL)
        .and_then(|x| x.get("usdPrice").and_then(|p| p.as_f64()))
        .unwrap_or(0.0);
    (p > 0.0).then_some(p)
}

/// Pay `amount_usd` of cashback to `recipient`, in SOL, from the treasury.
/// Returns the transaction signature on success. Blocking RPC work runs off the
/// async runtime.
pub async fn payout_sol(
    http: &reqwest::Client,
    recipient: &str,
    amount_usd: f64,
) -> Result<String, AppError> {
    let treasury = treasury_keypair()
        .ok_or_else(|| AppError::InvalidParams("Cashback claims are not available yet.".into()))?;
    let recipient = Pubkey::from_str(recipient)
        .map_err(|_| AppError::InvalidParams("Invalid wallet address.".into()))?;

    let price = sol_price_usd(http).await.ok_or_else(|| {
        AppError::Internal("Could not price SOL for the payout. Try again.".into())
    })?;
    let lamports = ((amount_usd / price) * 1_000_000_000.0) as u64;
    if lamports == 0 {
        return Err(AppError::InvalidParams(
            "Cashback amount is too small to pay out.".into(),
        ));
    }

    let endpoint = std::env::var("SOLANA_RPC")
        .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".to_string());
    let treasury_pk = treasury.pubkey();
    let ix = system_instruction::transfer(&treasury_pk, &recipient, lamports);

    let sig = tokio::task::spawn_blocking(move || -> Result<String, String> {
        let rpc = crate::solana::connection::SolanaRpc::new(&endpoint);
        let client = rpc.client();
        let blockhash = client.get_latest_blockhash().map_err(|e| e.to_string())?;
        // Re-fetch the keypair inside the blocking task (OnceLock, cheap).
        let kp = treasury_keypair().ok_or_else(|| "treasury vanished".to_string())?;
        let tx = Transaction::new_signed_with_payer(&[ix], Some(&treasury_pk), &[kp], blockhash);
        client
            .send_and_confirm_transaction(&tx)
            .map(|s| s.to_string())
            .map_err(|e| e.to_string())
    })
    .await
    .map_err(|e| AppError::Internal(format!("payout task join: {e}")))?
    .map_err(|e| {
        tracing::warn!(error = %e, recipient = %recipient, "cashback payout failed");
        AppError::Internal("The cashback payout could not be sent. Try again.".into())
    })?;

    Ok(sig)
}

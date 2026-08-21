//! Server-authoritative USD value of a confirmed trade, read from the chain.
//!
//! A wallet's traded volume decides its tier, and its tier decides its fee
//! discount and referral share — so the number must be one the client cannot
//! invent. Everything the client sends (`est_usd`, the amounts in `parameters`)
//! is ignored here. We fetch the confirmed transaction, read the wallet's own
//! token-balance deltas straight out of the transaction meta, and price them
//! server-side.
//!
//! The bias is deliberate: if the chain cannot be read or a side cannot be
//! priced, we return `None` and the trade simply does not count toward volume.
//! Volume may be undercounted, never inflated — the opposite failure would let
//! someone farm a tier they did not earn.

use std::collections::HashMap;
use std::str::FromStr;
use std::time::Duration;

use solana_client::rpc_config::RpcTransactionConfig;
use solana_sdk::commitment_config::CommitmentConfig;
use solana_sdk::signature::Signature;
use solana_transaction_status::option_serializer::OptionSerializer;
use solana_transaction_status::{UiTransactionEncoding, UiTransactionStatusMeta};

use crate::services::fees::{USDC_MINT, USDT_MINT, WSOL_MINT};
use crate::solana::tokens::get_token_info;

/// Stablecoins are priced at $1 with no network call — exact, and the common
/// case for one side of a swap. The registry is the source of truth for
/// symbols; a hardcoded name set goes stale the moment a new stable lists.
fn is_stable_mint(mint: &str) -> bool {
    if mint.eq_ignore_ascii_case(USDC_MINT) || mint.eq_ignore_ascii_case(USDT_MINT) {
        return true;
    }
    get_token_info(mint)
        .map(|t| {
            let s = t.symbol.to_uppercase();
            s.starts_with("USD")
                || s.ends_with("USD")
                || matches!(
                    s.as_str(),
                    "PYUSD" | "EURC" | "DAI" | "FRAX" | "USDE" | "USDS"
                )
        })
        .unwrap_or(false)
}

/// The wallet's own token-balance deltas in a confirmed tx: `(mint, |Δ|)` in
/// human units. Only the `wallet`'s balances are read; everyone else's are
/// ignored, so this cannot be inflated by co-signers or routing accounts.
fn wallet_token_deltas(meta: &UiTransactionStatusMeta, wallet: &str) -> Vec<(String, f64)> {
    let empty = Vec::new();
    let pre = match &meta.pre_token_balances {
        OptionSerializer::Some(v) => v,
        _ => &empty,
    };
    let post = match &meta.post_token_balances {
        OptionSerializer::Some(v) => v,
        _ => &empty,
    };

    // net = Σ post − Σ pre, per mint, for this owner only.
    let mut by_mint: HashMap<String, f64> = HashMap::new();
    for b in pre {
        if matches!(&b.owner, OptionSerializer::Some(o) if o == wallet) {
            *by_mint.entry(b.mint.clone()).or_insert(0.0) -=
                b.ui_token_amount.ui_amount.unwrap_or(0.0);
        }
    }
    for b in post {
        if matches!(&b.owner, OptionSerializer::Some(o) if o == wallet) {
            *by_mint.entry(b.mint.clone()).or_insert(0.0) +=
                b.ui_token_amount.ui_amount.unwrap_or(0.0);
        }
    }

    by_mint
        .into_iter()
        .filter(|(_, d)| d.abs() > 1e-9)
        .map(|(m, d)| (m, d.abs()))
        .collect()
}

/// Signed token deltas for `owner` — positive means credited.
fn signed_token_deltas(meta: &UiTransactionStatusMeta, owner: &str) -> Vec<(String, f64)> {
    let empty = Vec::new();
    let pre = match &meta.pre_token_balances {
        OptionSerializer::Some(v) => v,
        _ => &empty,
    };
    let post = match &meta.post_token_balances {
        OptionSerializer::Some(v) => v,
        _ => &empty,
    };
    let mut by_mint: HashMap<String, f64> = HashMap::new();
    for b in pre {
        if matches!(&b.owner, OptionSerializer::Some(o) if o == owner) {
            *by_mint.entry(b.mint.clone()).or_insert(0.0) -=
                b.ui_token_amount.ui_amount.unwrap_or(0.0);
        }
    }
    for b in post {
        if matches!(&b.owner, OptionSerializer::Some(o) if o == owner) {
            *by_mint.entry(b.mint.clone()).or_insert(0.0) +=
                b.ui_token_amount.ui_amount.unwrap_or(0.0);
        }
    }
    by_mint.into_iter().filter(|(_, d)| *d > 1e-9).collect()
}

/// Lamports credited to `owner` as native SOL by this transaction.
///
/// Token balances live in `*_token_balances` and are keyed by owner, but a plain
/// SOL transfer moves nothing there — it shows only as a lamport delta on the
/// account itself. The commission on several paths is exactly such a transfer,
/// so reading only tokens would score every one of them as unpaid.
fn native_sol_credit(meta: &UiTransactionStatusMeta, account_keys: &[String], owner: &str) -> f64 {
    let Some(idx) = account_keys.iter().position(|k| k == owner) else {
        return 0.0;
    };
    let (Some(pre), Some(post)) = (meta.pre_balances.get(idx), meta.post_balances.get(idx)) else {
        return 0.0;
    };
    let delta = *post as i128 - *pre as i128;
    if delta <= 0 {
        return 0.0;
    }
    delta as f64 / 1_000_000_000.0
}

/// What a confirmed transaction was worth, and what OPRAI was actually paid for it.
#[derive(Debug, Clone, Copy)]
pub struct ConfirmedValue {
    /// USD magnitude the trade moved for the user.
    pub notional_usd: f64,
    /// USD that actually reached the fee wallet, read from the chain. `None`
    /// when no fee wallet is configured, so callers can tell "not charging"
    /// apart from "charged nothing".
    pub fee_paid_usd: Option<f64>,
}

/// The USD value a confirmed trade actually moved for `wallet`, or `None` if the
/// The USD value a confirmed trade actually moved for `wallet`, or `None` if the
/// chain cannot be read / priced.
///
/// A swap's two sides are worth ~the same, so the trade's value is the *maximum*
/// USD magnitude across the wallet's deltas — counted once, and correct whether
/// the wallet was buying or selling. A reverted transaction (on-chain `err`)
/// contributes nothing.
pub async fn confirmed_trade_notional_usd(
    http: &reqwest::Client,
    signature: &str,
    wallet: &str,
) -> Option<f64> {
    confirmed_trade_value(http, signature, wallet)
        .await
        .map(|v| v.notional_usd)
}

/// Both halves of a confirmed transaction, read from one fetch: what it moved
/// for the user, and what reached the fee wallet.
///
/// The second half exists because the commission on several paths is a plain
/// transfer instruction. A user who signs their own transaction can drop that
/// instruction and the trade still settles — so a fee computed from the rate we
/// *intended* records revenue that never arrived, and (worse) funds a cashback
/// payout out of it.
pub async fn confirmed_trade_value(
    http: &reqwest::Client,
    signature: &str,
    wallet: &str,
) -> Option<ConfirmedValue> {
    let sig = Signature::from_str(signature).ok()?;
    let endpoint = std::env::var("SOLANA_RPC")
        .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".to_string());
    let wallet_owned = wallet.to_string();
    let fee_wallet = crate::services::fees::fee_wallet().map(|w| w.to_string());
    let fee_wallet_task = fee_wallet.clone();

    // The RPC client is blocking; keep it off the async runtime.
    let fetched = tokio::task::spawn_blocking(
        move || -> Option<(Vec<(String, f64)>, Vec<(String, f64)>)> {
            let rpc = crate::solana::connection::SolanaRpc::new(&endpoint);
            let cfg = RpcTransactionConfig {
                encoding: Some(UiTransactionEncoding::JsonParsed),
                commitment: Some(CommitmentConfig::confirmed()),
                max_supported_transaction_version: Some(0),
                ..Default::default()
            };
            let tx = rpc.client().get_transaction_with_config(&sig, cfg).ok()?;
            let meta = tx.transaction.meta?;
            if meta.err.is_some() {
                // Marked confirmed by the client but reverted on chain — no volume.
                return Some((Vec::new(), Vec::new()));
            }
            let user = wallet_token_deltas(&meta, &wallet_owned);

            let mut fee: Vec<(String, f64)> = Vec::new();
            if let Some(fw) = fee_wallet_task.as_deref() {
                fee.extend(signed_token_deltas(&meta, fw));
                let keys = account_keys_of(&tx.transaction.transaction);
                let sol = native_sol_credit(&meta, &keys, fw);
                if sol > 0.0 {
                    fee.push((WSOL_MINT.to_string(), sol));
                }
            }
            Some((user, fee))
        },
    )
    .await
    .ok()??;

    let (deltas, fee_credits) = fetched;
    let fee_paid_usd = match fee_wallet {
        None => None,
        Some(_) => Some(price_sides_usd(http, &fee_credits).await.unwrap_or(0.0)),
    };

    if deltas.is_empty() {
        return fee_paid_usd.map(|f| ConfirmedValue {
            notional_usd: 0.0,
            fee_paid_usd: Some(f),
        });
    }

    let notional_usd = price_sides_usd(http, &deltas).await?;
    Some(ConfirmedValue {
        notional_usd,
        fee_paid_usd,
    })
}

/// Account keys of a parsed transaction, in `pre_balances` / `post_balances` order.
fn account_keys_of(tx: &solana_transaction_status::EncodedTransaction) -> Vec<String> {
    use solana_transaction_status::{EncodedTransaction, UiMessage};
    match tx {
        EncodedTransaction::Json(ui) => match &ui.message {
            UiMessage::Parsed(m) => m.account_keys.iter().map(|k| k.pubkey.clone()).collect(),
            UiMessage::Raw(m) => m.account_keys.clone(),
        },
        _ => Vec::new(),
    }
}

/// Largest USD magnitude across `sides`, pricing stables at $1 and the rest in a
/// single batched Jupiter call.
///
/// A swap's two sides are worth ~the same, so taking the max counts the trade
/// once and is correct whether the wallet was buying or selling. For a fee the
/// same rule holds: the commission arrives on one side only.
async fn price_sides_usd(http: &reqwest::Client, sides: &[(String, f64)]) -> Option<f64> {
    if sides.is_empty() {
        return None;
    }
    let mut best = 0.0_f64;
    let mut to_price: Vec<(String, f64)> = Vec::new();
    for (mint, amt) in sides {
        if is_stable_mint(mint) {
            best = best.max(*amt);
        } else {
            to_price.push((mint.clone(), *amt));
        }
    }

    if !to_price.is_empty() {
        let ids = to_price
            .iter()
            .map(|(m, _)| m.as_str())
            .collect::<Vec<_>>()
            .join(",");
        let url = format!("https://lite-api.jup.ag/price/v3?ids={ids}");
        if let Ok(resp) = http.get(&url).timeout(Duration::from_secs(3)).send().await {
            if resp.status().is_success() {
                if let Ok(payload) = resp.json::<serde_json::Value>().await {
                    for (mint, amt) in &to_price {
                        let price = payload
                            .get(mint)
                            .and_then(|v| {
                                v.get("usdPrice").and_then(|p| p.as_f64()).or_else(|| {
                                    v.get("price").and_then(|p| match p {
                                        serde_json::Value::Number(n) => n.as_f64(),
                                        serde_json::Value::String(s) => s.parse::<f64>().ok(),
                                        _ => None,
                                    })
                                })
                            })
                            .unwrap_or(0.0);
                        if price > 0.0 {
                            best = best.max(amt * price);
                        }
                    }
                }
            }
        }
    }

    (best > 0.0).then_some(best)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Live check against a real confirmed swap. Ignored by default (needs a
    /// funded RPC); run with:
    ///   SOLANA_RPC=<url> cargo test onchain_live -- --ignored --nocapture
    #[tokio::test]
    #[ignore]
    async fn onchain_live() {
        // The verified USDT->BONK swap: wallet spent 0.150749 USDT, so the
        // server-authoritative notional must land near $0.15.
        let sig = "4k7twCupa8ueBrgWjSy5mwS6B5ViM5pVspLjNKAgyRcFH35psoLChXKuoz6LgjL1B16oLLVv8r5zgHBTYHHYxLQZ";
        let wallet = "GB5mfBPzMR5dntVsDAjc1kLaxS9tEhkBwRqDV8g4rBjt";
        let http = reqwest::Client::new();
        let v = confirmed_trade_notional_usd(&http, sig, wallet).await;
        println!("onchain notional = {v:?}");
        let v = v.expect("should read a notional from chain");
        assert!((0.10..0.25).contains(&v), "expected ~$0.15, got {v}");
    }
}

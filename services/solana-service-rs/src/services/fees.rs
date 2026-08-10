//! What OPRAI charges, in one place.
//!
//! A fee that is decided per protocol drifts: one path takes 50 bps, another
//! forgets, a third takes it twice. So the rate lives here, every caller asks
//! this module, and changing what we charge is one edit.
//!
//! Two rules run through everything below.
//!
//! **A missing fee wallet means no fee, never a broken action.** Every entry
//! point returns zero or `None` when nothing is configured, so the service
//! behaves exactly as it did before commissions existed until an address is
//! set. Revenue is worth less than a swap that works.
//!
//! **We only take fees in tokens worth holding.** Jupiter pays the platform
//! fee in one of the pair's mints, and a cut of every memecoin someone trades
//! would leave us with a wallet full of dust that costs more in rent than it
//! is worth. If neither side is SOL, USDC or USDT, the trade is free.

use std::str::FromStr;
use std::sync::OnceLock;

use solana_sdk::instruction::Instruction;
use solana_sdk::pubkey::Pubkey;

use crate::solana::tokens::get_token_info;

/// Long-tail tokens — anything outside our curated registry, which in practice
/// means pump.fun launches and fresh memecoins. Trading bots in this segment
/// charge 0.5–1%; this is the low end of what the market already accepts.
pub const MEMECOIN_BPS: u16 = 50;

/// Established pairs. Deliberately well under Phantom's 0.85%: a user who
/// compares us against swapping directly should not find a reason to leave.
pub const STANDARD_BPS: u16 = 20;

/// Stablecoin to stablecoin. Free.
///
/// These trades are large and the spread is nearly nothing, so a percentage
/// fee is conspicuous — 0.2% on a $10,000 USDC→USDT move is $20 for what the
/// user reads as a transfer. Charging it is the fastest way to teach someone
/// to route around us.
pub const STABLE_PAIR_BPS: u16 = 0;

/// pump.fun trades and launches.
pub const PUMPFUN_BPS: u16 = 50;

pub const WSOL_MINT: &str = "So11111111111111111111111111111111111111112";
pub const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
pub const USDT_MINT: &str = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB";

/// The mints we are willing to be paid in.
///
/// Ordered by preference: SOL first because it needs no conversion to pay for
/// anything on Solana.
const PAYABLE_MINTS: [&str; 3] = [WSOL_MINT, USDC_MINT, USDT_MINT];

/// Where commissions go. `OPRAI_FEE_WALLET`, read once.
///
/// An unparseable address is treated as absent rather than fatal — a typo in
/// an env var should cost the fee, not the service.
pub fn fee_wallet() -> Option<Pubkey> {
    static WALLET: OnceLock<Option<Pubkey>> = OnceLock::new();
    *WALLET.get_or_init(|| {
        let raw = std::env::var("OPRAI_FEE_WALLET").ok()?;
        let raw = raw.trim();
        if raw.is_empty() {
            return None;
        }
        match Pubkey::from_str(raw) {
            Ok(pk) => {
                tracing::info!(wallet = %pk, "OPRAI fee wallet configured");
                Some(pk)
            }
            Err(e) => {
                tracing::error!(value = %raw, error = %e, "OPRAI_FEE_WALLET is not a valid address — no fees will be taken");
                None
            }
        }
    })
}

fn is_stable(mint_or_symbol: &str) -> bool {
    let resolved = mint_or_symbol.trim();
    if resolved.eq_ignore_ascii_case(USDC_MINT) || resolved.eq_ignore_ascii_case(USDT_MINT) {
        return true;
    }
    // The registry is the source of truth for symbols; a hardcoded set of
    // stablecoin names goes stale the moment a new one lists.
    get_token_info(resolved)
        .map(|t| {
            let s = t.symbol.to_uppercase();
            s.starts_with("USD")
                || s.ends_with("USD")
                || matches!(s.as_str(), "PYUSD" | "EURC" | "DAI" | "FRAX" | "USDE")
        })
        .unwrap_or(false)
}

/// True when we have never heard of the token — the working definition of a
/// memecoin, and a good one: our registry holds what is established, and a
/// pump.fun mint from four minutes ago is by construction not in it.
fn is_long_tail(mint: &str) -> bool {
    get_token_info(mint.trim()).is_none()
}

/// The commission on a swap, in basis points.
pub fn swap_fee_bps(input_mint: &str, output_mint: &str) -> u16 {
    if fee_wallet().is_none() {
        return 0;
    }
    if is_stable(input_mint) && is_stable(output_mint) {
        return STABLE_PAIR_BPS;
    }
    if is_long_tail(input_mint) || is_long_tail(output_mint) {
        return MEMECOIN_BPS;
    }
    STANDARD_BPS
}

/// Every mint on this pair we would accept payment in, best first.
///
/// Jupiter's constraint: on an ExactIn swap the fee account's mint may be
/// either side; on ExactOut it may only be the input. Within that we only want
/// SOL, USDC or USDT — a cut of every memecoin traded would be dust worth less
/// than its own rent.
///
/// A list rather than a single choice, because the preferred mint's account
/// may not exist yet. Returning only the favourite meant a SOL→USDC swap went
/// uncharged while sitting next to a perfectly good USDC fee account, purely
/// because wSOL is listed first.
pub fn swap_fee_mints<'a>(
    input_mint: &'a str,
    output_mint: &'a str,
    exact_out: bool,
) -> Vec<&'a str> {
    let sides: &[&str] = if exact_out {
        &[input_mint]
    } else {
        &[output_mint, input_mint]
    };
    let mut out = Vec::new();
    for want in PAYABLE_MINTS {
        for c in sides {
            if c.eq_ignore_ascii_case(want) && !out.contains(c) {
                out.push(*c);
            }
        }
    }
    out
}

/// The first mint on this pair whose fee account actually exists.
pub async fn ready_swap_fee_mint<'a>(
    input_mint: &'a str,
    output_mint: &'a str,
    exact_out: bool,
) -> Option<(&'a str, Pubkey)> {
    for mint in swap_fee_mints(input_mint, output_mint, exact_out) {
        if let Some(account) = ready_fee_account(mint).await {
            return Some((mint, account));
        }
    }
    None
}

/// The fee account for `mint`, but only once the chain confirms it exists.
///
/// Jupiter does NOT validate the fee account when it builds a swap. It returns
/// a perfectly ordinary transaction naming an account that is not there, and
/// the failure arrives on chain, after the user has signed — a SOL→USDC swap
/// failed with error 6025 exactly this way. So "build it and see" is not a
/// design; the account has to be checked before it is named.
///
/// Cached, but asymmetrically: an account that exists never stops existing, so
/// that answer is kept for the life of the process. A missing one is only
/// trusted for a few minutes — the account may be created at any moment, by a
/// pump.fun trade carrying the idempotent create or by hand, and the fee
/// should start flowing then rather than at the next restart.
pub async fn ready_fee_account(mint: &str) -> Option<Pubkey> {
    /// How long to believe an account is still missing.
    const MISSING_TTL: std::time::Duration = std::time::Duration::from_secs(300);
    type Cache = std::collections::HashMap<String, (bool, std::time::Instant)>;
    static SEEN: OnceLock<std::sync::Mutex<Cache>> = OnceLock::new();
    let cache = SEEN.get_or_init(|| std::sync::Mutex::new(Cache::new()));

    let mint_pk = Pubkey::from_str(mint).ok()?;
    let account = fee_token_account(&mint_pk)?;

    if let Some((known, at)) = cache.lock().ok().and_then(|c| c.get(mint).copied()) {
        if known {
            return Some(account);
        }
        if at.elapsed() < MISSING_TTL {
            return None;
        }
    }

    let endpoint = std::env::var("SOLANA_RPC")
        .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".to_string());
    let exists = tokio::task::spawn_blocking(move || {
        crate::solana::connection::SolanaRpc::new(&endpoint)
            .client()
            .get_account(&account)
            .is_ok()
    })
    .await
    .unwrap_or(false);

    if !exists {
        tracing::warn!(
            mint = %mint,
            fee_account = %account,
            "fee account does not exist — swaps in this mint go uncharged until it is created"
        );
    }
    if let Ok(mut c) = cache.lock() {
        c.insert(mint.to_string(), (exists, std::time::Instant::now()));
    }
    exists.then_some(account)
}

/// The associated token account that receives the fee for a given mint.
///
/// Jupiter requires this account to exist already; it will not create it. The
/// caller checks, and skips the fee when it is missing — see
/// `swap_fee_account` in `swap.rs`.
pub fn fee_token_account(mint: &Pubkey) -> Option<Pubkey> {
    let owner = fee_wallet()?;
    Some(spl_associated_token_account::get_associated_token_address(
        &owner, mint,
    ))
}

/// An instruction that creates the fee token account for `mint`, paid by
/// whoever is already paying for this transaction.
///
/// Jupiter will not create the account it pays fees into, so until it exists
/// every swap in that pair goes uncharged. It costs about 0.002 SOL of rent,
/// once, ever — and the alternative is a manual ritual that has to be
/// remembered again the next time the fee wallet changes. `Idempotent` means
/// a transaction that includes it after the account exists simply does
/// nothing, so this is safe to leave in.
pub fn ensure_fee_account_ix(payer: &Pubkey, mint: &Pubkey) -> Option<Instruction> {
    let owner = fee_wallet()?;
    Some(
        spl_associated_token_account::instruction::create_associated_token_account_idempotent(
            payer,
            &owner,
            mint,
            &spl_token::id(),
        ),
    )
}

/// The commission on a pump.fun trade, in lamports.
///
/// Taken as a plain SOL transfer alongside the trade rather than through any
/// protocol's fee plumbing, because pump.fun has none for third parties — and
/// SOL needs no token account, so this path can never be skipped for a missing
/// ATA the way the Jupiter one can.
pub fn pumpfun_fee_lamports(trade_lamports: u64) -> u64 {
    if fee_wallet().is_none() {
        return 0;
    }
    trade_lamports.saturating_mul(PUMPFUN_BPS as u64) / 10_000
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_wallet_means_no_fee() {
        // The env var is unset in tests, which is the point: every rate must
        // fall to zero rather than quietly charging into the void.
        assert_eq!(swap_fee_bps(WSOL_MINT, USDC_MINT), 0);
        assert_eq!(pumpfun_fee_lamports(1_000_000_000), 0);
        assert!(fee_token_account(&Pubkey::new_unique()).is_none());
    }

    #[test]
    fn we_are_only_paid_in_tokens_worth_holding() {
        let meme = "9HiJnsEY9rFiaBDgRVPBtGDEbWUx9Rdhh6fnrkvDpump";
        // A memecoin bought with SOL pays us in SOL, not in the memecoin.
        assert_eq!(swap_fee_mints(WSOL_MINT, meme, false), vec![WSOL_MINT]);
        // Selling it for USDC pays us in USDC.
        assert_eq!(swap_fee_mints(meme, USDC_MINT, false), vec![USDC_MINT]);
        // Memecoin to memecoin pays us nothing — dust is not revenue.
        assert!(
            swap_fee_mints(meme, "AnotherMintxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", false).is_empty()
        );
        // Both sides payable: every option is offered, best first, so a
        // missing account on the favourite does not cost the fee.
        assert_eq!(
            swap_fee_mints(USDC_MINT, WSOL_MINT, false),
            vec![WSOL_MINT, USDC_MINT],
        );
        // ExactOut may only use the input side.
        assert!(swap_fee_mints(meme, USDC_MINT, true).is_empty());
        assert_eq!(swap_fee_mints(USDC_MINT, meme, true), vec![USDC_MINT]);
    }

    #[test]
    fn stablecoin_pairs_are_free() {
        assert!(is_stable(USDC_MINT) && is_stable(USDT_MINT));
        assert!(!is_stable(WSOL_MINT));
    }
}

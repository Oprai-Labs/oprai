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
/// means pump.fun launches and fresh memecoins. 1%, matching Trojan / BONKbot;
/// the reward for the user is CASHBACK on this fee (a tier % returned), not a
/// lower rate.
pub const MEMECOIN_BPS: u16 = 100;

/// Established pairs (SOL, blue-chips). Kept well under the 1% memecoin rate so
/// a user comparing us against swapping directly on a major pair has no reason
/// to leave; cashback applies here too.
pub const STANDARD_BPS: u16 = 30;

/// Stablecoin to stablecoin. Free.
///
/// These trades are large and the spread is nearly nothing, so a percentage
/// fee is conspicuous — 0.2% on a $10,000 USDC→USDT move is $20 for what the
/// user reads as a transfer. Charging it is the fastest way to teach someone
/// to route around us.
pub const STABLE_PAIR_BPS: u16 = 0;

/// pump.fun trades and launches. 1%, in line with the memecoin swap rate.
pub const PUMPFUN_BPS: u16 = 100;

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
        .map(|t| symbol_is_stable(&t.symbol))
        .unwrap_or(false)
}

/// Whether a ticker names a stablecoin.
///
/// Split out because callers that already hold a symbol — Kamino reserves
/// carry one — should not have to round-trip through the mint registry to ask.
/// One definition of the rule, two ways in.
pub fn symbol_is_stable(symbol: &str) -> bool {
    let s = symbol.trim().to_uppercase();
    s.starts_with("USD")
        || s.ends_with("USD")
        || matches!(s.as_str(), "PYUSD" | "EURC" | "DAI" | "FRAX" | "USDE")
}

/// Blue-chip mints that earn the STANDARD (non-memecoin) rate. Curated on
/// purpose: "listed in the registry" is NOT enough, because the registry also
/// lists established memecoins like BONK. So the standard rate is an explicit
/// allowlist (SOL, stablecoins are handled by is_stable, major LSTs, wrapped
/// majors) and everything else — registry-listed memecoins AND fresh pump.fun
/// mints alike — is charged the memecoin rate. Add a mint here to promote it.
const STANDARD_MINTS: [&str; 6] = [
    WSOL_MINT,
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So", // mSOL
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", // jitoSOL
    "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v", // jupSOL
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1", // bSOL
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh", // wBTC
];

/// True for blue-chip tokens (SOL, stablecoins, major LSTs, wrapped majors)
/// that earn the standard rate. Everything else is long-tail (memecoin rate),
/// including registry-listed memecoins — the fix for BONK being charged the
/// standard rate just because it was in the registry.
fn is_standard(mint: &str) -> bool {
    let m = mint.trim();
    is_stable(m) || STANDARD_MINTS.iter().any(|s| s.eq_ignore_ascii_case(m))
}

// ──────────────────────────────────────────────────────────────────────────────
// Tier cashback
// ──────────────────────────────────────────────────────────────────────────────
//
// A wallet's lifetime traded volume sets its tier. The fee itself is NOT
// discounted by tier (that model was dropped) — instead the user pays the full
// commission and earns a tier percentage of it back as CASHBACK, credited to a
// claimable ledger after each trade confirms. This mirrors Trojan/Padre-style
// loyalty ("10–45% of fees back") and keeps our headline rate at the market 1%.
//
// The thresholds and percentages mirror `analytics_schema.tier_config` and the
// frontend tier ladder. They are duplicated here on purpose: solana-service owns
// `solana_schema` and must not read `analytics_schema` (scoped-role isolation),
// so the copies are kept in sync by hand. Keep them identical.

/// Cumulative-volume floor (USD) for tiers 1..=6.
pub const TIER_MIN_VOLUME_USD: [f64; 6] =
    [0.0, 1_000.0, 10_000.0, 50_000.0, 250_000.0, 1_000_000.0];

/// Cashback — percent of the commission returned — for tiers 1..=6.
pub const TIER_CASHBACK_PCT: [u16; 6] = [10, 15, 20, 25, 30, 40];

/// Referral cashback — percent of a REFEREE's commission the referrer earns,
/// by the referrer's tier — for tiers 1..=6. Mirrors the frontend ladder.
/// Capped at 35% (Trojan's direct-referral rate) so the combined payout
/// (own cashback + referral) still leaves OPRAI a sector-standard margin.
pub const TIER_REFERRAL_PCT: [u16; 6] = [20, 24, 27, 30, 33, 35];

/// The tier (1..=6) for a lifetime volume.
pub fn tier_for_volume(volume_usd: f64) -> u8 {
    let mut tier = 1u8;
    for (i, &min) in TIER_MIN_VOLUME_USD.iter().enumerate() {
        if volume_usd >= min {
            tier = (i + 1) as u8;
        }
    }
    tier
}

/// The cashback percent for a tier, clamped to the valid range.
pub fn tier_cashback_pct(tier: u8) -> u16 {
    let idx = (tier.clamp(1, 6) - 1) as usize;
    TIER_CASHBACK_PCT[idx]
}

/// The cashback percent a wallet has earned through its lifetime volume.
pub fn cashback_pct_for_volume(volume_usd: f64) -> u16 {
    tier_cashback_pct(tier_for_volume(volume_usd))
}

/// The referral cashback percent for a tier, clamped to the valid range.
pub fn tier_referral_pct(tier: u8) -> u16 {
    let idx = (tier.clamp(1, 6) - 1) as usize;
    TIER_REFERRAL_PCT[idx]
}

/// The referral cashback percent a wallet earns, from its lifetime volume tier.
pub fn referral_pct_for_volume(volume_usd: f64) -> u16 {
    tier_referral_pct(tier_for_volume(volume_usd))
}

/// Apply a discount (percent off) to a base rate in bps. Retained as a generic
/// helper — the fee path calls it with 0 (no tier discount under the cashback
/// model), so it is a pass-through today. Rounds down, never below zero.
pub fn discounted_bps(base_bps: u16, discount_pct: u16) -> u16 {
    let d = discount_pct.min(100) as u32;
    ((base_bps as u32) * (100 - d) / 100) as u16
}

/// The commission on a swap, in basis points.
pub fn swap_fee_bps(input_mint: &str, output_mint: &str) -> u16 {
    if fee_wallet().is_none() {
        return 0;
    }
    if is_stable(input_mint) && is_stable(output_mint) {
        return STABLE_PAIR_BPS;
    }
    // Standard rate only when BOTH sides are blue-chip; otherwise the trade
    // touches a memecoin/long-tail token and earns the memecoin rate.
    if is_standard(input_mint) && is_standard(output_mint) {
        return STANDARD_BPS;
    }
    MEMECOIN_BPS
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
pub fn pumpfun_fee_lamports(trade_lamports: u64, discount_pct: u16) -> u64 {
    if fee_wallet().is_none() {
        return 0;
    }
    let bps = discounted_bps(PUMPFUN_BPS, discount_pct) as u64;
    trade_lamports.saturating_mul(bps) / 10_000
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_wallet_means_no_fee() {
        // The env var is unset in tests, which is the point: every rate must
        // fall to zero rather than quietly charging into the void.
        assert_eq!(swap_fee_bps(WSOL_MINT, USDC_MINT), 0);
        assert_eq!(pumpfun_fee_lamports(1_000_000_000, 0), 0);
        assert!(fee_token_account(&Pubkey::new_unique()).is_none());
    }

    #[test]
    fn tier_thresholds_map_to_the_right_tier() {
        assert_eq!(tier_for_volume(0.0), 1);
        assert_eq!(tier_for_volume(999.0), 1);
        assert_eq!(tier_for_volume(1_000.0), 2);
        assert_eq!(tier_for_volume(9_999.0), 2);
        assert_eq!(tier_for_volume(10_000.0), 3);
        assert_eq!(tier_for_volume(50_000.0), 4);
        assert_eq!(tier_for_volume(250_000.0), 5);
        assert_eq!(tier_for_volume(1_000_000.0), 6);
        assert_eq!(tier_for_volume(50_000_000.0), 6);
    }

    #[test]
    fn commission_rates_and_cashback() {
        // Market-rate commission: 1% memecoin, 0.3% blue-chip. discounted_bps is
        // a pass-through under the cashback model (called with 0).
        assert_eq!(discounted_bps(MEMECOIN_BPS, 0), 100);
        assert_eq!(discounted_bps(STANDARD_BPS, 0), 30);
        assert_eq!(discounted_bps(STABLE_PAIR_BPS, 0), 0);
        // Cashback percent scales with the volume tier (Bronze 10% -> Legend 40%).
        assert_eq!(cashback_pct_for_volume(0.0), 10); // Bronze
        assert_eq!(cashback_pct_for_volume(10_000.0), 20); // Gold
        assert_eq!(cashback_pct_for_volume(2_000_000.0), 40); // Legend
        assert_eq!(tier_cashback_pct(4), 25); // Platinum
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

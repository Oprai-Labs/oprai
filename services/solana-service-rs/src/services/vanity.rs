//! Vanity mint-address pool.
//!
//! Real pump.fun mints end in the base58 suffix `pump`. A Solana address IS an
//! ed25519 public key, so the only way to obtain a `…pump` address is to
//! generate random keypairs until one's pubkey happens to end in `pump`
//! (~58^4 ≈ 11.3M tries on average). pump.fun hides that latency by
//! pre-grinding a pool of ready keypairs; we do the same. Background worker
//! threads keep [`POOL_TARGET`] ground keypairs on hand, so a launch pops one
//! instantly. If the pool is cold or momentarily empty, callers fall back to a
//! plain random keypair so a launch NEVER blocks or fails.
//!
//! Safety: the mint keypair is a throwaway. After pump.fun's `create`, the mint
//! authority belongs to a program PDA (seeds `["mint-authority"]`) and the full
//! supply is already minted, so this keypair controls nothing afterward — it
//! only co-signs the create tx once and is then discarded. Handing it to the
//! client is therefore safe (it is the same secret the client would otherwise
//! have generated itself).

use std::collections::VecDeque;
use std::sync::{Condvar, Mutex, OnceLock};
use std::thread;

use solana_sdk::signature::{Keypair, Signer};

/// The base58 suffix every pump.fun mint carries.
const VANITY_SUFFIX: &str = "pump";
/// How many ground keypairs to keep ready at any time.
const POOL_TARGET: usize = 4;
/// Never dedicate more than this many CPU threads to grinding.
const MAX_WORKERS: usize = 4;

struct VanityPool {
    slots: Mutex<VecDeque<Keypair>>,
    /// Woken when a slot frees up (a keypair was taken) so a grinder refills it.
    below_target: Condvar,
}

static POOL: OnceLock<VanityPool> = OnceLock::new();

fn pool() -> &'static VanityPool {
    POOL.get_or_init(|| VanityPool {
        slots: Mutex::new(VecDeque::with_capacity(POOL_TARGET)),
        below_target: Condvar::new(),
    })
}

/// Spawn the background grinder threads. Call once at startup. Idempotent-safe
/// to call once; calling twice would double the worker count.
pub fn start_vanity_grinder() {
    let _ = pool(); // force init before workers reference it
    let workers = thread::available_parallelism()
        .map(|n| n.get().saturating_sub(1).clamp(1, MAX_WORKERS))
        .unwrap_or(2);
    for _ in 0..workers {
        thread::Builder::new()
            .name("vanity-grinder".into())
            .spawn(grind_loop)
            .ok();
    }
    tracing::info!(
        workers,
        target = POOL_TARGET,
        suffix = VANITY_SUFFIX,
        "vanity mint grinder started"
    );
}

fn grind_loop() {
    let p = pool();
    loop {
        // Park while the pool is already full; take_vanity_mint() notifies us
        // when a slot frees up.
        {
            let mut slots = p.slots.lock().unwrap();
            while slots.len() >= POOL_TARGET {
                slots = p.below_target.wait(slots).unwrap();
            }
        }
        // Grind one keypair OUTSIDE the lock (this is the CPU-bound part).
        let kp = grind_one();
        let mut slots = p.slots.lock().unwrap();
        if slots.len() < POOL_TARGET {
            slots.push_back(kp);
        }
        // else: filled up while we were grinding — drop this extra one.
    }
}

/// Base58 alphabet (Bitcoin/Solana ordering).
const B58_ALPHABET: &[u8; 58] = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

/// Generate random keypairs until one's pubkey ends in the vanity suffix.
fn grind_one() -> Keypair {
    let suffix = VANITY_SUFFIX.as_bytes();
    loop {
        let kp = Keypair::new();
        if base58_ends_with(&kp.pubkey().to_bytes(), suffix) {
            return kp;
        }
    }
}

/// True if the base58 encoding of `bytes` (a big-endian 256-bit number) ends
/// with `suffix`. Computes ONLY the trailing base58 digits via repeated
/// mod-58 division — no full base58 encode and no string allocation — so the
/// grind hot path stays cheap even in a debug build. Leading zero bytes only
/// add `'1'` characters to the FRONT of the encoding, so they never affect the
/// suffix and are correctly ignored here.
fn base58_ends_with(bytes: &[u8; 32], suffix: &[u8]) -> bool {
    // Divide the big-endian number by 58 once per suffix char, comparing each
    // remainder (least-significant base58 digit first) against the suffix read
    // right-to-left.
    let mut n = *bytes;
    for &want in suffix.iter().rev() {
        let mut rem: u32 = 0;
        for b in n.iter_mut() {
            let acc = rem * 256 + *b as u32;
            *b = (acc / 58) as u8;
            rem = acc % 58;
        }
        if B58_ALPHABET[rem as usize] != want {
            return false;
        }
    }
    true
}

/// Pop a pre-ground `…pump` keypair, or `None` if the pool is currently empty.
/// Callers should fall back to a plain [`Keypair::new`] on `None` so launches
/// never block.
pub fn take_vanity_mint() -> Option<Keypair> {
    let p = pool();
    let mut slots = p.slots.lock().unwrap();
    let kp = slots.pop_front();
    if kp.is_some() {
        // Wake a grinder to refill the freed slot.
        p.below_target.notify_one();
    }
    kp
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The fast trailing-digit check must agree with a real base58 encode for
    /// the last N characters, across many random pubkeys.
    #[test]
    fn base58_suffix_matches_full_encode() {
        for _ in 0..5_000 {
            let kp = Keypair::new();
            let bytes = kp.pubkey().to_bytes();
            let full = kp.pubkey().to_string();
            for suffix in ["p", "um", "ump", "pump", "1", "z"] {
                let s = suffix.as_bytes();
                assert_eq!(
                    base58_ends_with(&bytes, s),
                    full.as_bytes().ends_with(s),
                    "mismatch for suffix {suffix:?} on {full}"
                );
            }
        }
    }
}

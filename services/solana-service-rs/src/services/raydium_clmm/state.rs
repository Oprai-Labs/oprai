//! Raydium CLMM `PoolState` parser — minimal subset.
//!
//! We deliberately do NOT depend on Anchor or the on-chain program crate:
//! - The `raydium_amm_v3` source pulls in `anchor-lang` which conflicts with
//!   our `solana-sdk = 2` toolchain.
//! - We only need a handful of fields (mints, vaults, decimals, sqrt_price,
//!   current tick, tick spacing). A byte-offset reader is ~50 lines and
//!   stable as long as Raydium doesn't reorder the struct.
//!
//! Field layout (verified against
//! https://github.com/raydium-io/raydium-clmm/blob/master/programs/amm/src/states/pool.rs
//! commit current as of 2025):
//!
//! ```text
//! offset  size   field
//! ------  ----   ------------------------------------------------------------
//!   0      8     Anchor account discriminator ("global:PoolState"[..8])
//!   8      1     bump
//!   9     32     amm_config: Pubkey
//!  41     32     owner: Pubkey
//!  73     32     token_mint_0: Pubkey
//! 105     32     token_mint_1: Pubkey
//! 137     32     token_vault_0: Pubkey
//! 169     32     token_vault_1: Pubkey
//! 201     32     observation_key: Pubkey
//! 233      1     mint_decimals_0: u8
//! 234      1     mint_decimals_1: u8
//! 235      2     tick_spacing: u16
//! 237     16     liquidity: u128
//! 253     16     sqrt_price_x64: u128
//! 269      4     tick_current: i32
//! 273      2     padding3
//! 275      2     padding4
//! 277     16     fee_growth_global_0_x64
//! 293     16     fee_growth_global_1_x64
//! 309      8     protocol_fees_token_0
//! 317      8     protocol_fees_token_1
//! ...           (rewards, bitmap, padding — not needed for open_position)
//! ```
//!
//! If Raydium ships a struct change (extending mid-struct), the offsets
//! beyond that point shift and this reader breaks silently. Mitigation:
//! the `parse` function asserts the discriminator first and returns an
//! error early. We pin the visible offsets via constants for grep-ability.

use solana_sdk::pubkey::Pubkey;

use crate::error::AppError;

// ── Byte offsets — keep aligned with the doc-comment table above ──────────
const DISCRIMINATOR_LEN: usize = 8;
const OFF_TOKEN_MINT_0: usize = 73;
const OFF_TOKEN_MINT_1: usize = 105;
const OFF_TOKEN_VAULT_0: usize = 137;
const OFF_TOKEN_VAULT_1: usize = 169;
const OFF_MINT_DECIMALS_0: usize = 233;
const OFF_MINT_DECIMALS_1: usize = 234;
const OFF_TICK_SPACING: usize = 235;
const OFF_SQRT_PRICE_X64: usize = 253;
const OFF_TICK_CURRENT: usize = 269;
const MIN_ACCOUNT_LEN: usize = OFF_TICK_CURRENT + 4;

/// Discriminator for the `PoolState` account: `sha256("account:PoolState")[..8]`.
/// Hard-coded so we don't pull a hash dep into the hot path.
/// Pre-computed value: see Raydium IDL or run
/// `sha256("account:PoolState".as_bytes())` → first 8 bytes.
const POOL_STATE_DISCRIMINATOR: [u8; 8] = [247, 237, 227, 245, 215, 195, 222, 70];

/// Minimal subset of Raydium's `PoolState` — only the fields we need
/// to build `open_position_v2`.
#[derive(Debug, Clone)]
pub struct PoolStateView {
    pub token_mint_0: Pubkey,
    pub token_mint_1: Pubkey,
    pub token_vault_0: Pubkey,
    pub token_vault_1: Pubkey,
    pub mint_decimals_0: u8,
    pub mint_decimals_1: u8,
    pub tick_spacing: u16,
    pub sqrt_price_x64: u128,
    pub tick_current: i32,
}

impl PoolStateView {
    /// Parse a `PoolState` account from its raw bytes (as returned by
    /// `RpcClient::get_account_data`). Validates the Anchor discriminator
    /// first so a stale or wrong account fails loudly.
    pub fn parse(data: &[u8]) -> Result<Self, AppError> {
        if data.len() < MIN_ACCOUNT_LEN {
            return Err(AppError::ProtocolError(format!(
                "Raydium PoolState too short: got {} bytes, need ≥{}",
                data.len(),
                MIN_ACCOUNT_LEN
            )));
        }
        let disc: [u8; 8] = data[..DISCRIMINATOR_LEN].try_into().expect("slice len 8");
        if disc != POOL_STATE_DISCRIMINATOR {
            return Err(AppError::ProtocolError(format!(
                "Not a Raydium PoolState account (discriminator mismatch: {:?})",
                disc
            )));
        }

        let read_pubkey = |off: usize| -> Pubkey {
            let arr: [u8; 32] = data[off..off + 32].try_into().expect("slice len 32");
            Pubkey::new_from_array(arr)
        };
        let read_u8 = |off: usize| -> u8 { data[off] };
        let read_u16 = |off: usize| -> u16 {
            u16::from_le_bytes(data[off..off + 2].try_into().expect("slice len 2"))
        };
        let read_u128 = |off: usize| -> u128 {
            u128::from_le_bytes(data[off..off + 16].try_into().expect("slice len 16"))
        };
        let read_i32 = |off: usize| -> i32 {
            i32::from_le_bytes(data[off..off + 4].try_into().expect("slice len 4"))
        };

        Ok(PoolStateView {
            token_mint_0: read_pubkey(OFF_TOKEN_MINT_0),
            token_mint_1: read_pubkey(OFF_TOKEN_MINT_1),
            token_vault_0: read_pubkey(OFF_TOKEN_VAULT_0),
            token_vault_1: read_pubkey(OFF_TOKEN_VAULT_1),
            mint_decimals_0: read_u8(OFF_MINT_DECIMALS_0),
            mint_decimals_1: read_u8(OFF_MINT_DECIMALS_1),
            tick_spacing: read_u16(OFF_TICK_SPACING),
            sqrt_price_x64: read_u128(OFF_SQRT_PRICE_X64),
            tick_current: read_i32(OFF_TICK_CURRENT),
        })
    }
}

// ── Tick array helpers ────────────────────────────────────────────────────

/// Number of ticks per `TickArrayState` account. Constant in Raydium CLMM.
pub const TICK_ARRAY_SIZE: i32 = 60;

/// Start tick index of the `TickArrayState` that contains `tick`.
/// Tick arrays cover `TICK_ARRAY_SIZE * tick_spacing` ticks each, aligned
/// to multiples of that span. For negative ticks we round AWAY from zero
/// so the floor division yields the lower boundary (matches Raydium's
/// `TickArrayState::get_array_start_index`).
pub fn tick_array_start_index(tick: i32, tick_spacing: u16) -> i32 {
    let ticks_in_array = TICK_ARRAY_SIZE * (tick_spacing as i32);
    // floor-div toward negative infinity
    let mut start = tick / ticks_in_array;
    if tick < 0 && tick % ticks_in_array != 0 {
        start -= 1;
    }
    start * ticks_in_array
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tick_array_start_zero() {
        assert_eq!(tick_array_start_index(0, 1), 0);
        assert_eq!(tick_array_start_index(0, 60), 0);
    }

    #[test]
    fn tick_array_start_positive() {
        // spacing=1, array spans 60 ticks each
        assert_eq!(tick_array_start_index(59, 1), 0);
        assert_eq!(tick_array_start_index(60, 1), 60);
        assert_eq!(tick_array_start_index(61, 1), 60);
        // spacing=60, array spans 3600 ticks each
        assert_eq!(tick_array_start_index(3599, 60), 0);
        assert_eq!(tick_array_start_index(3600, 60), 3600);
    }

    #[test]
    fn tick_array_start_negative() {
        // -1 lands in the array starting at -60 (spacing=1)
        assert_eq!(tick_array_start_index(-1, 1), -60);
        assert_eq!(tick_array_start_index(-60, 1), -60);
        assert_eq!(tick_array_start_index(-61, 1), -120);
    }
}

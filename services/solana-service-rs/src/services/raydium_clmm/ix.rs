//! Raydium CLMM Anchor instruction encoders.
//!
//! Each `pub fn` here returns a `solana_sdk::instruction::Instruction` ready
//! to drop into a transaction. The data payload follows the Anchor calling
//! convention:
//!   - 8 bytes: `sha256("global:<method>")[..8]` discriminator
//!   - N bytes: borsh-encoded args in the order declared in the IDL
//!
//! Account orderings come from
//! https://github.com/raydium-io/raydium-clmm/blob/master/programs/amm/src/instructions/
//! and are pinned here as constants in each builder. Off-by-one in the
//! account list = signature-verification failure on-chain, so we comment
//! every position.

use solana_sdk::{
    instruction::{AccountMeta, Instruction},
    pubkey,
    pubkey::Pubkey,
    sysvar,
};

// ── Program & sysvar constants ────────────────────────────────────────────

/// Raydium CLMM program ID on mainnet & devnet (same address).
pub const RAYDIUM_CLMM_PROGRAM_ID: Pubkey = pubkey!("CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK");

/// Token-2022 program (some pools use it; we always pass the classic SPL
/// program for now since USDS/USDC are classic SPL).
pub const TOKEN_PROGRAM_ID: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");

pub const ASSOCIATED_TOKEN_PROGRAM_ID: Pubkey =
    pubkey!("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");

/// Anchor account-namespace discriminator for the legacy NFT metadata.
/// Not needed for `open_position_with_token22_nft` (we use that path).
pub const METAPLEX_METADATA_PROGRAM_ID: Pubkey =
    pubkey!("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s");

// ── Anchor discriminators ─────────────────────────────────────────────────
//
// Pre-computed offline: `sha256("global:<method>")[0..8]` for each method.
// (Computing in code would pull `sha2` into every TX build — and these
// values never change for a given program version.)

/// `open_position_v2(tick_lower, tick_upper, tick_array_lower_start_index,
/// tick_array_upper_start_index, liquidity, amount_0_max, amount_1_max,
/// with_metadata, base_flag)`
///
/// Source: raydium-clmm program, instruction order.
/// `sha256("global:open_position_v2")[..8]` = `[77, 184, 74, 214, 112, 86, 241, 199]`
pub const OPEN_POSITION_V2_DISCRIMINATOR: [u8; 8] = [77, 184, 74, 214, 112, 86, 241, 199];

/// `create_tick_array(tick_array_start_index: i32)` — initializes a
/// `TickArrayState` PDA. Required before `open_position_v2` if the
/// position's range spans a tick array that has never been used (no
/// prior LP has touched those ticks on this pool).
/// `sha256("global:create_tick_array")[..8]`
///   = `[253, 248, 163, 171, 253, 8, 8, 81]`
pub const CREATE_TICK_ARRAY_DISCRIMINATOR: [u8; 8] = [253, 248, 163, 171, 253, 8, 8, 81];

// ── PDA derivation helpers ────────────────────────────────────────────────

/// Personal position PDA — seeds = `["position", position_nft_mint]`.
pub fn personal_position_pda(position_nft_mint: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[b"position", position_nft_mint.as_ref()],
        &RAYDIUM_CLMM_PROGRAM_ID,
    )
    .0
}

/// Protocol position PDA — seeds = `["position", pool_id, tick_lower(BE i32), tick_upper(BE i32)]`.
pub fn protocol_position_pda(pool_id: &Pubkey, tick_lower: i32, tick_upper: i32) -> Pubkey {
    Pubkey::find_program_address(
        &[
            b"position",
            pool_id.as_ref(),
            &tick_lower.to_be_bytes(),
            &tick_upper.to_be_bytes(),
        ],
        &RAYDIUM_CLMM_PROGRAM_ID,
    )
    .0
}

/// Tick-array PDA — seeds = `["tick_array", pool_id, start_tick_index(BE i32)]`.
pub fn tick_array_pda(pool_id: &Pubkey, start_tick_index: i32) -> Pubkey {
    Pubkey::find_program_address(
        &[
            b"tick_array",
            pool_id.as_ref(),
            &start_tick_index.to_be_bytes(),
        ],
        &RAYDIUM_CLMM_PROGRAM_ID,
    )
    .0
}

// ── open_position_v2 builder ──────────────────────────────────────────────

/// All inputs needed to build the `open_position_v2` instruction.
/// The caller is responsible for: choosing the tick bounds, computing
/// liquidity from amounts (use `solana_clmm_raydium::liquidity_math`),
/// and setting slippage envelopes (`amount_0_max`, `amount_1_max`).
#[derive(Debug, Clone)]
pub struct OpenPositionV2Inputs {
    pub payer: Pubkey,
    pub position_nft_owner: Pubkey,
    /// Freshly-generated keypair public key — caller signs the TX with the
    /// matching secret. The CLMM program initializes this mint.
    pub position_nft_mint: Pubkey,
    pub position_nft_account: Pubkey, // ATA of mint for owner
    pub pool_id: Pubkey,
    pub user_token_account_0: Pubkey,
    pub user_token_account_1: Pubkey,
    pub token_vault_0: Pubkey,
    pub token_vault_1: Pubkey,
    pub tick_lower_index: i32,
    pub tick_upper_index: i32,
    pub tick_array_lower_start_index: i32,
    pub tick_array_upper_start_index: i32,
    pub liquidity: u128,
    pub amount_0_max: u64,
    pub amount_1_max: u64,
    pub with_metadata: bool,
    /// `Option<bool>` per Anchor IDL — `None` means "use the liquidity
    /// arg as-is", `Some(true)` means "treat amount_0_max as the source"
    /// (single-sided), `Some(false)` means "use amount_1_max".
    pub base_flag: Option<bool>,
}

/// Build the `open_position_v2` instruction. Pure function — no I/O.
pub fn open_position_v2(inputs: &OpenPositionV2Inputs) -> Instruction {
    // Derive PDAs the program will check against.
    let protocol_position = protocol_position_pda(
        &inputs.pool_id,
        inputs.tick_lower_index,
        inputs.tick_upper_index,
    );
    let personal_position = personal_position_pda(&inputs.position_nft_mint);
    let tick_array_lower = tick_array_pda(&inputs.pool_id, inputs.tick_array_lower_start_index);
    let tick_array_upper = tick_array_pda(&inputs.pool_id, inputs.tick_array_upper_start_index);

    // Account order — pinned to Raydium's IDL. Changing positions = on-chain failure.
    //  0  payer                              (signer, mut)
    //  1  position_nft_owner                 (read)
    //  2  position_nft_mint                  (signer, mut) — fresh keypair
    //  3  position_nft_account               (mut) — ATA created in this ix
    //  4  metadata_account                   (mut, optional — only if with_metadata)
    //  5  pool_state                         (mut)
    //  6  protocol_position                  (mut)
    //  7  tick_array_lower                   (mut)
    //  8  tick_array_upper                   (mut)
    //  9  personal_position                  (mut)
    // 10  token_account_0 (user)             (mut)
    // 11  token_account_1 (user)             (mut)
    // 12  token_vault_0 (pool)               (mut)
    // 13  token_vault_1 (pool)               (mut)
    // 14  rent                               (read)
    // 15  system_program                     (read)
    // 16  token_program                      (read)
    // 17  associated_token_program           (read)
    // 18  metadata_program                   (read, optional)
    let mut accounts = vec![
        AccountMeta::new(inputs.payer, true),
        AccountMeta::new_readonly(inputs.position_nft_owner, false),
        AccountMeta::new(inputs.position_nft_mint, true),
        AccountMeta::new(inputs.position_nft_account, false),
    ];

    if inputs.with_metadata {
        // Metaplex metadata PDA — seeds = ["metadata", program_id, mint]
        let (metadata_account, _) = Pubkey::find_program_address(
            &[
                b"metadata",
                METAPLEX_METADATA_PROGRAM_ID.as_ref(),
                inputs.position_nft_mint.as_ref(),
            ],
            &METAPLEX_METADATA_PROGRAM_ID,
        );
        accounts.push(AccountMeta::new(metadata_account, false));
    }

    accounts.extend_from_slice(&[
        AccountMeta::new(inputs.pool_id, false),
        AccountMeta::new(protocol_position, false),
        AccountMeta::new(tick_array_lower, false),
        AccountMeta::new(tick_array_upper, false),
        AccountMeta::new(personal_position, false),
        AccountMeta::new(inputs.user_token_account_0, false),
        AccountMeta::new(inputs.user_token_account_1, false),
        AccountMeta::new(inputs.token_vault_0, false),
        AccountMeta::new(inputs.token_vault_1, false),
        AccountMeta::new_readonly(sysvar::rent::ID, false),
        AccountMeta::new_readonly(solana_sdk::system_program::ID, false),
        AccountMeta::new_readonly(TOKEN_PROGRAM_ID, false),
        AccountMeta::new_readonly(ASSOCIATED_TOKEN_PROGRAM_ID, false),
    ]);

    if inputs.with_metadata {
        accounts.push(AccountMeta::new_readonly(
            METAPLEX_METADATA_PROGRAM_ID,
            false,
        ));
    }

    // Encode data: 8-byte discriminator + borsh-packed args.
    // Layout (matches Raydium IDL `open_position_v2`):
    //   i32 tick_lower_index
    //   i32 tick_upper_index
    //   i32 tick_array_lower_start_index
    //   i32 tick_array_upper_start_index
    //   u128 liquidity
    //   u64 amount_0_max
    //   u64 amount_1_max
    //   bool with_metadata        — Anchor IDL marks this as `bool`, not `Option`
    //   Option<bool> base_flag    — 1-byte tag (0 = None, 1 = Some) + bool body
    let mut data = Vec::with_capacity(8 + 4 + 4 + 4 + 4 + 16 + 8 + 8 + 1 + 2);
    data.extend_from_slice(&OPEN_POSITION_V2_DISCRIMINATOR);
    data.extend_from_slice(&inputs.tick_lower_index.to_le_bytes());
    data.extend_from_slice(&inputs.tick_upper_index.to_le_bytes());
    data.extend_from_slice(&inputs.tick_array_lower_start_index.to_le_bytes());
    data.extend_from_slice(&inputs.tick_array_upper_start_index.to_le_bytes());
    data.extend_from_slice(&inputs.liquidity.to_le_bytes());
    data.extend_from_slice(&inputs.amount_0_max.to_le_bytes());
    data.extend_from_slice(&inputs.amount_1_max.to_le_bytes());
    data.push(if inputs.with_metadata { 1 } else { 0 });
    match inputs.base_flag {
        None => data.push(0),
        Some(b) => {
            data.push(1);
            data.push(if b { 1 } else { 0 });
        }
    }

    Instruction {
        program_id: RAYDIUM_CLMM_PROGRAM_ID,
        accounts,
        data,
    }
}

// ── create_tick_array builder ────────────────────────────────────────────

/// Build the `create_tick_array` instruction. Initialises the
/// `TickArrayState` PDA for the given `tick_array_start_index` so
/// subsequent `open_position_v2` calls whose range spans this array
/// don't fail with Anchor error 3007 (`AccountDidNotDeserialize`).
///
/// Account order — pinned to Raydium's IDL:
///   0  signer / payer       (signer, mut)
///   1  pool_state           (read)
///   2  tick_array           (mut) — the PDA being initialised
///   3  system_program       (read)
pub fn create_tick_array(
    payer: Pubkey,
    pool_id: Pubkey,
    tick_array_start_index: i32,
) -> Instruction {
    let tick_array = tick_array_pda(&pool_id, tick_array_start_index);
    let accounts = vec![
        AccountMeta::new(payer, true),
        AccountMeta::new_readonly(pool_id, false),
        AccountMeta::new(tick_array, false),
        AccountMeta::new_readonly(solana_sdk::system_program::ID, false),
    ];
    let mut data = Vec::with_capacity(8 + 4);
    data.extend_from_slice(&CREATE_TICK_ARRAY_DISCRIMINATOR);
    data.extend_from_slice(&tick_array_start_index.to_le_bytes());
    Instruction {
        program_id: RAYDIUM_CLMM_PROGRAM_ID,
        accounts,
        data,
    }
}

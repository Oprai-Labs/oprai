use base64::Engine;
use borsh::BorshSerialize;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use solana_sdk::{
    instruction::{AccountMeta, Instruction},
    message::Message,
    pubkey::Pubkey,
    signature::{Keypair, Signer},
    transaction::Transaction,
};
use std::str::FromStr;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;

// ──────────────────────────────────────────────────────────────────────────────
// Constants (from pumpdotfun-sdk)
// ──────────────────────────────────────────────────────────────────────────────

/// Pump.fun bonding curve program
const PUMP_FUN_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";

/// Pump.fun AMM program (PumpSwap — graduated tokens)
const PUMP_AMM_PROGRAM_ID: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";

/// Mayhem program (for create_v2 fee routing)
const MAYHEM_PROGRAM_ID: &str = "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e";

/// Mayhem global params PDA
const GLOBAL_PARAMS_ACCOUNT: &str = "13ec7XdrjF3h3YcqBTFDSReRcUFwbCnJaAQspM4j6DDJ";

/// Mayhem sol vault
const SOL_VAULT: &str = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s";

/// Token-2022 program (new pump.fun tokens use Token-2022, not legacy SPL Token)
const TOKEN_2022_PROGRAM_ID: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";

/// Metaplex Token Metadata program (legacy create only)
const MPL_TOKEN_METADATA: &str = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s";

/// Pump.fun event authority PDA
const EVENT_AUTHORITY: &str = "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1";

/// Pump fee program (volume tracking + fee config)
const PUMP_FEE_PROGRAM_ID: &str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ";

/// Fee authority bytes embedded in fee_config PDA seeds (from pump.json IDL)
const PUMP_FEE_AUTHORITY: [u8; 32] = [
    1, 86, 224, 246, 147, 102, 90, 207, 68, 219, 21, 104, 191, 23, 91, 170,
    81, 137, 203, 151, 245, 210, 255, 59, 101, 93, 43, 182, 253, 109, 24, 176,
];

/// Pump.fun frontend REST API (v3)
const PUMP_FUN_API: &str = "https://frontend-api-v3.pump.fun";

/// Wrapped SOL mint
const SOL_MINT: &str = "So11111111111111111111111111111111111111112";

const PUMP_FUN_CREATE_FEE: f64 = 0.02;

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/// Launch token parameters (mirrors @oprai/types LaunchTokenParams).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LaunchTokenParams {
    pub name: String,
    pub symbol: String,
    pub description: String,
    /// Public HTTP URL of the token image (already uploaded to our own storage).
    #[serde(default)]
    pub image_url: Option<String>,
    /// Pre-hosted metadata JSON URI (already uploaded to /upload/metadata).
    /// When provided, this is used directly as the on-chain URI — no IPFS needed.
    /// When absent, falls back to building a metadata URI from other fields.
    #[serde(default)]
    pub metadata_uri: Option<String>,
    #[serde(default)]
    pub twitter: Option<String>,
    #[serde(default)]
    pub telegram: Option<String>,
    #[serde(default)]
    pub website: Option<String>,
    #[serde(default)]
    pub initial_buy_amount: Option<String>,
    /// Slippage for initial buy in percent (e.g. 10 = 10%). Default: 10.
    #[serde(default)]
    pub slippage: Option<f64>,
    /// Priority fee in SOL (e.g. 0.0005). Converted to microlamports/CU.
    #[serde(default)]
    pub priority_fee: Option<f64>,
    #[serde(default)]
    pub mayhem_mode: Option<String>,
    #[serde(default)]
    pub cashback: Option<String>,
    #[serde(default)]
    pub banner_url: Option<String>,
}

/// Parameters for a pumpfun buy or sell transaction (trade-local via PumpPortal).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PumpFunTradeParams {
    pub mint: String,
    pub amount: String,
    #[serde(default)]
    pub denominated_in_sol: Option<bool>,
    #[serde(default)]
    pub slippage: Option<f64>,
    #[serde(default)]
    pub priority_fee: Option<f64>,
}

/// Validate trade params (buy or sell) before building the PumpPortal transaction.
pub fn validate_pumpfun_trade_params(params: &PumpFunTradeParams) -> Result<(), AppError> {
    if params.mint.trim().is_empty() {
        return Err(AppError::InvalidParams("mint address is required".into()));
    }
    if params.mint.len() < 32 || params.mint.len() > 44 {
        return Err(AppError::InvalidParams(
            "Invalid mint address: must be 32–44 base58 characters".into(),
        ));
    }
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("amount must be a valid number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("amount must be greater than zero".into()));
    }
    if let Some(slippage) = params.slippage {
        if slippage < 0.0 || slippage > 100.0 {
            return Err(AppError::InvalidParams(
                "slippage must be between 0 and 100 percent".into(),
            ));
        }
    }
    if let Some(fee) = params.priority_fee {
        if fee < 0.0 {
            return Err(AppError::InvalidParams(
                "priorityFee must be non-negative".into(),
            ));
        }
    }
    Ok(())
}

/// Preview for token launch.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LaunchPreview {
    pub id: String,
    #[serde(rename = "type")]
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    pub params: LaunchTokenParams,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

/// Result from building a launch token transaction.
pub struct LaunchBuildResult {
    pub transaction_base64: String,
    pub preview: LaunchPreview,
}

// ──────────────────────────────────────────────────────────────────────────────
// Pump.fun Create Instruction (Anchor IDL based - from pumpdotfun-sdk)
// ──────────────────────────────────────────────────────────────────────────────

/// Create instruction data structure (Anchor borsh serialized)
#[derive(BorshSerialize)]
struct CreateArgs {
    name: String,
    symbol: String,
    uri: String,
    creator: Pubkey,
}

/// Anchor discriminator for "global:create" (legacy SPL Token, kept for reference)
const CREATE_DISCRIMINATOR: [u8; 8] = [24, 30, 200, 40, 5, 28, 7, 119];

/// Anchor discriminator for "global:buy"  (sha256("global:buy")[0..8])
const BUY_DISCRIMINATOR: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];

/// Anchor discriminator for "global:sell"  (sha256("global:sell")[0..8])
/// Used by BOTH the bonding-curve program and the PumpSwap AMM program.
const SELL_DISCRIMINATOR: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];

/// Pump.fun OptionBool — single-byte value used in buy/create_v2 args.
/// 0=None, 1=Some(false), 2=Some(true). Serializes as a single u8.
struct OptionBool(u8);

impl borsh::BorshSerialize for OptionBool {
    fn serialize<W: borsh::io::Write>(&self, writer: &mut W) -> borsh::io::Result<()> {
        writer.write_all(&[self.0])
    }
}

impl OptionBool {
    const None: OptionBool = OptionBool(0);
}

/// create_v2 args (Token-2022, Mayhem mode) — from pump.json IDL
#[derive(BorshSerialize)]
struct CreateV2Args {
    name: String,
    symbol: String,
    uri: String,
    creator: Pubkey,
    is_mayhem_mode: bool,
    is_cashback_enabled: OptionBool,
}

/// create_v2 discriminator (from pump.json IDL — NOT sha256-derived)
const CREATE_V2_DISCRIMINATOR: [u8; 8] = [214, 144, 76, 236, 95, 139, 49, 180];

/// Buy instruction args — from pump.json IDL (bonding curve)
#[derive(BorshSerialize)]
struct BuyArgs {
    amount: u64,
    max_sol_cost: u64,
    track_volume: OptionBool,
}

/// Sell instruction args — from pump.json IDL (bonding curve).
/// NOTE: sell has NO track_volume field (unlike buy).
#[derive(BorshSerialize)]
struct SellArgs {
    amount: u64,
    min_sol_output: u64,
}

/// PumpSwap AMM buy args — from pump_amm.json IDL
#[derive(BorshSerialize)]
struct PumpSwapBuyArgs {
    base_amount_out: u64,
    max_quote_amount_in: u64,
    track_volume: OptionBool,
}

/// PumpSwap AMM sell args — from pump_amm.json IDL.
/// NOTE: sell has NO track_volume field (unlike buy).
#[derive(BorshSerialize)]
struct PumpSwapSellArgs {
    base_amount_in: u64,
    min_quote_amount_out: u64,
}

// ──────────────────────────────────────────────────────────────────────────────
// PDA Derivation Functions (from pumpdotfun-sdk)
// ──────────────────────────────────────────────────────────────────────────────

/// Find PDA for bonding curve account: ["bonding-curve", mint]
fn find_bonding_curve_pda(mint: &Pubkey) -> Pubkey {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).expect("valid hardcoded address");
    Pubkey::find_program_address(&[b"bonding-curve", mint.as_ref()], &program_id).0
}

/// Find PDA for mint authority: ["mint-authority"]
fn find_mint_authority_pda() -> Pubkey {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).expect("valid hardcoded address");
    Pubkey::find_program_address(&[b"mint-authority"], &program_id).0
}

/// Find PDA for global account: ["global"]
fn find_global_pda() -> Pubkey {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).expect("valid hardcoded address");
    Pubkey::find_program_address(&[b"global"], &program_id).0
}

/// Find PDA for creator vault: ["creator-vault", creator]
fn find_creator_vault_pda(creator: &Pubkey) -> Pubkey {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).expect("valid hardcoded address");
    Pubkey::find_program_address(&[b"creator-vault", creator.as_ref()], &program_id).0
}

/// Find PDA for metadata account (derived from Metaplex Token Metadata program)
fn find_metadata_pda(mint: &Pubkey) -> Pubkey {
    let mpl_program = Pubkey::from_str(MPL_TOKEN_METADATA).expect("valid MPL_TOKEN_METADATA");
    Pubkey::find_program_address(
        &[b"metadata", mpl_program.as_ref(), mint.as_ref()],
        &mpl_program,
    )
    .0
}

/// Token-2022 program pubkey (new tokens use Token-2022, not legacy SPL Token)
fn token_2022_program() -> Pubkey {
    Pubkey::from_str(TOKEN_2022_PROGRAM_ID).expect("valid TOKEN_2022_PROGRAM_ID")
}

/// Get Token-2022 ATA for bonding curve (create_v2 tokens use Token-2022)
fn get_associated_bonding_curve(mint: &Pubkey) -> Pubkey {
    let bonding_curve = find_bonding_curve_pda(mint);
    spl_associated_token_account::get_associated_token_address_with_program_id(
        &bonding_curve,
        mint,
        &token_2022_program(),
    )
}

/// Find PDA for Mayhem state: ["mayhem-state", mint]
fn find_mayhem_state_pda(mint: &Pubkey) -> Pubkey {
    let mayhem = Pubkey::from_str(MAYHEM_PROGRAM_ID).expect("valid MAYHEM_PROGRAM_ID");
    Pubkey::find_program_address(&[b"mayhem-state", mint.as_ref()], &mayhem).0
}

/// Find PDA for global volume accumulator: ["global_volume_accumulator"]
fn find_global_volume_accumulator_pda() -> Pubkey {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).expect("valid PUMP_FUN_PROGRAM_ID");
    Pubkey::find_program_address(&[b"global_volume_accumulator"], &program_id).0
}

/// Find PDA for user volume accumulator: ["user_volume_accumulator", user]
fn find_user_volume_accumulator_pda(user: &Pubkey) -> Pubkey {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).expect("valid PUMP_FUN_PROGRAM_ID");
    Pubkey::find_program_address(&[b"user_volume_accumulator", user.as_ref()], &program_id).0
}

/// Find fee_config PDA — seeds: ["fee_config", fee_authority_bytes], derived from fee_program
fn find_fee_config_pda() -> Pubkey {
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID).expect("valid PUMP_FEE_PROGRAM_ID");
    Pubkey::find_program_address(&[b"fee_config", &PUMP_FEE_AUTHORITY], &fee_program).0
}

// ──────────────────────────────────────────────────────────────────────────────
// PumpSwap AMM — PDA helpers
// ──────────────────────────────────────────────────────────────────────────────

/// PumpSwap global config PDA (stores protocol_fee_recipient and other settings)
/// seeds: ["global_config"], program: PUMP_AMM_PROGRAM_ID
fn find_pumpswap_global_config_pda() -> Pubkey {
    let amm = Pubkey::from_str(PUMP_AMM_PROGRAM_ID).expect("valid");
    Pubkey::find_program_address(&[b"global_config"], &amm).0
}

/// PumpSwap event authority PDA (standard Anchor CPI event pattern)
/// seeds: ["__event_authority"], program: PUMP_AMM_PROGRAM_ID
fn find_pumpswap_event_authority_pda() -> Pubkey {
    let amm = Pubkey::from_str(PUMP_AMM_PROGRAM_ID).expect("valid");
    Pubkey::find_program_address(&[b"__event_authority"], &amm).0
}

/// PumpSwap coin creator vault authority PDA
/// seeds: ["coin_creator_vault", creator], program: PUMP_AMM_PROGRAM_ID
fn find_pumpswap_coin_creator_vault(creator: &Pubkey) -> Pubkey {
    let amm = Pubkey::from_str(PUMP_AMM_PROGRAM_ID).expect("valid");
    Pubkey::find_program_address(&[b"coin_creator_vault", creator.as_ref()], &amm).0
}

/// PumpSwap pool PDA — pool_index 0, seeded with coin_creator, base_mint, quote_mint
/// seeds: ["pool", index_u16_le, coin_creator, base_mint, quote_mint], program: PUMP_AMM_PROGRAM_ID
fn find_pumpswap_pool_pda(creator: &Pubkey, base_mint: &Pubkey, quote_mint: &Pubkey) -> Pubkey {
    let amm = Pubkey::from_str(PUMP_AMM_PROGRAM_ID).expect("valid");
    Pubkey::find_program_address(
        &[b"pool", &0u16.to_le_bytes(), creator.as_ref(), base_mint.as_ref(), quote_mint.as_ref()],
        &amm,
    ).0
}

/// PumpSwap global volume accumulator PDA
/// seeds: ["global_volume_accumulator"], program: PUMP_AMM_PROGRAM_ID
fn find_pumpswap_global_volume_accumulator() -> Pubkey {
    let amm = Pubkey::from_str(PUMP_AMM_PROGRAM_ID).expect("valid");
    Pubkey::find_program_address(&[b"global_volume_accumulator"], &amm).0
}

/// PumpSwap user volume accumulator PDA
/// seeds: ["user_volume_accumulator", user], program: PUMP_AMM_PROGRAM_ID
fn find_pumpswap_user_volume_accumulator(user: &Pubkey) -> Pubkey {
    let amm = Pubkey::from_str(PUMP_AMM_PROGRAM_ID).expect("valid");
    Pubkey::find_program_address(&[b"user_volume_accumulator", user.as_ref()], &amm).0
}

/// Known PumpSwap global_config PDA address (derived from ["global_config"] + AMM program).
/// Hardcoded to avoid recomputing on every call.
const PUMP_AMM_GLOBAL_CONFIG_ADDRESS: &str = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw";

/// Fetch the first protocol_fee_recipient from PumpSwap's GlobalConfig account on-chain.
///
/// GlobalConfig layout (from pump_amm.json IDL + on-chain verification):
///   [8]  discriminator
///   [32] admin: Pubkey
///   [8]  lp_fee_basis_points: u64
///   [8]  protocol_fee_basis_points: u64
///   [8×32] protocol_fee_recipients: [Pubkey; 8]   ← offset 56
///   ...
///
/// The buy/sell instruction accepts any of the 8 recipients; we use index 0.
async fn fetch_pumpswap_fee_recipient(rpc: &SolanaRpc) -> Result<Pubkey, AppError> {
    let global_config = Pubkey::from_str(PUMP_AMM_GLOBAL_CONFIG_ADDRESS)
        .expect("valid PUMP_AMM_GLOBAL_CONFIG_ADDRESS");
    let rpc = rpc.clone();
    let account = tokio::task::spawn_blocking(move || rpc.client().get_account(&global_config))
        .await
        .map_err(|e| AppError::Internal(format!("spawn error fetching global_config: {e}")))?
        .map_err(|e| AppError::Internal(format!("RPC error fetching global_config: {e}")))?;

    // offset = 8 (disc) + 32 (admin) + 8 (lp_fee) + 8 (proto_fee) = 56
    const FEE_RECIPIENTS_OFFSET: usize = 56;
    if account.data.len() < FEE_RECIPIENTS_OFFSET + 32 {
        return Err(AppError::Internal("GlobalConfig account data too short".into()));
    }
    Pubkey::try_from(&account.data[FEE_RECIPIENTS_OFFSET..FEE_RECIPIENTS_OFFSET + 32])
        .map_err(|_| AppError::Internal("Failed to parse protocol_fee_recipient from GlobalConfig".into()))
}

/// Fetch real pool reserves from on-chain token accounts for accurate PumpSwap pricing.
///
/// Returns (base_reserve_lamports, quote_reserve_lamports) — the actual token balances
/// held by the pool's ATAs. These are more accurate than API virtual reserves for
/// graduated tokens where the bonding curve values are stale.
async fn fetch_pumpswap_pool_reserves(
    rpc: &SolanaRpc,
    pool: &Pubkey,
    base_mint: &Pubkey,
) -> Result<(u64, u64), AppError> {
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid");
    let t22 = token_2022_program();
    let spl_tok = spl_token::id();

    let pool_base_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        pool, base_mint, &t22,
    );
    let pool_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        pool, &sol_mint, &spl_tok,
    );

    let rpc2 = rpc.clone();
    let base_balance = tokio::task::spawn_blocking(move || {
        rpc2.client().get_token_account_balance(&pool_base_ata)
    })
    .await
    .map_err(|e| AppError::Internal(format!("spawn error fetching base reserve: {e}")))?
    .map_err(|e| AppError::Internal(format!("RPC error fetching base reserve: {e}")))?;

    let rpc3 = rpc.clone();
    let quote_balance = tokio::task::spawn_blocking(move || {
        rpc3.client().get_token_account_balance(&pool_quote_ata)
    })
    .await
    .map_err(|e| AppError::Internal(format!("spawn error fetching quote reserve: {e}")))?
    .map_err(|e| AppError::Internal(format!("RPC error fetching quote reserve: {e}")))?;

    let base_amount: u64 = base_balance.amount.parse()
        .map_err(|_| AppError::Internal("Failed to parse base reserve amount".into()))?;
    let quote_amount: u64 = quote_balance.amount.parse()
        .map_err(|_| AppError::Internal("Failed to parse quote reserve amount".into()))?;

    Ok((base_amount, quote_amount))
}

/// Fee authority bytes for PumpSwap AMM fee_config PDA — DIFFERENT from bonding curve.
/// From pump_amm.json IDL fee_config seeds.
const PUMP_AMM_FEE_AUTHORITY: [u8; 32] = [
    12, 20, 222, 252, 130, 94, 198, 118, 148, 37, 8, 24, 187, 101, 64, 101,
    244, 41, 141, 49, 86, 213, 113, 180, 212, 248, 9, 12, 24, 233, 168, 99,
];

/// Find fee_config PDA for PumpSwap AMM — uses a different authority than the bonding curve.
fn find_pumpswap_fee_config_pda() -> Pubkey {
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID).expect("valid PUMP_FEE_PROGRAM_ID");
    Pubkey::find_program_address(&[b"fee_config", &PUMP_AMM_FEE_AUTHORITY], &fee_program).0
}

// ──────────────────────────────────────────────────────────────────────────────
// Build create_v2 Instruction (Token-2022, Mayhem mode — current pump.fun)
// ──────────────────────────────────────────────────────────────────────────────

fn build_create_v2_instruction(
    creator: &Pubkey,
    mint: &Pubkey,
    name: &str,
    symbol: &str,
    uri: &str,
    is_mayhem_mode: bool,
) -> Result<Instruction, AppError> {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid program ID: {e}")))?;
    let event_authority = Pubkey::from_str(EVENT_AUTHORITY)
        .map_err(|e| AppError::Internal(format!("Invalid event authority: {e}")))?;
    let mayhem_program = Pubkey::from_str(MAYHEM_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid mayhem program: {e}")))?;
    let global_params = Pubkey::from_str(GLOBAL_PARAMS_ACCOUNT)
        .map_err(|e| AppError::Internal(format!("Invalid global params: {e}")))?;
    let sol_vault = Pubkey::from_str(SOL_VAULT)
        .map_err(|e| AppError::Internal(format!("Invalid sol vault: {e}")))?;
    let t22 = token_2022_program();

    let bonding_curve = find_bonding_curve_pda(mint);
    let mint_authority = find_mint_authority_pda();
    let global = find_global_pda();
    let assoc_bonding_curve = get_associated_bonding_curve(mint);
    let mayhem_state = find_mayhem_state_pda(mint);
    let mayhem_token_vault = spl_associated_token_account::get_associated_token_address_with_program_id(
        &sol_vault, mint, &t22,
    );

    let args = CreateV2Args {
        name: name.to_string(),
        symbol: symbol.to_uppercase(),
        uri: uri.to_string(),
        creator: *creator,
        is_mayhem_mode,
        is_cashback_enabled: OptionBool::None,
    };
    let mut data = Vec::with_capacity(8 + 200);
    data.extend_from_slice(&CREATE_V2_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize create_v2: {e}")))?;

    let accounts = vec![
        AccountMeta::new(*mint, true),                                          // 0 mint
        AccountMeta::new_readonly(mint_authority, false),                       // 1 mint_authority (NOT writable per IDL)
        AccountMeta::new(bonding_curve, false),                                 // 2 bonding_curve
        AccountMeta::new(assoc_bonding_curve, false),                           // 3 assoc_bonding_curve (Token-2022 ATA)
        AccountMeta::new_readonly(global, false),                               // 4 global
        AccountMeta::new(*creator, true),                                       // 5 user/creator
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false),     // 6 system_program
        AccountMeta::new_readonly(t22, false),                                  // 7 token_2022_program
        AccountMeta::new_readonly(spl_associated_token_account::id(), false),   // 8 associated_token_program
        AccountMeta::new(mayhem_program, false),                                // 9 mayhem_program (writable)
        AccountMeta::new_readonly(global_params, false),                        // 10 global_params
        AccountMeta::new(sol_vault, false),                                     // 11 sol_vault (writable)
        AccountMeta::new(mayhem_state, false),                                  // 12 mayhem_state
        AccountMeta::new(mayhem_token_vault, false),                            // 13 mayhem_token_vault
        AccountMeta::new_readonly(event_authority, false),                      // 14 event_authority
        AccountMeta::new_readonly(program_id, false),                           // 15 program
    ];

    Ok(Instruction { program_id, accounts, data })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Create Instruction (legacy, kept for reference)
// ──────────────────────────────────────────────────────────────────────────────

fn build_create_instruction(
    creator: &Pubkey,
    mint: &Pubkey,
    name: &str,
    symbol: &str,
    uri: &str,
) -> Result<Instruction, AppError> {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid program ID: {}", e)))?;

    let mpl_token_metadata = Pubkey::from_str(MPL_TOKEN_METADATA)
        .map_err(|e| AppError::Internal(format!("Invalid MPL program ID: {}", e)))?;

    let event_authority = Pubkey::from_str(EVENT_AUTHORITY)
        .map_err(|e| AppError::Internal(format!("Invalid event authority: {}", e)))?;

    // Derive PDAs
    let bonding_curve = find_bonding_curve_pda(mint);
    let mint_authority = find_mint_authority_pda();
    let global = find_global_pda();
    let metadata = find_metadata_pda(mint);
    let associated_bonding_curve = get_associated_bonding_curve(mint);

    // Build instruction data: discriminator + args
    let args = CreateArgs {
        name: name.to_string(),
        symbol: symbol.to_uppercase(),
        uri: uri.to_string(),
        creator: *creator,
    };

    let mut data = Vec::with_capacity(8 + 150);
    data.extend_from_slice(&CREATE_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize instruction: {}", e)))?;

    // Build accounts list (14 accounts)
    let accounts = vec![
        AccountMeta::new(*mint, true),                    // 0: mint (signer, writable)
        AccountMeta::new(mint_authority, false),          // 1: mint_authority_pda (writable)
        AccountMeta::new(bonding_curve, false),           // 2: bonding_curve (writable)
        AccountMeta::new(associated_bonding_curve, false), // 3: associated_bonding_curve (writable)
        AccountMeta::new(global, false),                  // 4: global_pda (writable)
        AccountMeta::new_readonly(mpl_token_metadata, false), // 5: mpl_token_metadata (readonly)
        AccountMeta::new(metadata, false),                // 6: metadata_pda (writable)
        AccountMeta::new(*creator, true),                 // 7: user (signer, writable)
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 8: system_program
        AccountMeta::new_readonly(spl_token::id(), false), // 9: token_program
        AccountMeta::new_readonly(spl_associated_token_account::id(), false), // 10: associated_token_program
        AccountMeta::new_readonly(solana_sdk::sysvar::rent::id(), false), // 11: rent_sysvar
        AccountMeta::new_readonly(event_authority, false), // 12: event_authority (readonly)
        AccountMeta::new_readonly(program_id, false),     // 13: pump_fun_program (readonly)
    ];

    Ok(Instruction {
        program_id,
        accounts,
        data,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Buy Instruction (for initial buy)
// ──────────────────────────────────────────────────────────────────────────────

/// Fallback fee recipient for bonding curve (used if on-chain fetch fails).
const PUMP_FUN_FEE_RECIPIENT_FALLBACK: &str = "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM";

/// Fetch the bonding curve fee_recipient from the pump.fun global account on-chain.
///
/// Global account layout (from pump.json IDL `Global` struct):
///   [8]  discriminator
///   [1]  initialized: bool       — offset 8
///   [32] authority: Pubkey       — offset 9
///   [32] fee_recipient: Pubkey   — offset 41
///   ...
///
/// Fallback to hardcoded value on RPC failure.
async fn fetch_pumpfun_fee_recipient(rpc: &SolanaRpc) -> Pubkey {
    let global = find_global_pda();
    let rpc2 = rpc.clone();
    let result = tokio::task::spawn_blocking(move || rpc2.client().get_account(&global))
        .await;

    match result {
        Ok(Ok(account)) => {
            const FEE_RECIPIENT_OFFSET: usize = 41; // 8 disc + 1 bool + 32 pubkey
            if account.data.len() >= FEE_RECIPIENT_OFFSET + 32 {
                if let Ok(pk) = Pubkey::try_from(&account.data[FEE_RECIPIENT_OFFSET..FEE_RECIPIENT_OFFSET + 32]) {
                    return pk;
                }
            }
            tracing::warn!("pump.fun global account data too short or invalid, using fallback fee_recipient");
        }
        Ok(Err(e)) => tracing::warn!(error = %e, "RPC error fetching pump.fun global, using fallback fee_recipient"),
        Err(e) => tracing::warn!(error = %e, "spawn_blocking error fetching pump.fun global, using fallback fee_recipient"),
    }

    Pubkey::from_str(PUMP_FUN_FEE_RECIPIENT_FALLBACK).expect("valid fallback fee_recipient")
}

fn build_buy_instruction(
    buyer: &Pubkey,
    mint: &Pubkey,
    creator: &Pubkey,
    fee_recipient: &Pubkey,
    amount: u64,
    max_sol_cost: u64,
) -> Result<Instruction, AppError> {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid program ID: {}", e)))?;

    let event_authority = Pubkey::from_str(EVENT_AUTHORITY)
        .map_err(|e| AppError::Internal(format!("Invalid event authority: {}", e)))?;

    // Derive PDAs
    let global = find_global_pda();
    let bonding_curve = find_bonding_curve_pda(mint);
    let associated_bonding_curve = get_associated_bonding_curve(mint);
    let creator_vault = find_creator_vault_pda(creator);

    // User's token account (Token-2022)
    let user_token_account = spl_associated_token_account::get_associated_token_address_with_program_id(
        buyer, mint, &token_2022_program(),
    );

    // Derive volume/fee PDAs (added Nov 2025 — from pump.json IDL)
    let global_volume_acc = find_global_volume_accumulator_pda();
    let user_volume_acc = find_user_volume_accumulator_pda(buyer);
    let fee_config = find_fee_config_pda();
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid fee program: {e}")))?;

    // Build instruction data: discriminator + args
    let args = BuyArgs {
        amount,
        max_sol_cost,
        track_volume: OptionBool::None,
    };

    let mut data = Vec::with_capacity(8 + 17);
    data.extend_from_slice(&BUY_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize buy instruction: {}", e)))?;

    // Build accounts list (16 accounts — pump.json IDL)
    let accounts = vec![
        AccountMeta::new_readonly(global, false),                              // 0: global
        AccountMeta::new(*fee_recipient, false),                               // 1: fee_recipient
        AccountMeta::new_readonly(*mint, false),                               // 2: mint
        AccountMeta::new(bonding_curve, false),                                // 3: bonding_curve
        AccountMeta::new(associated_bonding_curve, false),                     // 4: associated_bonding_curve
        AccountMeta::new(user_token_account, false),                           // 5: associated_user
        AccountMeta::new(*buyer, true),                                        // 6: user
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false),    // 7: system_program
        AccountMeta::new_readonly(token_2022_program(), false),                // 8: token_program (Token-2022 for create_v2 tokens)
        AccountMeta::new(creator_vault, false),                                // 9: creator_vault
        AccountMeta::new_readonly(event_authority, false),                     // 10: event_authority
        AccountMeta::new_readonly(program_id, false),                          // 11: program
        AccountMeta::new_readonly(global_volume_acc, false),                   // 12: global_volume_accumulator
        AccountMeta::new(user_volume_acc, false),                              // 13: user_volume_accumulator
        AccountMeta::new_readonly(fee_config, false),                          // 14: fee_config
        AccountMeta::new_readonly(fee_program, false),                         // 15: fee_program
    ];

    Ok(Instruction {
        program_id,
        accounts,
        data,
    })
}

/// Build sell instruction for bonding curve.
///
/// 14 accounts per pump.json IDL — different order from buy:
/// creator_vault (#8) comes before token_program (#9).
/// No volume accumulator accounts (unlike buy).
fn build_sell_instruction(
    seller: &Pubkey,
    mint: &Pubkey,
    creator: &Pubkey,
    fee_recipient: &Pubkey,
    amount: u64,
    min_sol_output: u64,
) -> Result<Instruction, AppError> {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid program ID: {e}")))?;
    let event_authority = Pubkey::from_str(EVENT_AUTHORITY)
        .map_err(|e| AppError::Internal(format!("Invalid event authority: {e}")))?;

    let global = find_global_pda();
    let bonding_curve = find_bonding_curve_pda(mint);
    let associated_bonding_curve = get_associated_bonding_curve(mint);
    let creator_vault = find_creator_vault_pda(creator);
    let user_token_account = spl_associated_token_account::get_associated_token_address_with_program_id(
        seller, mint, &token_2022_program(),
    );
    let fee_config = find_fee_config_pda();
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid fee program: {e}")))?;

    let args = SellArgs { amount, min_sol_output };
    let mut data = Vec::with_capacity(8 + 16);
    data.extend_from_slice(&SELL_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize sell: {e}")))?;

    // 14 accounts — note creator_vault (#8) is BEFORE token_program (#9),
    // and there are NO volume accumulator accounts (unlike buy).
    let accounts = vec![
        AccountMeta::new_readonly(global, false),           // 0
        AccountMeta::new(*fee_recipient, false),            // 1
        AccountMeta::new_readonly(*mint, false),            // 2
        AccountMeta::new(bonding_curve, false),             // 3
        AccountMeta::new(associated_bonding_curve, false),  // 4
        AccountMeta::new(user_token_account, false),        // 5
        AccountMeta::new(*seller, true),                    // 6
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 7
        AccountMeta::new(creator_vault, false),             // 8 ← before token_program
        AccountMeta::new_readonly(token_2022_program(), false), // 9
        AccountMeta::new_readonly(event_authority, false),  // 10
        AccountMeta::new_readonly(program_id, false),       // 11
        AccountMeta::new_readonly(fee_config, false),       // 12
        AccountMeta::new_readonly(fee_program, false),      // 13
    ];

    Ok(Instruction { program_id, accounts, data })
}

// ──────────────────────────────────────────────────────────────────────────────
// PumpSwap AMM — Direct Instruction Builders
// ──────────────────────────────────────────────────────────────────────────────

/// Build PumpSwap AMM buy instruction.
///
/// 23 accounts per pump_amm.json IDL.
/// Key differences from bonding curve:
/// - pool address is passed in directly (from API, not computed via PDA)
/// - coin_creator_vault_ata is the wSOL (quote) ATA of vault authority, NOT base token ATA
/// - protocol_fee_recipient is readonly
/// - global_volume_accumulator is readonly
/// - fee_config uses PUMP_AMM_FEE_AUTHORITY (different from bonding curve)
fn build_pumpswap_buy_instruction(
    buyer: &Pubkey,
    pool: &Pubkey,
    base_mint: &Pubkey,
    creator: &Pubkey,
    protocol_fee: &Pubkey,
    base_amount_out: u64,
    max_quote_amount_in: u64,
) -> Result<Instruction, AppError> {
    let amm_program = Pubkey::from_str(PUMP_AMM_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid AMM program: {e}")))?;
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid SOL_MINT");
    let t22 = token_2022_program();
    let spl_tok = spl_token::id();
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid fee program: {e}")))?;

    let pool = *pool;
    let global_config = Pubkey::from_str(PUMP_AMM_GLOBAL_CONFIG_ADDRESS).expect("valid");
    let event_authority = find_pumpswap_event_authority_pda();
    let coin_creator_vault_authority = find_pumpswap_coin_creator_vault(creator);
    let global_vol_acc = find_pumpswap_global_volume_accumulator();
    let user_vol_acc = find_pumpswap_user_volume_accumulator(buyer);
    let fee_config = find_pumpswap_fee_config_pda();

    let user_base_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        buyer, base_mint, &t22,
    );
    let user_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        buyer, &sol_mint, &spl_tok,
    );
    let pool_base_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        &pool, base_mint, &t22,
    );
    let pool_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        &pool, &sol_mint, &spl_tok,
    );
    let protocol_fee_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        protocol_fee, &sol_mint, &spl_tok,
    );
    // coin_creator_vault_ata is the wSOL (quote) ATA of vault authority, NOT the base token ATA
    let coin_creator_vault_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        &coin_creator_vault_authority, &sol_mint, &spl_tok,
    );

    let args = PumpSwapBuyArgs { base_amount_out, max_quote_amount_in, track_volume: OptionBool::None };
    let mut data = Vec::with_capacity(8 + 17);
    data.extend_from_slice(&BUY_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize pumpswap buy: {e}")))?;

    let accounts = vec![
        AccountMeta::new(pool, false),                                      // 0
        AccountMeta::new(*buyer, true),                                     // 1
        AccountMeta::new_readonly(global_config, false),                    // 2
        AccountMeta::new_readonly(*base_mint, false),                       // 3
        AccountMeta::new_readonly(sol_mint, false),                         // 4
        AccountMeta::new(user_base_ata, false),                             // 5
        AccountMeta::new(user_quote_ata, false),                            // 6
        AccountMeta::new(pool_base_ata, false),                             // 7
        AccountMeta::new(pool_quote_ata, false),                            // 8
        AccountMeta::new_readonly(*protocol_fee, false),                    // 9 readonly per IDL
        AccountMeta::new(protocol_fee_quote_ata, false),                    // 10
        AccountMeta::new_readonly(t22, false),                              // 11
        AccountMeta::new_readonly(spl_tok, false),                          // 12
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 13
        AccountMeta::new_readonly(spl_associated_token_account::id(), false), // 14
        AccountMeta::new_readonly(event_authority, false),                  // 15
        AccountMeta::new_readonly(amm_program, false),                      // 16
        AccountMeta::new(coin_creator_vault_ata, false),                    // 17
        AccountMeta::new_readonly(coin_creator_vault_authority, false),     // 18
        AccountMeta::new_readonly(global_vol_acc, false),                   // 19 readonly per IDL
        AccountMeta::new(user_vol_acc, false),                              // 20
        AccountMeta::new_readonly(fee_config, false),                       // 21
        AccountMeta::new_readonly(fee_program, false),                      // 22
    ];

    Ok(Instruction { program_id: amm_program, accounts, data })
}

/// Build PumpSwap AMM sell instruction.
///
/// 21 accounts per pump_amm.json IDL.
/// Differences from buy:
/// - pool address is passed in directly (from API, not computed via PDA)
/// - NO track_volume arg
/// - coin_creator_vault_ata and coin_creator_vault_authority ARE present (same as buy)
/// - NO global_volume_accumulator and NO user_volume_accumulator (unlike buy)
/// - coin_creator_vault_ata is wSOL (quote) ATA of vault authority
fn build_pumpswap_sell_instruction(
    seller: &Pubkey,
    pool: &Pubkey,
    base_mint: &Pubkey,
    creator: &Pubkey,
    protocol_fee: &Pubkey,
    base_amount_in: u64,
    min_quote_amount_out: u64,
) -> Result<Instruction, AppError> {
    let amm_program = Pubkey::from_str(PUMP_AMM_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid AMM program: {e}")))?;
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid SOL_MINT");
    let t22 = token_2022_program();
    let spl_tok = spl_token::id();
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid fee program: {e}")))?;

    let pool = *pool;
    let global_config = Pubkey::from_str(PUMP_AMM_GLOBAL_CONFIG_ADDRESS).expect("valid");
    let event_authority = find_pumpswap_event_authority_pda();
    let coin_creator_vault_authority = find_pumpswap_coin_creator_vault(creator);
    let fee_config = find_pumpswap_fee_config_pda();

    let user_base_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        seller, base_mint, &t22,
    );
    let user_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        seller, &sol_mint, &spl_tok,
    );
    let pool_base_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        &pool, base_mint, &t22,
    );
    let pool_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        &pool, &sol_mint, &spl_tok,
    );
    let protocol_fee_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        protocol_fee, &sol_mint, &spl_tok,
    );
    // coin_creator_vault_ata is wSOL (quote) ATA of vault authority
    let coin_creator_vault_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        &coin_creator_vault_authority, &sol_mint, &spl_tok,
    );

    let args = PumpSwapSellArgs { base_amount_in, min_quote_amount_out };
    let mut data = Vec::with_capacity(8 + 16);
    data.extend_from_slice(&SELL_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize pumpswap sell: {e}")))?;

    let accounts = vec![
        AccountMeta::new(pool, false),                                      // 0
        AccountMeta::new(*seller, true),                                    // 1
        AccountMeta::new_readonly(global_config, false),                    // 2
        AccountMeta::new_readonly(*base_mint, false),                       // 3
        AccountMeta::new_readonly(sol_mint, false),                         // 4
        AccountMeta::new(user_base_ata, false),                             // 5
        AccountMeta::new(user_quote_ata, false),                            // 6
        AccountMeta::new(pool_base_ata, false),                             // 7
        AccountMeta::new(pool_quote_ata, false),                            // 8
        AccountMeta::new_readonly(*protocol_fee, false),                    // 9 readonly per IDL
        AccountMeta::new(protocol_fee_quote_ata, false),                    // 10
        AccountMeta::new_readonly(t22, false),                              // 11
        AccountMeta::new_readonly(spl_tok, false),                          // 12
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 13
        AccountMeta::new_readonly(spl_associated_token_account::id(), false), // 14
        AccountMeta::new_readonly(event_authority, false),                  // 15
        AccountMeta::new_readonly(amm_program, false),                      // 16
        AccountMeta::new(coin_creator_vault_ata, false),                    // 17
        AccountMeta::new_readonly(coin_creator_vault_authority, false),     // 18
        AccountMeta::new_readonly(fee_config, false),                       // 19
        AccountMeta::new_readonly(fee_program, false),                      // 20
    ];

    Ok(Instruction { program_id: amm_program, accounts, data })
}

/// Build Create ATA instruction for Token-2022 (idempotent)
fn build_create_ata_instruction(owner: &Pubkey, mint: &Pubkey) -> Instruction {
    spl_associated_token_account::instruction::create_associated_token_account_idempotent(
        owner,
        owner,
        mint,
        &token_2022_program(),
    )
}

/// Build Create ATA instruction for legacy SPL Token (idempotent, used for wSOL)
fn build_create_spl_ata_instruction(owner: &Pubkey, mint: &Pubkey) -> Instruction {
    spl_associated_token_account::instruction::create_associated_token_account_idempotent(
        owner,
        owner,
        mint,
        &spl_token::id(),
    )
}

/// Build instructions to wrap `lamports` of native SOL into a wSOL token account.
/// Returns: [create_wsol_ata, transfer_sol, sync_native]
fn build_wrap_sol_instructions(owner: &Pubkey, lamports: u64) -> Vec<Instruction> {
    let wsol_mint = Pubkey::from_str(SOL_MINT).expect("valid SOL_MINT");
    let wsol_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        owner, &wsol_mint, &spl_token::id(),
    );
    vec![
        spl_associated_token_account::instruction::create_associated_token_account_idempotent(
            owner, owner, &wsol_mint, &spl_token::id(),
        ),
        solana_sdk::system_instruction::transfer(owner, &wsol_ata, lamports),
        spl_token::instruction::sync_native(&spl_token::id(), &wsol_ata).expect("valid"),
    ]
}

/// Build instruction to close a wSOL token account and reclaim SOL.
fn build_close_wsol_instruction(owner: &Pubkey) -> Instruction {
    let wsol_mint = Pubkey::from_str(SOL_MINT).expect("valid SOL_MINT");
    let wsol_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        owner, &wsol_mint, &spl_token::id(),
    );
    spl_token::instruction::close_account(&spl_token::id(), &wsol_ata, owner, owner, &[])
        .expect("valid close_account instruction")
}

/// Fetch the latest blockhash from the RPC via spawn_blocking.
async fn get_blockhash(rpc: &SolanaRpc) -> Result<solana_sdk::hash::Hash, AppError> {
    let rpc = rpc.clone();
    tokio::task::spawn_blocking(move || rpc.get_latest_blockhash_with_retry())
        .await
        .map_err(|e| AppError::Internal(format!("Spawn blocking error: {e}")))?
        .map_err(|e| AppError::Internal(format!("Failed to get blockhash: {e}")))
}

/// Parse and compute (token_base_units, max_sol_cost_lamports) for a bonding curve buy.
fn parse_buy_amounts(
    params: &PumpFunTradeParams,
    v_sol: u64,
    v_tok: u64,
) -> Result<(u64, u64), AppError> {
    let amount: f64 = params.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a valid number".into()))?;
    let slippage_bps = ((params.slippage.unwrap_or(10.0)) * 100.0) as u64;
    let denominated_in_sol = params.denominated_in_sol.unwrap_or(true);

    if denominated_in_sol {
        let sol_lamports = (amount * 1_000_000_000.0) as u64;
        let tokens = estimate_tokens_for_sol(sol_lamports);
        let max_cost = apply_slippage(sol_lamports, slippage_bps);
        Ok((tokens, max_cost))
    } else {
        // Amount in human-readable tokens → convert to base units (6 decimals)
        let token_base = (amount * PUMP_TOKEN_DECIMALS as f64) as u64;
        let sol_needed = estimate_sol_for_tokens_out(v_sol, v_tok, token_base);
        let max_cost = apply_slippage(sol_needed, slippage_bps);
        Ok((token_base, max_cost))
    }
}

/// Parse and compute (token_base_units, min_sol_output_lamports) for a bonding curve sell.
fn parse_sell_amounts(
    params: &PumpFunTradeParams,
    v_sol: u64,
    v_tok: u64,
) -> Result<(u64, u64), AppError> {
    let amount: f64 = params.amount.parse()
        .map_err(|_| AppError::InvalidParams("amount must be a valid number".into()))?;
    let slippage_bps = ((params.slippage.unwrap_or(10.0)) * 100.0) as u64;
    let denominated_in_sol = params.denominated_in_sol.unwrap_or(false);

    if !denominated_in_sol {
        // Amount in human-readable tokens
        let token_base = (amount * PUMP_TOKEN_DECIMALS as f64) as u64;
        let sol_out = estimate_sol_for_tokens(v_sol, v_tok, token_base);
        let min_sol = apply_slippage_min(sol_out, slippage_bps);
        Ok((token_base, min_sol))
    } else {
        // Amount in SOL: find tokens needed to yield at least that much SOL
        let target_lamports = (amount * 1_000_000_000.0) as u64;
        // Reverse: tokens = (v_tok * sol_out) / (v_sol - sol_out)
        if target_lamports >= v_sol {
            return Err(AppError::InvalidParams("SOL amount exceeds pool liquidity".into()));
        }
        let vs = v_sol as u128;
        let vt = v_tok as u128;
        let so = target_lamports as u128;
        let token_base = ((vt * so) / (vs - so)) as u64;
        let min_sol = apply_slippage_min(target_lamports, slippage_bps);
        Ok((token_base, min_sol))
    }
}

/// Build compute budget instructions for a trade.
fn build_compute_budget_instructions(priority_fee_sol: f64) -> [Instruction; 2] {
    let compute_units: u64 = 300_000;
    let microlamports = ((priority_fee_sol * 1_000_000_000.0 * 1_000_000.0) as u64 / compute_units).max(1);
    [
        solana_sdk::compute_budget::ComputeBudgetInstruction::set_compute_unit_limit(compute_units as u32),
        solana_sdk::compute_budget::ComputeBudgetInstruction::set_compute_unit_price(microlamports),
    ]
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_launch_params(params: &LaunchTokenParams) -> Result<(), AppError> {
    if params.name.trim().is_empty() {
        return Err(AppError::InvalidParams("Token name is required".into()));
    }
    if params.name.len() > 32 {
        return Err(AppError::InvalidParams(
            "Token name must be 32 characters or less".into(),
        ));
    }
    if params.symbol.trim().is_empty() {
        return Err(AppError::InvalidParams("Token symbol is required".into()));
    }
    if params.symbol.len() > 10 {
        return Err(AppError::InvalidParams(
            "Token symbol must be 10 characters or less".into(),
        ));
    }
    if !params.symbol.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err(AppError::InvalidParams(
            "Token symbol must be alphanumeric".into(),
        ));
    }
    // Either a pre-hosted metadata URI or at minimum an image URL is required
    let has_metadata = params.metadata_uri.as_deref().map(|s| !s.trim().is_empty()).unwrap_or(false);
    let has_image = params.image_url.as_deref().map(|s| !s.trim().is_empty()).unwrap_or(false);
    if !has_metadata && !has_image {
        return Err(AppError::InvalidParams(
            "Token image is required. Upload via POST /upload/image, then POST /upload/metadata.".into(),
        ));
    }
    if params.description.len() > 500 {
        return Err(AppError::InvalidParams(
            "Token description must be 500 characters or less".into(),
        ));
    }
    if let Some(ref buy_amount) = params.initial_buy_amount {
        let a: f64 = buy_amount
            .parse()
            .map_err(|_| AppError::InvalidParams("Initial buy amount must be a number".into()))?;
        if a < 0.0 {
            return Err(AppError::InvalidParams(
                "Initial buy amount must be a positive number".into(),
            ));
        }
        if a > 100.0 {
            return Err(AppError::InvalidParams(
                "Initial buy amount too large (max 100 SOL)".into(),
            ));
        }
    }
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Metadata URI Resolution (self-hosted, no IPFS)
// ──────────────────────────────────────────────────────────────────────────────

/// Resolve the on-chain metadata URI for a token launch.
///
/// Priority:
///   1. Use `params.metadata_uri` if provided (uploaded via POST /upload/metadata).
///   2. Use `params.image_url` directly as a fallback (pump.fun also accepts a plain
///      image URL in the uri field for simple tokens without rich metadata).
///
/// Callers should prefer the two-step flow:
///   a) Upload image  → POST /upload/image  → get public image URL
///   b) Upload metadata → POST /upload/metadata → get metadata JSON URL
///   c) Pass metadata URL as `metadata_uri` to this function
fn resolve_metadata_uri(params: &LaunchTokenParams) -> Result<String, AppError> {
    if let Some(ref uri) = params.metadata_uri {
        if !uri.trim().is_empty() {
            tracing::info!(metadata_uri = %uri, "Using pre-uploaded metadata URI");
            return Ok(uri.clone());
        }
    }

    // Fallback: use image URL directly (works for pump.fun minimal tokens)
    if let Some(ref image_url) = params.image_url {
        if !image_url.trim().is_empty() {
            tracing::warn!(
                "No metadata_uri provided; falling back to image_url as token URI. \
                 Consider calling POST /upload/metadata first for rich metadata."
            );
            return Ok(image_url.clone());
        }
    }

    Err(AppError::InvalidParams(
        "Either metadata_uri or image_url is required. \
         Upload image via POST /upload/image, then metadata via POST /upload/metadata."
            .into(),
    ))
}

// ──────────────────────────────────────────────────────────────────────────────
// Bonding Curve Math
// ──────────────────────────────────────────────────────────────────────────────

/// Pump.fun initial virtual reserves (from pump.fun global account constants).
/// These determine the starting price of every new token.
const INITIAL_VIRTUAL_SOL_RESERVES: u64 = 30_000_000_000; // 30 SOL in lamports
const INITIAL_VIRTUAL_TOKEN_RESERVES: u64 = 1_073_000_191_000_000; // ~1.073T base units

/// Estimate the number of tokens received for a given SOL input on a fresh bonding curve.
///
/// Uses the constant-product AMM formula with pump.fun's virtual liquidity:
///   tokens_out = (virtual_token_reserves * sol_in) / (virtual_sol_reserves + sol_in)
///
/// This is an approximation for a fresh token.  For tokens with existing trading
/// history the actual curve state (virtual_sol_reserves, virtual_token_reserves)
/// should be fetched from the bonding curve account on-chain.
pub fn estimate_tokens_for_sol(sol_lamports: u64) -> u64 {
    let vsr = INITIAL_VIRTUAL_SOL_RESERVES as u128;
    let vtr = INITIAL_VIRTUAL_TOKEN_RESERVES as u128;
    let sol_in = sol_lamports as u128;
    // Constant-product: tokens_out = vtr * sol_in / (vsr + sol_in)
    ((vtr * sol_in) / (vsr + sol_in)) as u64
}

/// Calculate max_sol_cost with slippage tolerance (upper bound — add slippage to cost).
pub fn apply_slippage(lamports: u64, slippage_bps: u64) -> u64 {
    lamports + (lamports * slippage_bps / 10_000)
}

/// Apply slippage as a lower bound (subtract from expected output).
pub fn apply_slippage_min(lamports: u64, slippage_bps: u64) -> u64 {
    lamports.saturating_sub(lamports * slippage_bps / 10_000)
}

/// Estimate SOL received for a given token input using constant-product formula.
///
///   sol_out = (v_sol * tokens_in) / (v_tok + tokens_in)
pub fn estimate_sol_for_tokens(v_sol: u64, v_tok: u64, tokens_in: u64) -> u64 {
    let vs = v_sol as u128;
    let vt = v_tok as u128;
    let ti = tokens_in as u128;
    ((vs * ti) / (vt + ti)) as u64
}

/// Estimate SOL needed to buy exactly `tokens_out` tokens (reverse AMM formula).
///
///   sol_in = (v_sol * tokens_out) / (v_tok - tokens_out)
/// Returns u64::MAX when tokens_out >= v_tok (would drain the pool).
pub fn estimate_sol_for_tokens_out(v_sol: u64, v_tok: u64, tokens_out: u64) -> u64 {
    if tokens_out >= v_tok {
        return u64::MAX;
    }
    let vs = v_sol as u128;
    let vt = v_tok as u128;
    let to = tokens_out as u128;
    ((vs * to) / (vt - to)) as u64
}

/// Pump.fun token decimals (all tokens launched via pump.fun use 6 decimals).
const PUMP_TOKEN_DECIMALS: u64 = 1_000_000; // 10^6

// ──────────────────────────────────────────────────────────────────────────────
// Build launch TX (Direct Anchor instruction)
// ──────────────────────────────────────────────────────────────────────────────

/// Build a Pump.fun token launch transaction using direct Anchor instruction.
///
/// This creates a transaction with:
/// 1. Create instruction (creates the token)
/// 2. Optional: Create ATA instruction (if initial buy)
/// 3. Optional: Buy instruction (if initial buy specified)
pub fn build_launch_token_transaction_blocking(
    rpc: &SolanaRpc,
    creator_pubkey: &Pubkey,
    params: &LaunchTokenParams,
) -> Result<LaunchBuildResult, AppError> {
    validate_launch_params(params)?;

    let mint_keypair = Keypair::new();
    let mint_pubkey = mint_keypair.pubkey();

    // Step 1: Resolve metadata URI (pre-uploaded to our own storage, no IPFS)
    let metadata_uri = resolve_metadata_uri(params)?;

    // Step 2: Build the create_v2 instruction (Token-2022, Mayhem mode)
    let is_mayhem = params.mayhem_mode.as_deref().map(|s| s == "true").unwrap_or(false);
    let create_ix = build_create_v2_instruction(
        creator_pubkey,
        &mint_pubkey,
        &params.name,
        &params.symbol,
        &metadata_uri,
        is_mayhem,
    )?;

    tracing::info!(
        creator = %creator_pubkey,
        mint = %mint_pubkey,
        name = %params.name,
        symbol = %params.symbol,
        "Building pump.fun Create transaction"
    );

    // Step 3: Build additional instructions
    let mut instructions = Vec::new();

    // Compute budget — derive microlamports/CU from user's priorityFee (SOL)
    let compute_units: u64 = 300_000;
    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let microlamports_per_cu = ((priority_fee_sol * 1_000_000_000.0 * 1_000_000.0) as u64 / compute_units).max(1);
    let compute_budget_ix = solana_sdk::compute_budget::ComputeBudgetInstruction::set_compute_unit_limit(compute_units as u32);
    let compute_price_ix = solana_sdk::compute_budget::ComputeBudgetInstruction::set_compute_unit_price(microlamports_per_cu);
    instructions.push(compute_budget_ix);
    instructions.push(compute_price_ix);

    // Create instruction
    instructions.push(create_ix);

    // Step 4: Add initial buy if specified
    let initial_buy_sol = params.initial_buy_amount
        .as_ref()
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);

    if initial_buy_sol > 0.0 {
        tracing::info!(
            initial_buy_sol = initial_buy_sol,
            "Adding initial buy instruction"
        );

        // Create ATA instruction (idempotent - safe to call even if exists)
        let create_ata_ix = build_create_ata_instruction(creator_pubkey, &mint_pubkey);
        instructions.push(create_ata_ix);

        // Calculate buy parameters using accurate bonding curve formula.
        // pump.fun uses a constant-product AMM with virtual liquidity:
        //   tokens_out = (virtual_token_reserves * sol_in) / (virtual_sol_reserves + sol_in)
        let sol_cost_lamports = (initial_buy_sol * 1_000_000_000.0) as u64;
        let slippage_pct = params.slippage.unwrap_or(10.0);
        let slippage_bps = (slippage_pct * 100.0) as u64;
        let max_sol_cost = apply_slippage(sol_cost_lamports, slippage_bps);

        // Accurate token estimate using virtual reserves formula
        let token_amount = estimate_tokens_for_sol(sol_cost_lamports);

        tracing::info!(
            token_amount = token_amount,
            max_sol_cost_lamports = max_sol_cost,
            "Calculated initial buy parameters"
        );

        let fee_recipient_fallback = Pubkey::from_str(PUMP_FUN_FEE_RECIPIENT_FALLBACK)
            .expect("valid fallback fee_recipient");
        let buy_ix = build_buy_instruction(
            creator_pubkey,
            &mint_pubkey,
            creator_pubkey,
            &fee_recipient_fallback,
            token_amount,
            max_sol_cost,
        )?;
        instructions.push(buy_ix);
    }

    // Step 5: Get recent blockhash
    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Failed to get blockhash: {}", e)))?;

    // Step 6: Build the transaction
    let message = Message::new_with_blockhash(
        &instructions,
        Some(creator_pubkey),
        &blockhash,
    );

    let mut transaction = Transaction::new_unsigned(message);

    // Step 7: Sign with mint keypair (required for creating new mint)
    transaction.partial_sign(&[&mint_keypair], blockhash);
    // Note: Creator signature will be added by the frontend wallet

    tracing::info!(
        num_signatures = transaction.signatures.len(),
        num_instructions = transaction.message.instructions.len(),
        fee_payer = %transaction.message.account_keys[0],
        "Created unsigned pump.fun transaction"
    );

    // Step 8: Serialize to base64
    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Failed to serialize transaction: {}", e)))?;
    let tx_base64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    let mut warnings = Vec::new();
    if params.twitter.is_none() && params.telegram.is_none() && params.website.is_none() {
        warnings.push("No social links - consider adding for credibility".into());
    }
    if initial_buy_sol > 1.0 {
        warnings.push(format!(
            "Large initial buy: {} SOL",
            params.initial_buy_amount.as_deref().unwrap_or("0")
        ));
    }

    let preview = LaunchPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "launch_token".to_string(),
        description: format!(
            "Launch {} ({}) on Pump.fun",
            params.symbol, params.name
        ),
        estimated_fee: format!("~{} SOL", PUMP_FUN_CREATE_FEE + initial_buy_sol),
        params: params.clone(),
        warnings,
        requires_approval: true,
    };

    Ok(LaunchBuildResult {
        transaction_base64: tx_base64,
        preview,
    })
}

// (PumpPortal dependency removed — buy/sell now use direct on-chain Anchor instructions)

// ──────────────────────────────────────────────────────────────────────────────
// Pump.fun REST API helpers
// ──────────────────────────────────────────────────────────────────────────────

async fn pumpfun_get(http: &reqwest::Client, path: &str) -> Result<Value, AppError> {
    let url = format!("{PUMP_FUN_API}{path}");
    let resp = http
        .get(&url)
        .header("Accept", "application/json")
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("PumpFun API error: {e}")))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(AppError::Internal(format!("PumpFun API {status}: {body}")));
    }
    resp.json::<Value>()
        .await
        .map_err(|e| AppError::Internal(format!("PumpFun API parse: {e}")))
}

fn pf_response(action_type: &str, desc: String, data: Value) -> BuildResponse {
    BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: action_type.to_string(),
            description: desc,
            estimated_fee: "0".to_string(),
            estimated_refund: None,
            params: data,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    }
}

// ── Params structs ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct PumpFunMintParams {
    #[serde(default)]
    pub mint: Option<String>,
    #[serde(default)]
    pub token: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct PumpFunSearchParams {
    #[serde(default)]
    pub query: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct PumpFunListParams {
    #[serde(default)]
    pub limit: Option<u32>,
    #[serde(default)]
    pub offset: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct PumpFunUserParams {
    #[serde(default)]
    pub wallet: Option<String>,
}

pub fn validate_pumpfun_mint_params(p: &PumpFunMintParams) -> Result<(), AppError> {
    if p.mint.as_deref().unwrap_or("").is_empty() && p.token.as_deref().unwrap_or("").is_empty() {
        return Err(AppError::InvalidParams("mint address is required".into()));
    }
    Ok(())
}

// ── Query Actions ─────────────────────────────────────────────────────────────

pub async fn build_pumpfun_token_info(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunMintParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_mint_params(params)?;
    let mint = params.mint.as_deref().or(params.token.as_deref()).unwrap_or("");
    let data = pumpfun_get(http, &format!("/coins/{mint}")).await?;
    let name = data.get("name").and_then(|v| v.as_str()).unwrap_or(mint);
    let mc = data.get("usd_market_cap").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let complete = data.get("complete").and_then(|v| v.as_bool()).unwrap_or(false);
    let status = if complete { "graduated" } else { "bonding curve" };
    Ok(pf_response("pumpfun_token_info",
        format!("{name} ({mint:.8}…) — ${mc:.0} mcap — {status}"), data))
}

pub async fn build_pumpfun_trending(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunListParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(20).min(50);
    let offset = params.offset.unwrap_or(0);
    let data = pumpfun_get(http, &format!("/coins?offset={offset}&limit={limit}&sort=market_cap&order=DESC&includeNsfw=false")).await?;
    let arr = data.as_array().or_else(|| data.get("tokens").and_then(|v| v.as_array()));
    let count = arr.map(|a| a.len()).unwrap_or(0);
    Ok(pf_response("pumpfun_trending", format!("{count} trending tokens by market cap"), data))
}

pub async fn build_pumpfun_new(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunListParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(20).min(50);
    let offset = params.offset.unwrap_or(0);
    let data = pumpfun_get(http, &format!("/coins/latest?offset={offset}&limit={limit}&includeNsfw=false")).await?;
    let arr = data.as_array().or_else(|| data.get("tokens").and_then(|v| v.as_array()));
    let count = arr.map(|a| a.len()).unwrap_or(0);
    Ok(pf_response("pumpfun_new", format!("{count} newest tokens"), data))
}

pub async fn build_pumpfun_graduating(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunListParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(20).min(50);
    // Tokens currently on bonding curve (live): use the /coins/currently-live endpoint
    let data = pumpfun_get(http, &format!("/coins/currently-live?offset=0&limit={limit}&includeNsfw=false")).await?;
    // Filter for tokens with high bonding curve progress (virtual_sol > 75% of 85 SOL graduation target)
    let graduation_target_sol: f64 = 85_000_000_000.0; // 85 SOL in lamports
    let threshold = graduation_target_sol * 0.75;
    let coins = data.as_array().or_else(|| data.get("tokens").and_then(|v| v.as_array()));
    let graduating: Vec<Value> = coins
        .map(|arr| arr.iter()
            .filter(|c| {
                let v_sol = c.get("virtual_sol_reserves").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let complete = c.get("complete").and_then(|v| v.as_bool()).unwrap_or(false);
                !complete && v_sol >= threshold
            })
            .take(limit as usize)
            .cloned()
            .collect())
        .unwrap_or_default();
    let count = graduating.len();
    Ok(pf_response("pumpfun_graduating", format!("{count} tokens approaching graduation (>75% bonding curve)"), json!(graduating)))
}

pub async fn build_pumpfun_koth(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunListParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(10).min(20);
    let data = pumpfun_get(http, &format!("/coins/king/of/the/hill?limit={limit}&includeNsfw=false")).await?;
    let arr = data.as_array().or_else(|| data.get("tokens").and_then(|v| v.as_array()));
    let count = arr.map(|a| a.len()).unwrap_or(1); // single object or array
    Ok(pf_response("pumpfun_koth", format!("{count} King of the Hill token(s)"), data))
}

pub async fn build_pumpfun_search(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunSearchParams,
) -> Result<BuildResponse, AppError> {
    let query = params.query.as_deref().unwrap_or("").trim().to_string();
    if query.is_empty() {
        return Err(AppError::InvalidParams("search query is required".into()));
    }
    let limit = params.limit.unwrap_or(10).min(50);
    let encoded = urlencoding_encode(&query);
    let data = pumpfun_get(http, &format!("/coins/search?query={encoded}&limit={limit}&includeNsfw=false")).await?;
    let arr = data.as_array().or_else(|| data.get("coins").and_then(|v| v.as_array()));
    let count = arr.map(|a| a.len()).unwrap_or(0);
    Ok(pf_response("pumpfun_search", format!("{count} results for '{query}'"), data))
}

pub async fn build_pumpfun_comments(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunMintParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_mint_params(params)?;
    let mint = params.mint.as_deref().or(params.token.as_deref()).unwrap_or("");
    let data = pumpfun_get(http, &format!("/replies?mint={mint}&limit=50&offset=0")).await?;
    let count = data.as_array().map(|a| a.len())
        .or_else(|| data.get("replies").and_then(|v| v.as_array()).map(|a| a.len()))
        .unwrap_or(0);
    Ok(pf_response("pumpfun_comments", format!("{count} comment(s) for {mint}"), data))
}

pub async fn build_pumpfun_user(
    http: &reqwest::Client, wallet: &str, params: &PumpFunUserParams,
) -> Result<BuildResponse, AppError> {
    let target = params.wallet.as_deref().unwrap_or(wallet);
    // User profile is on the main API; /users/{wallet} path confirmed in v3
    let data = pumpfun_get(http, &format!("/users/{target}")).await?;
    let username = data.get("username").and_then(|v| v.as_str())
        .or_else(|| data.get("name").and_then(|v| v.as_str()))
        .unwrap_or(target);
    Ok(pf_response("pumpfun_user", format!("PumpFun user: {username}"), data))
}

pub async fn build_pumpfun_bonding_curve(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunMintParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_mint_params(params)?;
    let mint = params.mint.as_deref().or(params.token.as_deref()).unwrap_or("");
    let coin = pumpfun_get(http, &format!("/coins/{mint}")).await?;
    let v_sol = coin.get("virtual_sol_reserves").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let v_tok = coin.get("virtual_token_reserves").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let complete = coin.get("complete").and_then(|v| v.as_bool()).unwrap_or(false);
    let mc = coin.get("usd_market_cap").and_then(|v| v.as_f64()).unwrap_or(0.0);
    // price = virtual_sol_reserves / virtual_token_reserves (in SOL per base unit, x10^3 for decimals)
    let price_sol = if v_tok > 0.0 { v_sol / v_tok / 1e3 } else { 0.0 };
    let bc_addr = coin.get("bonding_curve").and_then(|v| v.as_str()).unwrap_or("unknown");
    let result = json!({
        "mint": mint,
        "bonding_curve": bc_addr,
        "virtual_sol_reserves": v_sol,
        "virtual_token_reserves": v_tok,
        "price_per_token_sol": price_sol,
        "usd_market_cap": mc,
        "complete": complete,
        "status": if complete { "graduated" } else { "active" },
    });
    Ok(pf_response("pumpfun_bonding_curve",
        format!("Bonding curve for {mint} — price: {price_sol:.8} SOL — ${mc:.0} mcap"), result))
}

fn urlencoding_encode(s: &str) -> String {
    s.chars().map(|c| match c {
        'A'..='Z' | 'a'..='z' | '0'..='9' | '-' | '_' | '.' | '~' => c.to_string(),
        ' ' => "%20".to_string(),
        _ => format!("%{:02X}", c as u8),
    }).collect()
}

// ── Bonding Curve Buy / Sell — Direct On-Chain Instructions ──────────────────

pub async fn build_pumpfun_buy(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    wallet: &str,
    params: &PumpFunTradeParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_trade_params(params)?;

    let buyer = Pubkey::from_str(wallet)
        .map_err(|_| AppError::InvalidParams("Invalid wallet address".into()))?;
    let mint = Pubkey::from_str(&params.mint)
        .map_err(|_| AppError::InvalidParams(format!("Invalid mint: {}", params.mint)))?;

    // Fetch coin data: creator for PDA derivation + virtual reserves for pricing
    let coin = pumpfun_get(http, &format!("/coins/{}", params.mint)).await?;
    let creator_str = coin.get("creator").and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Internal("Missing creator field in coin data".into()))?;
    let creator = Pubkey::from_str(creator_str)
        .map_err(|_| AppError::Internal("Invalid creator pubkey from API".into()))?;
    let name = coin.get("name").and_then(|v| v.as_str()).unwrap_or(&params.mint).to_string();
    let v_sol = coin.get("virtual_sol_reserves").and_then(|v| v.as_f64())
        .unwrap_or(INITIAL_VIRTUAL_SOL_RESERVES as f64) as u64;
    let v_tok = coin.get("virtual_token_reserves").and_then(|v| v.as_f64())
        .unwrap_or(INITIAL_VIRTUAL_TOKEN_RESERVES as f64) as u64;

    let (token_amount, max_sol_cost) = parse_buy_amounts(params, v_sol, v_tok)?;

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let [cu_limit_ix, cu_price_ix] = build_compute_budget_instructions(priority_fee_sol);

    // Fetch fee_recipient dynamically from on-chain global account (falls back to known address)
    let fee_recipient = fetch_pumpfun_fee_recipient(rpc).await;

    let create_ata_ix = build_create_ata_instruction(&buyer, &mint);
    let buy_ix = build_buy_instruction(&buyer, &mint, &creator, &fee_recipient, token_amount, max_sol_cost)?;

    let instructions = vec![cu_limit_ix, cu_price_ix, create_ata_ix, buy_ix];
    let blockhash = get_blockhash(rpc).await?;
    let message = Message::new_with_blockhash(&instructions, Some(&buyer), &blockhash);
    let transaction = Transaction::new_unsigned(message);
    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialize TX: {e}")))?;
    let tx_base64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    tracing::info!(
        buyer = %buyer, mint = %mint, token_amount, max_sol_cost,
        "Built pumpfun buy TX (direct on-chain)"
    );

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "pumpfun_buy".to_string(),
            description: format!("Buy {} on Pump.fun bonding curve ({})", name, &params.mint[..8.min(params.mint.len())]),
            estimated_fee: "~0.002 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params).unwrap_or_default(),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx_base64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

pub async fn build_pumpfun_sell(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    wallet: &str,
    params: &PumpFunTradeParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_trade_params(params)?;

    let seller = Pubkey::from_str(wallet)
        .map_err(|_| AppError::InvalidParams("Invalid wallet address".into()))?;
    let mint = Pubkey::from_str(&params.mint)
        .map_err(|_| AppError::InvalidParams(format!("Invalid mint: {}", params.mint)))?;

    let coin = pumpfun_get(http, &format!("/coins/{}", params.mint)).await?;
    let creator_str = coin.get("creator").and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Internal("Missing creator field in coin data".into()))?;
    let creator = Pubkey::from_str(creator_str)
        .map_err(|_| AppError::Internal("Invalid creator pubkey from API".into()))?;
    let name = coin.get("name").and_then(|v| v.as_str()).unwrap_or(&params.mint).to_string();
    let v_sol = coin.get("virtual_sol_reserves").and_then(|v| v.as_f64())
        .unwrap_or(INITIAL_VIRTUAL_SOL_RESERVES as f64) as u64;
    let v_tok = coin.get("virtual_token_reserves").and_then(|v| v.as_f64())
        .unwrap_or(INITIAL_VIRTUAL_TOKEN_RESERVES as f64) as u64;

    let (token_amount, min_sol_output) = parse_sell_amounts(params, v_sol, v_tok)?;

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let [cu_limit_ix, cu_price_ix] = build_compute_budget_instructions(priority_fee_sol);

    let fee_recipient = fetch_pumpfun_fee_recipient(rpc).await;

    let sell_ix = build_sell_instruction(&seller, &mint, &creator, &fee_recipient, token_amount, min_sol_output)?;
    let instructions = vec![cu_limit_ix, cu_price_ix, sell_ix];

    let blockhash = get_blockhash(rpc).await?;
    let message = Message::new_with_blockhash(&instructions, Some(&seller), &blockhash);
    let transaction = Transaction::new_unsigned(message);
    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialize TX: {e}")))?;
    let tx_base64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    tracing::info!(
        seller = %seller, mint = %mint, token_amount, min_sol_output,
        "Built pumpfun sell TX (direct on-chain)"
    );

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "pumpfun_sell".to_string(),
            description: format!("Sell {} on Pump.fun bonding curve ({})", name, &params.mint[..8.min(params.mint.len())]),
            estimated_fee: "~0.002 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params).unwrap_or_default(),
            warnings: vec![],
            requires_approval: true,
        },
        transaction: Some(tx_base64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ── PumpSwap AMM — Direct On-Chain Instructions (graduated tokens) ────────────

pub async fn build_pumpswap_buy(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    wallet: &str,
    params: &PumpFunTradeParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_trade_params(params)?;

    let buyer = Pubkey::from_str(wallet)
        .map_err(|_| AppError::InvalidParams("Invalid wallet address".into()))?;
    let mint = Pubkey::from_str(&params.mint)
        .map_err(|_| AppError::InvalidParams(format!("Invalid mint: {}", params.mint)))?;

    let coin = pumpfun_get(http, &format!("/coins/{}", params.mint)).await?;
    let creator_str = coin.get("creator").and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Internal("Missing creator field in coin data".into()))?;
    let creator = Pubkey::from_str(creator_str)
        .map_err(|_| AppError::Internal("Invalid creator pubkey from API".into()))?;
    let complete = coin.get("complete").and_then(|v| v.as_bool()).unwrap_or(false);
    if !complete {
        return Err(AppError::InvalidParams(
            "Token has not graduated to PumpSwap AMM yet. Use pumpfun_buy for bonding curve tokens.".into()
        ));
    }

    // Use pool address directly from API — PDA derivation is unreliable
    let pool_str = coin.get("pool_address").or_else(|| coin.get("raydium_pool"))
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Internal("Missing pool_address in coin data for graduated token".into()))?;
    let pool = Pubkey::from_str(pool_str)
        .map_err(|_| AppError::Internal(format!("Invalid pool address from API: {pool_str}")))?;

    // Fetch real pool reserves for accurate pricing (graduated tokens have stale virtual reserves)
    let (v_tok, v_sol) = match fetch_pumpswap_pool_reserves(rpc, &pool, &mint).await {
        Ok(reserves) => reserves,
        Err(e) => {
            tracing::warn!(error = %e, pool = %pool, "Failed to fetch PumpSwap pool reserves, falling back to API values");
            let vs = coin.get("virtual_sol_reserves").and_then(|v| v.as_f64())
                .unwrap_or(INITIAL_VIRTUAL_SOL_RESERVES as f64) as u64;
            let vt = coin.get("virtual_token_reserves").and_then(|v| v.as_f64())
                .unwrap_or(INITIAL_VIRTUAL_TOKEN_RESERVES as f64) as u64;
            (vt, vs)
        }
    };

    // For PumpSwap: base_amount_out = tokens, max_quote_amount_in = SOL (wSOL)
    let (base_amount_out, max_quote_in) = parse_buy_amounts(params, v_sol, v_tok)?;

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let [cu_limit_ix, cu_price_ix] = build_compute_budget_instructions(priority_fee_sol);

    // Fetch protocol fee recipient from GlobalConfig on-chain
    let fee_recipient = fetch_pumpswap_fee_recipient(rpc).await?;

    // Create base (Token-2022) and quote (wSOL legacy SPL) ATAs
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid");
    let create_base_ata_ix = build_create_ata_instruction(&buyer, &mint);
    let create_quote_ata_ix = build_create_spl_ata_instruction(&buyer, &sol_mint);

    // Wrap SOL → wSOL
    let wrap_ixs = build_wrap_sol_instructions(&buyer, max_quote_in);

    let swap_ix = build_pumpswap_buy_instruction(&buyer, &pool, &mint, &creator, &fee_recipient, base_amount_out, max_quote_in)?;
    let close_wsol_ix = build_close_wsol_instruction(&buyer);

    let mut instructions = vec![cu_limit_ix, cu_price_ix, create_base_ata_ix, create_quote_ata_ix];
    instructions.extend(wrap_ixs);
    instructions.push(swap_ix);
    instructions.push(close_wsol_ix);

    let blockhash = get_blockhash(rpc).await?;
    let message = Message::new_with_blockhash(&instructions, Some(&buyer), &blockhash);
    let transaction = Transaction::new_unsigned(message);
    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialize TX: {e}")))?;
    let tx_base64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    tracing::info!(
        buyer = %buyer, mint = %mint, base_amount_out, max_quote_in,
        "Built pumpswap buy TX (direct on-chain)"
    );

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "pumpswap_buy".to_string(),
            description: format!("Buy {} on PumpSwap AMM (graduated token {})", params.amount, &params.mint[..8.min(params.mint.len())]),
            estimated_fee: "~0.002 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params).unwrap_or_default(),
            warnings: vec!["Token has graduated from bonding curve to PumpSwap AMM".to_string()],
            requires_approval: true,
        },
        transaction: Some(tx_base64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

pub async fn build_pumpswap_sell(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    wallet: &str,
    params: &PumpFunTradeParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_trade_params(params)?;

    let seller = Pubkey::from_str(wallet)
        .map_err(|_| AppError::InvalidParams("Invalid wallet address".into()))?;
    let mint = Pubkey::from_str(&params.mint)
        .map_err(|_| AppError::InvalidParams(format!("Invalid mint: {}", params.mint)))?;

    let coin = pumpfun_get(http, &format!("/coins/{}", params.mint)).await?;
    let creator_str = coin.get("creator").and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Internal("Missing creator field in coin data".into()))?;
    let creator = Pubkey::from_str(creator_str)
        .map_err(|_| AppError::Internal("Invalid creator pubkey from API".into()))?;
    let complete = coin.get("complete").and_then(|v| v.as_bool()).unwrap_or(false);
    if !complete {
        return Err(AppError::InvalidParams(
            "Token has not graduated to PumpSwap AMM yet. Use pumpfun_sell for bonding curve tokens.".into()
        ));
    }

    // Use pool address directly from API — PDA derivation is unreliable
    let pool_str = coin.get("pool_address").or_else(|| coin.get("raydium_pool"))
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Internal("Missing pool_address in coin data for graduated token".into()))?;
    let pool = Pubkey::from_str(pool_str)
        .map_err(|_| AppError::Internal(format!("Invalid pool address from API: {pool_str}")))?;

    // Fetch real pool reserves for accurate pricing
    let (v_tok, v_sol) = match fetch_pumpswap_pool_reserves(rpc, &pool, &mint).await {
        Ok(reserves) => reserves,
        Err(e) => {
            tracing::warn!(error = %e, pool = %pool, "Failed to fetch PumpSwap pool reserves, falling back to API values");
            let vs = coin.get("virtual_sol_reserves").and_then(|v| v.as_f64())
                .unwrap_or(INITIAL_VIRTUAL_SOL_RESERVES as f64) as u64;
            let vt = coin.get("virtual_token_reserves").and_then(|v| v.as_f64())
                .unwrap_or(INITIAL_VIRTUAL_TOKEN_RESERVES as f64) as u64;
            (vt, vs)
        }
    };

    let (base_amount_in, min_quote_out) = parse_sell_amounts(params, v_sol, v_tok)?;

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let [cu_limit_ix, cu_price_ix] = build_compute_budget_instructions(priority_fee_sol);

    // Fetch protocol fee recipient from GlobalConfig on-chain
    let fee_recipient = fetch_pumpswap_fee_recipient(rpc).await?;

    // Create wSOL ATA to receive SOL proceeds
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid");
    let create_quote_ata_ix = build_create_spl_ata_instruction(&seller, &sol_mint);
    let swap_ix = build_pumpswap_sell_instruction(&seller, &pool, &mint, &creator, &fee_recipient, base_amount_in, min_quote_out)?;
    let close_wsol_ix = build_close_wsol_instruction(&seller);

    let instructions = vec![cu_limit_ix, cu_price_ix, create_quote_ata_ix, swap_ix, close_wsol_ix];

    let blockhash = get_blockhash(rpc).await?;
    let message = Message::new_with_blockhash(&instructions, Some(&seller), &blockhash);
    let transaction = Transaction::new_unsigned(message);
    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialize TX: {e}")))?;
    let tx_base64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    tracing::info!(
        seller = %seller, mint = %mint, base_amount_in, min_quote_out,
        "Built pumpswap sell TX (direct on-chain)"
    );

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "pumpswap_sell".to_string(),
            description: format!("Sell {} on PumpSwap AMM (graduated token {})", params.amount, &params.mint[..8.min(params.mint.len())]),
            estimated_fee: "~0.002 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params).unwrap_or_default(),
            warnings: vec!["Token has graduated from bonding curve to PumpSwap AMM".to_string()],
            requires_approval: true,
        },
        transaction: Some(tx_base64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

pub async fn build_pumpswap_pool_info(
    http: &reqwest::Client, _wallet: &str, params: &PumpFunMintParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_mint_params(params)?;
    let mint = params.mint.as_deref().or(params.token.as_deref()).unwrap_or("");
    let coin = pumpfun_get(http, &format!("/coins/{mint}")).await?;
    let pool_addr = coin.get("pool_address").or_else(|| coin.get("raydium_pool"))
        .and_then(|v| v.as_str()).unwrap_or("");
    let complete = coin.get("complete").and_then(|v| v.as_bool()).unwrap_or(false);

    if !complete || pool_addr.is_empty() {
        return Err(AppError::InvalidParams(
            "Token has not graduated to PumpSwap AMM yet. Use pumpfun_buy/sell for bonding curve tokens.".into()
        ));
    }

    // Derive canonical pool PDA using correct seeds from pump_amm.json IDL:
    // ["pool", index(u16 LE), coin_creator, base_mint, quote_mint]
    let creator_str = coin.get("creator").and_then(|v| v.as_str()).unwrap_or("");
    let creator_pubkey = Pubkey::from_str(creator_str)
        .unwrap_or_else(|_| Pubkey::from_str(PUMP_FUN_PROGRAM_ID).expect("valid"));
    let sol_mint_pubkey = Pubkey::from_str(SOL_MINT).expect("valid");
    let mint_pubkey = Pubkey::from_str(mint)
        .map_err(|_| AppError::InvalidParams(format!("Invalid mint: {mint}")))?;
    let pool_pda = find_pumpswap_pool_pda(&creator_pubkey, &mint_pubkey, &sol_mint_pubkey);

    let v_sol = coin.get("virtual_sol_reserves").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let mc = coin.get("usd_market_cap").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let result = json!({
        "mint": mint,
        "pool_address": pool_addr,
        "pool_pda": pool_pda.to_string(),
        "amm_program": PUMP_AMM_PROGRAM_ID,
        "usd_market_cap": mc,
        "virtual_sol_reserves": v_sol,
        "complete": true,
        "status": "graduated",
    });
    Ok(pf_response("pumpswap_pool_info",
        format!("PumpSwap pool for {mint} — ${mc:.0} mcap — pool: {}", &pool_addr[..8.min(pool_addr.len())]), result))
}

// ──────────────────────────────────────────────────────────────────────────────
// Tests
// ──────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    /// Verify the discriminators
    #[test]
    fn test_discriminators() {
        use sha2::{Digest, Sha256};

        // Create discriminator
        let mut hasher = Sha256::new();
        hasher.update(b"global:create");
        let hash = hasher.finalize();
        let expected_create: [u8; 8] = [hash[0], hash[1], hash[2], hash[3], hash[4], hash[5], hash[6], hash[7]];
        assert_eq!(CREATE_DISCRIMINATOR, expected_create, "Create discriminator mismatch!");
        assert_eq!(CREATE_DISCRIMINATOR, [24, 30, 200, 40, 5, 28, 7, 119]);

        // Buy discriminator
        let mut hasher = Sha256::new();
        hasher.update(b"global:buy");
        let hash = hasher.finalize();
        let expected_buy: [u8; 8] = [hash[0], hash[1], hash[2], hash[3], hash[4], hash[5], hash[6], hash[7]];
        assert_eq!(BUY_DISCRIMINATOR, expected_buy, "Buy discriminator mismatch!");
        assert_eq!(BUY_DISCRIMINATOR, [102, 6, 61, 18, 1, 218, 235, 234]);
    }

    /// Verify PDA derivation
    #[test]
    fn test_pda_derivation() {
        let mint = Pubkey::from_str("So11111111111111111111111111111111111111112").expect("valid SOL mint");

        let bonding_curve = find_bonding_curve_pda(&mint);
        assert!(!bonding_curve.to_bytes().iter().all(|&b| b == 0));

        let mint_authority = find_mint_authority_pda();
        assert!(!mint_authority.to_bytes().iter().all(|&b| b == 0));

        let global = find_global_pda();
        assert!(!global.to_bytes().iter().all(|&b| b == 0));

        let metadata = find_metadata_pda(&mint);
        assert!(!metadata.to_bytes().iter().all(|&b| b == 0));

        let creator_vault = find_creator_vault_pda(&mint);
        assert!(!creator_vault.to_bytes().iter().all(|&b| b == 0));
    }

    /// Verify validation rejects invalid params
    #[test]
    fn test_validation_rejects_invalid() {
        let params = LaunchTokenParams {
            name: "".to_string(),
            symbol: "TEST".to_string(),
            description: "Test token".to_string(),
            image_url: Some("https://example.com/image.png".to_string()),
            metadata_uri: None,
            twitter: None,
            telegram: None,
            website: None,
            initial_buy_amount: None,
            slippage: None,
            priority_fee: None,
            mayhem_mode: None,
            cashback: None,
            banner_url: None,
        };
        assert!(validate_launch_params(&params).is_err());

        let params = LaunchTokenParams {
            name: "Test".to_string(),
            symbol: "VERYLONGSYMBOL".to_string(),
            description: "Test token".to_string(),
            image_url: Some("https://example.com/image.png".to_string()),
            metadata_uri: None,
            twitter: None,
            telegram: None,
            website: None,
            initial_buy_amount: None,
            slippage: None,
            priority_fee: None,
            mayhem_mode: None,
            cashback: None,
            banner_url: None,
        };
        assert!(validate_launch_params(&params).is_err());

        let params = LaunchTokenParams {
            name: "Test".to_string(),
            symbol: "TEST".to_string(),
            description: "Test token".to_string(),
            image_url: None,
            metadata_uri: None,
            twitter: None,
            telegram: None,
            website: None,
            initial_buy_amount: None,
            slippage: None,
            priority_fee: None,
            mayhem_mode: None,
            cashback: None,
            banner_url: None,
        };
        assert!(validate_launch_params(&params).is_err());
    }

    /// Verify validation accepts valid params
    #[test]
    fn test_validation_accepts_valid() {
        let params = LaunchTokenParams {
            name: "Test Token".to_string(),
            symbol: "TEST".to_string(),
            description: "A test token for validation".to_string(),
            image_url: Some("https://example.com/image.png".to_string()),
            metadata_uri: None,
            twitter: Some("https://twitter.com/test".to_string()),
            telegram: None,
            website: None,
            initial_buy_amount: Some("1.5".to_string()),
            slippage: None,
            priority_fee: None,
            mayhem_mode: Some("true".to_string()),
            cashback: Some("true".to_string()),
            banner_url: None,
        };
        assert!(validate_launch_params(&params).is_ok());
    }
}

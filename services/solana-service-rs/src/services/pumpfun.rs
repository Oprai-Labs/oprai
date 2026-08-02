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

/// Token-2022 program (some pump.fun tokens use Token-2022)
const TOKEN_2022_PROGRAM_ID: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";

/// Legacy SPL Token program (many pump.fun tokens still use this)
const SPL_TOKEN_PROGRAM_ID: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";

/// Metaplex Token Metadata program (legacy create only)
const MPL_TOKEN_METADATA: &str = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s";

/// Pump.fun event authority PDA
const EVENT_AUTHORITY: &str = "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1";

/// Pump fee program (volume tracking + fee config)
const PUMP_FEE_PROGRAM_ID: &str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ";

/// Fee authority bytes for fee_config PDA seeds — from on-chain fee_config account at offset 8.
/// Verified against fee_config account data: 8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt
const PUMP_FEE_AUTHORITY: [u8; 32] = [
    253, 211, 187, 140, 171, 52, 28, 224, 82, 132, 87, 242, 195, 129, 125, 50, 120, 68, 25, 99,
    220, 213, 95, 237, 88, 186, 36, 201, 153, 221, 172, 2,
];

/// Pump.fun fee_config PDA — constant, owned by fee_program.
/// Hardcoded to avoid wrong PDA derivation from stale authority bytes.
const PUMP_FEE_CONFIG: &str = "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt";

/// Pump.fun global volume accumulator PDA — constant, owned by pump.fun program.
const PUMP_GLOBAL_VOLUME_ACC: &str = "Hq2wp8uJ9jCPsYgNHex8RtqdvMPfVGoYwjvF1ATiwn2Y";

/// One of the 8 BuybackFeeConfig accounts (remaining_accounts[1] for bonding-curve buy/sell).
/// Any of the 8 is valid; this is index 0.
const PUMP_BUYBACK_FEE_CONFIG: &str = "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD";

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
    /// Tokenized agent — pump.fun "Automated buybacks & burns" feature (NEW).
    /// Stored in params and metadata; on-chain instruction support pending IDL update.
    #[serde(default)]
    pub tokenized_agent: Option<String>,
    /// Pair with USDC — launch the bonding curve against USDC instead of SOL (NEW
    /// pump.fun feature). Requires a different create instruction (USDC quote vault)
    /// we don't have the spec for yet, so it's gated off; see build_launch.
    #[serde(default)]
    pub pair_with_usdc: Option<String>,
    /// Mint public key pre-generated by the frontend (base58).
    /// When provided, the backend uses this pubkey and does NOT partial-sign
    /// with the mint keypair — the frontend signs after wallet approval instead.
    /// This prevents Phantom's blockhash replacement from invalidating a stale
    /// mint signature during its internal simulation.
    #[serde(default)]
    pub mint_pubkey: Option<String>,
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
        return Err(AppError::InvalidParams(
            "amount must be greater than zero".into(),
        ));
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

/// Describes the initial dev-buy the frontend should perform after the create tx
/// confirms. The buy itself is built by `pumpfun_initial_buy` (PumpPortal) once the
/// token exists on-chain — this just carries what to buy. `None` = no initial buy.
#[derive(serde::Serialize)]
pub struct InitialBuyInfo {
    pub mint: String,
    pub amount_sol: f64,
    /// Mayhem-mode token — trades route through the Mayhem program, not the
    /// standard bonding curve. Informational; PumpPortal picks the pool.
    pub mayhem: bool,
}

/// Result from building a launch token transaction.
pub struct LaunchBuildResult {
    pub transaction_base64: String,
    /// Present when an initial dev-buy was requested; the frontend performs it as a
    /// follow-up after the create tx confirms (see `build_launch_token_transaction_blocking`).
    pub initial_buy: Option<InitialBuyInfo>,
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
    #[allow(dead_code)]
    const False: OptionBool = OptionBool(1);
    const True: OptionBool = OptionBool(2);
}

/// create_v2 args (Token-2022, Mayhem mode) — from pump.json IDL
/// is_cashback_enabled is a plain bool (not OptionBool): true=0x01, false=0x00.
/// Sending OptionBool(2)=0x02 triggers a Borsh deserialization error in simulation.
#[derive(BorshSerialize)]
struct CreateV2Args {
    name: String,
    symbol: String,
    uri: String,
    creator: Pubkey,
    is_mayhem_mode: bool,
    is_cashback_enabled: bool,
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

/// The bonding curve, read from the chain.
///
/// Everything the buy and sell paths need — reserves, graduation state, the
/// creator — lives in this account, and it exists the instant the create
/// transaction lands.
///
/// They were reading it from pump.fun's HTTP API instead, which 404s on a
/// token it has not indexed yet. That is precisely the first minutes of a
/// token's life, which for pump.fun is most of what anyone trades, and it is
/// why the initial buy after a launch had to be handed to PumpPortal. There
/// was never anything PumpPortal could do that we could not; it reads the
/// chain and we were reading an indexer.
#[derive(Debug, Clone)]
pub struct BondingCurveState {
    pub virtual_token_reserves: u64,
    pub virtual_sol_reserves: u64,
    pub real_token_reserves: u64,
    pub real_sol_reserves: u64,
    pub token_total_supply: u64,
    pub complete: bool,
    pub creator: Option<Pubkey>,
}

/// Layout, verified against a live account (115 bytes):
///   0..8    anchor discriminator
///   8..16   virtual_token_reserves   u64
///   16..24  virtual_sol_reserves     u64
///   24..32  real_token_reserves      u64
///   32..40  real_sol_reserves        u64
///   40..48  token_total_supply       u64
///   48      complete                 bool
///   49..81  creator                  Pubkey
pub async fn read_bonding_curve(
    rpc: &SolanaRpc,
    mint: &Pubkey,
) -> Result<BondingCurveState, AppError> {
    let pda = find_bonding_curve_pda(mint);
    let rpc2 = rpc.clone();
    let account = tokio::task::spawn_blocking(move || rpc2.client().get_account(&pda))
        .await
        .map_err(|e| AppError::Internal(format!("bonding curve read failed: {e}")))?
        .map_err(|_| {
            AppError::NotFound(
                "That token has no pump.fun bonding curve — it may have graduated or never \
                 existed"
                    .into(),
            )
        })?;

    let d = &account.data;
    if d.len() < 49 {
        return Err(AppError::ProtocolError(
            "That token's bonding curve could not be read".into(),
        ));
    }
    let u64_at = |o: usize| u64::from_le_bytes(d[o..o + 8].try_into().unwrap_or([0; 8]));

    Ok(BondingCurveState {
        virtual_token_reserves: u64_at(8),
        virtual_sol_reserves: u64_at(16),
        real_token_reserves: u64_at(24),
        real_sol_reserves: u64_at(32),
        token_total_supply: u64_at(40),
        complete: d[48] != 0,
        creator: if d.len() >= 81 {
            Pubkey::try_from(&d[49..81]).ok()
        } else {
            None
        },
    })
}

/// Which token program a mint belongs to, from the mint account's owner.
/// The API reported this too; the chain cannot be out of date about it.
pub async fn read_token_program(rpc: &SolanaRpc, mint: &Pubkey) -> Pubkey {
    let rpc2 = rpc.clone();
    let m = *mint;
    match tokio::task::spawn_blocking(move || rpc2.client().get_account(&m)).await {
        Ok(Ok(acc)) => acc.owner,
        _ => token_2022_program(),
    }
}

/// Find PDA for bonding-curve-v2: ["bonding-curve-v2", mint].
/// Used as remaining_accounts[0] (READONLY, typically uninitialized) in bonding-curve buy/sell
/// to satisfy the BuybackFeeRecipientMissing check (error 6023).
fn find_bonding_curve_v2_pda(mint: &Pubkey) -> Pubkey {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).expect("valid hardcoded address");
    Pubkey::find_program_address(&[b"bonding-curve-v2", mint.as_ref()], &program_id).0
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

/// Token-2022 program pubkey
fn token_2022_program() -> Pubkey {
    Pubkey::from_str(TOKEN_2022_PROGRAM_ID).expect("valid TOKEN_2022_PROGRAM_ID")
}

/// Legacy SPL Token program pubkey
fn spl_token_program() -> Pubkey {
    Pubkey::from_str(SPL_TOKEN_PROGRAM_ID).expect("valid SPL_TOKEN_PROGRAM_ID")
}

/// Get ATA for bonding curve using the detected token program.
fn get_associated_bonding_curve(mint: &Pubkey, token_prog: &Pubkey) -> Pubkey {
    let bonding_curve = find_bonding_curve_pda(mint);
    spl_associated_token_account::get_associated_token_address_with_program_id(
        &bonding_curve,
        mint,
        token_prog,
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
/// seeds: ["creator_vault", creator], program: PUMP_AMM_PROGRAM_ID  (verified from pump_amm IDL)
fn find_pumpswap_coin_creator_vault(creator: &Pubkey) -> Pubkey {
    let amm = Pubkey::from_str(PUMP_AMM_PROGRAM_ID).expect("valid");
    Pubkey::find_program_address(&[b"creator_vault", creator.as_ref()], &amm).0
}

/// PumpSwap pool PDA — pool_index 0, seeded with coin_creator, base_mint, quote_mint
/// seeds: ["pool", index_u16_le, coin_creator, base_mint, quote_mint], program: PUMP_AMM_PROGRAM_ID
fn find_pumpswap_pool_pda(creator: &Pubkey, base_mint: &Pubkey, quote_mint: &Pubkey) -> Pubkey {
    let amm = Pubkey::from_str(PUMP_AMM_PROGRAM_ID).expect("valid");
    Pubkey::find_program_address(
        &[
            b"pool",
            &0u16.to_le_bytes(),
            creator.as_ref(),
            base_mint.as_ref(),
            quote_mint.as_ref(),
        ],
        &amm,
    )
    .0
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

/// Parsed fields from PumpSwap's GlobalConfig account.
struct PumpSwapGlobalConfig {
    protocol_fee_recipient: Pubkey,
    buyback_fee_recipient: Pubkey,
}

/// Fetch fee recipients from PumpSwap's GlobalConfig account on-chain.
///
/// GlobalConfig layout (verified on-chain for ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw):
///   [0..8]    discriminator
///   [8..40]   admin: Pubkey
///   [40..48]  lp_fee_basis_points: u64
///   [48..56]  protocol_fee_basis_points: u64
///   [56]      disable_flags: u8
///   [57..313] protocol_fee_recipients: [Pubkey; 8]  ← use recipients[0] for protocol_fee
///   [313..321] coin_creator_fee_basis_points: u64
///   [321..353] admin_set_coin_creator_authority: Pubkey
///   [353..385] whitelist_pda: Pubkey
///   [385..417] reserved_fee_recipient: Pubkey
///   [417]     mayhem_mode_enabled: bool
///   [418..642] reserved_fee_recipients: [Pubkey; 7]
///   [642]     is_cashback_enabled: bool
///   [643..899] buyback_fee_recipients: [Pubkey; 8]  ← use recipients[0] for buyback_fee
///   [899..907] buyback_basis_points: u64
///   Total: 907 bytes
async fn fetch_pumpswap_global_config(rpc: &SolanaRpc) -> Result<PumpSwapGlobalConfig, AppError> {
    let global_config = Pubkey::from_str(PUMP_AMM_GLOBAL_CONFIG_ADDRESS)
        .expect("valid PUMP_AMM_GLOBAL_CONFIG_ADDRESS");
    let rpc = rpc.clone();
    let account = tokio::task::spawn_blocking(move || rpc.client().get_account(&global_config))
        .await
        .map_err(|e| AppError::Internal(format!("spawn error fetching global_config: {e}")))?
        .map_err(|e| AppError::Internal(format!("RPC error fetching global_config: {e}")))?;

    const PROTOCOL_FEE_OFFSET: usize = 57; // first of 8 protocol_fee_recipients
    const BUYBACK_FEE_OFFSET: usize = 643; // first of 8 buyback_fee_recipients

    if account.data.len() < BUYBACK_FEE_OFFSET + 32 {
        return Err(AppError::Internal(
            "GlobalConfig account data too short".into(),
        ));
    }
    let protocol_fee_recipient =
        Pubkey::try_from(&account.data[PROTOCOL_FEE_OFFSET..PROTOCOL_FEE_OFFSET + 32])
            .map_err(|_| AppError::Internal("Failed to parse protocol_fee_recipient".into()))?;
    let buyback_fee_recipient =
        Pubkey::try_from(&account.data[BUYBACK_FEE_OFFSET..BUYBACK_FEE_OFFSET + 32])
            .map_err(|_| AppError::Internal("Failed to parse buyback_fee_recipient".into()))?;

    Ok(PumpSwapGlobalConfig {
        protocol_fee_recipient,
        buyback_fee_recipient,
    })
}

/// All fields needed from a PumpSwap pool account (read in one RPC call).
struct PumpSwapPoolData {
    /// pool_base_token_account stored at offset 139 — the pool's base-token vault.
    pool_base_token_account: Pubkey,
    /// pool_quote_token_account stored at offset 171 — the pool's quote-token (wSOL) vault.
    pool_quote_token_account: Pubkey,
    /// coin_creator stored at offset 211 — used for creator_vault PDA derivation.
    coin_creator: Pubkey,
    /// is_cashback_coin at offset 244 — if true, user_volume_accumulator_wsol_ata goes in remaining_accounts[0].
    is_cashback_coin: bool,
}

/// Read all needed fields from a PumpSwap pool account in a single RPC call.
///
/// Pool account layout (Borsh, no padding — verified on-chain for multiple pools):
///   [0..8]    discriminator
///   [8]       pool_bump: u8
///   [9..11]   pool_index: u16
///   [11..43]  creator: Pubkey
///   [43..75]  base_mint: Pubkey
///   [75..107] quote_mint: Pubkey
///   [107..139] lp_mint: Pubkey
///   [139..171] pool_base_token_account: Pubkey  ← base vault (NOT an ATA of the pool)
///   [171..203] pool_quote_token_account: Pubkey ← quote vault (NOT an ATA of the pool)
///   [203..211] lp_supply: u64
///   [211..243] coin_creator: Pubkey
///   [243]     is_mayhem_mode: bool
///   [244]     is_cashback_coin: bool
async fn fetch_pumpswap_pool_data(
    rpc: &SolanaRpc,
    pool: &Pubkey,
) -> Result<PumpSwapPoolData, AppError> {
    let rpc2 = rpc.clone();
    let pool2 = *pool;
    let account = tokio::task::spawn_blocking(move || rpc2.client().get_account(&pool2))
        .await
        .map_err(|e| AppError::Internal(format!("spawn error fetching pool account: {e}")))?
        .map_err(|e| AppError::Internal(format!("RPC error fetching pool account: {e}")))?;

    const BASE_VAULT_OFFSET: usize = 139;
    const QUOTE_VAULT_OFFSET: usize = 171;
    const COIN_CREATOR_OFFSET: usize = 211;
    const IS_CASHBACK_COIN_OFFSET: usize = 244;

    if account.data.len() < IS_CASHBACK_COIN_OFFSET + 1 {
        return Err(AppError::Internal(format!(
            "Pool account too short ({} bytes) to read all fields",
            account.data.len()
        )));
    }

    let pool_base_token_account = Pubkey::try_from(
        &account.data[BASE_VAULT_OFFSET..BASE_VAULT_OFFSET + 32],
    )
    .map_err(|_| AppError::Internal("Failed to parse pool_base_token_account from pool".into()))?;
    let pool_quote_token_account = Pubkey::try_from(
        &account.data[QUOTE_VAULT_OFFSET..QUOTE_VAULT_OFFSET + 32],
    )
    .map_err(|_| AppError::Internal("Failed to parse pool_quote_token_account from pool".into()))?;
    let coin_creator =
        Pubkey::try_from(&account.data[COIN_CREATOR_OFFSET..COIN_CREATOR_OFFSET + 32])
            .map_err(|_| AppError::Internal("Failed to parse coin_creator from pool".into()))?;
    let is_cashback_coin = account.data[IS_CASHBACK_COIN_OFFSET] != 0;

    Ok(PumpSwapPoolData {
        pool_base_token_account,
        pool_quote_token_account,
        coin_creator,
        is_cashback_coin,
    })
}

/// Determine which token program owns a mint — classic SPL Token vs Token-2022.
///
/// pump.fun / PumpSwap tokens can be EITHER: older tokens (e.g. Fartcoin) are
/// classic SPL Token, newer launches are Token-2022. The PumpSwap AMM CPIs into
/// the mint's real owner and the base ATA must be created under it, so hardcoding
/// the wrong program id makes the tx fail simulation with `IncorrectProgramId`.
/// We read the mint account's owner and use it directly.
/// Read a mint account and return (owner token program, decimals).
/// SPL Mint and Token-2022 share the same base layout — `decimals` is at byte
/// offset 44 (after mint_authority COption<Pubkey> = 36 and supply u64 = 8).
async fn fetch_mint_info(rpc: &SolanaRpc, mint: &Pubkey) -> Result<(Pubkey, u8), AppError> {
    let rpc2 = rpc.clone();
    let mint2 = *mint;
    let account = tokio::task::spawn_blocking(move || rpc2.client().get_account(&mint2))
        .await
        .map_err(|e| AppError::Internal(format!("spawn error fetching mint account: {e}")))?
        .map_err(|e| AppError::Internal(format!("RPC error fetching mint account: {e}")))?;
    let decimals = account.data.get(44).copied().unwrap_or(6);
    Ok((account.owner, decimals))
}

async fn fetch_mint_token_program(rpc: &SolanaRpc, mint: &Pubkey) -> Result<Pubkey, AppError> {
    let (owner, _) = fetch_mint_info(rpc, mint).await?;
    if owner == token_2022_program() || owner == spl_token::id() {
        Ok(owner)
    } else {
        tracing::warn!(mint = %mint, owner = %owner, "Mint owned by unexpected program; defaulting to classic SPL Token");
        Ok(spl_token::id())
    }
}

/// Build a swap for a GRADUATED pump.fun token by routing through Jupiter.
///
/// Graduated tokens live on an external AMM — historically Raydium (older
/// tokens like Fartcoin) and now PumpSwap. Hand-building venue-specific AMM
/// instructions breaks the moment the venue differs (a Raydium pool fails the
/// PumpSwap program's owner check with AccountOwnedByWrongProgram). Jupiter's
/// aggregator routes every venue and returns a ready-to-sign versioned tx.
/// Amounts use the mint's real on-chain decimals so scaling is always correct.
async fn build_graduated_swap(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    wallet: &str,
    params: &PumpFunTradeParams,
    is_buy: bool,
) -> Result<BuildResponse, AppError> {
    let mint = Pubkey::from_str(&params.mint)
        .map_err(|_| AppError::InvalidParams(format!("Invalid mint: {}", params.mint)))?;
    let (_owner, decimals) = fetch_mint_info(rpc, &mint).await?;

    let amt: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid amount: {}", params.amount)))?;
    if !(amt > 0.0) {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    let slippage_bps = ((params.slippage.unwrap_or(10.0) * 100.0).round() as u64).clamp(1, 5000);

    // Direction + which leg the typed amount denominates (→ exact side & decimals).
    //   buy  + SOL amount (default) → ExactIn  SOL→mint   (SOL decimals)
    //   buy  + token amount         → ExactOut SOL→mint   (token decimals)
    //   sell + token amount (default) → ExactIn mint→SOL  (token decimals)
    //   sell + SOL amount           → ExactOut mint→SOL   (SOL decimals)
    let denom_sol = if is_buy {
        params.denominated_in_sol.unwrap_or(true)
    } else {
        params.denominated_in_sol.unwrap_or(false)
    };
    let (input_mint, output_mint) = if is_buy {
        (SOL_MINT.to_string(), params.mint.clone())
    } else {
        (params.mint.clone(), SOL_MINT.to_string())
    };
    let amount_is_sol = denom_sol;
    let amount_is_input = if is_buy { denom_sol } else { !denom_sol };
    let swap_mode = if amount_is_input {
        "ExactIn"
    } else {
        "ExactOut"
    };
    let amount_decimals: i32 = if amount_is_sol { 9 } else { decimals as i32 };
    let base_amount = (amt * 10f64.powi(amount_decimals)).round() as u64;
    if base_amount == 0 {
        return Err(AppError::InvalidParams(
            "Amount too small after decimal scaling".into(),
        ));
    }

    // 1) Quote via Jupiter public API (same source the frontend prices with).
    let quote_url = format!(
        "https://lite-api.jup.ag/swap/v1/quote?inputMint={input_mint}&outputMint={output_mint}\
         &amount={base_amount}&slippageBps={slippage_bps}&swapMode={swap_mode}"
    );
    let quote: serde_json::Value = http
        .get(&quote_url)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter quote request failed: {e}")))?
        .error_for_status()
        .map_err(|_| {
            AppError::InvalidParams("No Jupiter route found for this graduated token.".into())
        })?
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter quote decode: {e}")))?;

    // 2) Build the swap transaction (Jupiter returns a base64 versioned tx that
    //    already includes compute-budget, priority fee, and wSOL wrap/unwrap).
    let swap_body = serde_json::json!({
        "quoteResponse": quote,
        "userPublicKey": wallet,
        "wrapAndUnwrapSol": true,
        "dynamicComputeUnitLimit": true,
        "prioritizationFeeLamports": "auto",
    });
    let swap_resp: serde_json::Value = http
        .post("https://lite-api.jup.ag/swap/v1/swap")
        .json(&swap_body)
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter swap request failed: {e}")))?
        .error_for_status()
        .map_err(|e| AppError::Internal(format!("Jupiter swap build failed: {e}")))?
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("Jupiter swap decode: {e}")))?;

    let tx_b64 = swap_resp
        .get("swapTransaction")
        .and_then(|v| v.as_str())
        .ok_or_else(|| AppError::Internal("Missing swapTransaction from Jupiter".into()))?
        .to_string();

    let action_type = if is_buy {
        "pumpswap_buy"
    } else {
        "pumpswap_sell"
    };
    let verb = if is_buy { "Buy" } else { "Sell" };
    tracing::info!(
        mint = %mint, is_buy, base_amount, swap_mode, decimals,
        "Built graduated pump trade via Jupiter aggregator"
    );

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: action_type.to_string(),
            description: format!("{verb} {} on graduated market (via Jupiter)", params.amount),
            estimated_fee: "~0.001 SOL".to_string(),
            estimated_refund: None,
            params: serde_json::to_value(params).unwrap_or_default(),
            warnings: vec!["Graduated token — routed through Jupiter aggregator".to_string()],
            requires_approval: true,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Fetch real pool reserves from the pool's stored vault accounts (not ATA derivation).
///
/// Returns (base_reserve, quote_reserve) — the actual token balances held by the pool's vaults.
async fn fetch_pumpswap_pool_reserves(
    rpc: &SolanaRpc,
    pool_base_vault: &Pubkey,
    pool_quote_vault: &Pubkey,
) -> Result<(u64, u64), AppError> {
    let rpc2 = rpc.clone();
    let base_vault = *pool_base_vault;
    let base_balance =
        tokio::task::spawn_blocking(move || rpc2.client().get_token_account_balance(&base_vault))
            .await
            .map_err(|e| AppError::Internal(format!("spawn error fetching base reserve: {e}")))?
            .map_err(|e| AppError::Internal(format!("RPC error fetching base reserve: {e}")))?;

    let rpc3 = rpc.clone();
    let quote_vault = *pool_quote_vault;
    let quote_balance =
        tokio::task::spawn_blocking(move || rpc3.client().get_token_account_balance(&quote_vault))
            .await
            .map_err(|e| AppError::Internal(format!("spawn error fetching quote reserve: {e}")))?
            .map_err(|e| AppError::Internal(format!("RPC error fetching quote reserve: {e}")))?;

    let base_amount: u64 = base_balance
        .amount
        .parse()
        .map_err(|_| AppError::Internal("Failed to parse base reserve amount".into()))?;
    let quote_amount: u64 = quote_balance
        .amount
        .parse()
        .map_err(|_| AppError::Internal("Failed to parse quote reserve amount".into()))?;

    Ok((base_amount, quote_amount))
}

/// Fee authority bytes for PumpSwap AMM fee_config PDA — DIFFERENT from bonding curve.
/// From pump_amm.json IDL fee_config seeds.
const PUMP_AMM_FEE_AUTHORITY: [u8; 32] = [
    12, 20, 222, 252, 130, 94, 198, 118, 148, 37, 8, 24, 187, 101, 64, 101, 244, 41, 141, 49, 86,
    213, 113, 180, 212, 248, 9, 12, 24, 233, 168, 99,
];

/// Find fee_config PDA for PumpSwap AMM — uses a different authority than the bonding curve.
fn find_pumpswap_fee_config_pda() -> Pubkey {
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID).expect("valid PUMP_FEE_PROGRAM_ID");
    Pubkey::find_program_address(&[b"fee_config", &PUMP_AMM_FEE_AUTHORITY], &fee_program).0
}

// ──────────────────────────────────────────────────────────────────────────────
// Build create_v2 Instruction (Token-2022, Mayhem mode — current pump.fun)
// ──────────────────────────────────────────────────────────────────────────────

/// Build init_user_volume_accumulator instruction.
/// Must be called before create_v2 with cashback=true if the account doesn't exist yet.
/// Discriminator: [94, 6, 202, 115, 255, 96, 232, 183] (from pump.json IDL)
fn build_init_user_volume_accumulator_instruction(user: &Pubkey) -> Result<Instruction, AppError> {
    const DISCRIMINATOR: [u8; 8] = [94, 6, 202, 115, 255, 96, 232, 183];
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid program ID: {e}")))?;
    let event_authority = Pubkey::from_str(EVENT_AUTHORITY)
        .map_err(|e| AppError::Internal(format!("Invalid event authority: {e}")))?;
    let user_volume_acc = find_user_volume_accumulator_pda(user);
    let accounts = vec![
        AccountMeta::new(*user, true),            // payer (writable, signer)
        AccountMeta::new_readonly(*user, false),  // user (readonly)
        AccountMeta::new(user_volume_acc, false), // user_volume_accumulator (writable, PDA)
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // system_program
        AccountMeta::new_readonly(event_authority, false), // event_authority
        AccountMeta::new_readonly(program_id, false), // program
    ];
    Ok(Instruction {
        program_id,
        accounts,
        data: DISCRIMINATOR.to_vec(),
    })
}

fn build_create_v2_instruction(
    creator: &Pubkey,
    mint: &Pubkey,
    name: &str,
    symbol: &str,
    uri: &str,
    is_mayhem_mode: bool,
    is_cashback_enabled: bool,
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
    let assoc_bonding_curve = get_associated_bonding_curve(mint, &token_2022_program());
    let mayhem_state = find_mayhem_state_pda(mint);
    let mayhem_token_vault =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            &sol_vault, mint, &t22,
        );

    let args = CreateV2Args {
        name: name.to_string(),
        symbol: symbol.to_uppercase(),
        uri: uri.to_string(),
        creator: *creator,
        is_mayhem_mode,
        is_cashback_enabled,
    };
    let mut data = Vec::with_capacity(8 + 200);
    data.extend_from_slice(&CREATE_V2_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize create_v2: {e}")))?;

    // 16 accounts matching the pump.fun create_v2 IDL (confirmed via PumpPortal transaction decoding).
    // global_params [10] is writable — the program writes to it during cashback registration.
    let accounts = vec![
        AccountMeta::new(*mint, true),                    // 0 mint
        AccountMeta::new_readonly(mint_authority, false), // 1 mint_authority
        AccountMeta::new(bonding_curve, false),           // 2 bonding_curve
        AccountMeta::new(assoc_bonding_curve, false),     // 3 assoc_bonding_curve
        AccountMeta::new_readonly(global, false),         // 4 global
        AccountMeta::new(*creator, true),                 // 5 user/creator
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 6 system_program
        AccountMeta::new_readonly(t22, false),            // 7 token_2022_program
        AccountMeta::new_readonly(spl_associated_token_account::id(), false), // 8 associated_token_program
        AccountMeta::new(mayhem_program, false), // 9 mayhem_program (writable)
        AccountMeta::new(global_params, false),  // 10 global_params (writable)
        AccountMeta::new(sol_vault, false),      // 11 sol_vault (writable)
        AccountMeta::new(mayhem_state, false),   // 12 mayhem_state
        AccountMeta::new(mayhem_token_vault, false), // 13 mayhem_token_vault
        AccountMeta::new_readonly(event_authority, false), // 14 event_authority
        AccountMeta::new_readonly(program_id, false), // 15 program
    ];

    Ok(Instruction {
        program_id,
        accounts,
        data,
    })
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
    let associated_bonding_curve = get_associated_bonding_curve(mint, &token_2022_program());

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
        AccountMeta::new(*mint, true),           // 0: mint (signer, writable)
        AccountMeta::new(mint_authority, false), // 1: mint_authority_pda (writable)
        AccountMeta::new(bonding_curve, false),  // 2: bonding_curve (writable)
        AccountMeta::new(associated_bonding_curve, false), // 3: associated_bonding_curve (writable)
        AccountMeta::new(global, false),         // 4: global_pda (writable)
        AccountMeta::new_readonly(mpl_token_metadata, false), // 5: mpl_token_metadata (readonly)
        AccountMeta::new(metadata, false),       // 6: metadata_pda (writable)
        AccountMeta::new(*creator, true),        // 7: user (signer, writable)
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 8: system_program
        AccountMeta::new_readonly(spl_token::id(), false), // 9: token_program
        AccountMeta::new_readonly(spl_associated_token_account::id(), false), // 10: associated_token_program
        AccountMeta::new_readonly(solana_sdk::sysvar::rent::id(), false),     // 11: rent_sysvar
        AccountMeta::new_readonly(event_authority, false), // 12: event_authority (readonly)
        AccountMeta::new_readonly(program_id, false),      // 13: pump_fun_program (readonly)
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
    let result = tokio::task::spawn_blocking(move || rpc2.client().get_account(&global)).await;

    match result {
        Ok(Ok(account)) => {
            const FEE_RECIPIENT_OFFSET: usize = 41; // 8 disc + 1 bool + 32 pubkey
            if account.data.len() >= FEE_RECIPIENT_OFFSET + 32 {
                if let Ok(pk) =
                    Pubkey::try_from(&account.data[FEE_RECIPIENT_OFFSET..FEE_RECIPIENT_OFFSET + 32])
                {
                    return pk;
                }
            }
            tracing::warn!(
                "pump.fun global account data too short or invalid, using fallback fee_recipient"
            );
        }
        Ok(Err(e)) => {
            tracing::warn!(error = %e, "RPC error fetching pump.fun global, using fallback fee_recipient")
        }
        Err(e) => {
            tracing::warn!(error = %e, "spawn_blocking error fetching pump.fun global, using fallback fee_recipient")
        }
    }

    Pubkey::from_str(PUMP_FUN_FEE_RECIPIENT_FALLBACK).expect("valid fallback fee_recipient")
}

fn build_buy_instruction(
    buyer: &Pubkey,
    mint: &Pubkey,
    creator: &Pubkey,
    fee_recipient: &Pubkey,
    token_prog: &Pubkey,
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
    let associated_bonding_curve = get_associated_bonding_curve(mint, token_prog);
    let creator_vault = find_creator_vault_pda(creator);

    // User's token account — use the same token program as the mint
    let user_token_account =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            buyer, mint, token_prog,
        );

    // Volume/fee accounts — use hardcoded constants for global accounts to avoid
    // wrong PDA derivation. PUMP_GLOBAL_VOLUME_ACC and PUMP_FEE_CONFIG verified
    // against real on-chain transactions (owner confirmed via getAccountInfo).
    let global_volume_acc = Pubkey::from_str(PUMP_GLOBAL_VOLUME_ACC)
        .map_err(|e| AppError::Internal(format!("Invalid global_volume_acc: {e}")))?;
    let user_volume_acc = find_user_volume_accumulator_pda(buyer);
    let fee_config = Pubkey::from_str(PUMP_FEE_CONFIG)
        .map_err(|e| AppError::Internal(format!("Invalid fee_config: {e}")))?;
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

    // remaining_accounts for buyback (error 6062 BuybackFeeRecipientMissing if absent):
    //   [16] bonding-curve-v2 PDA (readonly, typically uninitialized — no v2 curve for this mint yet)
    //   [17] BuybackFeeConfig account (writable — one of 8 global configs, any is valid)
    let bonding_curve_v2 = find_bonding_curve_v2_pda(mint);
    let buyback_fee_cfg = Pubkey::from_str(PUMP_BUYBACK_FEE_CONFIG)
        .map_err(|e| AppError::Internal(format!("Invalid PUMP_BUYBACK_FEE_CONFIG: {e}")))?;

    // 16 declared + 2 remaining accounts (verified against real on-chain buy transactions).
    let accounts = vec![
        AccountMeta::new_readonly(global, false), // 0: global (readonly)
        AccountMeta::new(*fee_recipient, false),  // 1: fee_recipient (mut)
        AccountMeta::new_readonly(*mint, false),  // 2: mint (readonly)
        AccountMeta::new(bonding_curve, false),   // 3: bonding_curve (mut)
        AccountMeta::new(associated_bonding_curve, false), // 4: associated_bonding_curve (mut)
        AccountMeta::new(user_token_account, false), // 5: associated_user (mut)
        AccountMeta::new(*buyer, true),           // 6: user (mut, signer)
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 7: system_program
        AccountMeta::new_readonly(*token_prog, false), // 8: token_program (SPL or Token-2022)
        AccountMeta::new(creator_vault, false),   // 9: creator_vault (mut)
        AccountMeta::new_readonly(event_authority, false), // 10: event_authority
        AccountMeta::new_readonly(program_id, false), // 11: program
        AccountMeta::new_readonly(global_volume_acc, false), // 12: global_volume_accumulator (readonly)
        AccountMeta::new(user_volume_acc, false),            // 13: user_volume_accumulator (mut)
        AccountMeta::new_readonly(fee_config, false),        // 14: fee_config (readonly)
        AccountMeta::new_readonly(fee_program, false),       // 15: fee_program
        AccountMeta::new_readonly(bonding_curve_v2, false), // 16: bonding-curve-v2 (readonly, remaining)
        AccountMeta::new(buyback_fee_cfg, false),           // 17: BuybackFeeConfig (mut, remaining)
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
    token_prog: &Pubkey,
    amount: u64,
    min_sol_output: u64,
) -> Result<Instruction, AppError> {
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid program ID: {e}")))?;
    let event_authority = Pubkey::from_str(EVENT_AUTHORITY)
        .map_err(|e| AppError::Internal(format!("Invalid event authority: {e}")))?;

    let global = find_global_pda();
    let bonding_curve = find_bonding_curve_pda(mint);
    let associated_bonding_curve = get_associated_bonding_curve(mint, token_prog);
    let creator_vault = find_creator_vault_pda(creator);
    let user_token_account =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            seller, mint, token_prog,
        );
    let fee_config = Pubkey::from_str(PUMP_FEE_CONFIG)
        .map_err(|e| AppError::Internal(format!("Invalid fee_config: {e}")))?;
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid fee program: {e}")))?;

    let args = SellArgs {
        amount,
        min_sol_output,
    };
    let mut data = Vec::with_capacity(8 + 16);
    data.extend_from_slice(&SELL_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize sell: {e}")))?;

    // remaining_accounts for buyback (same as buy — error 6062 if absent):
    let bonding_curve_v2 = find_bonding_curve_v2_pda(mint);
    let buyback_fee_cfg = Pubkey::from_str(PUMP_BUYBACK_FEE_CONFIG)
        .map_err(|e| AppError::Internal(format!("Invalid PUMP_BUYBACK_FEE_CONFIG: {e}")))?;

    // 14 declared + 2 remaining — note creator_vault (#8) is BEFORE token_program (#9).
    let accounts = vec![
        AccountMeta::new_readonly(global, false), // 0: global (readonly)
        AccountMeta::new(*fee_recipient, false),  // 1: fee_recipient (mut)
        AccountMeta::new_readonly(*mint, false),  // 2: mint (readonly)
        AccountMeta::new(bonding_curve, false),   // 3: bonding_curve (mut)
        AccountMeta::new(associated_bonding_curve, false), // 4: associated_bonding_curve (mut)
        AccountMeta::new(user_token_account, false), // 5: associated_user (mut)
        AccountMeta::new(*seller, true),          // 6: user (mut, signer)
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 7: system_program
        AccountMeta::new(creator_vault, false),   // 8: creator_vault (mut, before token_program)
        AccountMeta::new_readonly(*token_prog, false), // 9: token_program (SPL or Token-2022)
        AccountMeta::new_readonly(event_authority, false), // 10: event_authority
        AccountMeta::new_readonly(program_id, false), // 11: program
        AccountMeta::new_readonly(fee_config, false), // 12: fee_config (readonly)
        AccountMeta::new_readonly(fee_program, false), // 13: fee_program
        AccountMeta::new_readonly(bonding_curve_v2, false), // 14: bonding-curve-v2 (readonly, remaining)
        AccountMeta::new(buyback_fee_cfg, false),           // 15: BuybackFeeConfig (mut, remaining)
    ];

    Ok(Instruction {
        program_id,
        accounts,
        data,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// PumpSwap AMM — Direct Instruction Builders
// ──────────────────────────────────────────────────────────────────────────────

/// Build PumpSwap AMM buy instruction.
///
/// 23 declared accounts + up to 3 remaining_accounts per pump_amm buy IDL.
/// Remaining accounts (from pump-swap-sdk v1.15+):
///   If is_cashback_coin: remaining[0] = user_vol_accumulator_wsol_ata (writable)
///   If coin_creator != default: remaining[N] = poolV2Pda(base_mint) (readonly)
///   Always: remaining[N+0] = buyback_fee_recipient (readonly)
///           remaining[N+1] = buyback_fee_recipient_wsol_ata (writable)
fn build_pumpswap_buy_instruction(
    buyer: &Pubkey,
    pool: &Pubkey,
    base_mint: &Pubkey,
    coin_creator: &Pubkey,
    protocol_fee: &Pubkey,
    pool_base_vault: &Pubkey, // pool_base_token_account from pool struct offset 139
    pool_quote_vault: &Pubkey, // pool_quote_token_account from pool struct offset 171
    base_amount_out: u64,
    max_quote_amount_in: u64,
    is_cashback_coin: bool,
    buyback_fee_recipient: &Pubkey,
    base_token_program: &Pubkey,
) -> Result<Instruction, AppError> {
    let amm_program = Pubkey::from_str(PUMP_AMM_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid AMM program: {e}")))?;
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid SOL_MINT");
    // Base token program is whatever owns the mint (classic SPL or Token-2022);
    // the quote leg (wSOL) is always classic SPL Token.
    let base_prog = *base_token_program;
    let spl_tok = spl_token::id();
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid fee program: {e}")))?;

    let pool = *pool;
    let global_config = Pubkey::from_str(PUMP_AMM_GLOBAL_CONFIG_ADDRESS).expect("valid");
    let event_authority = find_pumpswap_event_authority_pda();
    // coin_creator_vault_authority: PDA(["creator_vault", pool.coin_creator], pump_amm)
    // Must use pool.coin_creator (pool account at offset 211), not the API creator field.
    let coin_creator_vault_authority = find_pumpswap_coin_creator_vault(coin_creator);
    let fee_config = find_pumpswap_fee_config_pda();

    let user_base_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        buyer, base_mint, &base_prog,
    );
    let user_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        buyer, &sol_mint, &spl_tok,
    );
    let protocol_fee_quote_ata =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            protocol_fee,
            &sol_mint,
            &spl_tok,
        );
    // coin_creator_vault_ata is the wSOL (quote) ATA of vault authority, NOT the base token ATA
    let coin_creator_vault_ata =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            &coin_creator_vault_authority,
            &sol_mint,
            &spl_tok,
        );

    let global_vol_acc = find_pumpswap_global_volume_accumulator();
    let user_vol_acc = find_pumpswap_user_volume_accumulator(buyer);

    // Remaining accounts for buyback and poolV2 (required by pump_amm v1.15+):
    //   If is_cashback_coin → push user_vol_accumulator_wsol_ata (writable)
    //   If coin_creator != default → push poolV2Pda(base_mint) (readonly)
    //   Always → push buyback_fee_recipient (readonly), buyback_fee_recipient_wsol_ata (writable)
    let user_vol_wsol_ata =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            &user_vol_acc,
            &sol_mint,
            &spl_tok,
        );
    let pool_v2_pda =
        Pubkey::find_program_address(&[b"pool-v2", base_mint.as_ref()], &amm_program).0;
    let buyback_wsol_ata =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            buyback_fee_recipient,
            &sol_mint,
            &spl_tok,
        );
    let default_pubkey = Pubkey::default();

    let args = PumpSwapBuyArgs {
        base_amount_out,
        max_quote_amount_in,
        track_volume: OptionBool::None,
    };
    let mut data = Vec::with_capacity(8 + 17);
    data.extend_from_slice(&BUY_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize pumpswap buy: {e}")))?;

    // 23 declared accounts + remaining_accounts for buyback fee routing.
    let mut accounts = vec![
        AccountMeta::new(pool, false),                                      // 0
        AccountMeta::new(*buyer, true),                                     // 1
        AccountMeta::new_readonly(global_config, false),                    // 2
        AccountMeta::new_readonly(*base_mint, false),                       // 3
        AccountMeta::new_readonly(sol_mint, false),                         // 4
        AccountMeta::new(user_base_ata, false),                             // 5
        AccountMeta::new(user_quote_ata, false),                            // 6
        AccountMeta::new(*pool_base_vault, false),                          // 7
        AccountMeta::new(*pool_quote_vault, false),                         // 8
        AccountMeta::new_readonly(*protocol_fee, false),                    // 9
        AccountMeta::new(protocol_fee_quote_ata, false),                    // 10
        AccountMeta::new_readonly(base_prog, false), // 11 base token program (SPL or Token-2022)
        AccountMeta::new_readonly(spl_tok, false),   // 12 quote token program (wSOL = SPL)
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 13
        AccountMeta::new_readonly(spl_associated_token_account::id(), false), // 14
        AccountMeta::new_readonly(event_authority, false), // 15
        AccountMeta::new_readonly(amm_program, false), // 16
        AccountMeta::new(coin_creator_vault_ata, false), // 17
        AccountMeta::new_readonly(coin_creator_vault_authority, false), // 18
        AccountMeta::new_readonly(global_vol_acc, false), // 19 readonly
        AccountMeta::new(user_vol_acc, false),       // 20 writable
        AccountMeta::new_readonly(fee_config, false), // 21
        AccountMeta::new_readonly(fee_program, false), // 22
    ];

    // remaining_accounts[0]: user_vol_wsol_ata (cashback coins only)
    if is_cashback_coin {
        accounts.push(AccountMeta::new(user_vol_wsol_ata, false));
    }
    // remaining_accounts[N]: poolV2Pda (when coin_creator is set)
    if *coin_creator != default_pubkey {
        accounts.push(AccountMeta::new_readonly(pool_v2_pda, false));
    }
    // remaining_accounts always: buyback_fee_recipient + its wSOL ATA
    accounts.push(AccountMeta::new_readonly(*buyback_fee_recipient, false));
    accounts.push(AccountMeta::new(buyback_wsol_ata, false));

    Ok(Instruction {
        program_id: amm_program,
        accounts,
        data,
    })
}

/// Build PumpSwap AMM sell instruction.
///
/// 21 declared accounts + remaining_accounts for buyback (same structure as buy).
/// Sell cashback has 2 remaining accounts (wsol_ata + user_vol_acc) instead of buy's 1.
fn build_pumpswap_sell_instruction(
    seller: &Pubkey,
    pool: &Pubkey,
    base_mint: &Pubkey,
    coin_creator: &Pubkey,
    protocol_fee: &Pubkey,
    pool_base_vault: &Pubkey, // pool_base_token_account from pool struct offset 139
    pool_quote_vault: &Pubkey, // pool_quote_token_account from pool struct offset 171
    base_amount_in: u64,
    min_quote_amount_out: u64,
    is_cashback_coin: bool,
    buyback_fee_recipient: &Pubkey,
    base_token_program: &Pubkey,
) -> Result<Instruction, AppError> {
    let amm_program = Pubkey::from_str(PUMP_AMM_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid AMM program: {e}")))?;
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid SOL_MINT");
    // Base token program is whatever owns the mint (classic SPL or Token-2022);
    // the quote leg (wSOL) is always classic SPL Token.
    let base_prog = *base_token_program;
    let spl_tok = spl_token::id();
    let fee_program = Pubkey::from_str(PUMP_FEE_PROGRAM_ID)
        .map_err(|e| AppError::Internal(format!("Invalid fee program: {e}")))?;

    let pool = *pool;
    let global_config = Pubkey::from_str(PUMP_AMM_GLOBAL_CONFIG_ADDRESS).expect("valid");
    let event_authority = find_pumpswap_event_authority_pda();
    let coin_creator_vault_authority = find_pumpswap_coin_creator_vault(coin_creator);
    let fee_config = find_pumpswap_fee_config_pda();

    let user_base_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        seller, base_mint, &base_prog,
    );
    let user_quote_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        seller, &sol_mint, &spl_tok,
    );
    let protocol_fee_quote_ata =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            protocol_fee,
            &sol_mint,
            &spl_tok,
        );
    // coin_creator_vault_ata is wSOL (quote) ATA of vault authority
    let coin_creator_vault_ata =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            &coin_creator_vault_authority,
            &sol_mint,
            &spl_tok,
        );

    let global_vol_acc = find_pumpswap_global_volume_accumulator();
    let user_vol_acc = find_pumpswap_user_volume_accumulator(seller);
    let user_vol_wsol_ata =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            &user_vol_acc,
            &sol_mint,
            &spl_tok,
        );
    let pool_v2_pda =
        Pubkey::find_program_address(&[b"pool-v2", base_mint.as_ref()], &amm_program).0;
    let buyback_wsol_ata =
        spl_associated_token_account::get_associated_token_address_with_program_id(
            buyback_fee_recipient,
            &sol_mint,
            &spl_tok,
        );
    let default_pubkey = Pubkey::default();

    let args = PumpSwapSellArgs {
        base_amount_in,
        min_quote_amount_out,
    };
    let mut data = Vec::with_capacity(8 + 16);
    data.extend_from_slice(&SELL_DISCRIMINATOR);
    args.serialize(&mut data)
        .map_err(|e| AppError::Internal(format!("Failed to serialize pumpswap sell: {e}")))?;

    let mut accounts = vec![
        AccountMeta::new(pool, false),                                      // 0
        AccountMeta::new(*seller, true),                                    // 1
        AccountMeta::new_readonly(global_config, false),                    // 2
        AccountMeta::new_readonly(*base_mint, false),                       // 3
        AccountMeta::new_readonly(sol_mint, false),                         // 4
        AccountMeta::new(user_base_ata, false),                             // 5
        AccountMeta::new(user_quote_ata, false),                            // 6
        AccountMeta::new(*pool_base_vault, false),                          // 7
        AccountMeta::new(*pool_quote_vault, false),                         // 8
        AccountMeta::new_readonly(*protocol_fee, false),                    // 9
        AccountMeta::new(protocol_fee_quote_ata, false),                    // 10
        AccountMeta::new_readonly(base_prog, false), // 11 base token program (SPL or Token-2022)
        AccountMeta::new_readonly(spl_tok, false),   // 12 quote token program (wSOL = SPL)
        AccountMeta::new_readonly(solana_sdk::system_program::id(), false), // 13
        AccountMeta::new_readonly(spl_associated_token_account::id(), false), // 14
        AccountMeta::new_readonly(event_authority, false), // 15
        AccountMeta::new_readonly(amm_program, false), // 16
        AccountMeta::new(coin_creator_vault_ata, false), // 17
        AccountMeta::new_readonly(coin_creator_vault_authority, false), // 18
        AccountMeta::new_readonly(fee_config, false), // 19
        AccountMeta::new_readonly(fee_program, false), // 20
    ];

    // remaining_accounts[0..1]: cashback wsol_ata + user_vol_acc (sell has 2 accounts for cashback)
    if is_cashback_coin {
        accounts.push(AccountMeta::new(user_vol_wsol_ata, false));
        accounts.push(AccountMeta::new(user_vol_acc, false));
    }
    // remaining_accounts[N]: poolV2Pda (when coin_creator is set)
    if *coin_creator != default_pubkey {
        accounts.push(AccountMeta::new_readonly(pool_v2_pda, false));
    }
    // remaining_accounts always: buyback_fee_recipient + its wSOL ATA
    accounts.push(AccountMeta::new_readonly(*buyback_fee_recipient, false));
    accounts.push(AccountMeta::new(buyback_wsol_ata, false));

    Ok(Instruction {
        program_id: amm_program,
        accounts,
        data,
    })
}

/// Build Create ATA instruction for Token-2022 (idempotent)
fn build_create_ata_instruction(owner: &Pubkey, mint: &Pubkey, token_prog: &Pubkey) -> Instruction {
    spl_associated_token_account::instruction::create_associated_token_account_idempotent(
        owner, owner, mint, token_prog,
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
        owner,
        &wsol_mint,
        &spl_token::id(),
    );
    vec![
        spl_associated_token_account::instruction::create_associated_token_account_idempotent(
            owner,
            owner,
            &wsol_mint,
            &spl_token::id(),
        ),
        solana_sdk::system_instruction::transfer(owner, &wsol_ata, lamports),
        spl_token::instruction::sync_native(&spl_token::id(), &wsol_ata).expect("valid"),
    ]
}

/// Build instruction to close a wSOL token account and reclaim SOL.
fn build_close_wsol_instruction(owner: &Pubkey) -> Instruction {
    let wsol_mint = Pubkey::from_str(SOL_MINT).expect("valid SOL_MINT");
    let wsol_ata = spl_associated_token_account::get_associated_token_address_with_program_id(
        owner,
        &wsol_mint,
        &spl_token::id(),
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
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("amount must be a valid number".into()))?;
    let slippage_bps = ((params.slippage.unwrap_or(10.0)) * 100.0) as u64;
    let denominated_in_sol = params.denominated_in_sol.unwrap_or(true);

    if denominated_in_sol {
        let sol_lamports = (amount * 1_000_000_000.0) as u64;
        // Use real pool reserves (k-invariant): tokens_out = v_tok * sol_in / (v_sol + sol_in)
        let tokens = if v_sol > 0 && v_tok > 0 {
            ((v_tok as u128 * sol_lamports as u128) / (v_sol as u128 + sol_lamports as u128)) as u64
        } else {
            estimate_tokens_for_sol(sol_lamports)
        };
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
    let amount: f64 = params
        .amount
        .parse()
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
            return Err(AppError::InvalidParams(
                "SOL amount exceeds pool liquidity".into(),
            ));
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
    let microlamports =
        ((priority_fee_sol * 1_000_000_000.0 * 1_000_000.0) as u64 / compute_units).max(1);
    [
        solana_sdk::compute_budget::ComputeBudgetInstruction::set_compute_unit_limit(
            compute_units as u32,
        ),
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
    let has_metadata = params
        .metadata_uri
        .as_deref()
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false);
    let has_image = params
        .image_url
        .as_deref()
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false);
    if !has_metadata && !has_image {
        return Err(AppError::InvalidParams(
            "Token image is required. Upload via POST /upload/image, then POST /upload/metadata."
                .into(),
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
// Bonding Curve — Global Constants Query (live on-chain fetch)
// ──────────────────────────────────────────────────────────────────────────────

/// Fetch pump.fun global account on-chain and parse the curve initial constants.
///
/// The Global account stores the canonical initial reserves used to seed every
/// new bonding curve, plus the protocol fee bps and total supply. Pump.fun has
/// updated these in the past (graduation threshold went 85→75 SOL once), so
/// reading them live avoids stale hardcoded answers in analytical questions.
///
/// Layout (after 8-byte Anchor discriminator):
///   1   bool  initialized
///   32  Pubkey authority
///   32  Pubkey fee_recipient
///   8   u64   initial_virtual_token_reserves
///   8   u64   initial_virtual_sol_reserves
///   8   u64   initial_real_token_reserves
///   8   u64   token_total_supply
///   8   u64   fee_basis_points
/// = 113 bytes minimum
///
/// Falls back to compile-time constants if RPC fetch fails — the caller still
/// gets a usable answer for the math, just flagged as `source: "fallback"`.
///
/// Optional `params` for deterministic on-server math (model can delegate
/// arithmetic instead of doing it itself):
///   - `from_mc_sol` + `to_mc_sol` (both required together):
///         returns net + gross SOL needed to push market cap from one to
///         the other on the bonding curve, plus intermediate v_sol values.
///   - `sol_in_fresh`: SOL spent on a brand-new curve → `tokens_received`.
///   - `tokens_out_fresh`: tokens to receive on a brand-new curve →
///         `sol_needed_net` + `sol_needed_gross`.
///   - `mc_to_v_sol`: market cap (SOL) → corresponding `v_sol` along the curve.
///
/// All compute paths use the constant-product invariant (v_sol × v_tok =
/// const) and the live `protocol_fee_bps` so the result reflects the actual
/// protocol parameters, not stale assumptions.
pub async fn build_pumpfun_curve_global(
    rpc: &SolanaRpc,
    params: &serde_json::Value,
) -> Result<BuildResponse, AppError> {
    use solana_client::nonblocking::rpc_client::RpcClient as AsyncRpc;
    use solana_sdk::commitment_config::CommitmentConfig;

    let global_pda = find_global_pda();

    // Try live fetch first; fall back to constants on any error.
    let (v_sol_init, v_tok_init, real_tok_init, total_supply, fee_bps, source) = match async {
        let client = AsyncRpc::new_with_commitment(
            rpc.endpoint().to_string(),
            CommitmentConfig::confirmed(),
        );
        let acc = client
            .get_account(&global_pda)
            .await
            .map_err(|e| AppError::ProtocolError(format!("Fetch pump.fun global PDA: {e}")))?;
        let data = acc.data;
        if data.len() < 113 {
            return Err(AppError::ProtocolError(format!(
                "Pump.fun global account too short ({} bytes, expected ≥113)",
                data.len()
            )));
        }
        // Skip 8-byte discriminator + 1-byte initialized + 64 bytes (2 pubkeys) = 73
        let read_u64 = |off: usize| -> u64 {
            let mut buf = [0u8; 8];
            buf.copy_from_slice(&data[off..off + 8]);
            u64::from_le_bytes(buf)
        };
        Ok::<(u64, u64, u64, u64, u64), AppError>((
            read_u64(73),  // initial_virtual_token_reserves
            read_u64(81),  // initial_virtual_sol_reserves
            read_u64(89),  // initial_real_token_reserves
            read_u64(97),  // token_total_supply
            read_u64(105), // fee_basis_points
        ))
    }
    .await
    {
        Ok((vt, vs, rt, ts, fb)) => (vs, vt, rt, ts, fb, "on_chain"),
        Err(e) => {
            tracing::warn!("pumpfun_curve_global on-chain fetch failed, using constants: {e}");
            (
                INITIAL_VIRTUAL_SOL_RESERVES,
                INITIAL_VIRTUAL_TOKEN_RESERVES,
                793_100_000_000_000_u64, // ~793.1M tokens × 10^6 (canonical bonded supply)
                1_000_000_000_000_000_u64, // 1B tokens × 10^6
                100_u64,                 // 1% protocol fee
                "fallback",
            )
        }
    };

    // Convert to human-readable scales for the LLM.
    let v_sol_init_sol = v_sol_init as f64 / 1e9; // lamports → SOL
    let v_tok_init_units = v_tok_init as f64 / PUMP_TOKEN_DECIMALS as f64; // base → tokens
    let real_tok_init_units = real_tok_init as f64 / PUMP_TOKEN_DECIMALS as f64;
    let total_supply_units = total_supply as f64 / PUMP_TOKEN_DECIMALS as f64;

    // Derived: starting price (SOL per token) = v_sol / v_tok.
    let start_price_sol = if v_tok_init > 0 {
        (v_sol_init as f64) / (v_tok_init as f64)
    } else {
        0.0
    };

    // Bonded SOL needed to graduate: when complete=true, ~85 SOL of net buys
    // have entered the curve (after fees). This is the canonical threshold for
    // tokens launched in the current pump.fun version.
    let graduation_sol_target: f64 = 85.0;

    // ── Optional deterministic compute paths ──────────────────────────────────
    // The model can pass any of the inputs below to delegate the arithmetic to
    // the server. Output appears under `computed` in the response. Keeping the
    // tool as a single endpoint (vs. one tool per math case) keeps the LLM
    // tool surface small and avoids per-formula prompt bloat.
    let const_product = (v_sol_init as f64) * (v_tok_init as f64);
    let supply_units = total_supply_units;
    let fee_pct = (fee_bps as f64) / 10_000.0;

    // Helper: market cap (SOL) → required v_sol along the curve.
    // Derivation: mc = (v_sol × supply) / v_tok, and v_sol × v_tok = const,
    // so v_tok = const / v_sol → mc = (v_sol² × supply) / const →
    // v_sol = sqrt(mc × const / supply).
    let v_sol_from_mc = |mc_sol: f64| -> f64 {
        if supply_units <= 0.0 || const_product <= 0.0 {
            return 0.0;
        }
        // Convert mc_sol to lamports for unit consistency with v_sol/v_tok (base units).
        let mc_lamports = mc_sol * 1e9;
        let supply_base = total_supply as f64;
        let v_sol_base = (mc_lamports * const_product / supply_base).sqrt();
        v_sol_base / 1e9 // lamports → SOL
    };

    let mut computed = serde_json::Map::new();

    // 1) market-cap delta: from_mc_sol + to_mc_sol → SOL needed to push curve
    if let (Some(from_mc), Some(to_mc)) = (
        params.get("from_mc_sol").and_then(|v| v.as_f64()),
        params.get("to_mc_sol").and_then(|v| v.as_f64()),
    ) {
        if from_mc >= 0.0 && to_mc > from_mc {
            let v_sol_from = v_sol_from_mc(from_mc);
            let v_sol_to = v_sol_from_mc(to_mc);
            let net_sol = v_sol_to - v_sol_from;
            let gross_sol = if fee_pct < 1.0 {
                net_sol / (1.0 - fee_pct)
            } else {
                net_sol
            };
            computed.insert("mc_delta".into(), json!({
                "from_mc_sol":      from_mc,
                "to_mc_sol":        to_mc,
                "v_sol_at_from":    v_sol_from,
                "v_sol_at_to":      v_sol_to,
                "sol_needed_net":   net_sol,
                "sol_needed_gross": gross_sol,
                "fee_pct_applied":  fee_pct,
                "method":           "v_sol = √(mc × const_product / total_supply); gross = net / (1 − fee_pct)",
            }));
        }
    }

    // 2) buy on a fresh curve: sol_in_fresh → tokens_received
    if let Some(sol_in) = params.get("sol_in_fresh").and_then(|v| v.as_f64()) {
        if sol_in > 0.0 {
            // After fee deduction, only (1 − fee_pct) × sol_in actually enters the curve.
            let sol_in_after_fee = sol_in * (1.0 - fee_pct);
            let sol_in_lamports = sol_in_after_fee * 1e9;
            let tokens_out_base =
                (v_tok_init as f64) * sol_in_lamports / ((v_sol_init as f64) + sol_in_lamports);
            let tokens_out_units = tokens_out_base / (PUMP_TOKEN_DECIMALS as f64);
            // Effective price including fee.
            let effective_price = if tokens_out_units > 0.0 {
                sol_in / tokens_out_units
            } else {
                0.0
            };
            computed.insert("buy_fresh".into(), json!({
                "sol_in_gross":          sol_in,
                "sol_in_after_fee":      sol_in_after_fee,
                "tokens_received":       tokens_out_units,
                "effective_price_sol":   effective_price,
                "fee_pct_applied":       fee_pct,
                "method":                "tokens_out = (v_tok_init × sol_in_after_fee) / (v_sol_init + sol_in_after_fee)",
            }));
        }
    }

    // 3) reverse on a fresh curve: tokens_out_fresh → SOL needed
    if let Some(tokens_out) = params.get("tokens_out_fresh").and_then(|v| v.as_f64()) {
        if tokens_out > 0.0 && tokens_out < v_tok_init_units {
            let tokens_out_base = tokens_out * (PUMP_TOKEN_DECIMALS as f64);
            // sol_in (lamports, post-fee) = (v_sol × tokens_out) / (v_tok − tokens_out)
            let sol_in_lamports =
                (v_sol_init as f64) * tokens_out_base / ((v_tok_init as f64) - tokens_out_base);
            let sol_in_net = sol_in_lamports / 1e9;
            let sol_in_gross = if fee_pct < 1.0 {
                sol_in_net / (1.0 - fee_pct)
            } else {
                sol_in_net
            };
            computed.insert("buy_fresh_reverse".into(), json!({
                "tokens_target":    tokens_out,
                "sol_needed_net":   sol_in_net,
                "sol_needed_gross": sol_in_gross,
                "fee_pct_applied":  fee_pct,
                "method":           "sol_in = (v_sol_init × tokens_out) / (v_tok_init − tokens_out); gross = net / (1 − fee_pct)",
            }));
        }
    }

    // 4) market-cap → v_sol lookup (handy for "what v_sol corresponds to N SOL mc")
    if let Some(mc_sol) = params.get("mc_to_v_sol").and_then(|v| v.as_f64()) {
        if mc_sol > 0.0 {
            let v_sol = v_sol_from_mc(mc_sol);
            let v_tok = if v_sol > 0.0 {
                const_product / (v_sol * 1e9)
            } else {
                0.0
            };
            computed.insert("mc_to_v_sol".into(), json!({
                "mc_sol":           mc_sol,
                "v_sol":            v_sol,
                "v_tok":            v_tok,
                "method":           "v_sol = √(mc × const_product / total_supply); v_tok = const / v_sol",
            }));
        }
    }

    let result = json!({
        "program_id": PUMP_FUN_PROGRAM_ID,
        "global_pda": global_pda.to_string(),
        "source": source,
        "constants": {
            "initial_virtual_sol_reserves_lamports": v_sol_init,
            "initial_virtual_sol_reserves_sol":     v_sol_init_sol,
            "initial_virtual_token_reserves_base":  v_tok_init,
            "initial_virtual_token_reserves":       v_tok_init_units,
            "initial_real_token_reserves_base":     real_tok_init,
            "initial_real_token_reserves":          real_tok_init_units,
            "token_total_supply_base":              total_supply,
            "token_total_supply":                   total_supply_units,
            "protocol_fee_bps":                     fee_bps,
            "token_decimals":                       6,
            "const_product_base":                   const_product,
        },
        "derived": {
            "starting_price_sol_per_token": start_price_sol,
            "graduation_sol_target":        graduation_sol_target,
            "curve_formula":                "constant_product: tokens_out = (v_tok × sol_in) / (v_sol + sol_in)",
            "reverse_formula":              "sol_in = (v_sol × tokens_out) / (v_tok − tokens_out)",
            "market_cap_formula":           "market_cap_sol = (v_sol × token_total_supply) / v_tok",
            "v_sol_from_mc_formula":        "v_sol = √(mc × const_product / total_supply)",
        },
        "computed": computed,
        "notes": [
            "v_sol and v_tok are VIRTUAL reserves — they grow as the curve fills.",
            "Pass from_mc_sol+to_mc_sol, sol_in_fresh, tokens_out_fresh, or mc_to_v_sol for deterministic server-side math.",
            "Token migrates to PumpSwap AMM at ~85 SOL of bonded volume.",
        ],
    });

    let computed_summary = if !result["computed"]
        .as_object()
        .map(|m| m.is_empty())
        .unwrap_or(true)
    {
        format!(
            ", computed: {}",
            serde_json::to_string(&result["computed"]).unwrap_or_default()
        )
    } else {
        String::new()
    };

    Ok(pf_response(
        "pumpfun_curve_global",
        format!(
            "Pump.fun curve — v_sol: {:.2} SOL, v_tok: {:.0}, fee: {}bps, source: {}{}",
            v_sol_init_sol, v_tok_init_units, fee_bps, source, computed_summary
        ),
        result,
    ))
}

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

    // Use frontend-provided mint pubkey when available (preferred path).
    // The frontend generates the keypair, passes only the pubkey here, and
    // signs the transaction itself after wallet approval — so Phantom's
    // simulation sees no stale partial signature to invalidate.
    let (mint_keypair, mint_pubkey) = match &params.mint_pubkey {
        Some(pk_str) => {
            let pk = Pubkey::from_str(pk_str)
                .map_err(|_| AppError::InvalidParams("Invalid mintPubkey".into()))?;
            (None, pk)
        }
        None => {
            let kp = Keypair::new();
            let pk = kp.pubkey();
            (Some(kp), pk)
        }
    };

    // Step 1: Resolve metadata URI (pre-uploaded to our own storage, no IPFS)
    let metadata_uri = resolve_metadata_uri(params)?;

    // Step 2: Build the create_v2 instruction (Token-2022)
    //
    // Mayhem mode is not offered. A Mayhem token does not trade through the
    // bonding curve, so the dev-buy that follows the launch — and every later
    // trade — has to be routed through PumpPortal, who take 0.5% of it. Its
    // own instruction arguments are still undecoded, so we cannot build those
    // trades ourselves. Offering a switch that quietly sends the user's money
    // through a third party, for a volatility gimmick, is not a trade worth
    // making. `create_v2` still takes the flag; we always pass false.
    let is_mayhem = false;
    let is_cashback = params
        .cashback
        .as_deref()
        .map(|s| s == "true")
        .unwrap_or(false);
    let is_tokenized_agent = params
        .tokenized_agent
        .as_deref()
        .map(|s| s == "true")
        .unwrap_or(false);

    // The user may still ask for Mayhem in words; say why it is not there
    // rather than launching something different from what they asked for.
    if params.mayhem_mode.as_deref() == Some("true") {
        return Err(AppError::InvalidParams(
            "Mayhem Mode isn't available here. Those tokens trade outside the bonding \
             curve, which means every trade on them would have to be routed through a \
             third party that takes a cut. The launch works the same without it."
                .into(),
        ));
    }

    // Tokenized Agent flag has no slot in pump.fun's create_v2 args struct (only
    // `is_mayhem_mode` and `is_cashback_enabled` exist). Implementing it needs a new
    // field/ix from pump.fun's IDL — not just a validation tweak. Reject until ready.
    if is_tokenized_agent {
        return Err(AppError::InvalidParams(
            "Tokenized Agent is not yet supported by this app. Disable the toggle and try again."
                .into(),
        ));
    }

    // Pair-with-USDC needs a different create instruction (bonding curve quoted in
    // USDC, with a USDC vault instead of sol_vault). create_v2 only builds a SOL
    // curve, and neither our IDL nor PumpPortal exposes the USDC variant — so
    // silently creating a SOL token here would be wrong (the initial buy would also
    // be SOL, not USDC). Reject clearly until the USDC create is implemented.
    let is_pair_with_usdc = params
        .pair_with_usdc
        .as_deref()
        .map(|s| s == "true")
        .unwrap_or(false);
    if is_pair_with_usdc {
        return Err(AppError::InvalidParams(
            "Pairing with USDC isn't supported yet — this app can only launch SOL-paired tokens. \
             Turn off \"Pair with USDC\" and try again."
                .into(),
        ));
    }

    let create_ix = build_create_v2_instruction(
        creator_pubkey,
        &mint_pubkey,
        &params.name,
        &params.symbol,
        &metadata_uri,
        is_mayhem,
        is_cashback,
    )?;

    tracing::info!(
        creator = %creator_pubkey,
        mint = %mint_pubkey,
        name = %params.name,
        symbol = %params.symbol,
        "Building pump.fun Create transaction"
    );

    // Step 3: Build the CREATE transaction (create_v2 only).
    //
    // The initial dev-buy is NOT bundled here. Two reasons:
    //   1. Size: create_v2 (16 accounts) + ATA + buy (~9 more) blows past Solana's
    //      1232-byte legacy-tx limit for longer names/symbols/URIs ("Transaction
    //      too large: 1241 > 1232").
    //   2. Correctness: Mayhem-mode tokens do NOT trade through the standard
    //      bonding-curve buy — they route through the Mayhem program. Hand-building
    //      that (23 accounts, undocumented PDAs) risks real funds. Instead the
    //      frontend fetches the buy via `pumpfun_initial_buy` (PumpPortal trade-local,
    //      which builds the correct tx for ANY pool: bonding-curve or Mayhem) right
    //      after the create tx confirms and the token exists on-chain.
    let initial_buy_sol = params
        .initial_buy_amount
        .as_ref()
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    // priority_fee_sol is the total SOL budget for priority; convert to price-per-CU.
    let cu_price =
        |units: u64| ((priority_fee_sol * 1_000_000_000.0 * 1_000_000.0) as u64 / units).max(1);

    // create_v2 needs ~400k CU (mayhem/cashback registration included).
    const CREATE_CU: u64 = 400_000;
    let create_instructions = vec![
        solana_sdk::compute_budget::ComputeBudgetInstruction::set_compute_unit_limit(
            CREATE_CU as u32,
        ),
        solana_sdk::compute_budget::ComputeBudgetInstruction::set_compute_unit_price(cu_price(
            CREATE_CU,
        )),
        // Create instruction (cashback accounts are in remaining_accounts inside create_ix)
        create_ix,
    ];

    // The frontend does the initial buy as a follow-up (see doc above).
    let initial_buy = if initial_buy_sol > 0.0 {
        Some(InitialBuyInfo {
            mint: mint_pubkey.to_string(),
            amount_sol: initial_buy_sol,
            mayhem: is_mayhem,
        })
    } else {
        None
    };

    // Step 4: Get recent blockhash
    let blockhash = rpc
        .get_latest_blockhash_with_retry()
        .map_err(|e| AppError::Internal(format!("Failed to get blockhash: {}", e)))?;

    // Step 5: Build the CREATE transaction
    let create_message =
        Message::new_with_blockhash(&create_instructions, Some(creator_pubkey), &blockhash);

    let mut transaction = Transaction::new_unsigned(create_message);

    // Step 6: Sign with mint keypair only when backend generated it.
    // When mintPubkey was provided by the frontend, we skip signing here —
    // the frontend will add the mint signature after wallet approval.
    if let Some(ref kp) = mint_keypair {
        transaction.partial_sign(&[kp], blockhash);
    }

    tracing::info!(
        num_signatures = transaction.signatures.len(),
        num_instructions = transaction.message.instructions.len(),
        fee_payer = %transaction.message.account_keys[0],
        "Created unsigned pump.fun create transaction"
    );

    // Step 7: Serialize CREATE to base64
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
        description: format!("Launch {} ({}) on Pump.fun", params.symbol, params.name),
        estimated_fee: format!("~{} SOL", PUMP_FUN_CREATE_FEE + initial_buy_sol),
        params: params.clone(),
        warnings,
        requires_approval: true,
    };

    Ok(LaunchBuildResult {
        transaction_base64: tx_base64,
        initial_buy,
        preview,
    })
}

// Regular buy/sell use direct on-chain Anchor instructions (no PumpPortal).
// The ONE exception is the token-launch initial dev-buy below: a just-created token
// isn't in pump.fun's `/coins` index yet AND Mayhem-mode tokens don't trade via the
// standard bonding curve — so PumpPortal (which derives the correct tx from on-chain
// state for any pool) is the safe way to build that specific buy.

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
        .map_err(|e| {
            tracing::warn!("PumpFun API request error: {e}");
            AppError::Internal(
                "Pump.fun is temporarily unavailable. Please try again in a moment.".into(),
            )
        })?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        tracing::warn!("PumpFun API {status}: {body}");
        return Err(AppError::Internal(
            "Pump.fun is temporarily unavailable. Please try again in a moment.".into(),
        ));
    }
    resp.json::<Value>().await.map_err(|e| {
        tracing::warn!("PumpFun API parse error: {e}");
        AppError::Internal("Pump.fun returned an unexpected response. Please try again.".into())
    })
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
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunMintParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_mint_params(params)?;
    let mint = params
        .mint
        .as_deref()
        .or(params.token.as_deref())
        .unwrap_or("");
    let data = pumpfun_get(http, &format!("/coins/{mint}")).await?;
    let name = data.get("name").and_then(|v| v.as_str()).unwrap_or(mint);
    let mc = data
        .get("usd_market_cap")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let complete = data
        .get("complete")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let status = if complete {
        "graduated"
    } else {
        "bonding curve"
    };
    Ok(pf_response(
        "pumpfun_token_info",
        format!("{name} ({mint:.8}…) — ${mc:.0} mcap — {status}"),
        data,
    ))
}

pub async fn build_pumpfun_trending(
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunListParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(20).min(50);
    let offset = params.offset.unwrap_or(0);
    let data = pumpfun_get(
        http,
        &format!(
            "/coins?offset={offset}&limit={limit}&sort=market_cap&order=DESC&includeNsfw=false"
        ),
    )
    .await?;
    let arr = data
        .as_array()
        .or_else(|| data.get("tokens").and_then(|v| v.as_array()));
    let count = arr.map(|a| a.len()).unwrap_or(0);
    Ok(pf_response(
        "pumpfun_trending",
        format!("{count} trending tokens by market cap"),
        data,
    ))
}

pub async fn build_pumpfun_new(
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunListParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(20).min(50);
    let offset = params.offset.unwrap_or(0);
    // frontend-api-v3 has no /coins/latest route (it 404s as a mint lookup);
    // newest = the /coins list sorted by creation time, descending.
    let data = pumpfun_get(http, &format!("/coins?offset={offset}&limit={limit}&sort=created_timestamp&order=DESC&includeNsfw=false")).await?;
    let arr = data
        .as_array()
        .or_else(|| data.get("tokens").and_then(|v| v.as_array()));
    let count = arr.map(|a| a.len()).unwrap_or(0);
    Ok(pf_response(
        "pumpfun_new",
        format!("{count} newest tokens"),
        data,
    ))
}

pub async fn build_pumpfun_graduating(
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunListParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(20).min(50);
    // Tokens currently on bonding curve (live): use the /coins/currently-live endpoint
    let data = pumpfun_get(
        http,
        &format!("/coins/currently-live?offset=0&limit={limit}&includeNsfw=false"),
    )
    .await?;
    // Filter for tokens with high bonding curve progress (virtual_sol > 75% of 85 SOL graduation target)
    let graduation_target_sol: f64 = 85_000_000_000.0; // 85 SOL in lamports
    let threshold = graduation_target_sol * 0.75;
    let coins = data
        .as_array()
        .or_else(|| data.get("tokens").and_then(|v| v.as_array()));
    let graduating: Vec<Value> = coins
        .map(|arr| {
            arr.iter()
                .filter(|c| {
                    let v_sol = c
                        .get("virtual_sol_reserves")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0);
                    let complete = c.get("complete").and_then(|v| v.as_bool()).unwrap_or(false);
                    !complete && v_sol >= threshold
                })
                .take(limit as usize)
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    let count = graduating.len();
    Ok(pf_response(
        "pumpfun_graduating",
        format!("{count} tokens approaching graduation (>75% bonding curve)"),
        json!(graduating),
    ))
}

pub async fn build_pumpfun_koth(
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunListParams,
) -> Result<BuildResponse, AppError> {
    let limit = params.limit.unwrap_or(10).min(20);
    // frontend-api-v3 dropped the /coins/king-of-the-hill route. KOTH ==
    // the highest-market-cap tokens still on the bonding curve, so query the
    // /coins list sorted by market cap with the not-yet-graduated filter.
    let data = pumpfun_get(http, &format!("/coins?offset=0&limit={limit}&sort=market_cap&order=DESC&complete=false&includeNsfw=false")).await?;
    let arr = data
        .as_array()
        .or_else(|| data.get("tokens").and_then(|v| v.as_array()));
    let count = arr.map(|a| a.len()).unwrap_or(1); // single object or array
    Ok(pf_response(
        "pumpfun_koth",
        format!("{count} King of the Hill token(s)"),
        data,
    ))
}

pub async fn build_pumpfun_search(
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunSearchParams,
) -> Result<BuildResponse, AppError> {
    let query = params.query.as_deref().unwrap_or("").trim().to_string();
    if query.is_empty() {
        return Err(AppError::InvalidParams("search query is required".into()));
    }
    let limit = params.limit.unwrap_or(10).min(50);
    let encoded = urlencoding_encode(&query);
    // frontend-api-v3 has no /coins/search route (it 404s as a mint lookup);
    // search = the /coins list filtered by the searchTerm query parameter.
    let data = pumpfun_get(
        http,
        &format!("/coins?searchTerm={encoded}&offset=0&limit={limit}&includeNsfw=false"),
    )
    .await?;
    let arr = data
        .as_array()
        .or_else(|| data.get("coins").and_then(|v| v.as_array()));
    let count = arr.map(|a| a.len()).unwrap_or(0);
    Ok(pf_response(
        "pumpfun_search",
        format!("{count} results for '{query}'"),
        data,
    ))
}

pub async fn build_pumpfun_comments(
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunMintParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_mint_params(params)?;
    let mint = params
        .mint
        .as_deref()
        .or(params.token.as_deref())
        .unwrap_or("");
    // Correct path is `/replies/{mint}` with limit/offset/user/reverseOrder all
    // required (RepliesController_getReplies). Unlike /coins and /users, this
    // route is JWT-gated behind a pump.fun user session — unauthenticated
    // server-to-server calls 404. Degrade gracefully rather than surfacing a
    // raw "data source error" so the LLM can tell the user comments aren't
    // available instead of implying the token is broken.
    let path = format!("/replies/{mint}?limit=50&offset=0&user=&reverseOrder=true");
    match pumpfun_get(http, &path).await {
        Ok(data) => {
            let count = data
                .as_array()
                .map(|a| a.len())
                .or_else(|| {
                    data.get("replies")
                        .and_then(|v| v.as_array())
                        .map(|a| a.len())
                })
                .unwrap_or(0);
            Ok(pf_response(
                "pumpfun_comments",
                format!("{count} comment(s) for {mint}"),
                data,
            ))
        }
        Err(_) => Ok(pf_response(
            "pumpfun_comments",
            format!("Comments for {mint} are not available"),
            json!({
                "mint": mint,
                "available": false,
                "reason": "pump.fun comments require an authenticated pump.fun session and cannot be fetched server-side",
                "replies": [],
                "count": 0,
            }),
        )),
    }
}

pub async fn build_pumpfun_user(
    http: &reqwest::Client,
    wallet: &str,
    params: &PumpFunUserParams,
) -> Result<BuildResponse, AppError> {
    let target = params.wallet.as_deref().unwrap_or(wallet);
    // User profile is on the main API; /users/{wallet} path confirmed in v3
    let data = pumpfun_get(http, &format!("/users/{target}")).await?;
    let username = data
        .get("username")
        .and_then(|v| v.as_str())
        .or_else(|| data.get("name").and_then(|v| v.as_str()))
        .unwrap_or(target);
    Ok(pf_response(
        "pumpfun_user",
        format!("PumpFun user: {username}"),
        data,
    ))
}

pub async fn build_pumpfun_bonding_curve(
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunMintParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_mint_params(params)?;
    let mint = params
        .mint
        .as_deref()
        .or(params.token.as_deref())
        .unwrap_or("");
    let coin = pumpfun_get(http, &format!("/coins/{mint}")).await?;
    let v_sol = coin
        .get("virtual_sol_reserves")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let v_tok = coin
        .get("virtual_token_reserves")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let complete = coin
        .get("complete")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let mc = coin
        .get("usd_market_cap")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    // price = virtual_sol_reserves / virtual_token_reserves (in SOL per base unit, x10^3 for decimals)
    let price_sol = if v_tok > 0.0 {
        v_sol / v_tok / 1e3
    } else {
        0.0
    };
    let bc_addr = coin
        .get("bonding_curve")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");
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
    Ok(pf_response(
        "pumpfun_bonding_curve",
        format!("Bonding curve for {mint} — price: {price_sol:.8} SOL — ${mc:.0} mcap"),
        result,
    ))
}

fn urlencoding_encode(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            'A'..='Z' | 'a'..='z' | '0'..='9' | '-' | '_' | '.' | '~' => c.to_string(),
            ' ' => "%20".to_string(),
            _ => format!("%{:02X}", c as u8),
        })
        .collect()
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

    // Fetch coin data: creator, token_program, and reserves from pump.fun API.
    // Using the API's token_program is authoritative and avoids an extra RPC call
    // that can fail under rate limits, causing a silent fallback to SPL Token and
    // then a ConstraintSeeds error on associated_bonding_curve.
    // Everything that decides the trade comes from the chain. The API is asked
    // afterwards, and only for a display name — a token minted seconds ago is
    // absent from it, and that absence used to fail the whole buy.
    let curve = read_bonding_curve(rpc, &mint).await?;

    // Graduated tokens (complete: true) live on an external AMM (Raydium for older
    // tokens, PumpSwap for newer) — route through Jupiter, which handles either.
    if curve.complete {
        tracing::info!(mint = %mint, "Token graduated — routing pumpfun_buy → Jupiter aggregator");
        return build_graduated_swap(http, rpc, wallet, params, true).await;
    }

    let creator = curve.creator.ok_or_else(|| {
        AppError::ProtocolError("That token's bonding curve does not name a creator".into())
    })?;
    let v_sol = curve.virtual_sol_reserves;
    let v_tok = curve.virtual_token_reserves;
    let token_prog = read_token_program(rpc, &mint).await;
    tracing::info!(mint = %mint, token_program = %token_prog, "Token program from chain for buy");

    // Cosmetic only. A missing name costs the card a label; it must never cost
    // the user the trade.
    let name = pumpfun_get(http, &format!("/coins/{}", params.mint))
        .await
        .ok()
        .and_then(|c| c.get("name").and_then(|v| v.as_str()).map(str::to_string))
        .unwrap_or_else(|| params.mint.clone());

    let (token_amount, max_sol_cost) = parse_buy_amounts(params, v_sol, v_tok)?;

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let [cu_limit_ix, cu_price_ix] = build_compute_budget_instructions(priority_fee_sol);

    // Fetch fee_recipient dynamically from on-chain global account (falls back to known address)
    let fee_recipient = fetch_pumpfun_fee_recipient(rpc).await;

    let create_ata_ix = build_create_ata_instruction(&buyer, &mint, &token_prog);
    let buy_ix = build_buy_instruction(
        &buyer,
        &mint,
        &creator,
        &fee_recipient,
        &token_prog,
        token_amount,
        max_sol_cost,
    )?;

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
            description: format!(
                "Buy {} on Pump.fun bonding curve ({})",
                name,
                &params.mint[..8.min(params.mint.len())]
            ),
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

    // Same as the buy path: the chain decides, the API is decoration.
    let curve = read_bonding_curve(rpc, &mint).await?;

    // Graduated tokens live on an external AMM (Raydium/PumpSwap) — route through
    // Jupiter, which handles either venue.
    if curve.complete {
        tracing::info!(mint = %mint, "Token graduated — routing pumpfun_sell → Jupiter aggregator");
        return build_graduated_swap(http, rpc, wallet, params, false).await;
    }

    let creator = curve.creator.ok_or_else(|| {
        AppError::ProtocolError("That token's bonding curve does not name a creator".into())
    })?;
    let v_sol = curve.virtual_sol_reserves;
    let v_tok = curve.virtual_token_reserves;
    let token_prog = read_token_program(rpc, &mint).await;
    tracing::info!(mint = %mint, token_program = %token_prog, "Token program from chain for sell");

    let name = pumpfun_get(http, &format!("/coins/{}", params.mint))
        .await
        .ok()
        .and_then(|c| c.get("name").and_then(|v| v.as_str()).map(str::to_string))
        .unwrap_or_else(|| params.mint.clone());

    let (token_amount, min_sol_output) = parse_sell_amounts(params, v_sol, v_tok)?;

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let [cu_limit_ix, cu_price_ix] = build_compute_budget_instructions(priority_fee_sol);

    let fee_recipient = fetch_pumpfun_fee_recipient(rpc).await;

    let sell_ix = build_sell_instruction(
        &seller,
        &mint,
        &creator,
        &fee_recipient,
        &token_prog,
        token_amount,
        min_sol_output,
    )?;
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
            description: format!(
                "Sell {} on Pump.fun bonding curve ({})",
                name,
                &params.mint[..8.min(params.mint.len())]
            ),
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
    let complete = coin
        .get("complete")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !complete {
        return Err(AppError::InvalidParams(
            "Token has not graduated to PumpSwap AMM yet. Use pumpfun_buy for bonding curve tokens.".into()
        ));
    }

    // Use pool address directly from API — PDA derivation is unreliable
    let pool_str = coin
        .get("pool_address")
        .or_else(|| coin.get("raydium_pool"))
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            AppError::Internal("Missing pool_address in coin data for graduated token".into())
        })?;
    let pool = Pubkey::from_str(pool_str)
        .map_err(|_| AppError::Internal(format!("Invalid pool address from API: {pool_str}")))?;

    // Read pool data (coin_creator + vault addresses) and protocol fee concurrently.
    // Pool vaults are stored in the pool struct at offsets 139/171 — NOT derivable as ATAs.
    let api_fallback_sol = coin
        .get("virtual_sol_reserves")
        .and_then(|v| v.as_f64())
        .unwrap_or(INITIAL_VIRTUAL_SOL_RESERVES as f64) as u64;
    let api_fallback_tok = coin
        .get("virtual_token_reserves")
        .and_then(|v| v.as_f64())
        .unwrap_or(INITIAL_VIRTUAL_TOKEN_RESERVES as f64) as u64;

    let (pool_data_result, global_config_result, base_prog_result) = tokio::join!(
        fetch_pumpswap_pool_data(rpc, &pool),
        fetch_pumpswap_global_config(rpc),
        fetch_mint_token_program(rpc, &mint),
    );

    let pool_data = pool_data_result?;
    let global_cfg = global_config_result?;
    let base_token_program = base_prog_result?;
    let coin_creator = pool_data.coin_creator;
    let is_cashback_coin = pool_data.is_cashback_coin;
    let pool_base_vault = pool_data.pool_base_token_account;
    let pool_quote_vault = pool_data.pool_quote_token_account;
    let fee_recipient = global_cfg.protocol_fee_recipient;
    let buyback_fee_recipient = global_cfg.buyback_fee_recipient;

    tracing::info!(
        pool = %pool, coin_creator = %coin_creator,
        pool_base_vault = %pool_base_vault, pool_quote_vault = %pool_quote_vault,
        is_cashback_coin, base_token_program = %base_token_program, "PumpSwap pool data read from chain"
    );

    // Fetch reserves from the actual vault accounts (not ATA derivation)
    let (v_tok, v_sol) = match fetch_pumpswap_pool_reserves(
        rpc,
        &pool_base_vault,
        &pool_quote_vault,
    )
    .await
    {
        Ok(reserves) => reserves,
        Err(e) => {
            tracing::warn!(error = %e, pool = %pool, "Failed to fetch PumpSwap pool reserves, falling back to API values");
            (api_fallback_tok, api_fallback_sol)
        }
    };

    // For PumpSwap: base_amount_out = tokens, max_quote_amount_in = SOL (wSOL)
    let (base_amount_out, max_quote_in) = parse_buy_amounts(params, v_sol, v_tok)?;

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let [cu_limit_ix, cu_price_ix] = build_compute_budget_instructions(priority_fee_sol);

    // Create base and quote (wSOL legacy SPL) ATAs. The base ATA must be created
    // under the mint's ACTUAL owner program (classic SPL for older tokens like
    // Fartcoin, Token-2022 for newer launches) — hardcoding Token-2022 fails with
    // IncorrectProgramId. The quote leg (wSOL) is always classic SPL.
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid");
    let create_base_ata_ix = build_create_ata_instruction(&buyer, &mint, &base_token_program);
    let create_quote_ata_ix = build_create_spl_ata_instruction(&buyer, &sol_mint);

    // Wrap SOL → wSOL
    let wrap_ixs = build_wrap_sol_instructions(&buyer, max_quote_in);

    let swap_ix = build_pumpswap_buy_instruction(
        &buyer,
        &pool,
        &mint,
        &coin_creator,
        &fee_recipient,
        &pool_base_vault,
        &pool_quote_vault,
        base_amount_out,
        max_quote_in,
        is_cashback_coin,
        &buyback_fee_recipient,
        &base_token_program,
    )?;
    let close_wsol_ix = build_close_wsol_instruction(&buyer);

    let mut instructions = vec![
        cu_limit_ix,
        cu_price_ix,
        create_base_ata_ix,
        create_quote_ata_ix,
    ];
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
            description: format!(
                "Buy {} on PumpSwap AMM (graduated token {})",
                params.amount,
                &params.mint[..8.min(params.mint.len())]
            ),
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
    let complete = coin
        .get("complete")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !complete {
        return Err(AppError::InvalidParams(
            "Token has not graduated to PumpSwap AMM yet. Use pumpfun_sell for bonding curve tokens.".into()
        ));
    }

    // Use pool address directly from API — PDA derivation is unreliable
    let pool_str = coin
        .get("pool_address")
        .or_else(|| coin.get("raydium_pool"))
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            AppError::Internal("Missing pool_address in coin data for graduated token".into())
        })?;
    let pool = Pubkey::from_str(pool_str)
        .map_err(|_| AppError::Internal(format!("Invalid pool address from API: {pool_str}")))?;

    // Read pool data (coin_creator + vault addresses) and protocol fee concurrently.
    // Pool vaults are stored in the pool struct at offsets 139/171 — NOT derivable as ATAs.
    let api_fallback_sol = coin
        .get("virtual_sol_reserves")
        .and_then(|v| v.as_f64())
        .unwrap_or(INITIAL_VIRTUAL_SOL_RESERVES as f64) as u64;
    let api_fallback_tok = coin
        .get("virtual_token_reserves")
        .and_then(|v| v.as_f64())
        .unwrap_or(INITIAL_VIRTUAL_TOKEN_RESERVES as f64) as u64;

    let (pool_data_result, global_config_result, base_prog_result) = tokio::join!(
        fetch_pumpswap_pool_data(rpc, &pool),
        fetch_pumpswap_global_config(rpc),
        fetch_mint_token_program(rpc, &mint),
    );

    let pool_data = pool_data_result?;
    let global_cfg = global_config_result?;
    let base_token_program = base_prog_result?;
    let coin_creator = pool_data.coin_creator;
    let is_cashback_coin = pool_data.is_cashback_coin;
    let pool_base_vault = pool_data.pool_base_token_account;
    let pool_quote_vault = pool_data.pool_quote_token_account;
    let fee_recipient = global_cfg.protocol_fee_recipient;
    let buyback_fee_recipient = global_cfg.buyback_fee_recipient;

    tracing::info!(
        pool = %pool, coin_creator = %coin_creator,
        pool_base_vault = %pool_base_vault, pool_quote_vault = %pool_quote_vault,
        is_cashback_coin, base_token_program = %base_token_program, "PumpSwap pool data read from chain for sell"
    );

    // Fetch reserves from the actual vault accounts
    let (v_tok, v_sol) = match fetch_pumpswap_pool_reserves(
        rpc,
        &pool_base_vault,
        &pool_quote_vault,
    )
    .await
    {
        Ok(reserves) => reserves,
        Err(e) => {
            tracing::warn!(error = %e, pool = %pool, "Failed to fetch PumpSwap pool reserves, falling back to API values");
            (api_fallback_tok, api_fallback_sol)
        }
    };

    let (base_amount_in, min_quote_out) = parse_sell_amounts(params, v_sol, v_tok)?;

    let priority_fee_sol = params.priority_fee.unwrap_or(0.0005);
    let [cu_limit_ix, cu_price_ix] = build_compute_budget_instructions(priority_fee_sol);

    // Create wSOL ATA to receive SOL proceeds
    let sol_mint = Pubkey::from_str(SOL_MINT).expect("valid");
    let create_quote_ata_ix = build_create_spl_ata_instruction(&seller, &sol_mint);
    let swap_ix = build_pumpswap_sell_instruction(
        &seller,
        &pool,
        &mint,
        &coin_creator,
        &fee_recipient,
        &pool_base_vault,
        &pool_quote_vault,
        base_amount_in,
        min_quote_out,
        is_cashback_coin,
        &buyback_fee_recipient,
        &base_token_program,
    )?;
    let close_wsol_ix = build_close_wsol_instruction(&seller);

    let instructions = vec![
        cu_limit_ix,
        cu_price_ix,
        create_quote_ata_ix,
        swap_ix,
        close_wsol_ix,
    ];

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
            description: format!(
                "Sell {} on PumpSwap AMM (graduated token {})",
                params.amount,
                &params.mint[..8.min(params.mint.len())]
            ),
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
    http: &reqwest::Client,
    _wallet: &str,
    params: &PumpFunMintParams,
) -> Result<BuildResponse, AppError> {
    validate_pumpfun_mint_params(params)?;
    let mint = params
        .mint
        .as_deref()
        .or(params.token.as_deref())
        .unwrap_or("");
    let coin = pumpfun_get(http, &format!("/coins/{mint}")).await?;
    let pool_addr = coin
        .get("pool_address")
        .or_else(|| coin.get("raydium_pool"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let complete = coin
        .get("complete")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

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

    let v_sol = coin
        .get("virtual_sol_reserves")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    let mc = coin
        .get("usd_market_cap")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
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
    Ok(pf_response(
        "pumpswap_pool_info",
        format!(
            "PumpSwap pool for {mint} — ${mc:.0} mcap — pool: {}",
            &pool_addr[..8.min(pool_addr.len())]
        ),
        result,
    ))
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
        let expected_create: [u8; 8] = [
            hash[0], hash[1], hash[2], hash[3], hash[4], hash[5], hash[6], hash[7],
        ];
        assert_eq!(
            CREATE_DISCRIMINATOR, expected_create,
            "Create discriminator mismatch!"
        );
        assert_eq!(CREATE_DISCRIMINATOR, [24, 30, 200, 40, 5, 28, 7, 119]);

        // Buy discriminator
        let mut hasher = Sha256::new();
        hasher.update(b"global:buy");
        let hash = hasher.finalize();
        let expected_buy: [u8; 8] = [
            hash[0], hash[1], hash[2], hash[3], hash[4], hash[5], hash[6], hash[7],
        ];
        assert_eq!(
            BUY_DISCRIMINATOR, expected_buy,
            "Buy discriminator mismatch!"
        );
        assert_eq!(BUY_DISCRIMINATOR, [102, 6, 61, 18, 1, 218, 235, 234]);
    }

    /// Verify PDA derivation
    #[test]
    fn test_pda_derivation() {
        let mint = Pubkey::from_str("So11111111111111111111111111111111111111112")
            .expect("valid SOL mint");

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
            tokenized_agent: None,
            pair_with_usdc: None,
            mint_pubkey: None,
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
            tokenized_agent: None,
            pair_with_usdc: None,
            mint_pubkey: None,
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
            tokenized_agent: None,
            pair_with_usdc: None,
            mint_pubkey: None,
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
            tokenized_agent: None,
            pair_with_usdc: None,
            mint_pubkey: None,
        };
        assert!(validate_launch_params(&params).is_ok());
    }
}

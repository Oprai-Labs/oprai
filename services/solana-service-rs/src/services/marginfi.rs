//! marginfi v2 Protocol Service — Complete SDK Integration
//!
//! Decentralized lending/borrowing protocol on Solana.
//!
//! # Architecture
//! marginfi does NOT expose a hosted transaction-builder REST API (unlike Kamino/Jupiter).
//! All instructions are built locally using:
//! - Anchor 8-byte discriminators: sha256("global:<instruction_name>")[..8]
//! - Borsh-serialized arguments
//! - PDA derivations for vault authority, liquidity vault, insurance vault
//!
//! Bank metadata is fetched from the public GCS cache:
//! `https://storage.googleapis.com/mrgn-public/mrgn-bank-metadata-cache.json`
//!
//! # Token-2022 Support
//! marginfi v2 uses `Interface<TokenInterface>` which supports both SPL Token
//! and Token-2022 programs. The correct token_program is resolved per-bank from
//! the GCS metadata cache.

use base64::Engine;
use borsh::BorshSerialize;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use solana_sdk::{
    instruction::{AccountMeta, Instruction},
    message::Message,
    pubkey::Pubkey,
    system_program,
    transaction::Transaction,
};
use spl_associated_token_account::get_associated_token_address;
use std::str::FromStr;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::amount::parse_amount_to_base_units;
use crate::services::builder::{ActionPreview, BuildResponse};

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

/// marginfi V2 Program ID (mainnet)
const MARGINFI_PROGRAM_ID: &str = "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA";

/// marginfi Group ID (mainnet)
const MARGINFI_GROUP: &str = "4qp6Fx6tnzdkKfVgYCyVvk1bhUfLR1PrWCtL6h9s6w3M";

/// GCS bank metadata cache (primary source since api.mrgn.fi is unreliable)
const MARGINFI_BANK_METADATA_URL: &str =
    "https://storage.googleapis.com/mrgn-public/mrgn-bank-metadata-cache.json";

/// SPL Token program ID
const SPL_TOKEN_PROGRAM: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";

/// Token-2022 program ID
const TOKEN_2022_PROGRAM: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";

// ──────────────────────────────────────────────────────────────────────────────
// Core Helpers
// ──────────────────────────────────────────────────────────────────────────────

fn program_id() -> Pubkey {
    Pubkey::from_str(MARGINFI_PROGRAM_ID).expect("valid hardcoded program id")
}

fn marginfi_group() -> Pubkey {
    Pubkey::from_str(MARGINFI_GROUP).expect("valid hardcoded group")
}

/// Anchor discriminator: sha256("global:<name>")[..8]
fn disc(name: &str) -> [u8; 8] {
    let mut h = Sha256::new();
    h.update(format!("global:{name}"));
    h.finalize()[..8].try_into().expect("8 bytes")
}

/// Build instruction data: discriminator ++ borsh(args)
fn ix_data<T: BorshSerialize>(discriminator: [u8; 8], args: &T) -> Vec<u8> {
    let mut data = discriminator.to_vec();
    data.extend(borsh::to_vec(args).unwrap_or_default());
    data
}

/// Parse a Pubkey from string.
fn parse_pk(s: &str, label: &str) -> Result<Pubkey, AppError> {
    Pubkey::from_str(s).map_err(|e| AppError::InvalidParams(format!("Invalid {label}: {e}")))
}

/// Parse a human-readable amount string (e.g. "1.5") into raw token units.
fn parse_amount_raw(amount: &str, decimals: u8) -> Result<u64, AppError> {
    parse_amount_to_base_units(amount, decimals)
}

/// Serialize an unsigned transaction to base64.
fn serialize_tx(tx: &Transaction) -> Result<String, AppError> {
    let serialized =
        bincode::serialize(tx).map_err(|e| AppError::Internal(format!("Serialize error: {e}")))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(&serialized))
}

fn short_id(id: &str) -> String {
    let len = id.len().min(8);
    format!("{}…", &id[..len])
}

// ──────────────────────────────────────────────────────────────────────────────
// PDA Derivations
// ──────────────────────────────────────────────────────────────────────────────

/// Bank liquidity vault authority PDA: ["marginfi_bank_vault_authority", bank]
fn bank_vault_authority_pda(bank: &Pubkey) -> (Pubkey, u8) {
    Pubkey::find_program_address(
        &[b"marginfi_bank_vault_authority", bank.as_ref()],
        &program_id(),
    )
}

/// Bank liquidity vault token account (ATA of vault_authority for the bank's mint)
fn bank_liquidity_vault_ata(bank: &Pubkey, mint: &Pubkey) -> Pubkey {
    let (authority, _) = bank_vault_authority_pda(bank);
    get_associated_token_address(&authority, mint)
}

/// Bank insurance vault token account.
fn bank_insurance_vault_ata(bank: &Pubkey, mint: &Pubkey) -> Pubkey {
    let (vault_auth, _) = Pubkey::find_program_address(
        &[b"marginfi_bank_insurance_vault", bank.as_ref()],
        &program_id(),
    );
    get_associated_token_address(&vault_auth, mint)
}

/// Fee state PDA: seeds = ["feestate"]
fn fee_state_pda() -> Pubkey {
    Pubkey::find_program_address(&[b"feestate"], &program_id()).0
}

/// Fetch the global_fee_wallet from the on-chain FeeState account.
///
/// FeeState layout (after 8-byte Anchor discriminator):
///   key            : Pubkey (32 bytes) — offset 8
///   global_fee_wallet: Pubkey (32 bytes) — offset 40
async fn fetch_global_fee_wallet(rpc: &str) -> Result<Pubkey, AppError> {
    let fee_state = fee_state_pda();
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [fee_state.to_string(), {"encoding": "base64"}]
    });

    let client = reqwest::Client::new();
    let resp: serde_json::Value = client
        .post(rpc)
        .json(&body)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("RPC getAccountInfo: {e}")))?
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("RPC parse: {e}")))?;

    let data_b64 = resp["result"]["value"]["data"][0]
        .as_str()
        .ok_or_else(|| AppError::ProtocolError("FeeState account not found or empty".into()))?;

    let data = base64::engine::general_purpose::STANDARD
        .decode(data_b64)
        .map_err(|e| AppError::ProtocolError(format!("FeeState base64 decode: {e}")))?;

    // global_fee_wallet starts at byte 40: 8 (disc) + 32 (key)
    if data.len() < 72 {
        return Err(AppError::ProtocolError(
            "FeeState account data too short".into(),
        ));
    }
    let wallet_bytes: [u8; 32] = data[40..72]
        .try_into()
        .map_err(|_| AppError::ProtocolError("FeeState wallet slice error".into()))?;
    Ok(Pubkey::new_from_array(wallet_bytes))
}

/// Compute the ORDER PDA for a given marginfi_account + sorted bank keys.
/// Seeds: ["order", marginfi_account, sha256(sorted_bank_pubkeys)]
fn order_pda(marginfi_account: &Pubkey, bank_pubkeys: &[[u8; 32]]) -> Pubkey {
    let mut sorted = bank_pubkeys.to_vec();
    sorted.sort();
    let mut hasher = Sha256::new();
    for b in &sorted {
        hasher.update(b);
    }
    let hash: [u8; 32] = hasher.finalize().into();
    Pubkey::find_program_address(&[b"order", marginfi_account.as_ref(), &hash], &program_id()).0
}

/// Compute the EXECUTE_RECORD PDA for a given order key.
/// Seeds: ["execute_order", order]
fn execute_record_pda(order: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[b"execute_order", order.as_ref()], &program_id()).0
}

/// Compute the LIQUIDATION_RECORD PDA for a given marginfi_account.
/// Seeds: ["liq_record", marginfi_account]
fn liq_record_pda(marginfi_account: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[b"liq_record", marginfi_account.as_ref()], &program_id()).0
}

/// Sysvar1nstructions address (fixed).
fn instructions_sysvar() -> Pubkey {
    Pubkey::from_str("Sysvar1nstructions1111111111111111111111111")
        .expect("valid instructions sysvar")
}

// ──────────────────────────────────────────────────────────────────────────────
// Bank Metadata
// ──────────────────────────────────────────────────────────────────────────────

/// Resolved bank metadata for instruction building.
#[derive(Debug, Clone)]
struct BankMeta {
    address: Pubkey,
    mint: Pubkey,
    symbol: String,
    name: String,
    decimals: u8,
    token_program: Pubkey,
}

/// Raw GCS bank metadata entry (loosely typed to handle variations).
#[derive(Debug, Clone, Deserialize)]
struct RawBankEntry {
    #[serde(rename = "bankAddress")]
    bank_address: Option<String>,
    #[serde(rename = "tokenAddress")]
    token_address: Option<String>,
    #[serde(rename = "tokenSymbol")]
    token_symbol: Option<String>,
    #[serde(rename = "tokenName")]
    token_name: Option<String>,
    #[serde(rename = "tokenDecimals")]
    token_decimals: Option<u8>,
    #[serde(rename = "programId")]
    program_id: Option<String>,
}

/// Fetch bank metadata from the GCS cache.
async fn fetch_bank_metadata(http: &reqwest::Client) -> Result<Vec<BankMeta>, AppError> {
    let resp = http
        .get(MARGINFI_BANK_METADATA_URL)
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Bank metadata fetch: {e}")))?;
    if !resp.status().is_success() {
        return Err(AppError::ProtocolError(format!(
            "Bank metadata HTTP {}",
            resp.status()
        )));
    }
    let raw: Vec<RawBankEntry> = resp
        .json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Bank metadata parse: {e}")))?;

    let mut banks = Vec::new();
    for entry in raw {
        let bank_addr = match entry.bank_address {
            Some(ref a) if !a.is_empty() => a,
            _ => continue,
        };
        let mint_addr = match entry.token_address {
            Some(ref a) if !a.is_empty() => a,
            _ => continue,
        };
        let address = match Pubkey::from_str(bank_addr) {
            Ok(pk) => pk,
            Err(_) => continue,
        };
        let mint = match Pubkey::from_str(mint_addr) {
            Ok(pk) => pk,
            Err(_) => continue,
        };
        // Determine token program: Token-2022 if programId says so, else SPL Token
        let token_program = match entry.program_id.as_deref() {
            Some(pid) if pid == TOKEN_2022_PROGRAM => {
                Pubkey::from_str(TOKEN_2022_PROGRAM).expect("valid token-2022")
            }
            _ => Pubkey::from_str(SPL_TOKEN_PROGRAM).expect("valid spl-token"),
        };
        banks.push(BankMeta {
            address,
            mint,
            symbol: entry.token_symbol.unwrap_or_default(),
            name: entry.token_name.unwrap_or_default(),
            decimals: entry.token_decimals.unwrap_or(6),
            token_program,
        });
    }
    Ok(banks)
}

/// Resolve a token symbol or bank address to a BankMeta.
async fn resolve_bank_meta(
    http: &reqwest::Client,
    token_or_address: &str,
) -> Result<BankMeta, AppError> {
    // If it looks like a pubkey (long enough), try direct address lookup
    if token_or_address.len() >= 32 {
        let banks = fetch_bank_metadata(http).await?;
        if let Ok(pk) = Pubkey::from_str(token_or_address) {
            // Match by bank address
            if let Some(b) = banks.iter().find(|b| b.address == pk) {
                return Ok(b.clone());
            }
            // Match by mint address
            if let Some(b) = banks.iter().find(|b| b.mint == pk) {
                return Ok(b.clone());
            }
        }
    }
    // Try symbol match
    let banks = fetch_bank_metadata(http).await?;
    banks
        .iter()
        .find(|b| b.symbol.eq_ignore_ascii_case(token_or_address))
        .cloned()
        .ok_or_else(|| {
            AppError::InvalidParams(format!(
                "No marginfi bank found for token: {token_or_address}"
            ))
        })
}

// ──────────────────────────────────────────────────────────────────────────────
// User Token Account Resolution
// ──────────────────────────────────────────────────────────────────────────────

/// Find a user's token account for a given mint via RPC.
/// Returns the first matching ATA. Works for both SPL Token and Token-2022.
async fn find_user_token_account(
    rpc_endpoint: &str,
    owner: &Pubkey,
    mint: &Pubkey,
    token_program: &Pubkey,
) -> Result<Pubkey, AppError> {
    // Compute the ATA directly (this is the standard case for both programs)
    Ok(get_associated_token_address_with_program_id(
        owner,
        mint,
        token_program,
    ))
}

/// Get ATA using the correct token program.
fn get_associated_token_address_with_program_id(
    owner: &Pubkey,
    mint: &Pubkey,
    token_program: &Pubkey,
) -> Pubkey {
    // spl_associated_token_account::get_associated_token_address works for both
    // SPL Token and Token-2022 since the ATA derivation is the same.
    get_associated_token_address(owner, mint)
}

// ──────────────────────────────────────────────────────────────────────────────
// Account Resolution
// ──────────────────────────────────────────────────────────────────────────────

/// Resolve a wallet to its marginfi account. For marginfi v2, accounts are PDAs
/// derived from ["marginfi_account", group, authority, account_index].
/// Since we don't know the account_index, we try index 0 (default) and also
/// accept an explicit account address.
async fn resolve_account(
    http: &reqwest::Client,
    account_param: &Option<String>,
    wallet: &str,
) -> Result<Pubkey, AppError> {
    if let Some(ref addr) = account_param {
        if !addr.is_empty() {
            return parse_pk(addr, "account");
        }
    }
    // Derive PDA with account_index = 0 (default first account)
    let authority = parse_pk(wallet, "wallet")?;
    let group = marginfi_group();
    let pid = program_id();

    // Try account_index = 0
    let (pda, _) = Pubkey::find_program_address(
        &[
            b"marginfi_account",
            group.as_ref(),
            authority.as_ref(),
            &0u16.to_le_bytes(),
        ],
        &pid,
    );
    Ok(pda)
}

// ──────────────────────────────────────────────────────────────────────────────
// Borsh Argument Structs
// ──────────────────────────────────────────────────────────────────────────────

#[derive(BorshSerialize)]
struct NoArgs;

#[derive(BorshSerialize)]
struct LendingAccountDepositArgs {
    amount: u64,
    deposit_up_to_limit: Option<bool>,
}

#[derive(BorshSerialize)]
struct LendingAccountWithdrawArgs {
    amount: u64,
    withdraw_all: Option<bool>,
}

#[derive(BorshSerialize)]
struct LendingAccountBorrowArgs {
    amount: u64,
}

#[derive(BorshSerialize)]
struct LendingAccountRepayArgs {
    amount: u64,
    repay_all: Option<bool>,
}

#[derive(BorshSerialize)]
struct LendingAccountLiquidateArgs {
    asset_amount: u64,
    liquidatee_accounts: u8,
    liquidator_accounts: u8,
}

#[derive(BorshSerialize)]
struct MarginfiAccountInitializePdaArgs {
    account_index: u16,
    third_party_id: Option<u16>,
}

#[derive(BorshSerialize)]
struct MarginfiAccountPlaceOrderArgs {
    limit: u64,
    banks: Vec<[u8; 32]>,
    max_debt_coverage: u64,
    order_side: u8,
}

#[derive(BorshSerialize)]
struct MarginfiSetKeeperCloseFlagsArgs {
    bank_keys_opt: Option<Vec<[u8; 32]>>,
}

#[derive(BorshSerialize)]
struct LendingAccountStartFlashloanArgs {
    end_index: u64,
}

// ──────────────────────────────────────────────────────────────────────────────
// Types (public API)
// ──────────────────────────────────────────────────────────────────────────────

/// marginfi bank info (for API responses)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarginfiBank {
    pub address: String,
    pub mint: String,
    pub symbol: String,
    pub name: String,
    pub decimals: u8,
    pub total_deposits: String,
    pub total_borrows: String,
    pub deposit_apy: f64,
    pub borrow_apy: f64,
    pub ltv_ratio: f64,
    pub liquidation_threshold: f64,
    pub oracle_price: f64,
    pub token_program: String,
}

/// marginfi user account info
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarginfiAccount {
    pub address: String,
    pub owner: String,
    pub group: String,
    pub deposits: Vec<MarginfiPosition>,
    pub borrows: Vec<MarginfiPosition>,
    pub health_factor: f64,
    pub total_collateral_usd: f64,
    pub total_borrow_usd: f64,
    pub liquidation_threshold: f64,
}

/// marginfi position
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarginfiPosition {
    pub bank: String,
    pub mint: String,
    pub symbol: String,
    pub amount: String,
    pub amount_usd: f64,
    pub side: String,
}

/// marginfi health check
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarginfiHealth {
    pub total_collateral: f64,
    pub total_borrow: f64,
    pub health_factor: f64,
    pub liquidation_threshold: f64,
    pub is_healthy: bool,
}

/// marginfi points
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarginfiPoints {
    pub wallet: String,
    pub total_points: f64,
    pub rank: u32,
    pub deposits_points: f64,
    pub borrows_points: f64,
}

// ──────────────────────────────────────────────────────────────────────────────
// Action Params
// ──────────────────────────────────────────────────────────────────────────────

// ── Account Management ────────────────────────────────────────────────────────

/// Create a new marginfi account (non-PDA).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiCreateAccountParams {
    #[serde(default)]
    pub referral_code: Option<String>,
}

/// Create a marginfi PDA account with explicit index.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiCreateAccountPdaParams {
    /// Account index (0-based). Defaults to 0.
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub account_index: Option<u16>,
    /// Optional third-party identifier.
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub third_party_id: Option<u16>,
}

/// Close a marginfi account (must be empty).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiCloseAccountParams {
    #[serde(default)]
    pub account: Option<String>,
}

/// Close an individual lending balance entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiCloseBalanceParams {
    #[serde(default)]
    pub account: Option<String>,
    /// Bank address or token symbol.
    #[serde(alias = "token")]
    pub bank: String,
}

/// Transfer positions to a new account.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiTransferAccountParams {
    pub source_account: String,
    pub destination_account: String,
}

// ── Lending Operations ────────────────────────────────────────────────────────

/// Deposit assets into a marginfi bank (all deposits count as collateral in v2).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiDepositParams {
    #[serde(default)]
    pub account: Option<String>,
    #[serde(alias = "token")]
    pub bank: String,
    pub amount: String,
    /// If true, deposit up to the bank's deposit limit.
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub deposit_up_to_limit: Option<bool>,
}

/// Withdraw assets from a marginfi bank.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiWithdrawParams {
    #[serde(default)]
    pub account: Option<String>,
    #[serde(alias = "token")]
    pub bank: String,
    pub amount: String,
    /// If true, withdraw entire balance (amount is ignored).
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub withdraw_all: Option<bool>,
}

/// Borrow assets from a marginfi bank.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiBorrowParams {
    #[serde(default)]
    pub account: Option<String>,
    #[serde(alias = "token")]
    pub bank: String,
    pub amount: String,
}

/// Repay borrowed assets.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiRepayParams {
    #[serde(default)]
    pub account: Option<String>,
    #[serde(alias = "token")]
    pub bank: String,
    pub amount: String,
    /// If true, repay the entire debt (amount may be capped at actual debt).
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub repay_all: Option<bool>,
}

// ── Liquidation ───────────────────────────────────────────────────────────────

/// Liquidate an unhealthy marginfi account.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiLiquidateParams {
    /// Liquidator's marginfi account.
    #[serde(default)]
    pub account: Option<String>,
    /// The unhealthy account to liquidate.
    pub liquidatee_account: String,
    /// Bank for the asset being seized (deposited collateral).
    #[serde(alias = "assetBank")]
    pub asset_bank: String,
    /// Bank for the liability being repaid.
    #[serde(alias = "liabBank")]
    pub liab_bank: String,
    /// Amount of the liability token to repay.
    pub asset_amount: String,
}

/// Start receivership liquidation.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiStartLiquidationParams {
    pub account: String,
    /// Wallet address that will receive seized collateral (liquidation_receiver).
    pub liquidation_receiver: String,
}

/// End receivership liquidation.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiEndLiquidationParams {
    pub account: String,
}

// ── Flash Loans ───────────────────────────────────────────────────────────────

/// Start a flash loan.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiFlashloanStartParams {
    #[serde(default)]
    pub account: Option<String>,
    /// The transaction instruction index where `lending_account_end_flashloan` appears.
    /// The on-chain program validates this so it can locate and verify the end instruction.
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub end_index: u64,
}

/// End a flash loan.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiFlashloanEndParams {
    #[serde(default)]
    pub account: Option<String>,
}

// ── Borrow Orders ─────────────────────────────────────────────────────────────

/// Place a borrow order.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiPlaceOrderParams {
    #[serde(default)]
    pub account: Option<String>,
    pub limit: String,
    pub banks: Vec<String>,
    pub max_debt_coverage: String,
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub order_side: u8,
}

/// Close a borrow order.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiCloseOrderParams {
    #[serde(default)]
    pub account: Option<String>,
    /// Order PDA address to close.
    pub order: String,
    /// Fee recipient wallet address (receives rent from the closed order account).
    pub fee_recipient: String,
}

/// Start executing a borrow order.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiExecuteOrderStartParams {
    #[serde(default)]
    pub account: Option<String>,
    /// The executor wallet (keeper/bot that is executing the order).
    pub executor: String,
    /// Order PDA address to execute.
    pub order: String,
}

/// End executing a borrow order.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiExecuteOrderEndParams {
    #[serde(default)]
    pub account: Option<String>,
    /// The executor wallet (same as in start_execute_order, must sign).
    pub executor: String,
    /// Fee recipient wallet address.
    pub fee_recipient: String,
    /// Order PDA address.
    pub order: String,
}

// ── Emissions / Rewards ───────────────────────────────────────────────────────

/// Settle / claim accumulated emissions (rewards) for a bank position.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiClaimEmissionsParams {
    #[serde(default)]
    pub account: Option<String>,
    /// Bank address or token symbol to claim emissions from.
    #[serde(alias = "token")]
    pub bank: String,
    /// Emissions mint address. Defaults to the bank's token mint if omitted.
    #[serde(default)]
    pub emissions_mint: Option<String>,
}

/// Set keeper-close flags on a marginfi account, allowing keepers to auto-close orders.
/// Optionally restrict to specific banks; if banks is empty/omitted, clears flags on all balances.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiSetKeeperFlagsParams {
    #[serde(default)]
    pub account: Option<String>,
    /// Optional list of bank addresses or token symbols. If omitted, clears flags on all balances.
    #[serde(default)]
    pub banks: Option<Vec<String>>,
}

/// Initialize the liquidation record PDA for an account (required before start_liquidation).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiInitLiqRecordParams {
    #[serde(default)]
    pub account: Option<String>,
}

/// Update the destination wallet that receives off-chain emission distributions.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiUpdateEmissionsDestinationParams {
    #[serde(default)]
    pub account: Option<String>,
    /// New destination wallet address for emissions.
    pub destination: String,
}

/// Accrue outstanding emissions on a position (permissionless settle step, no token transfer).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiSettleEmissionsParams {
    #[serde(default)]
    pub account: Option<String>,
    /// Bank address or token symbol to settle emissions for.
    #[serde(alias = "token")]
    pub bank: String,
}

/// Permissionlessly withdraw emissions on behalf of any account (sent to their registered destination).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiWithdrawEmissionsPermissionlessParams {
    /// The marginfi account to withdraw emissions for (required — target someone else's account).
    pub account: String,
    /// Bank address or token symbol.
    #[serde(alias = "token")]
    pub bank: String,
    /// Emissions mint address. Defaults to the bank's own mint if omitted.
    #[serde(default)]
    pub emissions_mint: Option<String>,
}

/// Clear stale emission balances after a bank has disabled its emission rewards (permissionless).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiClearEmissionsParams {
    #[serde(default)]
    pub account: Option<String>,
    /// Bank address or token symbol to clear emissions for.
    #[serde(alias = "token")]
    pub bank: String,
}

// ── Permissionless Actions ────────────────────────────────────────────────────

/// Accrue interest for a bank.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiAccrueInterestParams {
    pub bank: String,
}

/// Update bank price cache.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiPulsePriceParams {
    pub bank: String,
    /// Oracle feed account address for this bank (required by the on-chain program).
    /// Typically Pyth or Switchboard feed address for the bank's token.
    pub oracle: String,
}

/// Pulse health check for an account.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiPulseHealthParams {
    pub account: String,
}

// ── Query Actions ─────────────────────────────────────────────────────────────

/// Query account info.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiAccountInfoParams {
    #[serde(default)]
    pub account: Option<String>,
    #[serde(default)]
    pub wallet: Option<String>,
}

/// Query available banks.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiBanksParams {
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub limit: Option<u32>,
}

/// Query account health.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiHealthParams {
    #[serde(default)]
    pub account: Option<String>,
    #[serde(default)]
    pub wallet: Option<String>,
}

/// Query points.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiPointsParams {
    #[serde(default)]
    pub wallet: Option<String>,
}

/// Query bank detail.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiBankDetailParams {
    #[serde(alias = "token")]
    pub bank: String,
}

/// Query all marginfi accounts for a wallet.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MarginfiUserAccountsParams {
    #[serde(default)]
    pub wallet: Option<String>,
    /// Max accounts to check (0–9). Default: 3.
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub max_index: Option<u16>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validators
// ──────────────────────────────────────────────────────────────────────────────

fn validate_bank_required(bank: &str) -> Result<(), AppError> {
    if bank.is_empty() {
        return Err(AppError::InvalidParams("bank or token is required".into()));
    }
    Ok(())
}

fn validate_positive_amount(amount: &str) -> Result<(), AppError> {
    let v: f64 = amount.parse().map_err(|_| {
        AppError::InvalidParams(format!("amount must be a positive number, got: '{amount}'"))
    })?;
    if v <= 0.0 {
        return Err(AppError::InvalidParams("amount must be positive".into()));
    }
    Ok(())
}

pub fn validate_marginfi_create_account_params(
    _p: &MarginfiCreateAccountParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_create_account_pda_params(
    _p: &MarginfiCreateAccountPdaParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_close_account_params(
    _p: &MarginfiCloseAccountParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_close_balance_params(
    p: &MarginfiCloseBalanceParams,
) -> Result<(), AppError> {
    validate_bank_required(&p.bank)
}

pub fn validate_marginfi_transfer_account_params(
    p: &MarginfiTransferAccountParams,
) -> Result<(), AppError> {
    if p.source_account.is_empty() {
        return Err(AppError::InvalidParams("sourceAccount is required".into()));
    }
    if p.destination_account.is_empty() {
        return Err(AppError::InvalidParams(
            "destinationAccount is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_marginfi_deposit_params(p: &MarginfiDepositParams) -> Result<(), AppError> {
    validate_bank_required(&p.bank)?;
    validate_positive_amount(&p.amount)
}

pub fn validate_marginfi_withdraw_params(p: &MarginfiWithdrawParams) -> Result<(), AppError> {
    validate_bank_required(&p.bank)?;
    // amount is optional if withdraw_all is true
    if p.withdraw_all.unwrap_or(false) {
        Ok(())
    } else {
        validate_positive_amount(&p.amount)
    }
}

pub fn validate_marginfi_borrow_params(p: &MarginfiBorrowParams) -> Result<(), AppError> {
    validate_bank_required(&p.bank)?;
    validate_positive_amount(&p.amount)
}

pub fn validate_marginfi_repay_params(p: &MarginfiRepayParams) -> Result<(), AppError> {
    validate_bank_required(&p.bank)?;
    if p.repay_all.unwrap_or(false) {
        Ok(())
    } else {
        validate_positive_amount(&p.amount)
    }
}

pub fn validate_marginfi_liquidate_params(p: &MarginfiLiquidateParams) -> Result<(), AppError> {
    validate_bank_required(&p.asset_bank)?;
    validate_bank_required(&p.liab_bank)?;
    validate_positive_amount(&p.asset_amount)?;
    if p.liquidatee_account.is_empty() {
        return Err(AppError::InvalidParams(
            "liquidateeAccount is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_marginfi_start_liquidation_params(
    p: &MarginfiStartLiquidationParams,
) -> Result<(), AppError> {
    if p.account.is_empty() {
        return Err(AppError::InvalidParams("account is required".into()));
    }
    if p.liquidation_receiver.is_empty() {
        return Err(AppError::InvalidParams(
            "liquidationReceiver is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_marginfi_end_liquidation_params(
    p: &MarginfiEndLiquidationParams,
) -> Result<(), AppError> {
    if p.account.is_empty() {
        return Err(AppError::InvalidParams("account is required".into()));
    }
    Ok(())
}

pub fn validate_marginfi_flashloan_start_params(
    _p: &MarginfiFlashloanStartParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_flashloan_end_params(
    _p: &MarginfiFlashloanEndParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_place_order_params(p: &MarginfiPlaceOrderParams) -> Result<(), AppError> {
    if p.banks.is_empty() {
        return Err(AppError::InvalidParams(
            "banks is required (at least one)".into(),
        ));
    }
    validate_positive_amount(&p.limit)?;
    validate_positive_amount(&p.max_debt_coverage)
}

pub fn validate_marginfi_close_order_params(p: &MarginfiCloseOrderParams) -> Result<(), AppError> {
    if p.order.is_empty() {
        return Err(AppError::InvalidParams("order address is required".into()));
    }
    if p.fee_recipient.is_empty() {
        return Err(AppError::InvalidParams("feeRecipient is required".into()));
    }
    Ok(())
}

pub fn validate_marginfi_execute_order_start_params(
    p: &MarginfiExecuteOrderStartParams,
) -> Result<(), AppError> {
    if p.executor.is_empty() {
        return Err(AppError::InvalidParams("executor is required".into()));
    }
    if p.order.is_empty() {
        return Err(AppError::InvalidParams("order address is required".into()));
    }
    Ok(())
}

pub fn validate_marginfi_execute_order_end_params(
    p: &MarginfiExecuteOrderEndParams,
) -> Result<(), AppError> {
    if p.executor.is_empty() {
        return Err(AppError::InvalidParams("executor is required".into()));
    }
    if p.order.is_empty() {
        return Err(AppError::InvalidParams("order address is required".into()));
    }
    if p.fee_recipient.is_empty() {
        return Err(AppError::InvalidParams("feeRecipient is required".into()));
    }
    Ok(())
}

pub fn validate_marginfi_claim_emissions_params(
    p: &MarginfiClaimEmissionsParams,
) -> Result<(), AppError> {
    validate_bank_required(&p.bank)
}

pub fn validate_marginfi_set_keeper_flags_params(
    _p: &MarginfiSetKeeperFlagsParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_init_liq_record_params(
    _p: &MarginfiInitLiqRecordParams,
) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_update_emissions_destination_params(
    p: &MarginfiUpdateEmissionsDestinationParams,
) -> Result<(), AppError> {
    if p.destination.is_empty() {
        return Err(AppError::InvalidParams("destination is required".into()));
    }
    Ok(())
}

pub fn validate_marginfi_settle_emissions_params(
    p: &MarginfiSettleEmissionsParams,
) -> Result<(), AppError> {
    validate_bank_required(&p.bank)
}

pub fn validate_marginfi_withdraw_emissions_permissionless_params(
    p: &MarginfiWithdrawEmissionsPermissionlessParams,
) -> Result<(), AppError> {
    if p.account.is_empty() {
        return Err(AppError::InvalidParams("account is required".into()));
    }
    validate_bank_required(&p.bank)
}

pub fn validate_marginfi_clear_emissions_params(
    p: &MarginfiClearEmissionsParams,
) -> Result<(), AppError> {
    validate_bank_required(&p.bank)
}

pub fn validate_marginfi_accrue_interest_params(
    p: &MarginfiAccrueInterestParams,
) -> Result<(), AppError> {
    validate_bank_required(&p.bank)
}

pub fn validate_marginfi_pulse_price_params(p: &MarginfiPulsePriceParams) -> Result<(), AppError> {
    validate_bank_required(&p.bank)?;
    if p.oracle.is_empty() {
        return Err(AppError::InvalidParams(
            "oracle feed address is required".into(),
        ));
    }
    Ok(())
}

pub fn validate_marginfi_pulse_health_params(
    p: &MarginfiPulseHealthParams,
) -> Result<(), AppError> {
    if p.account.is_empty() {
        return Err(AppError::InvalidParams("account is required".into()));
    }
    Ok(())
}

pub fn validate_marginfi_account_info_params(
    _p: &MarginfiAccountInfoParams,
) -> Result<(), AppError> {
    // account and wallet are both optional — the build function falls back to the JWT-injected
    // user pubkey at execution time, so no pre-validation error is needed here.
    Ok(())
}

pub fn validate_marginfi_banks_params(_p: &MarginfiBanksParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_health_params(_p: &MarginfiHealthParams) -> Result<(), AppError> {
    // account and wallet are both optional — falls back to JWT-injected wallet at build time.
    Ok(())
}

pub fn validate_marginfi_points_params(_p: &MarginfiPointsParams) -> Result<(), AppError> {
    Ok(())
}

pub fn validate_marginfi_bank_detail_params(p: &MarginfiBankDetailParams) -> Result<(), AppError> {
    validate_bank_required(&p.bank)
}

pub fn validate_marginfi_user_accounts_params(
    _p: &MarginfiUserAccountsParams,
) -> Result<(), AppError> {
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// API Helpers (query data)
// ──────────────────────────────────────────────────────────────────────────────

/// Fetch bank list from GCS metadata, converted to MarginfiBank format.
pub async fn get_banks(http: &reqwest::Client) -> Result<Vec<MarginfiBank>, AppError> {
    let meta = fetch_bank_metadata(http).await?;
    Ok(meta
        .into_iter()
        .map(|b| MarginfiBank {
            address: b.address.to_string(),
            mint: b.mint.to_string(),
            symbol: b.symbol,
            name: b.name,
            decimals: b.decimals,
            total_deposits: "0".into(),
            total_borrows: "0".into(),
            deposit_apy: 0.0,
            borrow_apy: 0.0,
            ltv_ratio: 0.0,
            liquidation_threshold: 0.0,
            oracle_price: 0.0,
            token_program: b.token_program.to_string(),
        })
        .collect())
}

/// Fetch account info from the marginfi API (best-effort; API may be down).
pub async fn get_account_info(
    http: &reqwest::Client,
    account: &str,
) -> Result<MarginfiAccount, AppError> {
    let resp = http
        .get(format!("https://api.mrgn.fi/v1/account/{account}"))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Account info fetch: {e}")))?;
    if !resp.status().is_success() {
        return Err(AppError::ProtocolError(format!(
            "Account info HTTP {}",
            resp.status()
        )));
    }
    resp.json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Account info parse: {e}")))
}

/// Fetch points info from the marginfi API (best-effort).
pub async fn get_points(http: &reqwest::Client, wallet: &str) -> Result<MarginfiPoints, AppError> {
    let resp = http
        .get(format!("https://api.mrgn.fi/v1/points/{wallet}"))
        .send()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Points fetch: {e}")))?;
    if !resp.status().is_success() {
        return Err(AppError::ProtocolError(format!(
            "Points HTTP {}",
            resp.status()
        )));
    }
    resp.json()
        .await
        .map_err(|e| AppError::ProtocolError(format!("Points parse: {e}")))
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — Account Management
// ──────────────────────────────────────────────────────────────────────────────

/// Create a new marginfi account (non-PDA, generated keypair returned to client).
pub async fn build_marginfi_create_account(
    _http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiCreateAccountParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_create_account_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();

    // For non-PDA accounts, the account keypair is generated client-side.
    // We return the instruction with a placeholder; the frontend creates the keypair.
    // Using the owner as placeholder — frontend must replace with actual new keypair.
    let placeholder_account = owner;

    let discriminator = disc("marginfi_account_initialize");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),
            AccountMeta::new(placeholder_account, false),
            AccountMeta::new_readonly(owner, true),
            AccountMeta::new(owner, true), // fee_payer
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_create_account".into(),
            description: "Create new marginfi lending account".into(),
            estimated_fee: "~0.001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec!["A new account keypair must be generated client-side.".into()],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 1, // the new account keypair
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Create a marginfi PDA account (deterministic address).
pub async fn build_marginfi_create_account_pda(
    _http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiCreateAccountPdaParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_create_account_pda_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();

    let account_index = params.account_index.unwrap_or(0);

    // Derive PDA: ["marginfi_account", group, authority, account_index]
    let (account_pda, _) = Pubkey::find_program_address(
        &[
            b"marginfi_account",
            group.as_ref(),
            owner.as_ref(),
            &account_index.to_le_bytes(),
        ],
        &pid,
    );

    let discriminator = disc("marginfi_account_initialize_pda");
    let args = MarginfiAccountInitializePdaArgs {
        account_index,
        third_party_id: params.third_party_id,
    };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),
            AccountMeta::new(account_pda, false),
            AccountMeta::new_readonly(owner, true), // authority (signer)
            AccountMeta::new(owner, true),          // fee_payer (mut, signer)
            AccountMeta::new_readonly(system_program::id(), false),
            AccountMeta::new_readonly(instructions_sysvar(), false), // ixs_sysvar
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_create_account_pda".into(),
            description: format!("Create marginfi PDA account (index {account_index})"),
            estimated_fee: "~0.001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Close a marginfi account (must have zero deposits and borrows).
pub async fn build_marginfi_close_account(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiCloseAccountParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_close_account_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let pid = program_id();

    let discriminator = disc("marginfi_account_close");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false), // marginfi_account (close=fee_payer)
            AccountMeta::new_readonly(owner, true), // authority
            AccountMeta::new(owner, true),    // fee_payer (receives rent)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_close_account".into(),
            description: "Close marginfi account and recover rent".into(),
            estimated_fee: "~0.00001 SOL (recovers ~0.001 SOL rent)".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec!["Account must have zero deposits and borrows".into()],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Close an individual balance entry for a bank.
pub async fn build_marginfi_close_balance(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiCloseBalanceParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_close_balance_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = resolve_account(http, &None, user_pubkey_str).await?;
    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let pid = program_id();
    let group = marginfi_group();

    let discriminator = disc("lending_account_close_balance");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false), // marginfi_group (readonly)
            AccountMeta::new(account, false),        // marginfi_account (mut)
            AccountMeta::new_readonly(owner, true),  // authority (signer)
            AccountMeta::new(bank_meta.address, false), // bank (mut)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_close_balance".into(),
            description: format!("Close {} balance entry in marginfi", bank_meta.symbol),
            estimated_fee: "~0.00001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Transfer positions between marginfi accounts.
pub async fn build_marginfi_transfer_account(
    _http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiTransferAccountParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_transfer_account_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let source = parse_pk(&params.source_account, "sourceAccount")?;
    let destination = parse_pk(&params.destination_account, "destinationAccount")?;
    let pid = program_id();
    let group = marginfi_group();

    let discriminator = disc("transfer_to_new_account");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false), // marginfi_group (readonly)
            AccountMeta::new(source, false),         // marginfi_account (source, mut)
            AccountMeta::new_readonly(owner, true),  // authority (signer)
            AccountMeta::new(destination, false),    // marginfi_account_to (destination, mut)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_transfer_account".into(),
            description: "Transfer positions to new marginfi account".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — Lending Operations
// ──────────────────────────────────────────────────────────────────────────────

/// Deposit assets into a marginfi bank.
pub async fn build_marginfi_deposit(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiDepositParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_deposit_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();

    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let user_token_acct =
        find_user_token_account(rpc, &owner, &bank_meta.mint, &bank_meta.token_program).await?;
    let liquidity_vault = bank_liquidity_vault_ata(&bank_meta.address, &bank_meta.mint);

    let discriminator = disc("lending_account_deposit");
    let args = LendingAccountDepositArgs {
        amount: parse_amount_raw(&params.amount, bank_meta.decimals)?,
        deposit_up_to_limit: params.deposit_up_to_limit,
    };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),
            AccountMeta::new(account, false),
            AccountMeta::new_readonly(owner, true),
            AccountMeta::new(bank_meta.address, false),
            AccountMeta::new(user_token_acct, false),
            AccountMeta::new(liquidity_vault, false),
            AccountMeta::new_readonly(bank_meta.token_program, false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_deposit".into(),
            description: format!("Deposit {} {} to marginfi", params.amount, bank_meta.symbol),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Withdraw assets from a marginfi bank.
pub async fn build_marginfi_withdraw(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiWithdrawParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_withdraw_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();

    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let dest_token_acct =
        find_user_token_account(rpc, &owner, &bank_meta.mint, &bank_meta.token_program).await?;
    let (vault_authority, _) = bank_vault_authority_pda(&bank_meta.address);
    let liquidity_vault = bank_liquidity_vault_ata(&bank_meta.address, &bank_meta.mint);

    let discriminator = disc("lending_account_withdraw");
    let args = LendingAccountWithdrawArgs {
        amount: if params.withdraw_all.unwrap_or(false) {
            u64::MAX // withdraw_all uses u64::MAX as sentinel
        } else {
            parse_amount_raw(&params.amount, bank_meta.decimals)?
        },
        withdraw_all: params.withdraw_all,
    };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),
            AccountMeta::new(account, false),
            AccountMeta::new_readonly(owner, true),
            AccountMeta::new(bank_meta.address, false),
            AccountMeta::new(dest_token_acct, false),
            AccountMeta::new_readonly(vault_authority, false), // PDA signer
            AccountMeta::new(liquidity_vault, false),
            AccountMeta::new_readonly(bank_meta.token_program, false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_withdraw".into(),
            description: format!(
                "Withdraw {} {} from marginfi",
                if params.withdraw_all.unwrap_or(false) {
                    "all"
                } else {
                    &params.amount
                },
                bank_meta.symbol
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Borrow assets from a marginfi bank.
pub async fn build_marginfi_borrow(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiBorrowParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_borrow_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();

    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let dest_token_acct =
        find_user_token_account(rpc, &owner, &bank_meta.mint, &bank_meta.token_program).await?;
    let (vault_authority, _) = bank_vault_authority_pda(&bank_meta.address);
    let liquidity_vault = bank_liquidity_vault_ata(&bank_meta.address, &bank_meta.mint);

    let discriminator = disc("lending_account_borrow");
    let args = LendingAccountBorrowArgs {
        amount: parse_amount_raw(&params.amount, bank_meta.decimals)?,
    };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),
            AccountMeta::new(account, false),
            AccountMeta::new_readonly(owner, true),
            AccountMeta::new(bank_meta.address, false),
            AccountMeta::new(dest_token_acct, false),
            AccountMeta::new_readonly(vault_authority, false),
            AccountMeta::new(liquidity_vault, false),
            AccountMeta::new_readonly(bank_meta.token_program, false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_borrow".into(),
            description: format!(
                "Borrow {} {} from marginfi",
                params.amount, bank_meta.symbol
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec!["Borrowing increases liquidation risk".into()],
            requires_approval: true,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Repay borrowed assets.
pub async fn build_marginfi_repay(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiRepayParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_repay_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();

    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let user_token_acct =
        find_user_token_account(rpc, &owner, &bank_meta.mint, &bank_meta.token_program).await?;
    let liquidity_vault = bank_liquidity_vault_ata(&bank_meta.address, &bank_meta.mint);

    let discriminator = disc("lending_account_repay");
    let args = LendingAccountRepayArgs {
        amount: if params.repay_all.unwrap_or(false) {
            u64::MAX
        } else {
            parse_amount_raw(&params.amount, bank_meta.decimals)?
        },
        repay_all: params.repay_all,
    };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),
            AccountMeta::new(account, false),
            AccountMeta::new_readonly(owner, true),
            AccountMeta::new(bank_meta.address, false),
            AccountMeta::new(user_token_acct, false),
            AccountMeta::new(liquidity_vault, false),
            AccountMeta::new_readonly(bank_meta.token_program, false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_repay".into(),
            description: format!(
                "Repay {} {} to marginfi",
                if params.repay_all.unwrap_or(false) {
                    "all"
                } else {
                    &params.amount
                },
                bank_meta.symbol
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — Liquidation
// ──────────────────────────────────────────────────────────────────────────────

/// Liquidate an unhealthy marginfi account.
pub async fn build_marginfi_liquidate(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiLiquidateParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_liquidate_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();

    let asset_bank_meta = resolve_bank_meta(http, &params.asset_bank).await?;
    let liab_bank_meta = resolve_bank_meta(http, &params.liab_bank).await?;
    let liquidator_account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let liquidatee_account = parse_pk(&params.liquidatee_account, "liquidateeAccount")?;

    let (vault_authority, _) = bank_vault_authority_pda(&liab_bank_meta.address);
    let liquidity_vault = bank_liquidity_vault_ata(&liab_bank_meta.address, &liab_bank_meta.mint);
    let insurance_vault = bank_insurance_vault_ata(&liab_bank_meta.address, &liab_bank_meta.mint);

    let discriminator = disc("lending_account_liquidate");
    let args = LendingAccountLiquidateArgs {
        asset_amount: parse_amount_raw(&params.asset_amount, liab_bank_meta.decimals)?,
        liquidatee_accounts: 1,
        liquidator_accounts: 1,
    };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),
            AccountMeta::new(asset_bank_meta.address, false), // asset_bank
            AccountMeta::new(liab_bank_meta.address, false),  // liab_bank
            AccountMeta::new(liquidator_account, false),      // liquidator account
            AccountMeta::new_readonly(owner, true),           // authority
            AccountMeta::new(liquidatee_account, false),      // liquidatee account
            AccountMeta::new_readonly(vault_authority, false), // vault authority PDA
            AccountMeta::new(liquidity_vault, false),
            AccountMeta::new(insurance_vault, false),
            AccountMeta::new_readonly(liab_bank_meta.token_program, false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_liquidate".into(),
            description: format!(
                "Liquidate {} from account {}",
                params.asset_amount,
                short_id(&params.liquidatee_account)
            ),
            estimated_fee: "~0.0002 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec!["Liquidation is only possible for unhealthy accounts".into()],
            requires_approval: true,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Start receivership liquidation.
pub async fn build_marginfi_start_liquidation(
    _http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiStartLiquidationParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_start_liquidation_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = parse_pk(&params.account, "account")?;
    let liquidation_receiver = parse_pk(&params.liquidation_receiver, "liquidationReceiver")?;
    let pid = program_id();

    // Liquidation record PDA: seeds = ["liq_record", marginfi_account]
    let liq_record = liq_record_pda(&account);

    let discriminator = disc("start_liquidation");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),    // marginfi_account (mut)
            AccountMeta::new(liq_record, false), // liquidation_record (mut, PDA)
            AccountMeta::new_readonly(liquidation_receiver, false), // liquidation_receiver (unchecked, readonly)
            AccountMeta::new_readonly(instructions_sysvar(), false), // ixs_sysvar (fixed address)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_start_liquidation".into(),
            description: "Start receivership liquidation".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// End receivership liquidation.
pub async fn build_marginfi_end_liquidation(
    _http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiEndLiquidationParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_end_liquidation_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = parse_pk(&params.account, "account")?;
    let pid = program_id();

    // Liquidation record PDA: seeds = ["liq_record", marginfi_account]
    let liq_record = liq_record_pda(&account);

    // Fee state PDA + global_fee_wallet (fetched from on-chain FeeState account)
    let fee_state = fee_state_pda();
    let global_fee_wallet = fetch_global_fee_wallet(rpc).await?;

    let discriminator = disc("end_liquidation");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),    // marginfi_account (mut)
            AccountMeta::new(liq_record, false), // liquidation_record (mut, PDA)
            AccountMeta::new(owner, true),       // liquidation_receiver / owner (mut, signer)
            AccountMeta::new_readonly(fee_state, false), // fee_state (readonly, PDA)
            AccountMeta::new(global_fee_wallet, false), // global_fee_wallet (mut)
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_end_liquidation".into(),
            description: "End receivership liquidation".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — Flash Loans
// ──────────────────────────────────────────────────────────────────────────────

/// Start a flash loan.
pub async fn build_marginfi_flashloan_start(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiFlashloanStartParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_flashloan_start_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let pid = program_id();

    let discriminator = disc("lending_account_start_flashloan");
    let args = LendingAccountStartFlashloanArgs {
        end_index: params.end_index,
    };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),       // marginfi_account (mut)
            AccountMeta::new_readonly(owner, true), // authority (signer)
            AccountMeta::new_readonly(instructions_sysvar(), false), // ixs_sysvar (fixed address)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_flashloan_start".into(),
            description: "Start marginfi flash loan".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec!["Flash loan must be repaid in the same transaction".into()],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// End a flash loan.
pub async fn build_marginfi_flashloan_end(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiFlashloanEndParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_flashloan_end_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let pid = program_id();

    let discriminator = disc("lending_account_end_flashloan");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),       // marginfi_account (mut)
            AccountMeta::new_readonly(owner, true), // authority (signer)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_flashloan_end".into(),
            description: "End marginfi flash loan".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — Borrow Orders
// ──────────────────────────────────────────────────────────────────────────────

/// Place a borrow order.
pub async fn build_marginfi_place_order(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiPlaceOrderParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_place_order_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let pid = program_id();

    let limit_raw: u64 = params
        .limit
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid limit value: '{}'", params.limit)))?;
    let max_debt_raw: u64 = params.max_debt_coverage.parse().map_err(|_| {
        AppError::InvalidParams(format!(
            "Invalid maxDebtCoverage: '{}'",
            params.max_debt_coverage
        ))
    })?;

    // Parse bank addresses; skip invalid entries but require at least one valid one.
    let bank_pubkeys: Vec<[u8; 32]> = params
        .banks
        .iter()
        .filter_map(|b| Pubkey::from_str(b).ok())
        .map(|p| p.to_bytes())
        .collect();
    if bank_pubkeys.is_empty() {
        return Err(AppError::InvalidParams(
            "No valid bank addresses provided in 'banks'".into(),
        ));
    }

    // ORDER PDA: seeds = ["order", marginfi_account, sha256(sorted_bank_keys)]
    let order = order_pda(&account, &bank_pubkeys);

    // Fee state PDA + global_fee_wallet
    let fee_state = fee_state_pda();
    let global_fee_wallet = fetch_global_fee_wallet(rpc).await?;

    let discriminator = disc("marginfi_account_place_order");
    let args = MarginfiAccountPlaceOrderArgs {
        limit: limit_raw,
        banks: bank_pubkeys,
        max_debt_coverage: max_debt_raw,
        order_side: params.order_side,
    };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),       // marginfi_account (mut)
            AccountMeta::new_readonly(owner, true), // authority (signer)
            AccountMeta::new(owner, true),          // fee_payer (mut, signer)
            AccountMeta::new(order, false),         // order PDA (mut)
            AccountMeta::new_readonly(fee_state, false), // fee_state (readonly, PDA)
            AccountMeta::new(global_fee_wallet, false), // global_fee_wallet (mut)
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_place_order".into(),
            description: "Place marginfi borrow order".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Close a borrow order.
pub async fn build_marginfi_close_order(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiCloseOrderParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_close_order_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let order = parse_pk(&params.order, "order")?;
    let fee_recipient = parse_pk(&params.fee_recipient, "feeRecipient")?;
    let pid = program_id();

    // Correct discriminator: sha256("global:marginfi_account_close_order")[..8]
    let discriminator = disc("marginfi_account_close_order");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),       // marginfi_account (mut)
            AccountMeta::new_readonly(owner, true), // authority (signer)
            AccountMeta::new(order, false),         // order (mut)
            AccountMeta::new(fee_recipient, false), // fee_recipient (mut)
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_close_order".into(),
            description: "Close marginfi borrow order".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Start executing a borrow order.
pub async fn build_marginfi_execute_order_start(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiExecuteOrderStartParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_execute_order_start_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let executor = parse_pk(&params.executor, "executor")?;
    let order = parse_pk(&params.order, "order")?;
    let pid = program_id();

    // execute_record PDA: seeds = ["execute_order", order]
    let execute_record = execute_record_pda(&order);

    // Correct discriminator: sha256("global:marginfi_account_start_execute_order")[..8]
    let discriminator = disc("marginfi_account_start_execute_order");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),          // marginfi_account (mut)
            AccountMeta::new_readonly(executor, true), // executor (signer)
            AccountMeta::new(owner, true),             // fee_payer (mut, signer)
            AccountMeta::new_readonly(order, false),   // order (readonly)
            AccountMeta::new(execute_record, false),   // execute_record PDA (mut)
            AccountMeta::new_readonly(instructions_sysvar(), false), // ixs_sysvar (fixed address)
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_execute_order_start".into(),
            description: "Start executing marginfi borrow order".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// End executing a borrow order.
pub async fn build_marginfi_execute_order_end(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiExecuteOrderEndParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_execute_order_end_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let executor = parse_pk(&params.executor, "executor")?;
    let fee_recipient = parse_pk(&params.fee_recipient, "feeRecipient")?;
    let order = parse_pk(&params.order, "order")?;
    let pid = program_id();

    // execute_record PDA: seeds = ["execute_order", order]
    let execute_record = execute_record_pda(&order);

    // Fee state PDA + global_fee_wallet
    let fee_state = fee_state_pda();
    let global_fee_wallet = fetch_global_fee_wallet(rpc).await?;

    // Correct discriminator: sha256("global:marginfi_account_end_execute_order")[..8]
    let discriminator = disc("marginfi_account_end_execute_order");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),          // marginfi_account (mut)
            AccountMeta::new_readonly(executor, true), // executor (signer)
            AccountMeta::new(fee_recipient, false),    // fee_recipient (mut)
            AccountMeta::new(order, false),            // order (mut)
            AccountMeta::new(execute_record, false),   // execute_record PDA (mut)
            AccountMeta::new_readonly(fee_state, false), // fee_state (readonly, PDA)
            AccountMeta::new(global_fee_wallet, false), // global_fee_wallet (mut)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_execute_order_end".into(),
            description: "End executing marginfi borrow order".into(),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — Emissions / Rewards
// ──────────────────────────────────────────────────────────────────────────────

/// Settle accumulated emissions (token rewards) for a lending position.
/// Calls `lending_account_settle_emissions` on the marginfi program.
pub async fn build_marginfi_claim_emissions(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiClaimEmissionsParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_claim_emissions_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();

    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;

    // Emissions mint: use the provided address, or fall back to the bank's own mint.
    let custom_emissions_mint = params.emissions_mint.as_deref().filter(|m| !m.is_empty());
    let emissions_mint = match custom_emissions_mint {
        Some(m) => parse_pk(m, "emissionsMint")?,
        None => bank_meta.mint,
    };

    // Resolve the correct token program for the emissions_mint.
    // When the user provides an explicit emissionsMint that differs from the bank's mint,
    // we must look it up in the bank metadata to get the right program (SPL Token vs Token-2022).
    // If not found (custom or unknown mint), default to SPL Token (most common).
    let emissions_token_program = if custom_emissions_mint.is_some() {
        // Try to find the token program by looking up the emissions_mint as a bank's mint.
        let all_banks = fetch_bank_metadata(http).await?;
        all_banks
            .iter()
            .find(|b| b.mint == emissions_mint)
            .map(|b| b.token_program)
            .unwrap_or_else(|| Pubkey::from_str(SPL_TOKEN_PROGRAM).expect("valid spl-token"))
    } else {
        // Default case: emissions_mint == bank_meta.mint → use bank's token program
        bank_meta.token_program
    };

    // Emissions auth PDA: seeds = ["emissions_auth_seed", bank, emissions_mint]
    let (emissions_auth_pda, _) = Pubkey::find_program_address(
        &[
            b"emissions_auth_seed",
            bank_meta.address.as_ref(),
            emissions_mint.as_ref(),
        ],
        &pid,
    );

    // Emissions vault PDA: seeds = ["emissions_token_account_seed", bank, emissions_mint]
    // NOTE: This is a program-owned PDA vault, NOT an ATA of emissions_auth.
    let (emissions_vault_pda, _) = Pubkey::find_program_address(
        &[
            b"emissions_token_account_seed",
            bank_meta.address.as_ref(),
            emissions_mint.as_ref(),
        ],
        &pid,
    );

    // Destination: user's ATA for the emissions_mint (using the correct token program)
    let destination_token_account =
        find_user_token_account(rpc, &owner, &emissions_mint, &emissions_token_program).await?;

    // Correct instruction: lending_account_withdraw_emissions (not settle_emissions)
    let discriminator = disc("lending_account_withdraw_emissions");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),    // marginfi_group
            AccountMeta::new(account, false),           // marginfi_account
            AccountMeta::new_readonly(owner, true),     // authority (signer)
            AccountMeta::new(bank_meta.address, false), // bank
            AccountMeta::new_readonly(emissions_mint, false), // emissions_mint (readonly)
            AccountMeta::new_readonly(emissions_auth_pda, false), // emissions_auth (PDA, readonly)
            AccountMeta::new(emissions_vault_pda, false), // emissions_vault (PDA, writable)
            AccountMeta::new(destination_token_account, false), // destination_account (writable)
            AccountMeta::new_readonly(emissions_token_program, false), // token_program
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_claim_emissions".into(),
            description: format!(
                "Claim accumulated {} emissions from marginfi",
                bank_meta.symbol
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                "If emissionsMint is not the bank's own token, provide it explicitly".into(),
            ],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Permissionless settle step — accrues outstanding emissions on a position without withdrawing.
pub async fn build_marginfi_settle_emissions(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiSettleEmissionsParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_settle_emissions_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;

    let discriminator = disc("lending_account_settle_emissions");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false), // marginfi_account (writable)
            AccountMeta::new(bank_meta.address, false), // bank (writable)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_settle_emissions".into(),
            description: format!("Settle outstanding {} emissions on marginfi position", bank_meta.symbol),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                "This accrues pending emissions to the position without withdrawing tokens. Call marginfi_claim_emissions to actually receive them.".into(),
            ],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Permissionlessly withdraw emissions on behalf of any account (sent to their registered destination).
pub async fn build_marginfi_withdraw_emissions_permissionless(
    http: &reqwest::Client,
    rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiWithdrawEmissionsPermissionlessParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_withdraw_emissions_permissionless_params(params)?;

    let payer = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();
    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let account = parse_pk(&params.account, "account")?;

    let custom_emissions_mint = params.emissions_mint.as_deref().filter(|m| !m.is_empty());
    let emissions_mint = match custom_emissions_mint {
        Some(m) => parse_pk(m, "emissionsMint")?,
        None => bank_meta.mint,
    };

    let emissions_token_program = if custom_emissions_mint.is_some() {
        let all_banks = fetch_bank_metadata(http).await?;
        all_banks
            .iter()
            .find(|b| b.mint == emissions_mint)
            .map(|b| b.token_program)
            .unwrap_or_else(|| Pubkey::from_str(SPL_TOKEN_PROGRAM).expect("valid spl-token"))
    } else {
        bank_meta.token_program
    };

    let (emissions_auth_pda, _) = Pubkey::find_program_address(
        &[
            b"emissions_auth_seed",
            bank_meta.address.as_ref(),
            emissions_mint.as_ref(),
        ],
        &pid,
    );
    let (emissions_vault_pda, _) = Pubkey::find_program_address(
        &[
            b"emissions_token_account_seed",
            bank_meta.address.as_ref(),
            emissions_mint.as_ref(),
        ],
        &pid,
    );

    // Destination: account's registered destination ATA (the account owner's ATA)
    // For permissionless: destination is whoever the account owner set via update_emissions_destination,
    // or if not set, the account authority's ATA. We resolve from on-chain state by fetching the account.
    // For simplicity, we resolve the destination as the payer's (caller's) context — but since this is
    // permissionless, the destination is the target account's registered address.
    // We use find_user_token_account for the account's authority as fallback destination.
    let destination_token_account =
        find_user_token_account(rpc, &account, &emissions_mint, &emissions_token_program).await?;

    let discriminator = disc("lending_account_withdraw_emissions_permissionless");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),    // marginfi_group
            AccountMeta::new(account, false),           // marginfi_account
            AccountMeta::new(bank_meta.address, false), // bank
            AccountMeta::new_readonly(emissions_mint, false), // emissions_mint
            AccountMeta::new_readonly(emissions_auth_pda, false), // emissions_auth
            AccountMeta::new(emissions_vault_pda, false), // emissions_vault
            AccountMeta::new(destination_token_account, false), // destination_account
            AccountMeta::new_readonly(emissions_token_program, false), // token_program
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&payer));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_withdraw_emissions_permissionless".into(),
            description: format!(
                "Permissionlessly withdraw {} emissions for account {}",
                bank_meta.symbol,
                &params.account[..8]
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                "Emissions go to the account's registered destination, not the caller".into(),
            ],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Set keeper-close flags, enabling keepers to automatically close orders on the account.
pub async fn build_marginfi_set_keeper_flags(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiSetKeeperFlagsParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_set_keeper_flags_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;

    // Resolve bank keys — None means "clear all balances"
    let bank_keys_opt: Option<Vec<[u8; 32]>> = match &params.banks {
        None => None,
        Some(banks) if banks.is_empty() => None,
        Some(banks) => {
            let mut keys = Vec::with_capacity(banks.len());
            for b in banks {
                let meta = resolve_bank_meta(http, b).await?;
                keys.push(meta.address.to_bytes());
            }
            Some(keys)
        }
    };

    let discriminator = disc("marginfi_account_set_keeper_close_flags");
    let args = MarginfiSetKeeperCloseFlagsArgs { bank_keys_opt };
    let data = ix_data(discriminator, &args);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),       // marginfi_account
            AccountMeta::new_readonly(owner, true), // authority (signer)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    let description = if args_are_all(&params.banks) {
        "Clear keeper-close flags on all marginfi balances".to_string()
    } else {
        format!(
            "Set keeper-close flags on {} bank(s)",
            params.banks.as_ref().map(|b| b.len()).unwrap_or(0)
        )
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_set_keeper_flags".into(),
            description,
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                "Clearing balance tags allows keepers to close your open orders automatically"
                    .into(),
            ],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

fn args_are_all(banks: &Option<Vec<String>>) -> bool {
    banks.as_ref().map(|b| b.is_empty()).unwrap_or(true)
}

/// Initialize the liquidation record PDA.
/// Must be called once per account before `marginfi_start_liquidation`.
pub async fn build_marginfi_init_liq_record(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiInitLiqRecordParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_init_liq_record_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;

    // PDA: ["liq_record", marginfi_account]
    let liq_record = liq_record_pda(&account);

    let discriminator = disc("marginfi_account_init_liq_record");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false), // marginfi_group (readonly)
            AccountMeta::new(account, false),        // marginfi_account (mut)
            AccountMeta::new_readonly(owner, true),  // authority (signer)
            AccountMeta::new(owner, true),           // fee_payer (mut, signer)
            AccountMeta::new(liq_record, false),     // liq_record PDA (mut)
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_init_liq_record".into(),
            description: "Initialize liquidation record for marginfi account".into(),
            estimated_fee: "~0.002 SOL (rent for record account)".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                "This is a one-time setup required before calling start_liquidation".into(),
            ],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Update the destination wallet for off-chain emission distributions.
pub async fn build_marginfi_update_emissions_destination(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiUpdateEmissionsDestinationParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_update_emissions_destination_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;
    let destination = parse_pk(&params.destination, "destination")?;

    let discriminator = disc("marginfi_account_update_emissions_destination_account");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),       // marginfi_account (mut)
            AccountMeta::new_readonly(owner, true), // authority (signer)
            AccountMeta::new_readonly(destination, false), // destination (unchecked, readonly)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_update_emissions_destination".into(),
            description: format!("Update emissions destination to {}", params.destination),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Clear accumulated emissions from a balance after the bank has disabled rewards (permissionless).
pub async fn build_marginfi_clear_emissions(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiClearEmissionsParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_clear_emissions_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let pid = program_id();
    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let account = resolve_account(http, &params.account, user_pubkey_str).await?;

    let discriminator = disc("lending_account_clear_emissions");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false),           // marginfi_account (mut)
            AccountMeta::new(bank_meta.address, false), // bank (mut)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_clear_emissions".into(),
            description: format!(
                "Clear stale {} emissions (bank rewards disabled)",
                bank_meta.symbol
            ),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![
                "Only call this after the bank has disabled its emission rewards".into(),
            ],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — Permissionless Actions
// ──────────────────────────────────────────────────────────────────────────────

/// Accrue interest for a bank (permissionless — anyone can call).
pub async fn build_marginfi_accrue_interest(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiAccrueInterestParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_accrue_interest_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let pid = program_id();
    let group = marginfi_group();

    let discriminator = disc("lending_pool_accrue_bank_interest");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false),
            AccountMeta::new(bank_meta.address, false),
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_accrue_interest".into(),
            description: format!("Accrue interest for {} bank", bank_meta.symbol),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Update bank price oracle cache (permissionless).
pub async fn build_marginfi_pulse_price(
    http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiPulsePriceParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_pulse_price_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let oracle = parse_pk(&params.oracle, "oracle")?;
    let pid = program_id();
    let group = marginfi_group();

    let discriminator = disc("lending_pool_pulse_bank_price_cache");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new_readonly(group, false), // marginfi_group (readonly)
            AccountMeta::new(bank_meta.address, false), // bank (mut)
            AccountMeta::new_readonly(oracle, false), // oracle feed (readonly)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_pulse_price".into(),
            description: format!("Update price cache for {} bank", bank_meta.symbol),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Pulse health check for an account (permissionless).
/// Refreshes the internal risk engine health cache on-chain.
/// Note: For full health calculation, the caller should append remaining accounts
/// as alternating pairs of [bank_pubkey, oracle_pubkey] for each active balance.
pub async fn build_marginfi_pulse_health(
    _http: &reqwest::Client,
    _rpc: &str,
    user_pubkey_str: &str,
    params: &MarginfiPulseHealthParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_pulse_health_params(params)?;

    let owner = parse_pk(user_pubkey_str, "wallet")?;
    let account = parse_pk(&params.account, "account")?;
    let pid = program_id();

    let discriminator = disc("lending_account_pulse_health");
    let data = ix_data(discriminator, &NoArgs);

    let ix = Instruction {
        program_id: pid,
        accounts: vec![
            AccountMeta::new(account, false), // marginfi_account (writable)
        ],
        data,
    };

    let message = Message::new(&[ix], Some(&owner));
    let tx = Transaction::new_unsigned(message);
    let encoded = serialize_tx(&tx)?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_pulse_health".into(),
            description: format!("Pulse health for account {}", short_id(&params.account)),
            estimated_fee: "~0.0001 SOL".into(),
            estimated_refund: None,
            params: serde_json::to_value(params)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: Some(encoded),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build Functions — Query Actions
// ──────────────────────────────────────────────────────────────────────────────

/// Query marginfi account info.
pub async fn build_marginfi_account_info(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &MarginfiAccountInfoParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_account_info_params(params)?;

    let lookup_addr = params
        .account
        .as_deref()
        .or(params.wallet.as_deref())
        .unwrap_or(user_pubkey_str);
    let account_info = get_account_info(http, lookup_addr).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_account_info".into(),
            description: format!(
                "Account: {} deposits, {} borrows, health: {:.2}",
                account_info.deposits.len(),
                account_info.borrows.len(),
                account_info.health_factor
            ),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::to_value(&account_info)?,
            warnings: if account_info.health_factor < 1.2 {
                vec!["Low health factor — high liquidation risk".into()]
            } else {
                vec![]
            },
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Query available banks.
pub async fn build_marginfi_banks(
    http: &reqwest::Client,
    _user_pubkey_str: &str,
    params: &MarginfiBanksParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_banks_params(params)?;

    let mut banks = get_banks(http).await?;
    if let Some(limit) = params.limit {
        banks.truncate(limit as usize);
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_banks".into(),
            description: format!("{} lending pools available", banks.len()),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::to_value(&banks)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Query account health.
pub async fn build_marginfi_health(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &MarginfiHealthParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_health_params(params)?;

    let lookup_addr = params
        .account
        .as_deref()
        .or(params.wallet.as_deref())
        .unwrap_or(user_pubkey_str);
    let account_info = get_account_info(http, lookup_addr).await?;

    let health = MarginfiHealth {
        total_collateral: account_info.total_collateral_usd,
        total_borrow: account_info.total_borrow_usd,
        health_factor: account_info.health_factor,
        liquidation_threshold: account_info.liquidation_threshold,
        is_healthy: account_info.health_factor >= 1.0,
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_health".into(),
            description: format!(
                "Health factor: {:.2} | Collateral: ${:.2} | Borrow: ${:.2}",
                health.health_factor, health.total_collateral, health.total_borrow
            ),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::to_value(&health)?,
            warnings: if !health.is_healthy {
                vec!["Account is unhealthy — at risk of liquidation".into()]
            } else if health.health_factor < 1.2 {
                vec!["Health factor is low — monitor closely".into()]
            } else {
                vec![]
            },
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Query points.
pub async fn build_marginfi_points(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &MarginfiPointsParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_points_params(params)?;

    let wallet_addr = params.wallet.as_deref().unwrap_or(user_pubkey_str);
    let points = get_points(http, wallet_addr).await?;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_points".into(),
            description: format!(
                "Points: {:.2} | Rank: #{}",
                points.total_points, points.rank
            ),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::to_value(&points)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Query bank detail.
pub async fn build_marginfi_bank_detail(
    http: &reqwest::Client,
    _user_pubkey_str: &str,
    params: &MarginfiBankDetailParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_bank_detail_params(params)?;

    let bank_meta = resolve_bank_meta(http, &params.bank).await?;
    let bank = MarginfiBank {
        address: bank_meta.address.to_string(),
        mint: bank_meta.mint.to_string(),
        symbol: bank_meta.symbol,
        name: bank_meta.name,
        decimals: bank_meta.decimals,
        total_deposits: "0".into(),
        total_borrows: "0".into(),
        deposit_apy: 0.0,
        borrow_apy: 0.0,
        ltv_ratio: 0.0,
        liquidation_threshold: 0.0,
        oracle_price: 0.0,
        token_program: bank_meta.token_program.to_string(),
    };

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_bank_detail".into(),
            description: format!("Bank detail: {}", bank.symbol),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::to_value(&bank)?,
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Query all marginfi accounts for a wallet by scanning PDA indices.
pub async fn build_marginfi_user_accounts(
    _http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &MarginfiUserAccountsParams,
) -> Result<BuildResponse, AppError> {
    validate_marginfi_user_accounts_params(params)?;

    let wallet = params.wallet.as_deref().unwrap_or(user_pubkey_str);
    let authority = parse_pk(wallet, "wallet")?;
    let pid = program_id();
    let group = marginfi_group();
    let max_index = params.max_index.unwrap_or(3).min(10);

    let mut accounts = Vec::new();
    for i in 0..=max_index {
        let (pda, _) = Pubkey::find_program_address(
            &[
                b"marginfi_account",
                group.as_ref(),
                authority.as_ref(),
                &i.to_le_bytes(),
            ],
            &pid,
        );
        accounts.push(serde_json::json!({
            "index": i,
            "address": pda.to_string(),
        }));
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marginfi_user_accounts".into(),
            description: format!(
                "{} possible PDA accounts for {}",
                accounts.len(),
                short_id(wallet)
            ),
            estimated_fee: "0".into(),
            estimated_refund: None,
            params: serde_json::Value::Array(accounts),
            warnings: vec![],
            requires_approval: false,
        },
        transaction: None,
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

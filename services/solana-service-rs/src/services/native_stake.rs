/// Native SOL Staking — create, deactivate, withdraw, split, merge stake accounts.
///
/// Uses raw Solana stake program instructions (no liquid staking protocol).
/// Stake account keypair is generated backend-side and partially signed,
/// so the frontend only needs the user's main wallet signature.
use base64::Engine;
use serde::{Deserialize, Serialize};
use solana_sdk::{
    message::Message, pubkey::Pubkey, signature::Keypair, signer::Signer, system_instruction,
    transaction::Transaction,
};
use std::str::FromStr;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::solana::connection::SolanaRpc;

const STAKE_ACCOUNT_SPACE: usize = 200; // size of StakeStateV2
const LAMPORTS_PER_SOL: f64 = 1_000_000_000.0;

// ──────────────────────────────────────────────────────────────────────────────
// Params
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeStakeParams {
    /// SOL amount to stake (minimum 1 SOL)
    pub amount: String,
    /// Validator vote account pubkey
    pub validator_vote_account: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeDeactivateParams {
    /// Stake account pubkey to deactivate
    pub stake_account: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeWithdrawStakeParams {
    /// Stake account pubkey
    pub stake_account: String,
    /// Lamports to withdraw, or "all" to close the account entirely
    #[serde(default = "default_all")]
    pub amount: String,
}

fn default_all() -> String {
    "all".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeSplitStakeParams {
    /// Source stake account pubkey
    pub stake_account: String,
    /// SOL amount to split off into a new account (min 1 SOL)
    pub amount: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeMergeStakeParams {
    /// Destination stake account (keeps the balance after merge)
    pub destination_stake_account: String,
    /// Source stake account (will be closed after merge)
    pub source_stake_account: String,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_native_stake_params(p: &NativeStakeParams) -> Result<(), AppError> {
    let amount: f64 = p
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amount < 1.0 {
        return Err(AppError::InvalidParams("Minimum stake is 1 SOL".into()));
    }
    Pubkey::from_str(&p.validator_vote_account)
        .map_err(|_| AppError::InvalidParams("Invalid validator vote account pubkey".into()))?;
    Ok(())
}

pub fn validate_native_deactivate_params(p: &NativeDeactivateParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid stake account pubkey".into()))?;
    Ok(())
}

pub fn validate_native_withdraw_params(p: &NativeWithdrawStakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid stake account pubkey".into()))?;
    if p.amount != "all" {
        let lamports: u64 = p.amount.parse().map_err(|_| {
            AppError::InvalidParams("amount must be lamports (integer) or 'all'".into())
        })?;
        if lamports == 0 {
            return Err(AppError::InvalidParams("amount must be > 0".into()));
        }
    }
    Ok(())
}

pub fn validate_native_split_params(p: &NativeSplitStakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid stake account pubkey".into()))?;
    let amount: f64 = p
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("amount must be a number".into()))?;
    if amount < 1.0 {
        return Err(AppError::InvalidParams(
            "Split amount must be at least 1 SOL".into(),
        ));
    }
    Ok(())
}

pub fn validate_native_merge_params(p: &NativeMergeStakeParams) -> Result<(), AppError> {
    Pubkey::from_str(&p.destination_stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid destination stake account pubkey".into()))?;
    Pubkey::from_str(&p.source_stake_account)
        .map_err(|_| AppError::InvalidParams("Invalid source stake account pubkey".into()))?;
    if p.destination_stake_account == p.source_stake_account {
        return Err(AppError::InvalidParams(
            "Source and destination must be different accounts".into(),
        ));
    }
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

fn short(pubkey: &str) -> String {
    if pubkey.len() < 12 {
        return pubkey.to_string();
    }
    format!("{}…{}", &pubkey[..8], &pubkey[pubkey.len() - 4..])
}

fn serialize_tx(tx: &Transaction) -> Result<String, AppError> {
    let bytes = bincode::serialize(tx)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(&bytes))
}

// ──────────────────────────────────────────────────────────────────────────────
// Build: Create + Initialize + Delegate
// ──────────────────────────────────────────────────────────────────────────────

/// Stake SOL natively: creates a new stake account, initializes it, and
/// delegates to the specified validator in one atomic transaction.
///
/// The stake account keypair is generated here and partially signed by the
/// backend — the frontend only needs the user's wallet signature.
pub fn build_native_stake(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &NativeStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_native_stake_params(params)?;

    let sol_amount: f64 = params.amount.parse().unwrap();
    let stake_lamports = (sol_amount * LAMPORTS_PER_SOL) as u64;
    let vote_pubkey = Pubkey::from_str(&params.validator_vote_account).unwrap();

    // Rent exemption for the stake account (~0.00228 SOL)
    let rent = rpc
        .client()
        .get_minimum_balance_for_rent_exemption(STAKE_ACCOUNT_SPACE)
        .map_err(|e| AppError::Internal(format!("Rent query failed: {e}")))?;

    let total_lamports = stake_lamports + rent;

    // New ephemeral stake account keypair — backend partially signs it
    let stake_kp = Keypair::new();
    let stake_pubkey = stake_kp.pubkey();

    // Instruction 1: create account
    let create_ix = system_instruction::create_account(
        user_pubkey,
        &stake_pubkey,
        total_lamports,
        STAKE_ACCOUNT_SPACE as u64,
        &solana_sdk::stake::program::id(),
    );

    // Instruction 2: initialize (set authorities, no lockup)
    let authorized = solana_sdk::stake::state::Authorized {
        staker: *user_pubkey,
        withdrawer: *user_pubkey,
    };
    let init_ix = solana_sdk::stake::instruction::initialize(
        &stake_pubkey,
        &authorized,
        &solana_sdk::stake::state::Lockup::default(),
    );

    // Instruction 3: delegate to validator
    let delegate_ix =
        solana_sdk::stake::instruction::delegate_stake(&stake_pubkey, user_pubkey, &vote_pubkey);

    let blockhash = rpc.get_latest_blockhash_with_retry()?;
    let message = Message::new(&[create_ix, init_ix, delegate_ix], Some(user_pubkey));
    // Compute actual fee before balance check
    let fee = rpc.client().get_fee_for_message(&message).unwrap_or(10_000);

    // SOL balance check: stake + rent + actual tx fee
    let sol_balance = rpc.client().get_balance(user_pubkey).unwrap_or(0);
    if total_lamports + fee > sol_balance {
        return Err(AppError::InvalidParams(format!(
            "Insufficient SOL. Need {:.6} SOL (stake + rent + fee), have {:.6} SOL",
            (total_lamports + fee) as f64 / LAMPORTS_PER_SOL,
            sol_balance as f64 / LAMPORTS_PER_SOL,
        )));
    }

    let mut tx = Transaction::new_unsigned(message);
    tx.message.recent_blockhash = blockhash;
    // Partial sign with stake account keypair so frontend only signs once
    tx.partial_sign(&[&stake_kp], blockhash);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "native_stake".to_string(),
            description: format!(
                "Stake {:.4} SOL with validator {} → new account {}",
                sol_amount,
                short(&params.validator_vote_account),
                short(&stake_pubkey.to_string()),
            ),
            estimated_fee: format!("{:.6} SOL", fee as f64 / LAMPORTS_PER_SOL),
            estimated_refund: None,
            params: serde_json::json!({
                "amount": params.amount,
                "validatorVoteAccount": params.validator_vote_account,
                "stakeAccount": stake_pubkey.to_string(),
            }),
            warnings: vec![
                format!("New stake account address: {}", stake_pubkey),
                "Activation takes ~1 epoch (~2–3 days) before earning rewards.".to_string(),
                format!(
                    "~{:.6} SOL rent deducted for the stake account (returned on close).",
                    rent as f64 / LAMPORTS_PER_SOL
                ),
            ],
            requires_approval: true,
        },
        transaction: Some(serialize_tx(&tx)?),
        additional_signers_required: 0, // stake keypair already signed by backend
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "stakeAccount": stake_pubkey.to_string(),
            "validatorVoteAccount": params.validator_vote_account,
            "stakelamports": total_lamports,
        })),
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build: Deactivate
// ──────────────────────────────────────────────────────────────────────────────

pub fn build_native_stake_deactivate(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &NativeDeactivateParams,
) -> Result<BuildResponse, AppError> {
    validate_native_deactivate_params(params)?;

    let stake_pubkey = Pubkey::from_str(&params.stake_account).unwrap();

    // Verify the account exists
    let account = rpc.client().get_account(&stake_pubkey).map_err(|_| {
        AppError::InvalidParams(format!(
            "Stake account {} not found on-chain",
            params.stake_account
        ))
    })?;
    let stake_lamports = account.lamports;

    let deactivate_ix =
        solana_sdk::stake::instruction::deactivate_stake(&stake_pubkey, user_pubkey);

    let blockhash = rpc.get_latest_blockhash_with_retry()?;
    let message = Message::new(&[deactivate_ix], Some(user_pubkey));
    let fee = rpc.client().get_fee_for_message(&message).unwrap_or(5_000);

    let mut tx = Transaction::new_unsigned(message);
    tx.message.recent_blockhash = blockhash;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "native_stake_deactivate".to_string(),
            description: format!(
                "Deactivate stake account {} ({:.4} SOL)",
                short(&params.stake_account),
                stake_lamports as f64 / LAMPORTS_PER_SOL,
            ),
            estimated_fee: format!("{:.6} SOL", fee as f64 / LAMPORTS_PER_SOL),
            estimated_refund: None,
            params: serde_json::to_value(params).unwrap_or_default(),
            warnings: vec![
                "Deactivation takes ~1 epoch (~2–3 days) to complete.".to_string(),
                "You will stop earning rewards immediately.".to_string(),
                "Use 'Withdraw' after deactivation to recover your SOL.".to_string(),
            ],
            requires_approval: true,
        },
        transaction: Some(serialize_tx(&tx)?),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build: Withdraw
// ──────────────────────────────────────────────────────────────────────────────

pub fn build_native_stake_withdraw(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &NativeWithdrawStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_native_withdraw_params(params)?;

    let stake_pubkey = Pubkey::from_str(&params.stake_account).unwrap();

    let account = rpc.client().get_account(&stake_pubkey).map_err(|_| {
        AppError::InvalidParams(format!(
            "Stake account {} not found on-chain",
            params.stake_account
        ))
    })?;
    let available = account.lamports;

    // SOL, like every other stake action — this one alone took lamports, so a
    // user who typed the amount they could see ("0.5") had it read as half a
    // billionth of a SOL, and `parse().unwrap()` panicked the request outright
    // the moment the string had a decimal point in it.
    let withdraw_lamports: u64 = if params.amount.trim() == "all" {
        available
    } else {
        let sol: f64 = params.amount.trim().parse().map_err(|_| {
            AppError::InvalidParams(format!(
                "\"{}\" is not an amount. Enter a number of SOL, or \"all\".",
                params.amount
            ))
        })?;
        if sol <= 0.0 {
            return Err(AppError::InvalidParams(
                "Enter an amount greater than zero, or \"all\".".into(),
            ));
        }
        let lamps = (sol * LAMPORTS_PER_SOL) as u64;
        if lamps > available {
            return Err(AppError::InvalidParams(format!(
                "That stake account holds {:.4} SOL, which is less than the {} you asked to withdraw.",
                available as f64 / LAMPORTS_PER_SOL,
                sol
            )));
        }
        lamps
    };

    let withdraw_ix = solana_sdk::stake::instruction::withdraw(
        &stake_pubkey,
        user_pubkey, // withdraw authority
        user_pubkey, // destination (user's main wallet)
        withdraw_lamports,
        None, // custodian (none — no lockup)
    );

    let blockhash = rpc.get_latest_blockhash_with_retry()?;
    let message = Message::new(&[withdraw_ix], Some(user_pubkey));
    let fee = rpc.client().get_fee_for_message(&message).unwrap_or(5_000);

    let mut tx = Transaction::new_unsigned(message);
    tx.message.recent_blockhash = blockhash;

    let is_full_close = withdraw_lamports == available;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "native_stake_withdraw".to_string(),
            description: format!(
                "Withdraw {:.6} SOL from {}{}",
                withdraw_lamports as f64 / LAMPORTS_PER_SOL,
                short(&params.stake_account),
                if is_full_close {
                    " (closes account)"
                } else {
                    ""
                },
            ),
            estimated_fee: format!("{:.6} SOL", fee as f64 / LAMPORTS_PER_SOL),
            // Fee is paid from the main wallet, not deducted from the withdrawal amount
            estimated_refund: Some(format!(
                "+{:.6} SOL",
                withdraw_lamports as f64 / LAMPORTS_PER_SOL
            )),
            params: serde_json::to_value(params).unwrap_or_default(),
            warnings: if is_full_close {
                vec![
                    "Withdrawing the full balance will permanently close this stake account."
                        .to_string(),
                ]
            } else {
                vec![]
            },
            requires_approval: true,
        },
        transaction: Some(serialize_tx(&tx)?),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build: Split
// ──────────────────────────────────────────────────────────────────────────────

pub fn build_native_stake_split(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &NativeSplitStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_native_split_params(params)?;

    // Both of these were `unwrap()`: a mistyped account or a non-numeric
    // amount took the whole request down with a panic instead of answering.
    let stake_pubkey = Pubkey::from_str(&params.stake_account).map_err(|_| {
        AppError::InvalidParams(format!("{} is not a valid account address.", params.stake_account))
    })?;
    let split_sol: f64 = params.amount.trim().parse().map_err(|_| {
        AppError::InvalidParams(format!("\"{}\" is not an amount of SOL.", params.amount))
    })?;
    let split_lamports = (split_sol * LAMPORTS_PER_SOL) as u64;

    let account = rpc.client().get_account(&stake_pubkey).map_err(|_| {
        AppError::InvalidParams(format!(
            "Stake account {} not found on-chain",
            params.stake_account
        ))
    })?;

    let rent = rpc
        .client()
        .get_minimum_balance_for_rent_exemption(STAKE_ACCOUNT_SPACE)
        .unwrap_or(2_282_880);

    // Source must keep at least rent-exempt minimum after split
    let source_remaining = account.lamports.saturating_sub(split_lamports);
    if source_remaining > 0 && source_remaining < rent {
        return Err(AppError::InvalidParams(format!(
            "Source account would have {:.6} SOL remaining — below rent-exempt minimum. \
             Either split less or withdraw the full amount.",
            source_remaining as f64 / LAMPORTS_PER_SOL
        )));
    }
    if split_lamports > account.lamports {
        return Err(AppError::InvalidParams(format!(
            "Requested split {:.6} SOL but stake account only has {:.6} SOL",
            split_lamports as f64 / LAMPORTS_PER_SOL,
            account.lamports as f64 / LAMPORTS_PER_SOL,
        )));
    }

    // New stake account keypair — backend partially signs
    let new_stake_kp = Keypair::new();
    let new_stake_pubkey = new_stake_kp.pubkey();

    // Create empty destination stake account (split instruction will fund it)
    let create_ix = system_instruction::create_account(
        user_pubkey,
        &new_stake_pubkey,
        rent,
        STAKE_ACCOUNT_SPACE as u64,
        &solana_sdk::stake::program::id(),
    );

    // Split instruction moves `split_lamports` from source to destination
    let split_ixs = solana_sdk::stake::instruction::split(
        &stake_pubkey,
        user_pubkey,
        split_lamports,
        &new_stake_pubkey,
    );

    let mut instructions = vec![create_ix];
    instructions.extend(split_ixs);

    let blockhash = rpc.get_latest_blockhash_with_retry()?;
    let message = Message::new(&instructions, Some(user_pubkey));
    let fee = rpc.client().get_fee_for_message(&message).unwrap_or(10_000);

    let mut tx = Transaction::new_unsigned(message);
    tx.message.recent_blockhash = blockhash;
    tx.partial_sign(&[&new_stake_kp], blockhash);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "native_stake_split".to_string(),
            description: format!(
                "Split {:.4} SOL from {} → new account {}",
                params.amount.parse::<f64>().unwrap_or(0.0),
                short(&params.stake_account),
                short(&new_stake_pubkey.to_string()),
            ),
            estimated_fee: format!("{:.6} SOL", fee as f64 / LAMPORTS_PER_SOL),
            estimated_refund: None,
            params: serde_json::json!({
                "stakeAccount": params.stake_account,
                "amount": params.amount,
                "newStakeAccount": new_stake_pubkey.to_string(),
            }),
            warnings: vec![
                format!("New stake account: {}", new_stake_pubkey),
                format!(
                    "~{:.6} SOL rent paid from your wallet for the new stake account.",
                    rent as f64 / LAMPORTS_PER_SOL
                ),
                "The new account inherits the same validator delegation.".to_string(),
            ],
            requires_approval: true,
        },
        transaction: Some(serialize_tx(&tx)?),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: Some(serde_json::json!({
            "newStakeAccount": new_stake_pubkey.to_string(),
        })),
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Build: Merge
// ──────────────────────────────────────────────────────────────────────────────

pub fn build_native_stake_merge(
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &NativeMergeStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_native_merge_params(params)?;

    let dest_pubkey = Pubkey::from_str(&params.destination_stake_account).unwrap();
    let source_pubkey = Pubkey::from_str(&params.source_stake_account).unwrap();

    let dest_account = rpc.client().get_account(&dest_pubkey).map_err(|_| {
        AppError::InvalidParams(format!(
            "Destination stake account {} not found",
            params.destination_stake_account
        ))
    })?;
    let source_account = rpc.client().get_account(&source_pubkey).map_err(|_| {
        AppError::InvalidParams(format!(
            "Source stake account {} not found",
            params.source_stake_account
        ))
    })?;

    let total_after = dest_account.lamports + source_account.lamports;

    let merge_ixs =
        solana_sdk::stake::instruction::merge(&dest_pubkey, &source_pubkey, user_pubkey);

    let blockhash = rpc.get_latest_blockhash_with_retry()?;
    let message = Message::new(&merge_ixs, Some(user_pubkey));
    let fee = rpc.client().get_fee_for_message(&message).unwrap_or(5_000);

    let mut tx = Transaction::new_unsigned(message);
    tx.message.recent_blockhash = blockhash;

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "native_stake_merge".to_string(),
            description: format!(
                "Merge {} → {} (total {:.4} SOL)",
                short(&params.source_stake_account),
                short(&params.destination_stake_account),
                total_after as f64 / LAMPORTS_PER_SOL,
            ),
            estimated_fee: format!("{:.6} SOL", fee as f64 / LAMPORTS_PER_SOL),
            estimated_refund: Some(format!(
                "+{:.6} SOL",
                source_account.lamports as f64 / LAMPORTS_PER_SOL
            )),
            params: serde_json::to_value(params).unwrap_or_default(),
            warnings: vec![
                "Both accounts must have the same stake authority and withdraw authority.".to_string(),
                "Both must be fully inactive OR both active with the same validator and epoch credits.".to_string(),
                "Source account will be permanently closed after merge.".to_string(),
            ],
            requires_approval: true,
        },
        transaction: Some(serialize_tx(&tx)?),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

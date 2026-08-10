use serde::{Deserialize, Serialize};
use solana_client::rpc_request::TokenAccountsFilter;
use solana_sdk::{message::Message, pubkey::Pubkey, transaction::Transaction};
use spl_associated_token_account::get_associated_token_address_with_program_id;
use uuid::Uuid;

use crate::error::AppError;
use crate::solana::connection::SolanaRpc;
use crate::solana::tokens::{get_token_info, resolve_token_address, SOL_MINT};

/// Maximum accounts per batch close (safe legacy TX size limit).
const MAX_CLOSE_PER_TX: usize = 15;

// ──────────────────────────────────────────────────────────────────────────────
// Params
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BurnParams {
    /// Token symbol (e.g. "BONK") or mint address. Also accepted as "token".
    #[serde(alias = "token")]
    pub mint: String,
    /// Amount to burn, or "all" to burn entire balance and close the account.
    pub amount: String,
    /// Close the ATA after burning to reclaim ~0.002 SOL rent.
    #[serde(
        rename = "closeMint",
        default,
        deserialize_with = "crate::services::params::lenient"
    )]
    pub close_mint: bool,
}

/// Batch close of multiple **empty** token accounts to reclaim rent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloseAccountsParams {
    /// List of token mint addresses (or known symbols) whose ATAs should be closed.
    ///
    /// Accepts a JSON array or one comma-separated string: the model sends the
    /// first, the action card — whose params are all strings — sends the second.
    #[serde(deserialize_with = "crate::services::params::string_or_vec")]
    pub mints: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BurnPreview {
    pub id: String,
    #[serde(rename = "type")]
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub estimated_refund: Option<String>,
    pub params: serde_json::Value,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

pub struct BurnBuildResult {
    pub transaction: Transaction,
    pub preview: BurnPreview,
    /// Every transaction in order, when the work does not fit in one.
    ///
    /// `transaction` stays the LAST of them, because that is the one the card
    /// treats as the action; the earlier ones are signed first as steps.
    pub batches: Vec<Transaction>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────


/// Close and burn, built by hand so they work under either token program.
///
/// `spl_token::instruction::*` validates that the program id you pass is the
/// legacy one and refuses anything else — "The account did not have the
/// expected program id" — so it cannot express a Token-2022 instruction at all.
/// The layouts are identical across the two programs; only the id differs.
fn close_account_ix(
    program: &Pubkey,
    account: &Pubkey,
    destination: &Pubkey,
    owner: &Pubkey,
) -> solana_sdk::instruction::Instruction {
    use solana_sdk::instruction::{AccountMeta, Instruction};
    Instruction {
        program_id: *program,
        accounts: vec![
            AccountMeta::new(*account, false),
            AccountMeta::new(*destination, false),
            AccountMeta::new_readonly(*owner, true),
        ],
        data: vec![9], // CloseAccount
    }
}

fn burn_checked_ix(
    program: &Pubkey,
    account: &Pubkey,
    mint: &Pubkey,
    owner: &Pubkey,
    amount: u64,
    decimals: u8,
) -> solana_sdk::instruction::Instruction {
    use solana_sdk::instruction::{AccountMeta, Instruction};
    let mut data = vec![15u8]; // BurnChecked
    data.extend_from_slice(&amount.to_le_bytes());
    data.push(decimals);
    Instruction {
        program_id: *program,
        accounts: vec![
            AccountMeta::new(*account, false),
            AccountMeta::new(*mint, false),
            AccountMeta::new_readonly(*owner, true),
        ],
        data,
    }
}

pub fn validate_burn_params(params: &BurnParams) -> Result<(), AppError> {
    if params.mint.trim().is_empty() {
        return Err(AppError::InvalidParams(
            "burn requires a token mint address or symbol".into(),
        ));
    }
    if params.amount != "all" {
        let amount: f64 = params.amount.parse().map_err(|_| {
            AppError::InvalidParams("amount must be a positive number or 'all'".into())
        })?;
        if amount <= 0.0 {
            return Err(AppError::InvalidParams("amount must be positive".into()));
        }
    }
    Ok(())
}

pub fn validate_close_accounts_params(params: &CloseAccountsParams) -> Result<(), AppError> {
    if params.mints.is_empty() {
        return Err(AppError::InvalidParams(
            "At least one mint address is required".into(),
        ));
    }
    // No upper bound here any more. Fifteen was ours, not Solana's — the real
    // constraint is the 1232-byte transaction, and telling a user with nineteen
    // empty accounts to "split into multiple batches" by hand is asking them to
    // do the arithmetic we are better placed to do. They are batched below.
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Builders
// ──────────────────────────────────────────────────────────────────────────────

/// Build a burn (+ optional close) transaction for a single SPL token.
pub fn build_burn_transaction(
    rpc: &SolanaRpc,
    owner: &Pubkey,
    params: &BurnParams,
) -> Result<BurnBuildResult, AppError> {
    validate_burn_params(params)?;

    let token_address = resolve_token_address(&params.mint);
    if token_address == SOL_MINT || params.mint.to_uppercase() == "SOL" {
        return Err(AppError::InvalidParams("Cannot burn native SOL".into()));
    }

    let mint_pubkey: Pubkey = token_address
        .parse()
        .map_err(|_| AppError::InvalidParams(format!("Invalid token mint: {}", params.mint)))?;

    // Token-2022 mints live under a different program, and both the ATA
    // derivation and the burn/close instructions have to name it. Assuming the
    // legacy program built transactions that could not execute for any
    // Token-2022 token — which is most of what a wallet accumulates now.
    let token_program = rpc
        .client()
        .get_account(&mint_pubkey)
        .map(|a| a.owner)
        .unwrap_or_else(|_| spl_token::id());
    let ata = get_associated_token_address_with_program_id(owner, &mint_pubkey, &token_program);

    let (token_decimals, token_symbol) = match get_token_info(&params.mint) {
        Some(info) => (info.decimals, info.symbol.to_string()),
        None => (9u8, params.mint[..6.min(params.mint.len())].to_uppercase()),
    };

    // Verify the token account exists and get current balance.
    let balance_info = rpc.client().get_token_account_balance(&ata).map_err(|_| {
        AppError::InvalidParams(format!(
            "No {} token account found. Your balance is 0 {}.",
            token_symbol, token_symbol,
        ))
    })?;
    let current_balance_raw = balance_info.amount.parse::<u64>().unwrap_or(0);
    let current_balance_ui = balance_info.ui_amount.unwrap_or(0.0);

    let mut will_close = params.close_mint;

    let burn_amount: u64 = if params.amount == "all" {
        will_close = true; // always close when burning all
        current_balance_raw
    } else {
        let amount_float: f64 = params
            .amount
            .parse::<f64>()
            .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?;
        let raw = (amount_float * 10_f64.powi(token_decimals as i32)) as u64;
        if raw > current_balance_raw {
            return Err(AppError::InvalidParams(format!(
                "Insufficient {} balance. Requested: {}, Available: {}",
                token_symbol, amount_float, current_balance_ui,
            )));
        }
        raw
    };

    // close-only = empty account, just needs a close instruction
    let is_close_only = burn_amount == 0 && will_close;

    if !is_close_only && burn_amount == 0 {
        return Err(AppError::InvalidParams(
            "Nothing to do: amount is zero and closeMint is false".into(),
        ));
    }

    let mut instructions = Vec::new();

    if burn_amount > 0 {
        instructions.push(
            burn_checked_ix(
                &token_program,
                &ata,
                &mint_pubkey,
                owner,
                burn_amount,
                token_decimals,
            ),
        );
    }

    if will_close {
        instructions.push(close_account_ix(&token_program, &ata, owner, owner));
    }

    // Get actual rent locked in the ATA (used for refund estimate).
    let rent_lamports = if will_close {
        rpc.client()
            .get_account(&ata)
            .map(|a| a.lamports)
            .unwrap_or(2_039_280)
    } else {
        0
    };

    let blockhash = rpc.get_latest_blockhash_with_retry()?;
    // Batched, because a Solana transaction is 1232 bytes and each close costs
    // roughly forty of them. Fifteen per transaction leaves comfortable room.
    let chunks: Vec<Vec<solana_sdk::instruction::Instruction>> = instructions
        .chunks(MAX_CLOSE_PER_TX)
        .map(|c| c.to_vec())
        .collect();
    let message = Message::new(&chunks[0], Some(owner));
    let fee = rpc.client().get_fee_for_message(&message).unwrap_or(5_000) * chunks.len() as u64;

    // Verify SOL covers the fee.
    let sol_balance = rpc.client().get_balance(owner).unwrap_or(0);
    if fee > sol_balance {
        return Err(AppError::InvalidParams(format!(
            "Insufficient SOL for transaction fee. Required: {:.6} SOL, Available: {:.6} SOL",
            fee as f64 / 1_000_000_000.0,
            sol_balance as f64 / 1_000_000_000.0,
        )));
    }

    let estimated_fee = format!("{:.6} SOL", fee as f64 / 1_000_000_000.0);
    let net_refund = rent_lamports.saturating_sub(fee);
    let estimated_refund = if will_close {
        Some(format!("+{:.6} SOL", net_refund as f64 / 1_000_000_000.0))
    } else {
        None
    };

    let close_suffix = if will_close { " + close account" } else { "" };
    let human_amount = if params.amount == "all" {
        format!("all {}{}", token_symbol, close_suffix)
    } else {
        format!("{} {}{}", params.amount, token_symbol, close_suffix)
    };

    let action_type = if is_close_only {
        "close_account"
    } else {
        "burn"
    };

    let description = if is_close_only {
        format!("Close empty {} account", token_symbol)
    } else {
        format!("Burn {}", human_amount)
    };

    let mut warnings = Vec::new();
    if is_close_only {
        warnings.push(
            "Closing empty token account to reclaim SOL rent. This is SAFE and reversible."
                .to_string(),
        );
        warnings.push(
            "You can recreate this account anytime by receiving or buying the token again."
                .to_string(),
        );
    } else {
        warnings.push(
            "⚠️ PERMANENT ACTION: Burned tokens cannot be recovered. This is irreversible."
                .to_string(),
        );
        warnings.push(format!(
            "You are burning {} {} (current balance: {:.4}). Double-check before confirming.",
            if params.amount == "all" {
                "ALL".to_string()
            } else {
                params.amount.clone()
            },
            token_symbol,
            current_balance_ui,
        ));
        if will_close {
            warnings.push(format!(
                "Token account will be closed after burning, returning ~{:.6} SOL in rent.",
                rent_lamports as f64 / 1_000_000_000.0,
            ));
        }
    }

    let preview = BurnPreview {
        id: Uuid::new_v4().to_string(),
        action_type: action_type.to_string(),
        description,
        estimated_fee,
        estimated_refund,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings,
        requires_approval: true,
    };

    let batches: Vec<Transaction> = chunks
        .iter()
        .map(|ixs| {
            let mut tx = Transaction::new_unsigned(Message::new(ixs, Some(owner)));
            tx.message.recent_blockhash = blockhash;
            tx
        })
        .collect();

    Ok(BurnBuildResult {
        // The last batch is the action; the ones before it are signed as steps.
        transaction: batches.last().cloned().unwrap(),
        preview,
        batches,
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Scan
// ──────────────────────────────────────────────────────────────────────────────

/// Scan all token accounts for the wallet and return those with zero balance.
/// Returns (empty_accounts_json, total_recoverable_sol).
pub fn scan_empty_accounts(
    rpc: &SolanaRpc,
    owner: &Pubkey,
) -> Result<(Vec<serde_json::Value>, f64, usize), AppError> {
    let accounts = rpc
        .client()
        .get_token_accounts_by_owner(owner, TokenAccountsFilter::ProgramId(spl_token::id()))
        .map_err(|e| AppError::SolanaRpcError(format!("Failed to scan token accounts: {e}")))?;

    let mut empty: Vec<serde_json::Value> = Vec::new();
    let mut total_rent: u64 = 0;

    for keyed in accounts {
        // UiAccountData implements Serialize; navigate without importing the enum.
        let data = serde_json::to_value(&keyed.account.data).unwrap_or_default();

        let amount_str = data
            .pointer("/parsed/info/tokenAmount/amount")
            .and_then(|v| v.as_str())
            .unwrap_or("1");

        let mint = data
            .pointer("/parsed/info/mint")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if mint.is_empty() || mint == SOL_MINT {
            continue;
        }
        if amount_str.parse::<u64>().unwrap_or(1) > 0 {
            continue;
        }

        let rent = keyed.account.lamports;
        let symbol = get_token_info(&mint)
            .map(|i| i.symbol.to_string())
            .unwrap_or_else(|| format!("{}…", &mint[..8.min(mint.len())]));

        total_rent += rent;
        empty.push(serde_json::json!({
            "ata":          keyed.pubkey,
            "mint":         mint,
            "symbol":       symbol,
            "recoverableSol": format!("{:.6}", rent as f64 / 1_000_000_000.0),
        }));
    }

    let total_sol = total_rent as f64 / 1_000_000_000.0;
    Ok((empty, total_sol, total_rent as usize))
}

/// Build a batch close transaction for multiple **empty** token accounts.
/// Non-empty accounts are silently skipped with a warning in the preview.
pub fn build_close_accounts_transaction(
    rpc: &SolanaRpc,
    owner: &Pubkey,
    params: &CloseAccountsParams,
) -> Result<BurnBuildResult, AppError> {
    validate_close_accounts_params(params)?;

    let mut instructions = Vec::new();
    let mut total_rent: u64 = 0;
    let mut closed_symbols: Vec<String> = Vec::new();
    let mut skipped_non_empty: Vec<String> = Vec::new();

    for mint_str in &params.mints {
        let token_address = resolve_token_address(mint_str);
        if token_address == SOL_MINT || mint_str.to_uppercase() == "SOL" {
            continue;
        }

        let mint_pubkey: Pubkey = match token_address.parse::<Pubkey>() {
            Ok(p) => p,
            Err(_) => continue,
        };

        // Which token program owns this mint. Fourteen of this wallet's
        // nineteen empty accounts are Token-2022, and every one of them was
        // being closed with the legacy program id — a transaction that cannot
        // succeed, built from a list that looked right.
        let token_program = match rpc.client().get_account(&mint_pubkey) {
            Ok(acc) => acc.owner,
            Err(_) => continue,
        };
        let ata = get_associated_token_address_with_program_id(owner, &mint_pubkey, &token_program);

        let symbol = get_token_info(mint_str)
            .map(|i| i.symbol.to_string())
            .unwrap_or_else(|| format!("{}…", &mint_str[..6.min(mint_str.len())]));

        // Verify balance is actually 0 — refuse to batch-close non-empty accounts.
        let balance = rpc
            .client()
            .get_token_account_balance(&ata)
            .ok()
            .and_then(|b| b.amount.parse::<u64>().ok())
            .unwrap_or(1); // non-existent account → treat as non-zero → skip

        if balance > 0 {
            skipped_non_empty.push(symbol);
            continue;
        }

        // Get actual rent (skip if account doesn't exist on-chain).
        let rent = match rpc.client().get_account(&ata) {
            Ok(acc) => acc.lamports,
            Err(_) => continue,
        };

        total_rent += rent;
        closed_symbols.push(symbol);

        instructions.push(close_account_ix(&token_program, &ata, owner, owner));
    }

    if instructions.is_empty() {
        return Err(AppError::InvalidParams(
            "No closeable empty token accounts found in the provided list. \
             All accounts either have a non-zero balance or do not exist."
                .into(),
        ));
    }

    let blockhash = rpc.get_latest_blockhash_with_retry()?;
    // Batched, because a Solana transaction is 1232 bytes and each close costs
    // roughly forty of them. Fifteen per transaction leaves comfortable room.
    let chunks: Vec<Vec<solana_sdk::instruction::Instruction>> = instructions
        .chunks(MAX_CLOSE_PER_TX)
        .map(|c| c.to_vec())
        .collect();
    let message = Message::new(&chunks[0], Some(owner));
    let fee = rpc.client().get_fee_for_message(&message).unwrap_or(5_000) * chunks.len() as u64;

    // Verify SOL covers the fee.
    let sol_balance = rpc.client().get_balance(owner).unwrap_or(0);
    if fee > sol_balance {
        return Err(AppError::InvalidParams(format!(
            "Insufficient SOL for transaction fee. Required: {:.6} SOL, Available: {:.6} SOL",
            fee as f64 / 1_000_000_000.0,
            sol_balance as f64 / 1_000_000_000.0,
        )));
    }

    let net_refund = total_rent.saturating_sub(fee);
    let count = closed_symbols.len();

    let names_preview = if closed_symbols.len() > 4 {
        format!("{}, +{} more", closed_symbols[..4].join(", "), count - 4)
    } else {
        closed_symbols.join(", ")
    };

    let description = format!(
        "Close {} empty account{} ({})",
        count,
        if count == 1 { "" } else { "s" },
        names_preview,
    );

    let estimated_fee = format!("{:.6} SOL", fee as f64 / 1_000_000_000.0);
    let estimated_refund = format!("+{:.6} SOL", net_refund as f64 / 1_000_000_000.0);

    let mut warnings = vec![format!(
        "Closing {} empty token account{}. This is SAFE and fully reversible — \
         accounts can be recreated automatically when you next receive that token.",
        count,
        if count == 1 { "" } else { "s" },
    )];

    if !skipped_non_empty.is_empty() {
        warnings.push(format!(
            "Skipped {} account{} with non-zero balance: {}. \
             Use 'Burn & Close' to permanently remove those.",
            skipped_non_empty.len(),
            if skipped_non_empty.len() == 1 {
                ""
            } else {
                "s"
            },
            skipped_non_empty.join(", "),
        ));
    }

    let preview = BurnPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "close_accounts".to_string(),
        description,
        estimated_fee,
        estimated_refund: Some(estimated_refund),
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings,
        requires_approval: true,
    };

    let batches: Vec<Transaction> = chunks
        .iter()
        .map(|ixs| {
            let mut tx = Transaction::new_unsigned(Message::new(ixs, Some(owner)));
            tx.message.recent_blockhash = blockhash;
            tx
        })
        .collect();

    Ok(BurnBuildResult {
        // The last batch is the action; the ones before it are signed as steps.
        transaction: batches.last().cloned().unwrap(),
        preview,
        batches,
    })
}

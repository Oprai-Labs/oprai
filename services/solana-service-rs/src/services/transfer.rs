use serde::{Deserialize, Serialize};
use solana_sdk::{
    message::Message,
    pubkey::Pubkey,
    system_instruction,
    transaction::Transaction,
};
use spl_associated_token_account::get_associated_token_address;
use spl_token::instruction as spl_ix;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::amount::parse_amount_to_base_units;
use crate::solana::connection::SolanaRpc;
use crate::solana::tokens::{get_token_info, resolve_token_address, SOL_MINT};

// ──────────────────────────────────────────────────────────────────────────────
// Params
// ──────────────────────────────────────────────────────────────────────────────

/// Transfer request parameters (mirrors @oprai/types TransferParams).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferParams {
    pub to: String,
    pub amount: String,
    pub token: String,
    #[serde(rename = "tokenDecimals")]
    pub token_decimals: Option<u8>,
}

/// Validated transfer action preview returned to the client.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TransferPreview {
    pub id: String,
    #[serde(rename = "type")]
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    pub params: TransferParams,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

/// Result of building a transfer transaction.
pub struct TransferBuildResult {
    pub transaction: Transaction,
    pub preview: TransferPreview,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_transfer_params(params: &TransferParams) -> Result<(), AppError> {
    // Validate recipient address.
    params
        .to
        .parse::<Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid recipient address".into()))?;

    // Validate amount.
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams(
            "Amount must be a positive number".into(),
        ));
    }

    // Token must be specified.
    if params.token.trim().is_empty() {
        return Err(AppError::InvalidParams("Token must be specified".into()));
    }

    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Builder
// ──────────────────────────────────────────────────────────────────────────────

/// Build a SOL or SPL token transfer transaction.
pub fn build_transfer_transaction(
    rpc: &SolanaRpc,
    from_pubkey: &Pubkey,
    params: &TransferParams,
) -> Result<TransferBuildResult, AppError> {
    validate_transfer_params(params)?;

    let to_pubkey: Pubkey = params.to.parse()?;
    let token_address = resolve_token_address(&params.token);
    let is_native_sol =
        token_address == SOL_MINT || params.token.to_uppercase() == "SOL";

    // Resolve decimals and symbol.
    let (token_decimals, token_symbol) = match get_token_info(&params.token) {
        Some(info) => (info.decimals, info.symbol.to_string()),
        None => (
            params.token_decimals.unwrap_or(9),
            params.token.to_uppercase(),
        ),
    };

    let amount_in_smallest = parse_amount_to_base_units(&params.amount, token_decimals)?;
    // Used only for human-readable messages and threshold comparisons (not financial math).
    let amount_float: f64 = params.amount.parse().unwrap_or(0.0);

    let mut instructions = Vec::new();
    let mut ata_creation_needed = false;

    if is_native_sol {
        // Preliminary balance check (fee refinement happens after estimation).
        let sol_balance = rpc
            .client()
            .get_balance(from_pubkey)
            .map_err(|e| AppError::SolanaRpcError(e.to_string()))?;
        if amount_in_smallest > sol_balance {
            return Err(AppError::InvalidParams(format!(
                "Insufficient SOL balance. Requested: {:.9} SOL, Available: {:.9} SOL",
                amount_float,
                sol_balance as f64 / 1_000_000_000.0,
            )));
        }

        instructions.push(system_instruction::transfer(
            from_pubkey,
            &to_pubkey,
            amount_in_smallest,
        ));
    } else {
        let mint_pubkey: Pubkey = token_address
            .parse()
            .map_err(|_| AppError::InvalidParams("Invalid token mint address".into()))?;

        let source_ata = get_associated_token_address(from_pubkey, &mint_pubkey);
        let dest_ata = get_associated_token_address(&to_pubkey, &mint_pubkey);

        // Check sender SPL token balance.
        match rpc.client().get_token_account_balance(&source_ata) {
            Ok(bal) => {
                let available = bal.amount.parse::<u64>().unwrap_or(0);
                if amount_in_smallest > available {
                    return Err(AppError::InvalidParams(format!(
                        "Insufficient {} balance. Requested: {}, Available: {}",
                        token_symbol,
                        amount_float,
                        bal.ui_amount.unwrap_or(0.0),
                    )));
                }
            }
            Err(_) => {
                return Err(AppError::InvalidParams(format!(
                    "No {} token account found. You have 0 {}.",
                    token_symbol, token_symbol,
                )));
            }
        }

        // Check if destination ATA exists; create if missing.
        if rpc.client().get_account(&dest_ata).is_err() {
            ata_creation_needed = true;
            instructions.push(
                spl_associated_token_account::instruction::create_associated_token_account(
                    from_pubkey,
                    &to_pubkey,
                    &mint_pubkey,
                    &spl_token::id(),
                ),
            );
        }

        instructions.push(
            spl_ix::transfer_checked(
                &spl_token::id(),
                &source_ata,
                &mint_pubkey,
                &dest_ata,
                from_pubkey,
                &[],
                amount_in_smallest,
                token_decimals,
            )
            .map_err(|e| AppError::SolanaRpcError(e.to_string()))?,
        );
    }

    let blockhash = rpc.get_latest_blockhash_with_retry()?;
    let message = Message::new(&instructions, Some(from_pubkey));
    let fee = rpc
        .client()
        .get_fee_for_message(&message)
        .unwrap_or(5_000);
    let estimated_fee = format!("{:.6} SOL", fee as f64 / 1_000_000_000.0);

    // Verify SOL covers fee (and ATA rent if needed).
    let sol_balance = rpc.client().get_balance(from_pubkey).unwrap_or(0);
    // ATA creation rent-exempt minimum is ~0.00203928 SOL = 2_039_280 lamports.
    let ata_rent: u64 = if ata_creation_needed { 2_039_280 } else { 0 };
    let sol_needed = if is_native_sol {
        amount_in_smallest.saturating_add(fee)
    } else {
        fee.saturating_add(ata_rent)
    };
    if sol_needed > sol_balance {
        return Err(AppError::InvalidParams(format!(
            "Insufficient SOL for transaction fees. Required: {:.6} SOL, Available: {:.6} SOL",
            sol_needed as f64 / 1_000_000_000.0,
            sol_balance as f64 / 1_000_000_000.0,
        )));
    }

    let short_to = format!(
        "{}...{}",
        &params.to[..4.min(params.to.len())],
        &params.to[params.to.len().saturating_sub(4)..]
    );

    let mut warnings = Vec::new();
    if is_native_sol && amount_float > 1.0 {
        warnings.push(format!("Large SOL transfer: {} SOL", params.amount));
    } else if !is_native_sol {
        let large_threshold = match token_symbol.as_str() {
            "USDC" | "USDT" | "PYUSD" => 1_000.0_f64,
            _ => 100_000.0_f64,
        };
        if amount_float > large_threshold {
            warnings.push(format!(
                "Large {} transfer: {} {}",
                token_symbol, params.amount, token_symbol,
            ));
        }
    }
    if ata_creation_needed {
        warnings.push(format!(
            "Recipient has no {} account. A new account will be created (~0.002 SOL rent fee).",
            token_symbol,
        ));
    }

    let preview = TransferPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "transfer".to_string(),
        description: format!(
            "Transfer {} {} to {}",
            params.amount, token_symbol, short_to
        ),
        estimated_fee,
        params: params.clone(),
        warnings,
        requires_approval: true,
    };

    let mut tx = Transaction::new_unsigned(Message::new(&instructions, Some(from_pubkey)));
    tx.message.recent_blockhash = blockhash;

    Ok(TransferBuildResult {
        transaction: tx,
        preview,
    })
}

//! Marinade Finance Service
//!
//! Liquid staking protocol for SOL. Stake SOL → receive mSOL.
//! Features: stake, unstake (instant/delayed), stats.

use base64::Engine;
use serde::{Deserialize, Serialize};
use solana_sdk::{
    instruction::{AccountMeta, Instruction},
    message::Message,
    pubkey::Pubkey,
    system_program,
    transaction::Transaction,
};
use std::str::FromStr;
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};

// ──────────────────────────────────────────────────────────────────────────────
// Constants
// ──────────────────────────────────────────────────────────────────────────────

/// Marinade Finance Program ID
pub const MARINADE_PROGRAM_ID: &str = "MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD";

/// mSOL token mint
pub const MSOL_MINT: &str = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So";

/// Marinade State Account (holds protocol state)
pub const MARINADE_STATE: &str = "8szGkuLTAux9XMgZ2vtY39jVSowEcpBfFfD8hXSEqdGC";

/// Marinade Reserve SOL PDA — source account for claim transfers (from IDL: reservePda)
pub const MARINADE_RESERVE: &str = "Du3Ysj1wKbxPKkuPPnvzQLQh8oMSVifs3jGZjJWXFmHN";

/// Marinade public API base URL — served by Marinade's indexer.
/// Note: the older `mainnet-assets.marinade.finance` host has been retired.
pub const MARINADE_API: &str = "https://api.marinade.finance";

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/// Marinade stats response
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarinadeStats {
    #[serde(rename = "msolPrice")]
    pub msol_price: Option<String>,
    #[serde(rename = "totalSolStaked")]
    pub total_sol_staked: Option<String>,
    #[serde(rename = "apy")]
    pub apy: Option<f64>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Action Params
// ──────────────────────────────────────────────────────────────────────────────

/// Params for marinade_stake: Stake SOL → receive mSOL
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarinadeStakeParams {
    /// Amount of SOL to stake (in SOL, not lamports)
    pub amount: String,
    /// Optional slippage in basis points (default: 50)
    #[serde(default)]
    pub slippage_bps: Option<u16>,
}

/// Params for marinade_unstake: Instant unstake mSOL → SOL
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarinadeUnstakeParams {
    /// Amount of mSOL to unstake (in mSOL, not lamports)
    pub amount: String,
    /// Optional slippage in basis points (default: 50)
    #[serde(default)]
    pub slippage_bps: Option<u16>,
}

/// Params for marinade_delayed_unstake: Delayed unstake mSOL → SOL
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarinadeDelayedUnstakeParams {
    /// Amount of mSOL to unstake (in mSOL, not lamports)
    pub amount: String,
}

/// Params for marinade_claim_ticket: claim SOL after the ~2-epoch waiting period.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarinadeClaimTicketParams {
    /// The on-chain order ticket account pubkey (returned by marinade_delayed_unstake).
    #[serde(rename = "ticketAccount", alias = "ticket_account")]
    pub ticket_account: String,
}

// Legacy stake/unstake params (for backward compatibility)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StakeParams {
    pub amount: String,
    #[serde(default)]
    pub validator: Option<String>,
    #[serde(default)]
    pub protocol: Option<String>,
    #[serde(default)]
    pub instant_unstake: Option<bool>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_marinade_claim_ticket_params(
    params: &MarinadeClaimTicketParams,
) -> Result<(), AppError> {
    if params.ticket_account.trim().is_empty() {
        return Err(AppError::InvalidParams(
            "ticket_account is required (pubkey returned by marinade_delayed_unstake)".into(),
        ));
    }
    Pubkey::from_str(&params.ticket_account).map_err(|_| {
        AppError::InvalidParams("ticket_account must be a valid Solana pubkey".into())
    })?;
    Ok(())
}

pub fn validate_marinade_stake_params(params: &MarinadeStakeParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid amount: must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    if let Some(bps) = params.slippage_bps {
        if bps > 10000 {
            return Err(AppError::InvalidParams(
                "Slippage must be <= 10000 bps (100%)".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_marinade_unstake_params(params: &MarinadeUnstakeParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid amount: must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    if let Some(bps) = params.slippage_bps {
        if bps > 10000 {
            return Err(AppError::InvalidParams(
                "Slippage must be <= 10000 bps (100%)".into(),
            ));
        }
    }
    Ok(())
}

pub fn validate_marinade_delayed_unstake_params(
    params: &MarinadeDelayedUnstakeParams,
) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Invalid amount: must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// API Helpers
// ──────────────────────────────────────────────────────────────────────────────

/// Fetch Marinade stats from the public API. Returns None if the upstream
/// endpoint is unavailable — callers MUST treat this as a hard failure rather
/// than substituting defaults.
pub async fn get_marinade_stats(http: &reqwest::Client) -> Option<MarinadeStats> {
    // /tlv carries the real-time totals; we map them into the legacy
    // MarinadeStats shape so existing callers (apy/stats descriptions) keep
    // working. Note: /tlv doesn't expose an APY field, so apy stays None.
    let resp = http.get(format!("{MARINADE_API}/tlv")).send().await.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    resp.json::<serde_json::Value>()
        .await
        .ok()
        .map(|v| MarinadeStats {
            msol_price: None, // populated separately by get_msol_exchange_rate
            total_sol_staked: v
                .get("total_sol")
                .and_then(|n| n.as_f64())
                .map(|n| n.to_string()),
            apy: None,
        })
}

/// Get the current mSOL/SOL exchange rate from Marinade's own indexer.
///
/// Source: `https://api.marinade.finance/msol/price_sol` — returns the rate
/// as a bare numeric body, recomputed by Marinade's indexer on every Solana
/// slot. This is the live spot price, NOT a daily APY rollup.
///
/// Returns an error if the rate cannot be obtained — there is intentionally
/// **no static fallback constant** because using one would silently mislead
/// the user (e.g. previewing 0.1 SOL → 0.1 mSOL when the true rate is ~0.73).
/// Build paths must propagate the error so the UI can display a clear message
/// and the user can retry.
pub async fn get_msol_exchange_rate(http: &reqwest::Client) -> Result<f64, AppError> {
    let resp = http
        .get(format!("{MARINADE_API}/msol/price_sol"))
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("Marinade rate fetch failed: {e}")))?;
    if !resp.status().is_success() {
        return Err(AppError::Internal(format!(
            "Marinade rate upstream HTTP {}",
            resp.status()
        )));
    }
    let body = resp
        .text()
        .await
        .map_err(|e| AppError::Internal(format!("Marinade rate read failed: {e}")))?;
    let v: f64 = body.trim().parse().map_err(|e| {
        AppError::Internal(format!("Marinade rate parse failed: {e} (body={body:?})"))
    })?;
    if !v.is_finite() || v <= 0.0 {
        return Err(AppError::Internal(format!(
            "Marinade rate out of range: {v}"
        )));
    }
    Ok(v)
}

// ──────────────────────────────────────────────────────────────────────────────
// Transaction Builders
// ──────────────────────────────────────────────────────────────────────────────

/// Build marinade_stake transaction: Stake SOL → receive mSOL
pub async fn build_marinade_stake(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &MarinadeStakeParams,
) -> Result<BuildResponse, AppError> {
    validate_marinade_stake_params(params)?;

    let wallet_pubkey = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {}", e)))?;
    let exchange_rate = get_msol_exchange_rate(http).await?;
    let slippage = params.slippage_bps.unwrap_or(50);
    let amount_lamports = (params
        .amount
        .parse::<f64>()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?
        * 1_000_000_000.0) as u64;
    let expected_msol = params
        .amount
        .parse::<f64>()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?
        / exchange_rate;

    let program_id = Pubkey::from_str(MARINADE_PROGRAM_ID).unwrap_or_default();
    let msol_mint = Pubkey::from_str(MSOL_MINT).unwrap_or_default();
    let state = Pubkey::from_str(MARINADE_STATE).unwrap_or_default();

    // Create instruction for stake (simplified - actual Marinade SDK would have more accounts)
    let ix = Instruction {
        program_id,
        accounts: vec![
            AccountMeta::new(wallet_pubkey, true), // Payer/Signer
            AccountMeta::new(state, false),        // Marinade state
            AccountMeta::new(msol_mint, false),    // mSOL mint
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data: vec![
            0u8, // Deposit instruction discriminator
        ]
        .into_iter()
        .chain(amount_lamports.to_le_bytes().iter().copied())
        .chain((slippage as u64).to_le_bytes().iter().copied())
        .collect(),
    };

    let message = Message::new(&[ix], Some(&wallet_pubkey));
    let tx = Transaction::new_unsigned(message);

    let serialized = bincode::serialize(&tx)
        .map_err(|e| AppError::Internal(format!("Serialize error: {}", e)))?;
    let encoded = base64::engine::general_purpose::STANDARD.encode(&serialized);

    let mut warnings = vec![];
    if params
        .amount
        .parse::<f64>()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?
        > 100.0
    {
        warnings.push("Large stake amount - consider splitting for safety".into());
    }

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marinade_stake".into(),
            description: format!(
                "Stake {} SOL → ~{:.4} mSOL (Marinade)",
                params.amount, expected_msol
            ),
            estimated_fee: "~0.001 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "amount": params.amount,
                "expectedMsol": format!("{expected_msol:.6}"),
                "exchangeRate": exchange_rate,
            }),
            warnings,
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

/// Build marinade_unstake transaction: Instant unstake mSOL → SOL
pub async fn build_marinade_unstake(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &MarinadeUnstakeParams,
) -> Result<BuildResponse, AppError> {
    validate_marinade_unstake_params(params)?;

    let wallet_pubkey = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {}", e)))?;
    let exchange_rate = get_msol_exchange_rate(http).await?;
    let slippage = params.slippage_bps.unwrap_or(50);
    let amount_lamports = (params
        .amount
        .parse::<f64>()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?
        * 1_000_000_000.0) as u64;
    let expected_sol = params
        .amount
        .parse::<f64>()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?
        * exchange_rate;

    let program_id = Pubkey::from_str(MARINADE_PROGRAM_ID).unwrap_or_default();
    let msol_mint = Pubkey::from_str(MSOL_MINT).unwrap_or_default();
    let state = Pubkey::from_str(MARINADE_STATE).unwrap_or_default();

    let ix = Instruction {
        program_id,
        accounts: vec![
            AccountMeta::new(wallet_pubkey, true),
            AccountMeta::new(state, false),
            AccountMeta::new(msol_mint, false),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data: vec![
            1u8, // Instant unstake instruction discriminator
        ]
        .into_iter()
        .chain(amount_lamports.to_le_bytes().iter().copied())
        .chain((slippage as u64).to_le_bytes().iter().copied())
        .collect(),
    };

    let message = Message::new(&[ix], Some(&wallet_pubkey));
    let tx = Transaction::new_unsigned(message);

    let serialized = bincode::serialize(&tx)
        .map_err(|e| AppError::Internal(format!("Serialize error: {}", e)))?;
    let encoded = base64::engine::general_purpose::STANDARD.encode(&serialized);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marinade_unstake".into(),
            description: format!(
                "Instant Unstake {} mSOL → ~{:.4} SOL (Marinade)",
                params.amount, expected_sol
            ),
            estimated_fee: "~0.3% fee".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "amount": params.amount,
                "expectedSol": format!("{expected_sol:.6}"),
                "exchangeRate": exchange_rate,
            }),
            warnings: vec!["Instant unstake has a ~0.3% fee".into()],
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

/// Build marinade_delayed_unstake transaction: Delayed unstake mSOL → SOL
pub async fn build_marinade_delayed_unstake(
    http: &reqwest::Client,
    user_pubkey_str: &str,
    params: &MarinadeDelayedUnstakeParams,
) -> Result<BuildResponse, AppError> {
    validate_marinade_delayed_unstake_params(params)?;

    let wallet_pubkey = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {}", e)))?;
    let exchange_rate = get_msol_exchange_rate(http).await?;
    let amount_lamports = (params
        .amount
        .parse::<f64>()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?
        * 1_000_000_000.0) as u64;
    let expected_sol = params
        .amount
        .parse::<f64>()
        .map_err(|_| AppError::InvalidParams("Invalid amount".into()))?
        * exchange_rate;

    let program_id = Pubkey::from_str(MARINADE_PROGRAM_ID).unwrap_or_default();
    let msol_mint = Pubkey::from_str(MSOL_MINT).unwrap_or_default();
    let state = Pubkey::from_str(MARINADE_STATE).unwrap_or_default();

    let ix = Instruction {
        program_id,
        accounts: vec![
            AccountMeta::new(wallet_pubkey, true),
            AccountMeta::new(state, false),
            AccountMeta::new(msol_mint, false),
            AccountMeta::new_readonly(system_program::id(), false),
        ],
        data: vec![
            2u8, // Delayed unstake instruction discriminator
        ]
        .into_iter()
        .chain(amount_lamports.to_le_bytes().iter().copied())
        .collect(),
    };

    let message = Message::new(&[ix], Some(&wallet_pubkey));
    let tx = Transaction::new_unsigned(message);

    let serialized = bincode::serialize(&tx)
        .map_err(|e| AppError::Internal(format!("Serialize error: {}", e)))?;
    let encoded = base64::engine::general_purpose::STANDARD.encode(&serialized);

    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marinade_delayed_unstake".into(),
            description: format!(
                "Delayed Unstake {} mSOL → ~{:.4} SOL (Marinade)",
                params.amount, expected_sol
            ),
            estimated_fee: "~0.001 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({
                "amount": params.amount,
                "expectedSol": format!("{expected_sol:.6}"),
                "exchangeRate": exchange_rate,
            }),
            warnings: vec!["Delayed unstake takes ~2 epochs (~4 days)".into()],
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
// Claim order ticket (after delayed unstake epoch wait)
// ──────────────────────────────────────────────────────────────────────────────

/// Build a transaction to claim SOL from a matured Marinade order ticket.
///
/// After `marinade_delayed_unstake`, the user receives an order ticket. Once
/// the ~2-epoch waiting period elapses, this instruction burns the ticket and
/// transfers the SOL back to the user.
///
/// Anchor discriminator (sha256("global:claim")[..8]):
/// [0xb7, 0x12, 0x46, 0x9c, 0x94, 0x6d, 0xa1, 0x22]
pub async fn build_marinade_claim_ticket(
    user_pubkey_str: &str,
    params: &MarinadeClaimTicketParams,
) -> Result<BuildResponse, AppError> {
    validate_marinade_claim_ticket_params(params)?;

    let wallet_pubkey = Pubkey::from_str(user_pubkey_str)
        .map_err(|e| AppError::InvalidParams(format!("Invalid wallet: {e}")))?;
    let ticket_pubkey = Pubkey::from_str(&params.ticket_account)
        .map_err(|e| AppError::InvalidParams(format!("Invalid ticket_account: {e}")))?;

    let program_id = Pubkey::from_str(MARINADE_PROGRAM_ID).unwrap_or_default();
    let state = Pubkey::from_str(MARINADE_STATE).unwrap_or_default();
    let reserve_pda = Pubkey::from_str(MARINADE_RESERVE).unwrap_or_default();

    // Anchor discriminator: sha256("global:claim")[..8]
    let discriminator: [u8; 8] = [0xb7, 0x12, 0x46, 0x9c, 0x94, 0x6d, 0xa1, 0x22];

    // Accounts per marinade_finance.json IDL (6 accounts):
    // state, reservePda, ticketAccount, transferSolTo, clock, systemProgram
    let ix = Instruction {
        program_id,
        accounts: vec![
            AccountMeta::new(state, false),         // 0: state
            AccountMeta::new(reserve_pda, false),   // 1: reservePda (was missing!)
            AccountMeta::new(ticket_pubkey, false), // 2: ticketAccount
            AccountMeta::new(wallet_pubkey, false), // 3: transferSolTo (not signer per IDL)
            AccountMeta::new_readonly(solana_sdk::sysvar::clock::id(), false), // 4: clock
            AccountMeta::new_readonly(system_program::id(), false), // 5: systemProgram
        ],
        data: discriminator.to_vec(),
    };

    let message = Message::new(&[ix], Some(&wallet_pubkey));
    let tx = Transaction::new_unsigned(message);
    let serialized =
        bincode::serialize(&tx).map_err(|e| AppError::Internal(format!("Serialize error: {e}")))?;
    let encoded = base64::engine::general_purpose::STANDARD.encode(&serialized);

    let short = &params.ticket_account[..8.min(params.ticket_account.len())];
    Ok(BuildResponse {
        preview: ActionPreview {
            id: Uuid::new_v4().to_string(),
            action_type: "marinade_claim_ticket".into(),
            description: format!("Claim Marinade order ticket {}... → SOL", short),
            estimated_fee: "~0.000005 SOL".into(),
            estimated_refund: None,
            params: serde_json::json!({ "ticketAccount": params.ticket_account }),
            warnings: vec!["This will fail if the ticket has not yet matured. \
                 Marinade delayed unstakes take ~2 epochs (~4 days)."
                .into()],
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

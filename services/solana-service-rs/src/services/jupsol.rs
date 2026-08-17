/// JupSOL liquid staking via Jupiter Swap API.
///
/// JupSOL (mint: `jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v`) is Jupiter's
/// liquid staking token. Stake/unstake is performed via Jupiter's own swap API.
///
/// Stake:   SOL  → JupSOL  (SOL mint → JupSOL mint)
/// Unstake: JupSOL → SOL   (JupSOL mint → SOL mint)
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::builder::{ActionPreview, BuildResponse};
use crate::services::swap::{build_swap_transaction, SwapParams};

/// JupSOL token mint on mainnet.
pub const JUPSOL_MINT: &str = "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v";
/// Native SOL wrapped mint address.
pub const SOL_MINT: &str = "So11111111111111111111111111111111111111112";

/// Parameters for JupSOL stake / unstake operations.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JupSolParams {
    /// Human-readable amount (e.g. "1.5").
    pub amount: String,
    /// Optional slippage in basis points (default: 50 = 0.5%).
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub slippage_bps: Option<u32>,
}

pub fn validate_jupsol_params(params: &JupSolParams) -> Result<(), AppError> {
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams("Amount must be positive".into()));
    }
    Ok(())
}

/// Stake SOL → JupSOL via Jupiter swap.
pub async fn build_jupsol_stake_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &JupSolParams,
) -> Result<BuildResponse, AppError> {
    validate_jupsol_params(params)?;

    let swap_params = SwapParams {
        dexes: None,
        input_mint: SOL_MINT.to_string(),
        output_mint: JUPSOL_MINT.to_string(),
        amount: params.amount.clone(),
        slippage_bps: params.slippage_bps.or(Some(50)),
        only_direct_routes: None,
        swap_mode: None,
        priority_fee: None,
        restrict_intermediate_tokens: None,
        fee_discount_pct: 0,
    };

    let result = build_swap_transaction(http, jupiter_api_key, user_pubkey, &swap_params).await?;

    let amount: f64 = params.amount.parse().unwrap_or(0.0);
    let out_amount: f64 = result.quote.out_amount.parse::<u64>().unwrap_or(0) as f64 / 1e9; // JupSOL has 9 decimals

    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "jupsol_stake".to_string(),
        description: format!(
            "Stake {:.4} SOL → {:.6} JupSOL (Jupiter liquid staking, 0% fee)",
            amount, out_amount,
        ),
        estimated_fee: result.preview.estimated_fee,
        estimated_refund: None,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings: result.preview.warnings,
        requires_approval: true,
    };

    Ok(BuildResponse {
        preview,
        transaction: Some(result.transaction_base64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

/// Unstake JupSOL → SOL via Jupiter swap.
pub async fn build_jupsol_unstake_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &JupSolParams,
) -> Result<BuildResponse, AppError> {
    validate_jupsol_params(params)?;

    let swap_params = SwapParams {
        dexes: None,
        input_mint: JUPSOL_MINT.to_string(),
        output_mint: SOL_MINT.to_string(),
        amount: params.amount.clone(),
        slippage_bps: params.slippage_bps.or(Some(50)),
        only_direct_routes: None,
        swap_mode: None,
        priority_fee: None,
        restrict_intermediate_tokens: None,
        fee_discount_pct: 0,
    };

    let result = build_swap_transaction(http, jupiter_api_key, user_pubkey, &swap_params).await?;

    let amount: f64 = params.amount.parse().unwrap_or(0.0);
    let out_amount: f64 = result.quote.out_amount.parse::<u64>().unwrap_or(0) as f64 / 1e9; // SOL has 9 decimals

    let preview = ActionPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "jupsol_unstake".to_string(),
        description: format!("Unstake {:.4} JupSOL → {:.6} SOL", amount, out_amount,),
        estimated_fee: result.preview.estimated_fee,
        estimated_refund: None,
        params: serde_json::to_value(params).unwrap_or_default(),
        warnings: result.preview.warnings,
        requires_approval: true,
    };

    Ok(BuildResponse {
        preview,
        transaction: Some(result.transaction_base64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

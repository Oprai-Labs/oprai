use rust_decimal::prelude::*;
use rust_decimal::Decimal;

use crate::error::AppError;

/// Converts a human-readable amount string (e.g. "1.5") to base units (e.g. 1_500_000 for USDC).
/// Uses rust_decimal to avoid f64 overflow and precision loss.
pub fn parse_amount_to_base_units(amount_str: &str, decimals: u8) -> Result<u64, AppError> {
    let amount = Decimal::from_str(amount_str)
        .map_err(|_| AppError::InvalidParams(format!("Invalid amount format: {amount_str}")))?;

    if amount.is_sign_negative() || amount.is_zero() {
        return Err(AppError::InvalidParams(
            "Amount must be positive".to_string(),
        ));
    }

    let multiplier = Decimal::from(10u64.pow(u32::from(decimals)));
    let base_units = amount * multiplier;

    base_units
        .to_u64()
        .ok_or_else(|| AppError::InvalidParams(format!("Amount {amount_str} exceeds maximum representable value")))
}

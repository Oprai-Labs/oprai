use serde::{Deserialize, Serialize};

use crate::error::AppError;

/// HTTP client for auth-service's `/internal/spending/*` endpoints.
///
/// These endpoints implement the **server-side** spending cap. The frontend
/// has its own check (`SpendingLimitService.check`) but that is informational
/// only — a malicious or buggy client can call the gateway directly and bypass
/// the JS guard. The hard-stop lives here.
#[derive(Clone)]
pub struct SpendingClient {
    http: reqwest::Client,
    base_url: String,
    internal_api_key: String,
}

#[derive(Serialize)]
struct CheckRequest<'a> {
    wallet: &'a str,
    #[serde(rename = "amountUsd")]
    amount_usd: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SpendingCheckResponse {
    pub allowed: bool,
    #[serde(default)]
    pub reason: String,
    #[serde(default, rename = "limitUsd")]
    pub limit_usd: f64,
    #[serde(default, rename = "currentDailyUsd")]
    pub current_daily_usd: f64,
}

#[derive(Serialize)]
struct CommitRequest<'a> {
    wallet: &'a str,
    #[serde(rename = "amountUsd")]
    amount_usd: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SpendingCommitResponse {
    #[serde(rename = "newDailyTotal")]
    pub new_daily_total: f64,
}

impl SpendingClient {
    pub fn new(http: reqwest::Client, base_url: String, internal_api_key: String) -> Self {
        Self {
            http,
            base_url,
            internal_api_key,
        }
    }

    /// Check the cap. Returns Ok with `allowed=false` when the user has
    /// exceeded a limit; the caller turns that into an `InvalidParams` /
    /// `Forbidden` response.
    pub async fn check(
        &self,
        wallet: &str,
        amount_usd: f64,
    ) -> Result<SpendingCheckResponse, AppError> {
        if amount_usd <= 0.0 {
            // Free actions (data-only queries, zero-value transfers) skip the
            // call so we don't add a hot-path round-trip for them.
            return Ok(SpendingCheckResponse {
                allowed: true,
                reason: String::new(),
                limit_usd: 0.0,
                current_daily_usd: 0.0,
            });
        }
        let url = format!("{}/internal/spending/check", self.base_url);
        let resp = self
            .http
            .post(&url)
            .header("X-Internal-Api-Key", &self.internal_api_key)
            .json(&CheckRequest { wallet, amount_usd })
            .timeout(std::time::Duration::from_secs(2))
            .send()
            .await
            .map_err(|e| AppError::Internal(format!("spending check transport: {e}")))?;

        if !resp.status().is_success() {
            return Err(AppError::Internal(format!(
                "spending check returned {}",
                resp.status()
            )));
        }
        resp.json::<SpendingCheckResponse>()
            .await
            .map_err(|e| AppError::Internal(format!("spending check decode: {e}")))
    }

    /// Atomically add `amount_usd` to today's counter. Called from
    /// `/actions/submit` after a transaction has been broadcast.
    pub async fn commit(
        &self,
        wallet: &str,
        amount_usd: f64,
    ) -> Result<SpendingCommitResponse, AppError> {
        let url = format!("{}/internal/spending/commit", self.base_url);
        let resp = self
            .http
            .post(&url)
            .header("X-Internal-Api-Key", &self.internal_api_key)
            .json(&CommitRequest { wallet, amount_usd })
            .timeout(std::time::Duration::from_secs(2))
            .send()
            .await
            .map_err(|e| AppError::Internal(format!("spending commit transport: {e}")))?;

        if !resp.status().is_success() {
            return Err(AppError::Internal(format!(
                "spending commit returned {}",
                resp.status()
            )));
        }
        resp.json::<SpendingCommitResponse>()
            .await
            .map_err(|e| AppError::Internal(format!("spending commit decode: {e}")))
    }
}

/// Convenience: fail-closed check. Use this from action handlers — it returns
/// a 403/InvalidParams with the user-facing reason when the cap is reached.
pub async fn enforce_spending_cap(
    client: &SpendingClient,
    wallet: &str,
    amount_usd: f64,
) -> Result<(), AppError> {
    let resp = client.check(wallet, amount_usd).await?;
    if resp.allowed {
        return Ok(());
    }
    let msg = match resp.reason.as_str() {
        "per_tx" => format!(
            "Transaction worth ${:.2} exceeds your per-transaction limit of ${:.2}.",
            amount_usd, resp.limit_usd
        ),
        "daily" => format!(
            "Daily spending limit of ${:.2} reached (today: ${:.2}, requested: ${:.2}).",
            resp.limit_usd, resp.current_daily_usd, amount_usd
        ),
        other => format!("Spending limit hit ({}).", other),
    };
    Err(AppError::InvalidParams(msg))
}

/// Best-effort USD value of a Jupiter swap quote.
///
/// Strategy:
///   1. If either side is a known stablecoin (USDC/USDT), compute directly
///      from its atomic amount and decimals — this is exact.
///   2. Otherwise, fetch a price for the *output* side from Jupiter's Price API
///      (the side we're "buying" tells us what the user really wants). On any
///      failure, return 0 so the cap is skipped — fail-open is acceptable here
///      because the cap is a backstop, not the only line of defence.
pub async fn estimate_swap_usd(
    http: &reqwest::Client,
    input_mint: &str,
    output_mint: &str,
    in_amount_atomic: &str,
    out_amount_atomic: &str,
) -> f64 {
    use crate::solana::tokens::{get_token_info, COMMON_TOKENS};

    fn parse_atomic(amount_str: &str, decimals: u8) -> Option<f64> {
        let n: f64 = amount_str.parse().ok()?;
        Some(n / 10f64.powi(decimals as i32))
    }

    fn is_stable(symbol: &str) -> bool {
        matches!(symbol, "USDC" | "USDT" | "DAI" | "USDS" | "PYUSD")
    }

    // Stablecoin shortcut — exact pricing, no network call.
    if let Some(info) = get_token_info(output_mint) {
        if is_stable(&info.symbol) {
            return parse_atomic(out_amount_atomic, info.decimals).unwrap_or(0.0);
        }
    }
    if let Some(info) = get_token_info(input_mint) {
        if is_stable(&info.symbol) {
            return parse_atomic(in_amount_atomic, info.decimals).unwrap_or(0.0);
        }
    }

    // Otherwise, look up the output side. Jupiter's Price API (`lite-api.jup.ag`)
    // is free and unauthenticated, perfect for a backstop estimate.
    let url = format!("https://lite-api.jup.ag/price/v3?ids={}", output_mint);
    let resp = match http
        .get(&url)
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .await
    {
        Ok(r) => r,
        Err(_) => return 0.0,
    };
    if !resp.status().is_success() {
        return 0.0;
    }
    let payload: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(_) => return 0.0,
    };
    let price = payload
        .get(output_mint)
        .and_then(|v| v.get("usdPrice").and_then(|p| p.as_f64()).or_else(|| {
            v.get("price").and_then(|p| match p {
                serde_json::Value::Number(n) => n.as_f64(),
                serde_json::Value::String(s) => s.parse::<f64>().ok(),
                _ => None,
            })
        }))
        .unwrap_or(0.0);
    if price <= 0.0 {
        return 0.0;
    }
    // Need decimals for output mint. Prefer the registry; fall back to a
    // sensible default of 9 (Solana's most common decimal width).
    let decimals = COMMON_TOKENS
        .values()
        .find(|t| t.address == output_mint)
        .map(|t| t.decimals)
        .unwrap_or(9);
    let out_human = parse_atomic(out_amount_atomic, decimals).unwrap_or(0.0);
    out_human * price
}

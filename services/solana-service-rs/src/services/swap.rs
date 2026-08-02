use std::str::FromStr;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::amount::parse_amount_to_base_units;
use crate::services::fees;
use crate::solana::tokens::{get_token_info, resolve_token_address, COMMON_TOKENS};

/// Set of registry symbols we treat as "stable" for slippage purposes — pegs
/// that should never need >100 bps of room except in extreme depegs. The
/// actual ceiling is enforced by `slippage_ceiling_bps()` below.
const STABLE_SYMBOLS: &[&str] = &["USDC", "USDT", "DAI", "USDS", "PYUSD"];

/// Compute the per-pair slippage ceiling based on what we know about the
/// tokens involved. The user (or LLM) can request anything they want up to
/// MAX_SLIPPAGE_BPS, but we override that with this tighter bound when the
/// pair tells us the request must be smaller. Applied in `validate_swap_params`.
///
/// Returns slippage in basis points.
fn slippage_ceiling_bps(input_mint: &str, output_mint: &str) -> u32 {
    fn registered(mint: &str) -> Option<&'static str> {
        COMMON_TOKENS
            .values()
            .find(|t| t.address == mint || t.symbol.eq_ignore_ascii_case(mint))
            .map(|t| t.symbol.as_str())
    }
    let in_sym = registered(input_mint);
    let out_sym = registered(output_mint);

    let is_stable = |s: Option<&str>| match s {
        Some(sym) => STABLE_SYMBOLS.iter().any(|&t| t.eq_ignore_ascii_case(sym)),
        None => false,
    };

    // Stable ↔ stable — 50 bps is generous; depegs aside, the AMM cost
    // is essentially zero on these routes.
    if is_stable(in_sym) && is_stable(out_sym) {
        return 50;
    }
    // SOL ↔ stable, or stable ↔ blue-chip — 200 bps. Covers normal volatility.
    if is_stable(in_sym) || is_stable(out_sym) {
        return 200;
    }
    // Both sides registered (e.g. SOL → JUP) — 500 bps. Liquid pairs.
    if in_sym.is_some() && out_sym.is_some() {
        return 500;
    }
    // At least one side is unverified — most likely a memecoin or a freshly
    // launched token. Allow up to 1500 bps; the registry-rejection on Unknown
    // mints handles the truly unsafe case.
    1500
}

// ──────────────────────────────────────────────────────────────────────────────
// Jupiter API constants
// ──────────────────────────────────────────────────────────────────────────────

/// Maximum allowed slippage (30%) — shared with routes/actions.rs
pub const MAX_SLIPPAGE_BPS: u32 = 3000;

/// Paid API (requires x-api-key header) — higher rate limits, priority routing.
const JUPITER_PAID_QUOTE: &str = "https://api.jup.ag/swap/v1/quote";
const JUPITER_PAID_SWAP: &str = "https://api.jup.ag/swap/v1/swap";
/// Public API — no authentication required, lower rate limits.
///
/// NOTE: Jupiter retired the legacy `quote-api.jup.ag/v6/*` host (it now
/// NXDOMAINs) and consolidated everything under `lite-api.jup.ag/swap/v1/*`.
/// The path schema matches the paid API exactly, so callers don't need to
/// branch on auth-mode for anything other than the host + header.
const JUPITER_PUB_QUOTE: &str = "https://lite-api.jup.ag/swap/v1/quote";
const JUPITER_PUB_SWAP: &str = "https://lite-api.jup.ag/swap/v1/swap";

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/// Swap request parameters (mirrors @oprai/types SwapParams).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SwapParams {
    pub input_mint: String,
    pub output_mint: String,
    pub amount: String,
    #[serde(default)]
    pub slippage_bps: Option<u32>,
    #[serde(default)]
    pub only_direct_routes: Option<bool>,
    #[serde(default)]
    pub dexes: Option<String>,
    /// "ExactIn" (default), "ExactOut", or shorthand "in"/"out" — normalized automatically.
    #[serde(default)]
    pub swap_mode: Option<String>,
    /// Priority fee level: "auto" (default), "low", "medium", "high", or exact lamports as string.
    #[serde(default)]
    pub priority_fee: Option<String>,
    /// Restrict intermediate tokens to a safe list (reduces routing complexity, lowers MEV risk).
    #[serde(default)]
    pub restrict_intermediate_tokens: Option<bool>,
}

/// Swap quote returned by Jupiter (partial -- we preserve the full JSON too).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SwapQuote {
    pub input_mint: String,
    pub output_mint: String,
    pub in_amount: String,
    pub out_amount: String,
    pub other_amount_threshold: String,
    pub swap_mode: String,
    pub slippage_bps: u32,
    pub price_impact_pct: String,
    #[serde(default)]
    pub route_plan: Vec<serde_json::Value>,
    #[serde(default)]
    pub platform_fee: Option<serde_json::Value>,
}

/// Quote request body used for the REST endpoint.
/// Fields are snake_case to match what the Angular frontend sends.
/// `amount` accepts both number and string (frontend sends string).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QuoteRequest {
    pub input_mint: String,
    pub output_mint: String,
    #[serde(deserialize_with = "deserialize_amount")]
    pub amount: String,
    #[serde(default, deserialize_with = "deserialize_optional_u32")]
    pub slippage_bps: Option<u32>,
    #[serde(default)]
    pub only_direct_routes: Option<bool>,
    #[serde(default)]
    pub swap_mode: Option<String>,
    #[serde(default)]
    pub restrict_intermediate_tokens: Option<bool>,
    /// Restrict routing to specific venues, e.g. "Whirlpool". A venue-scoped
    /// action (orca_swap) executes through exactly one DEX, so its preview
    /// must be quoted the same way or the number shown is from a route the
    /// transaction will not take.
    #[serde(default)]
    pub dexes: Option<String>,
}

fn deserialize_optional_u32<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> Result<Option<u32>, D::Error> {
    use serde::de::Error;
    let v: Option<serde_json::Value> = serde::Deserialize::deserialize(d)?;
    match v {
        None => Ok(None),
        Some(serde_json::Value::Null) => Ok(None),
        Some(serde_json::Value::Number(n)) => n
            .as_u64()
            .map(|n| Some(n as u32))
            .ok_or_else(|| D::Error::custom("expected unsigned integer")),
        Some(serde_json::Value::String(s)) => s
            .parse::<u32>()
            .map(Some)
            .map_err(|_| D::Error::custom(format!("cannot parse '{s}' as u32"))),
        Some(other) => Err(D::Error::custom(format!(
            "expected number or string for slippage_bps, got {other}"
        ))),
    }
}

fn deserialize_amount<'de, D: serde::Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    use serde::de::Error;
    let v: serde_json::Value = serde::Deserialize::deserialize(d)?;
    match v {
        serde_json::Value::String(s) => Ok(s),
        serde_json::Value::Number(n) => Ok(n.to_string()),
        other => Err(D::Error::custom(format!(
            "expected string or number for amount, got {other}"
        ))),
    }
}

/// Preview shown to the user before signing.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SwapPreview {
    pub id: String,
    #[serde(rename = "type")]
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    pub params: SwapParams,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

/// Result of building a swap transaction.
pub struct SwapBuildResult {
    /// Base64-encoded versioned transaction from Jupiter.
    pub transaction_base64: String,
    pub preview: SwapPreview,
    pub quote: SwapQuote,
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_swap_params(params: &SwapParams) -> Result<(), AppError> {
    let input_addr = resolve_token_address(&params.input_mint);
    input_addr
        .parse::<solana_sdk::pubkey::Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid input token".into()))?;

    let output_addr = resolve_token_address(&params.output_mint);
    output_addr
        .parse::<solana_sdk::pubkey::Pubkey>()
        .map_err(|_| AppError::InvalidParams("Invalid output token".into()))?;

    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams(
            "Amount must be a positive number".into(),
        ));
    }

    if input_addr == output_addr {
        return Err(AppError::InvalidParams("Cannot swap same token".into()));
    }

    if let Some(slippage) = params.slippage_bps {
        if slippage > MAX_SLIPPAGE_BPS {
            return Err(AppError::InvalidParams(format!(
                "Slippage {} bps exceeds maximum allowed value of {} (30%)",
                slippage, MAX_SLIPPAGE_BPS
            )));
        }
        // Per-pair ceiling — stricter than MAX_SLIPPAGE_BPS for liquid /
        // stable pairs. A stable→stable swap with 1000 bps slippage is
        // almost certainly a routing accident or sandwich-attack setup.
        let ceiling = slippage_ceiling_bps(&input_addr, &output_addr);
        if slippage > ceiling {
            return Err(AppError::InvalidParams(format!(
                "Slippage {} bps is too high for this pair — ceiling is {} bps. \
                 Lower the slippage or pick a different route.",
                slippage, ceiling
            )));
        }
    }

    Ok(())
}

// ──────────────────────────────────────────────────────────────────────────────
// Jupiter quote
// ──────────────────────────────────────────────────────────────────────────────

/// Fetch a swap quote from Jupiter API.
///
/// Uses the paid endpoint (`api.jup.ag`) when an API key is provided,
/// falling back to the official public endpoint (`lite-api.jup.ag/swap/v1`).
pub async fn get_swap_quote(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    params: &SwapParams,
) -> Result<SwapQuote, AppError> {
    let input_mint = resolve_token_address(&params.input_mint);
    let output_mint = resolve_token_address(&params.output_mint);
    let slippage_bps = params.slippage_bps.unwrap_or(50);

    // Normalize swapMode FIRST so we know which token's decimals the `amount`
    // is denominated in. Jupiter's `amount` parameter is the EXACT side:
    //   ExactIn  → amount = input quantity   (input decimals)
    //   ExactOut → amount = output quantity  (output decimals)
    // Treating it as input always (the old bug) silently mis-scales every
    // ExactOut quote — "buy 5 USDC" with 9-decimal SOL would request
    // 5_000_000_000 USDC out (5e9 atomic = 5_000 USDC). The Jupiter response
    // looked plausible because Jupiter just routes whatever atomic amount it
    // gets, so the user ended up paying ~1000× the SOL they expected.
    let swap_mode_raw = params.swap_mode.as_deref().unwrap_or("ExactIn");
    let swap_mode = match swap_mode_raw.to_lowercase().as_str() {
        "in" | "exactin" => "ExactIn",
        "out" | "exactout" => "ExactOut",
        other => {
            tracing::warn!("Unknown swapMode '{}', defaulting to ExactIn", other);
            "ExactIn"
        }
    };

    let amount_decimals = if swap_mode == "ExactOut" {
        get_token_info(&params.output_mint)
            .map(|t| t.decimals)
            .unwrap_or(9)
    } else {
        get_token_info(&params.input_mint)
            .map(|t| t.decimals)
            .unwrap_or(9)
    };

    let amount_in_smallest = parse_amount_to_base_units(&params.amount, amount_decimals)?;
    let only_direct = params.only_direct_routes.unwrap_or(false);
    let dexes_qs = params
        .dexes
        .as_deref()
        .filter(|d| !d.is_empty())
        .map(|d| format!("&dexes={d}"))
        .unwrap_or_default();

    let restrict_intermediate = params.restrict_intermediate_tokens.unwrap_or(false);

    // OPRAI's commission. It has to be declared on the QUOTE — the swap
    // endpoint only honours a fee that the quote already priced in — and it
    // is zero whenever no fee wallet is configured or the pair is one we
    // charge nothing for.
    let platform_fee_qs = match platform_fee_bps_for(params, swap_mode) {
        0 => String::new(),
        bps => format!("&platformFeeBps={bps}"),
    };

    let base_url = if jupiter_api_key.is_some() {
        JUPITER_PAID_QUOTE
    } else {
        JUPITER_PUB_QUOTE
    };
    let url = format!(
        "{base_url}?\
         inputMint={input_mint}&\
         outputMint={output_mint}&\
         amount={amount_in_smallest}&\
         slippageBps={slippage_bps}&\
         swapMode={swap_mode}&\
         onlyDirectRoutes={only_direct}&\
         restrictIntermediateTokens={restrict_intermediate}{dexes_qs}{platform_fee_qs}",
    );

    let mut req = http.get(&url);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    let response = req.send().await?;
    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        // Log the raw upstream body for debugging, but return a clean message —
        // never surface Jupiter's raw response to the user.
        tracing::warn!("Jupiter quote failed ({status}): {body}");
        return Err(AppError::JupiterApiError(
            "No route available for this trade right now. The pair may lack liquidity or the amount may be too small.".into(),
        ));
    }

    let quote: SwapQuote = response.json().await.map_err(|e| {
        tracing::warn!("Failed to parse Jupiter quote: {e}");
        AppError::JupiterApiError(
            "Couldn't read the swap quote. Please try again in a moment.".into(),
        )
    })?;
    Ok(quote)
}


// ──────────────────────────────────────────────────────────────────────────────
// OPRAI commission
// ──────────────────────────────────────────────────────────────────────────────

/// The fee to declare on a quote, in basis points.
///
/// Zero unless there is a fee wallet, the pair is one we charge for, AND one
/// side is a mint we can actually be paid in — quoting a fee we then cannot
/// collect would only mis-price the route.
fn platform_fee_bps_for(params: &SwapParams, swap_mode: &str) -> u16 {
    let input = resolve_token_address(&params.input_mint);
    let output = resolve_token_address(&params.output_mint);
    if fees::swap_fee_mint(&input, &output, swap_mode == "ExactOut").is_none() {
        return 0;
    }
    fees::swap_fee_bps(&input, &output)
}

/// Fee mints we have stopped trying to use, because Jupiter rejected a build
/// that named them and the identical build succeeded without.
///
/// Jupiter requires the fee's token account to exist and will not create it,
/// so a missing ATA turns every swap in that pair into an error. Rather than
/// carry an RPC handle down here to check, the failure is observed once and
/// remembered: the first swap pays a retry, the rest are unaffected, and the
/// log says exactly which account to create.
fn unusable_fee_mints() -> &'static std::sync::Mutex<std::collections::HashSet<String>> {
    static SET: std::sync::OnceLock<std::sync::Mutex<std::collections::HashSet<String>>> =
        std::sync::OnceLock::new();
    SET.get_or_init(|| std::sync::Mutex::new(std::collections::HashSet::new()))
}

fn fee_account_for(params: &SwapParams, swap_mode: &str) -> Option<(String, String)> {
    let input = resolve_token_address(&params.input_mint);
    let output = resolve_token_address(&params.output_mint);
    let mint = fees::swap_fee_mint(&input, &output, swap_mode == "ExactOut")?.to_string();
    if unusable_fee_mints().lock().ok()?.contains(&mint) {
        return None;
    }
    let mint_pk = solana_sdk::pubkey::Pubkey::from_str(&mint).ok()?;
    let account = fees::fee_token_account(&mint_pk)?;
    Some((mint, account.to_string()))
}

// ──────────────────────────────────────────────────────────────────────────────
// Jupiter swap TX
// ──────────────────────────────────────────────────────────────────────────────

/// Build a swap transaction via Jupiter /swap endpoint.
///
/// Uses the paid endpoint (`api.jup.ag`) when an API key is provided,
/// falling back to the official public endpoint (`lite-api.jup.ag/swap/v1`).
pub async fn build_swap_transaction(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    user_pubkey: &str,
    params: &SwapParams,
) -> Result<SwapBuildResult, AppError> {
    validate_swap_params(params)?;

    let quote = get_swap_quote(http, jupiter_api_key, params).await?;

    let prioritization_fee = match params.priority_fee.as_deref() {
        Some("low") => serde_json::json!(1_000),
        Some("medium") => serde_json::json!(10_000),
        Some("high") => serde_json::json!(100_000),
        Some("auto") | None => serde_json::json!("auto"),
        Some(exact) => exact
            .parse::<u64>()
            .map(|n| serde_json::json!(n))
            .unwrap_or(serde_json::json!("auto")),
    };

    let swap_api_url = if jupiter_api_key.is_some() {
        JUPITER_PAID_SWAP
    } else {
        JUPITER_PUB_SWAP
    };

    let swap_mode = params.swap_mode.as_deref().unwrap_or("ExactIn");
    let exact_out = swap_mode.eq_ignore_ascii_case("exactout") || swap_mode.eq_ignore_ascii_case("out");
    let fee_target = fee_account_for(params, if exact_out { "ExactOut" } else { "ExactIn" });

    let post_swap = |fee: Option<String>| {
        let mut body = serde_json::json!({
            "quoteResponse": quote,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": true,
            "dynamicComputeUnitLimit": true,
            "prioritizationFeeLamports": prioritization_fee,
        });
        if let Some(account) = fee {
            body["feeAccount"] = serde_json::Value::String(account);
        }
        let mut req = http.post(swap_api_url).json(&body);
        if let Some(key) = jupiter_api_key {
            req = req.header("x-api-key", key);
        }
        req.send()
    };

    let mut swap_response = post_swap(fee_target.as_ref().map(|(_, acct)| acct.clone())).await?;

    // A build that names a fee account can fail for one reason we can fix
    // ourselves: the account does not exist yet. Retry once without it — a
    // user's swap must never fail because our commission plumbing is not set
    // up — and remember, so the next swap does not pay for the same lesson.
    if !swap_response.status().is_success() {
        if let Some((mint, account)) = fee_target.as_ref() {
            let status = swap_response.status();
            let retry = post_swap(None).await?;
            if retry.status().is_success() {
                tracing::error!(
                    mint = %mint,
                    fee_account = %account,
                    rejected_status = %status,
                    "Jupiter rejected the fee account — it almost certainly does not exist. \
                     Create this associated token account to start collecting fees on this mint. \
                     Swapping continues without a fee until then."
                );
                if let Ok(mut set) = unusable_fee_mints().lock() {
                    set.insert(mint.clone());
                }
                swap_response = retry;
            }
        }
    }

    if !swap_response.status().is_success() {
        let status = swap_response.status();
        let body = swap_response.text().await.unwrap_or_default();
        tracing::warn!("Jupiter swap build failed ({status}): {body}");
        return Err(AppError::JupiterApiError(
            "Couldn't build this transaction right now. Please try again in a moment.".into(),
        ));
    }

    let swap_data: serde_json::Value = swap_response.json().await?;
    let tx_base64 = swap_data["swapTransaction"]
        .as_str()
        .ok_or_else(|| AppError::JupiterApiError("Missing swapTransaction in response".into()))?
        .to_string();

    // Build preview.
    let input_token = get_token_info(&params.input_mint);
    let output_token = get_token_info(&params.output_mint);
    let input_symbol = input_token
        .map(|t| t.symbol.to_string())
        .unwrap_or_else(|| params.input_mint[..4.min(params.input_mint.len())].to_string());
    let output_symbol = output_token
        .map(|t| t.symbol.to_string())
        .unwrap_or_else(|| params.output_mint[..4.min(params.output_mint.len())].to_string());
    let output_decimals = output_token.map(|t| t.decimals).unwrap_or(9);

    let out_amount_float: f64 =
        quote.out_amount.parse::<u64>().unwrap_or(0) as f64 / 10_f64.powi(output_decimals as i32);

    let mut warnings = Vec::new();
    let price_impact: f64 = match quote.price_impact_pct.parse() {
        Ok(v) => v,
        Err(e) => {
            tracing::warn!(
                "failed to parse price_impact_pct='{}': {}",
                quote.price_impact_pct,
                e
            );
            0.0
        }
    };
    if price_impact > 1.0 {
        warnings.push(format!("High price impact: {price_impact:.2}%"));
    }
    if price_impact > 5.0 {
        warnings.push("Very high price impact! Consider smaller trade.".into());
    }

    let preview = SwapPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "swap".to_string(),
        description: format!(
            "Swap {} {} -> {:.6} {}",
            params.amount, input_symbol, out_amount_float, output_symbol
        ),
        estimated_fee: "~0.001 SOL".to_string(),
        params: params.clone(),
        warnings,
        requires_approval: true,
    };

    Ok(SwapBuildResult {
        transaction_base64: tx_base64,
        preview,
        quote,
    })
}

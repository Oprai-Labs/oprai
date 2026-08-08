use std::str::FromStr;

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use solana_sdk::pubkey::Pubkey;

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
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub slippage_bps: Option<u32>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
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
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
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
    /// What OPRAI will actually take on this trade, in basis points.
    ///
    /// Not from Jupiter — we attach it on the way out. The card used to read
    /// `platformFee.feeBps`, which is only present when the fee goes through
    /// Jupiter's own mechanism; on the SOL-transfer route that field is empty
    /// and the card cheerfully announced "Free" while charging 0.5%. One field
    /// that always means the same thing.
    #[serde(default, skip_deserializing)]
    pub oprai_fee_bps: Option<u16>,
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
    get_swap_quote_with_fee(http, jupiter_api_key, params, None).await
}

/// The quote, with an explicit say over the platform fee.
///
/// The fee has to be declared here and honoured on the build, and the two must
/// agree: a quote that prices in a platform fee, sent to a build that names no
/// fee account, produces a route Jupiter accepts and the chain rejects (error
/// 6025). So the retry path re-quotes with the fee forced off rather than
/// reusing the priced-in one.
async fn get_swap_quote_with_fee(
    http: &reqwest::Client,
    jupiter_api_key: Option<&str>,
    params: &SwapParams,
    fee_bps_override: Option<u16>,
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
    let declared_fee_bps = match fee_bps_override {
        Some(v) => v,
        None => platform_fee_bps_for(params, swap_mode).await,
    };
    let platform_fee_qs = match declared_fee_bps {
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

    // One retry on a rate limit. Jupiter's 429s are bursty — several cards
    // polling at once is enough to trip one — and they clear in well under a
    // second, so a single short wait turns a visible failure into a pause
    // nobody notices.
    let mut response = send_quote(http, &url, jupiter_api_key).await?;
    if response.status().as_u16() == 429 {
        tokio::time::sleep(std::time::Duration::from_millis(700)).await;
        response = send_quote(http, &url, jupiter_api_key).await?;
    }

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().await.unwrap_or_default();
        // Log the raw upstream body for debugging, but return a clean message —
        // never surface Jupiter's raw response to the user.
        tracing::warn!("Jupiter quote failed ({status}): {body}");
        // A rate limit is our problem, not the user's token. Telling someone
        // their pair lacks liquidity when we were simply throttled sends them
        // off to change a perfectly good trade.
        if status.as_u16() == 429 {
            return Err(AppError::JupiterApiError(
                "Pricing is busy right now. Give it a moment and try again — nothing is wrong with this trade.".into(),
            ));
        }
        return Err(AppError::JupiterApiError(
            "No route available for this trade right now. The pair may lack liquidity or the amount may be too small.".into(),
        ));
    }

    let mut quote: SwapQuote = response.json().await.map_err(|e| {
        tracing::warn!("Failed to parse Jupiter quote: {e}");
        AppError::JupiterApiError(
            "Couldn't read the swap quote. Please try again in a moment.".into(),
        )
    })?;
    // Whichever route the fee takes, say the same number.
    quote.oprai_fee_bps = Some(if declared_fee_bps > 0 {
        declared_fee_bps
    } else if sol_side_fee_lamports(params, &quote).is_some() {
        fees::swap_fee_bps(&input_mint, &output_mint)
    } else {
        0
    });
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
async fn platform_fee_bps_for(params: &SwapParams, swap_mode: &str) -> u16 {
    let input = resolve_token_address(&params.input_mint);
    let output = resolve_token_address(&params.output_mint);
    // Only price in a fee we can actually collect. Jupiter will happily quote
    // and build one against an account that does not exist, and the failure
    // then lands on chain after the user has signed.
    if fees::ready_swap_fee_mint(&input, &output, swap_mode == "ExactOut")
        .await
        .is_none()
    {
        return 0;
    }
    fees::swap_fee_bps(&input, &output)
}

async fn fee_account_for(params: &SwapParams, swap_mode: &str) -> Option<(String, String)> {
    let input = resolve_token_address(&params.input_mint);
    let output = resolve_token_address(&params.output_mint);
    let (mint, account) =
        fees::ready_swap_fee_mint(&input, &output, swap_mode == "ExactOut").await?;
    Some((mint.to_string(), account.to_string()))
}

/// The commission on a swap where SOL is one of the two sides, in lamports.
///
/// Taken from the SOL leg because SOL is the one thing every wallet holds and
/// the one balance that needs no account to receive. On a sale into SOL the
/// basis is the slippage-protected minimum rather than the expected proceeds —
/// the same rule the pump.fun sell uses, and for the same reason: it is the
/// only figure at build time the transaction cannot fall below.
fn sol_side_fee_lamports(params: &SwapParams, quote: &SwapQuote) -> Option<u64> {
    let input = resolve_token_address(&params.input_mint);
    let output = resolve_token_address(&params.output_mint);
    let bps = fees::swap_fee_bps(&input, &output) as u64;
    if bps == 0 {
        return None;
    }
    let lamports = if input.eq_ignore_ascii_case(fees::WSOL_MINT) {
        quote.in_amount.parse::<u64>().ok()?
    } else if output.eq_ignore_ascii_case(fees::WSOL_MINT) {
        // What the swap guarantees to deliver, not what it hopes to.
        quote.other_amount_threshold.parse::<u64>().ok()?
    } else {
        return None;
    };
    let fee = lamports.saturating_mul(bps) / 10_000;
    (fee > 0).then_some(fee)
}

/// One quote request, so the retry above does not duplicate the header setup.
async fn send_quote(
    http: &reqwest::Client,
    url: &str,
    jupiter_api_key: Option<&str>,
) -> Result<reqwest::Response, AppError> {
    let mut req = http.get(url);
    if let Some(key) = jupiter_api_key {
        req = req.header("x-api-key", key);
    }
    Ok(req.send().await?)
}

/// Split a SOL-funded swap into "what gets traded" and "what we take".
///
/// Returns the params to quote with — the requested amount less the fee — and
/// the fee itself, so the user's total outflow is the number they typed.
/// Only applies when SOL is the INPUT: on a sale into SOL the fee comes out of
/// the proceeds, which is already money the user is receiving rather than
/// spending.
fn sol_input_fee_split(params: &SwapParams, taking_sol_fee: bool) -> Option<(SwapParams, u64)> {
    if !taking_sol_fee {
        return None;
    }
    let input = resolve_token_address(&params.input_mint);
    if !input.eq_ignore_ascii_case(fees::WSOL_MINT) {
        return None;
    }
    // ExactOut fixes the amount RECEIVED, so there is no input figure to take
    // the fee out of without changing what the user gets.
    if matches!(params.swap_mode.as_deref(), Some(m) if m.eq_ignore_ascii_case("exactout") || m.eq_ignore_ascii_case("out")) {
        return None;
    }
    let output = resolve_token_address(&params.output_mint);
    let bps = fees::swap_fee_bps(&input, &output) as u64;
    if bps == 0 {
        return None;
    }
    let requested = (params.amount.trim().parse::<f64>().ok()? * 1_000_000_000.0) as u64;
    let fee = requested.saturating_mul(bps) / 10_000;
    if fee == 0 || fee >= requested {
        return None;
    }
    let mut net = params.clone();
    net.amount = format!("{:.9}", (requested - fee) as f64 / 1_000_000_000.0);
    Some((net, fee))
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

    // Which route the fee takes has to be settled BEFORE quoting: the SOL
    // route changes the amount we quote for.
    let swap_mode = params.swap_mode.as_deref().unwrap_or("ExactIn");
    let exact_out = swap_mode.eq_ignore_ascii_case("exactout") || swap_mode.eq_ignore_ascii_case("out");
    let mode = if exact_out { "ExactOut" } else { "ExactIn" };
    // Only name a fee account when there is a fee to put in it. Jupiter
    // rejects the whole build otherwise — "platformFee must be greater than 0
    // when feeAccount is set" — which broke every stablecoin pair, the one
    // case we deliberately charge nothing for.
    let fee_target = if platform_fee_bps_for(params, mode).await > 0 {
        fee_account_for(params, mode).await
    } else {
        None
    };

    // When the commission is a SOL transfer, it has to come OUT of the amount,
    // not sit on top of it.
    //
    // A user typing "0.1 SOL" paid 0.1024: the swap took the full 0.1 and our
    // transfer was appended after it. That breaks the promise the card makes,
    // it is the opposite of what the pump.fun path does, and it is the kind of
    // difference someone only discovers by reading their own transaction. So
    // the trade is quoted for the amount minus the fee, and the two together
    // add up to what was asked for.
    let (params_for_quote, prepaid_sol_fee) = match sol_input_fee_split(params, fee_target.is_none()) {
        Some((net_params, fee)) => (net_params, Some(fee)),
        None => (params.clone(), None),
    };
    let params = &params_for_quote;
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


    let post_swap = |quote: &SwapQuote, fee: Option<String>| {
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

    let swap_response = post_swap(&quote, fee_target.as_ref().map(|(_, acct)| acct.clone())).await?;

    if !swap_response.status().is_success() {
        let status = swap_response.status();
        let body = swap_response.text().await.unwrap_or_default();
        tracing::warn!("Jupiter swap build failed ({status}): {body}");
        return Err(AppError::JupiterApiError(
            "Couldn't build this transaction right now. Please try again in a moment.".into(),
        ));
    }

    let swap_data: serde_json::Value = swap_response.json().await?;
    let mut tx_base64 = swap_data["swapTransaction"]
        .as_str()
        .ok_or_else(|| AppError::JupiterApiError("Missing swapTransaction in response".into()))?
        .to_string();

    // When Jupiter's own fee mechanism is unavailable, take the commission the
    // way every trading bot does: a plain SOL transfer riding along in the same
    // transaction. It needs no token account to exist first, which is the whole
    // problem with the platform-fee route — that one left every SOL-side swap
    // uncharged until somebody happened to make an unrelated pump.fun trade,
    // and a commission that depends on an unrelated action is not a commission.
    if fee_target.is_none() {
        if let (Some(wallet), Ok(payer)) = (fees::fee_wallet(), Pubkey::from_str(user_pubkey)) {
            if let Some(fee) = prepaid_sol_fee.or_else(|| sol_side_fee_lamports(params, &quote)) {
                let before = tx_base64.len();
                tx_base64 =
                    crate::services::memo::attach_sol_transfer(&tx_base64, &payer, &wallet, fee);
                if tx_base64.len() == before {
                    tracing::warn!(fee, "could not attach the SOL fee — swap goes uncharged");
                }
            }
        }
    }

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

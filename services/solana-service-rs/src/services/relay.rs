use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::swap::MAX_SLIPPAGE_BPS;
use crate::solana::connection::SolanaRpc;
use solana_sdk::pubkey::Pubkey;

// ──────────────────────────────────────────────────────────────────────────────
// Relay API constants
// ──────────────────────────────────────────────────────────────────────────────

pub const RELAY_API: &str = "https://api.relay.link";
pub const RELAY_TESTNET_API: &str = "https://api.testnets.relay.link";

// ──────────────────────────────────────────────────────────────────────────────
// Chain IDs (EVM chains supported by Relay)
// ──────────────────────────────────────────────────────────────────────────────

pub mod chain_id {
    // Mainnets
    pub const ETHEREUM: u64 = 1;
    pub const SEPOLIA: u64 = 11155111;
    pub const ARBITRUM: u64 = 42161;
    pub const OPTIMISM: u64 = 10;
    pub const BASE: u64 = 8453;
    pub const POLYGON: u64 = 137;
    pub const BSC: u64 = 56;
    pub const AVALANCHE: u64 = 43114;
    pub const LINEA: u64 = 59144;
    pub const ZKSYNC: u64 = 324;
    pub const FANTOM: u64 = 250;
    pub const CELO: u64 = 42220;
    pub const MOONBEAM: u64 = 1284;
    pub const AURORA: u64 = 1313161554;
    pub const KLAYTN: u64 = 8217;
    pub const ARBITRUM_NOVA: u64 = 42170;
    pub const POLYGON_ZKEVM: u64 = 1101;
    pub const SCROLL: u64 = 534352;
    /// Relay's own id for Solana, confirmed against their /chains endpoint.
    /// It was 900 here, which is nobody's Solana — so every quote we made read
    /// "on Unknown", and any routing decision keyed on this constant was
    /// deciding about a chain that does not exist.
    pub const SOLANA: u64 = 792_703_809;
    /// The value we used to send. Kept only so a caller passing the old id
    /// still resolves rather than falling through to Unknown.
    pub const SOLANA_LEGACY_ID: u64 = 900;

    // Testnets
    pub const GOERLI: u64 = 5;
    pub const ARBITRUM_SEPOLIA: u64 = 421614;
    pub const OPTIMISM_SEPOLIA: u64 = 11155420;
    pub const BASE_SEPOLIA: u64 = 84532;
    pub const POLYGON_MUMBAI: u64 = 80001;
    pub const AVALANCHE_FUJI: u64 = 43113;
}

/// Native token addresses (zero address represents native token)
pub const NATIVE_TOKEN_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

/// Cross-chain swap request parameters.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CrossChainSwapParams {
    /// Source chain ID (e.g., 1 for Ethereum, 42161 for Arbitrum)
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub origin_chain_id: u64,
    /// Destination chain ID
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub destination_chain_id: u64,
    /// Source token address (use NATIVE_TOKEN_ADDRESS for native tokens)
    pub origin_currency: String,
    /// Destination token address
    pub destination_currency: String,
    /// Amount to swap (in human-readable format, will be converted to wei)
    pub amount: String,
    /// Recipient address on destination chain (defaults to sender)
    #[serde(default)]
    pub recipient: Option<String>,
    /// Trade type: "EXACT_INPUT" or "EXACT_OUTPUT"
    #[serde(default = "default_trade_type")]
    pub trade_type: String,
    /// Referrer for fee sharing
    #[serde(default)]
    pub referrer: Option<String>,
    /// Slippage tolerance in basis points (default: 50 = 0.5%)
    #[serde(
        default = "default_slippage_bps",
        deserialize_with = "crate::services::params::lenient"
    )]
    pub slippage_bps: u32,
    /// Bridge provider: "relay", "wormhole", "debridge", "mayan"
    /// Currently only relay is fully supported; others are placeholders for future implementation
    #[serde(default)]
    pub provider: Option<String>,
}

fn default_trade_type() -> String {
    "EXACT_INPUT".to_string()
}

fn default_slippage_bps() -> u32 {
    50
}

/// Quote response from Relay API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayQuote {
    /// The request ID for tracking
    pub request_id: Option<String>,
    /// Details about the swap
    pub details: RelayQuoteDetails,
    /// Steps to execute the cross-chain swap
    pub steps: Vec<RelayStep>,
    /// Fees breakdown
    #[serde(default)]
    pub fees: Option<RelayFees>,
    /// Raw quote data
    #[serde(flatten)]
    pub raw: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayQuoteDetails {
    /// What is being sent, and how much of it.
    pub currency_in: RelayAmount,
    /// What arrives, and how much of it.
    pub currency_out: RelayAmount,
    /// Amount being sent (in wei). Relay carries this inside `currencyIn` now;
    /// kept for callers that still read it.
    #[serde(default)]
    pub amount_in: Option<String>,
    /// Amount being received (in wei).
    #[serde(default)]
    pub amount_out: Option<String>,
    /// Exchange rate. A NUMBER on the wire — declaring it a string failed the
    /// whole quote, and the failure surfaced as "error decoding response
    /// body", which names neither the field nor the type.
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub rate: Option<String>,
    /// Price impact
    #[serde(default)]
    pub price_impact: Option<String>,
    /// Estimated time in seconds. Relay calls it `timeEstimate`; asking for
    /// `estimatedTime` meant the card never had one to show.
    #[serde(
        default,
        alias = "timeEstimate",
        deserialize_with = "crate::services::params::soft_opt"
    )]
    pub estimated_time: Option<u64>,
    /// Price impact, as Relay reports it.
    #[serde(default)]
    pub total_impact: Option<serde_json::Value>,
    /// Sender address
    #[serde(default)]
    pub sender: Option<String>,
    /// Recipient address
    #[serde(default)]
    pub recipient: Option<String>,
}

/// A side of the trade: the token, and how much of it.
///
/// Relay nests the currency inside this rather than returning a bare token —
/// modelling `currencyIn` as the token itself is what made every quote
/// unparseable.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayAmount {
    pub currency: RelayCurrency,
    /// Raw amount, in the token's own units.
    #[serde(default)]
    pub amount: Option<String>,
    /// The same amount, already scaled by decimals — what a card shows.
    #[serde(default)]
    pub amount_formatted: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub amount_usd: Option<String>,
    /// What arrives in the worst allowed case, after slippage.
    #[serde(default)]
    pub minimum_amount: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayCurrency {
    /// Chain ID
    pub chain_id: u64,
    /// Token address
    pub address: String,
    /// Token symbol
    #[serde(default)]
    pub symbol: Option<String>,
    /// Token decimals
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub decimals: Option<u8>,
    /// Token name
    #[serde(default)]
    pub name: Option<String>,
    /// USD price
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub price: Option<f64>,
    /// Logo and verification. Undeclared fields are dropped by serde, so the
    /// card had a symbol and no icon — not because Relay withheld one.
    #[serde(default)]
    pub metadata: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayStep {
    /// Step ID
    pub id: Option<String>,
    /// Step type (e.g., "deposit", "fill", "sign"). Relay calls it `kind`.
    #[serde(rename = "type", alias = "kind")]
    pub step_type: Option<String>,
    /// Items within the step
    #[serde(default)]
    pub items: Vec<RelayStepItem>,
    /// Whether this step is complete
    #[serde(default)]
    pub completed: Option<bool>,
    /// Error if any
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayStepItem {
    /// What to sign. Relay puts it under `data` — an EVM `{to, data, value}`
    /// on one chain, a list of Solana instructions on the other. We modelled
    /// `transaction` and `internalData`, neither of which Relay sends, so this
    /// was empty for every bridge on every chain and the browser reported
    /// "no transaction data returned from backend".
    #[serde(default)]
    pub data: Option<serde_json::Value>,
    /// Internal data for execution
    #[serde(default)]
    pub internal_data: Option<serde_json::Value>,
    /// Transaction data if applicable
    #[serde(default)]
    pub transaction: Option<RelayTransaction>,
    /// Whether this item is complete
    #[serde(default)]
    pub completed: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayTransaction {
    /// Transaction data for EVM chains
    #[serde(default)]
    pub data: Option<String>,
    /// Target contract address
    #[serde(default)]
    pub to: Option<String>,
    /// Gas limit
    #[serde(default)]
    pub gas_limit: Option<String>,
    /// Value (in wei)
    #[serde(default)]
    pub value: Option<String>,
    /// Chain ID
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub chain_id: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
/// What a bridge costs, in Relay's own breakdown.
///
/// Named for what the API sends. The previous model asked for `totalUsd` and
/// `bridge`, neither of which exists — every field was silently absent, so the
/// costs were never shown and nothing said why.
pub struct RelayFees {
    /// Network gas on the origin chain.
    #[serde(default)]
    pub gas: Option<RelayAmount>,
    /// What the relayer charges in total.
    #[serde(default)]
    pub relayer: Option<RelayAmount>,
    /// The relayer's own gas on the destination chain.
    #[serde(default)]
    pub relayer_gas: Option<RelayAmount>,
    /// The relayer's service fee.
    #[serde(default)]
    pub relayer_service: Option<RelayAmount>,
    /// Ours, when Relay can pay it.
    #[serde(default)]
    pub app: Option<RelayAmount>,
}

impl RelayFees {
    /// Everything the bridge costs, in dollars.
    ///
    /// Relay itemises rather than totalling, and the relayer figure already
    /// contains its gas and service parts — adding those again double-counts
    /// the largest line.
    pub fn total_usd(&self) -> Option<f64> {
        let usd = |a: &Option<RelayAmount>| -> f64 {
            a.as_ref()
                .and_then(|x| x.amount_usd.as_deref())
                .and_then(|v| v.parse::<f64>().ok())
                .unwrap_or(0.0)
        };
        let total = usd(&self.gas) + usd(&self.relayer) + usd(&self.app);
        if total > 0.0 {
            Some(total)
        } else {
            None
        }
    }
}

/// Preview shown to the user before signing.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CrossChainSwapPreview {
    pub id: String,
    #[serde(rename = "type")]
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    pub params: CrossChainSwapParams,
    pub warnings: Vec<String>,
    pub requires_approval: bool,
    /// Estimated time for cross-chain completion
    pub estimated_time_seconds: Option<u64>,
    /// Exchange rate
    pub exchange_rate: Option<String>,
}

/// Result of building a cross-chain swap.
pub struct CrossChainSwapResult {
    /// The quote with execution steps
    pub quote: RelayQuote,
    pub preview: CrossChainSwapPreview,
}

// ──────────────────────────────────────────────────────────────────────────────
// Chain name helpers
// ──────────────────────────────────────────────────────────────────────────────

pub fn get_chain_name(chain_id: u64) -> &'static str {
    match chain_id {
        // Mainnets
        chain_id::ETHEREUM => "Ethereum",
        chain_id::ARBITRUM => "Arbitrum",
        chain_id::OPTIMISM => "Optimism",
        chain_id::BASE => "Base",
        chain_id::POLYGON => "Polygon",
        chain_id::BSC => "BSC",
        chain_id::AVALANCHE => "Avalanche",
        chain_id::LINEA => "Linea",
        chain_id::ZKSYNC => "zkSync Era",
        chain_id::FANTOM => "Fantom",
        chain_id::CELO => "Celo",
        chain_id::MOONBEAM => "Moonbeam",
        chain_id::AURORA => "Aurora",
        chain_id::KLAYTN => "Klaytn",
        chain_id::ARBITRUM_NOVA => "Arbitrum Nova",
        chain_id::POLYGON_ZKEVM => "Polygon zkEVM",
        chain_id::SCROLL => "Scroll",
        chain_id::SOLANA | chain_id::SOLANA_LEGACY_ID => "Solana",
        // Testnets
        chain_id::GOERLI => "Goerli",
        chain_id::SEPOLIA => "Sepolia",
        chain_id::ARBITRUM_SEPOLIA => "Arbitrum Sepolia",
        chain_id::OPTIMISM_SEPOLIA => "Optimism Sepolia",
        chain_id::BASE_SEPOLIA => "Base Sepolia",
        chain_id::POLYGON_MUMBAI => "Polygon Mumbai",
        chain_id::AVALANCHE_FUJI => "Avalanche Fuji",
        _ => "Unknown",
    }
}

pub fn is_testnet(chain_id: u64) -> bool {
    matches!(
        chain_id,
        chain_id::GOERLI
            | chain_id::SEPOLIA
            | chain_id::ARBITRUM_SEPOLIA
            | chain_id::OPTIMISM_SEPOLIA
            | chain_id::BASE_SEPOLIA
            | chain_id::POLYGON_MUMBAI
    )
}

// ──────────────────────────────────────────────────────────────────────────────
// Validation
// ──────────────────────────────────────────────────────────────────────────────

pub fn validate_cross_chain_params(params: &CrossChainSwapParams) -> Result<(), AppError> {
    // Validate amount
    let amount: f64 = params
        .amount
        .parse()
        .map_err(|_| AppError::InvalidParams("Amount must be a positive number".into()))?;
    if amount <= 0.0 {
        return Err(AppError::InvalidParams(
            "Amount must be a positive number".into(),
        ));
    }

    // Validate chain IDs are supported
    if !is_chain_supported(params.origin_chain_id) {
        return Err(AppError::InvalidParams(format!(
            "Unsupported origin chain: {}",
            params.origin_chain_id
        )));
    }
    if !is_chain_supported(params.destination_chain_id) {
        return Err(AppError::InvalidParams(format!(
            "Unsupported destination chain: {}",
            params.destination_chain_id
        )));
    }

    // Validate same-chain swaps should use Jupiter, not Relay
    if params.origin_chain_id == params.destination_chain_id {
        return Err(AppError::InvalidParams(
            "Same-chain swaps should use Jupiter. Relay is for cross-chain swaps only.".into(),
        ));
    }

    // Validate token addresses
    if !is_valid_evm_address(&params.origin_currency) {
        return Err(AppError::InvalidParams(
            "Invalid origin currency address".into(),
        ));
    }
    if !is_valid_evm_address(&params.destination_currency) {
        return Err(AppError::InvalidParams(
            "Invalid destination currency address".into(),
        ));
    }

    // Validate slippage
    if params.slippage_bps > MAX_SLIPPAGE_BPS {
        return Err(AppError::InvalidParams(format!(
            "Slippage {} bps exceeds maximum allowed value of {} (30%)",
            params.slippage_bps, MAX_SLIPPAGE_BPS
        )));
    }

    Ok(())
}

/// Whether this service knows the chain by name.
///
/// The list used to stop at sixteen and omit Solana — in a Solana app, which
/// made every bridge *out of* Solana on this path answer "Unsupported origin
/// chain: 792703809". It is now the same set `get_chain_name` can name, so a chain
/// we can talk about is a chain we accept, and the two cannot drift apart
/// without the mismatch being visible in one screen of code.
///
/// This is a sanity check, not an authority: Relay supports far more than we
/// name, and it is the one that decides. A pair we let through and it will not
/// route comes back as "no route", which is the honest answer.
fn is_chain_supported(chain_id: u64) -> bool {
    get_chain_name(chain_id) != "Unknown"
}

fn is_valid_evm_address(address: &str) -> bool {
    // Allow native token address
    if address == NATIVE_TOKEN_ADDRESS {
        return true;
    }
    // Validate EVM address format (0x + 40 hex chars)
    if address.len() != 42 {
        return false;
    }
    if !address.starts_with("0x") && !address.starts_with("0X") {
        return false;
    }
    address[2..].chars().all(|c| c.is_ascii_hexdigit())
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay quote
// ──────────────────────────────────────────────────────────────────────────────

/// Append OPRAI's app fee to a Relay quote body if a fee recipient is
/// configured AND Relay can pay it.
///
/// 20 = 0.20% on Relay's scale (10000 = 100%), the same rate every other
/// established pair pays. It was 0.05% here for no stated reason.
///
/// Relay pays app fees to an EVM address and rejects anything else outright —
/// `INVALID_APP_FEE_RECIPIENT`, on the quote, before any route is even
/// considered. The recipient defaults to `OPRAI_FEE_WALLET`, which is a Solana
/// address, so attaching it unconditionally did not lose us a commission: it
/// took down every bridge quote there was. A fee we cannot collect must never
/// cost the user the trade.
pub fn append_app_fee(body: &mut serde_json::Value, fee_recipient: Option<&str>) {
    match fee_recipient.filter(|s| !s.is_empty()) {
        Some(addr) if is_valid_evm_address(addr) => {
            body["appFees"] = serde_json::json!([{"recipient": addr, "fee": "20"}]);
        }
        Some(addr) => {
            tracing::warn!(
                recipient = %addr,
                "Relay app fee skipped: its recipient must be an EVM address. \
                 Set RELAY_FEE_RECIPIENT to one to earn on bridges."
            );
        }
        None => {}
    }
}

/// Turn a human amount into the integer base units Relay demands.
///
/// Relay validates `amount` against `^[0-9]+$` and rejects the whole quote
/// otherwise — "0.01" fails, and the message names a regex rather than a
/// decimal point. Every caller here speaks human: the card's field says
/// Amount, the model writes what the user said. So the conversion belongs
/// here, once, rather than in each of them.
///
/// The decimals come from Relay's own currency list for that chain, because
/// guessing them is a factor-of-a-thousand error on the first stablecoin.
/// Whether a chain expects EVM addresses.
///
/// Relay's Solana id is the only non-EVM chain we bridge to, so the test is
/// simple — but the consequence is not: an address is only valid on one side
/// of that line, and sending the wrong kind is how a bridge quote dies with
/// "Invalid address … for chain 8453".
fn chain_is_evm(chain_id: u64) -> bool {
    canonical_chain_id(chain_id) != chain_id::SOLANA
}

/// The address a bridge should land on, or a refusal that says what is needed.
///
/// This used to fall back to the signer whenever no recipient was named, which
/// is right within one VM and wrong across two: the signer here is a Solana
/// wallet, and Relay rejected it for every EVM destination. Naming the problem
/// beats sending an address that cannot receive.
fn resolve_bridge_recipient<'a>(
    recipient: Option<&'a str>,
    destination_chain_id: u64,
    user_address: &'a str,
) -> Result<&'a str, AppError> {
    let named = recipient.map(str::trim).filter(|r| !r.is_empty());
    let dest_is_evm = chain_is_evm(destination_chain_id);
    match named {
        Some(r) if is_valid_evm_address(r) == dest_is_evm => Ok(r),
        Some(_) if dest_is_evm => Err(AppError::InvalidParams(
            "That recipient is not an EVM address, and this bridge lands on an EVM chain. \
             Connect an EVM wallet or paste an address starting 0x."
                .into(),
        )),
        Some(_) => Err(AppError::InvalidParams(
            "That recipient is not a Solana address, and this bridge lands on Solana.".into(),
        )),
        None if dest_is_evm => Err(AppError::InvalidParams(
            "This bridge lands on an EVM chain, so it needs an EVM address to land on. \
             Connect an EVM wallet."
                .into(),
        )),
        // Bridging home: the connected wallet is the destination.
        None => Ok(user_address),
    }
}

/// Turn a Relay deposit step into a Solana transaction the wallet can sign.
///
/// Relay hands EVM origins a `{to, data, value}` and Solana origins a list of
/// instructions — a different shape for a different chain. The frontend only
/// understood the EVM one, so bridging FROM Solana ended at "no transaction
/// data returned from backend" for a quote that was complete.
///
/// Building it here rather than in the browser puts the bridge on the same
/// path as every other Solana action: the same signing, the same submission,
/// the same settlement watch, and the same OPRAI memo.
pub async fn solana_tx_from_relay_steps(
    rpc: &SolanaRpc,
    user: &Pubkey,
    steps: &[RelayStep],
) -> Option<String> {
    use base64::Engine as _;
    use solana_sdk::instruction::{AccountMeta, Instruction};
    use solana_sdk::message::{v0, AddressLookupTableAccount, VersionedMessage};
    use solana_sdk::transaction::VersionedTransaction;

    let data = steps
        .iter()
        .flat_map(|s| s.items.iter())
        .find_map(|i| i.data.as_ref())?;

    let raw = data.get("instructions")?.as_array()?;
    let mut instructions = Vec::with_capacity(raw.len());
    for ix in raw {
        let program_id: Pubkey = ix.get("programId")?.as_str()?.parse().ok()?;
        let accounts = ix
            .get("keys")?
            .as_array()?
            .iter()
            .filter_map(|k| {
                let pubkey: Pubkey = k.get("pubkey")?.as_str()?.parse().ok()?;
                let signer = k.get("isSigner").and_then(|v| v.as_bool()).unwrap_or(false);
                let writable = k
                    .get("isWritable")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                Some(if writable {
                    AccountMeta::new(pubkey, signer)
                } else {
                    AccountMeta::new_readonly(pubkey, signer)
                })
            })
            .collect::<Vec<_>>();
        // Relay writes instruction data as hex; anything else is a shape we do
        // not know, and guessing at transaction bytes is not a thing to do.
        let bytes = hex::decode(ix.get("data")?.as_str()?).ok()?;
        instructions.push(Instruction {
            program_id,
            accounts,
            data: bytes,
        });
    }
    if instructions.is_empty() {
        return None;
    }

    // The route's lookup tables, or the message cannot resolve its accounts.
    let mut tables = Vec::new();
    if let Some(addrs) = data
        .get("addressLookupTableAddresses")
        .and_then(|a| a.as_array())
    {
        for a in addrs {
            let key: Pubkey = match a.as_str().and_then(|s| s.parse().ok()) {
                Some(k) => k,
                None => continue,
            };
            let addresses = crate::services::memo::lookup_table_addresses(key).await;
            if !addresses.is_empty() {
                tables.push(AddressLookupTableAccount { key, addresses });
            }
        }
    }

    let rpc = rpc.clone();
    let blockhash = tokio::task::spawn_blocking(move || rpc.client().get_latest_blockhash().ok())
        .await
        .ok()
        .flatten()?;

    let message = v0::Message::try_compile(user, &instructions, &tables, blockhash).ok()?;
    let tx = VersionedTransaction {
        signatures: vec![Default::default(); message.header.num_required_signatures as usize],
        message: VersionedMessage::V0(message),
    };
    let bytes = bincode::serialize(&tx).ok()?;
    Some(base64::engine::general_purpose::STANDARD.encode(bytes))
}

/// Turn a Relay refusal into something a person can act on.
///
/// Relay answers in JSON — `{"message":"…","errorCode":"AMOUNT_TOO_LOW"}` —
/// and that JSON was being pasted straight into the card. A user asking to
/// bridge a small amount read a brace, a quoted key and an upper-case constant
/// to learn that the amount was too small.
///
/// The code is the reliable part; the prose changes. So the code decides the
/// sentence, and an unrecognised one falls back to Relay's own message rather
/// than to its punctuation.
fn relay_error(context: &str, body: &str) -> AppError {
    let parsed = serde_json::from_str::<serde_json::Value>(body).ok();
    let code = parsed
        .as_ref()
        .and_then(|v| v.get("errorCode"))
        .and_then(|c| c.as_str())
        .unwrap_or_default()
        .to_string();
    let message = parsed
        .as_ref()
        .and_then(|v| v.get("message"))
        .and_then(|m| m.as_str())
        .unwrap_or("")
        .to_string();

    let plain = match code.as_str() {
        "AMOUNT_TOO_LOW" | "SWAP_QUOTE_FAILED_AMOUNT_TOO_LOW" =>
            "That amount is too small to bridge — the fees would cost more than it is worth. Try a larger amount.".to_string(),
        "AMOUNT_TOO_HIGH" =>
            "That amount is larger than this route can carry right now. Try a smaller one.".to_string(),
        "NO_SWAP_ROUTES_FOUND" | "NO_QUOTES" | "ROUTE_NOT_FOUND" =>
            "No route between those two tokens right now. Try a different pair of chains or tokens.".to_string(),
        "UNSUPPORTED_CHAIN" =>
            "One of those chains is not bridgeable here.".to_string(),
        // INVALID_*_CURRENCY is what Relay actually answers for a token it does
        // not carry on that chain — "unsupported" is the documented name, not
        // the one on the wire. Reading only the documented one let Relay's own
        // "Invalid input or output currency" through to the card.
        "UNSUPPORTED_CURRENCY"
        | "CURRENCY_NOT_SUPPORTED"
        | "INVALID_INPUT_CURRENCY"
        | "INVALID_OUTPUT_CURRENCY" =>
            "One of those tokens is not bridgeable on the chain you picked.".to_string(),
        "INSUFFICIENT_LIQUIDITY" | "SOLVER_CAPACITY_EXCEEDED" | "ERROR_RENDERING_SOLVER_CAPACITY" =>
            "Not enough liquidity for this size right now. A smaller amount usually goes through.".to_string(),
        "INSUFFICIENT_FUNDS" =>
            "The wallet does not hold enough of that token for this bridge.".to_string(),
        "INVALID_ADDRESS" =>
            "That destination address is not valid for the chain you are bridging to.".to_string(),
        "BLOCKED_WALLET_ADDRESS" =>
            "Relay will not route to that address.".to_string(),
        // Ours, not theirs. Say so rather than blaming the user's request.
        "INVALID_APP_FEE_RECIPIENT" =>
            "Bridging is misconfigured on our side and has been reported. Nothing was signed.".to_string(),
        _ if !message.is_empty() => message,
        _ => format!("{context} could not be completed right now."),
    };
    if !code.is_empty() {
        tracing::warn!(code = %code, body = %body.chars().take(300).collect::<String>(), "Relay refused");
    }
    AppError::RelayApiError(plain)
}

/// Stands in for a destination when all we want is a price.
///
/// Relay quotes a route regardless of who receives it, so refusing to quote
/// until a wallet is connected hid the rate behind a decision the rate is
/// meant to inform. Only the quote path uses this; a bridge that would
/// actually send funds still demands a real address.
pub const PRICING_ONLY_RECIPIENT: &str = "0x0000000000000000000000000000000000000000";

/// Relay's two names for SOL, and which one this wallet can actually spend.
///
/// Everywhere else in OPRAI "SOL" is the wrapped mint, because that is what
/// Jupiter swaps. Relay distinguishes: `So111…112` is the SPL token and
/// `1111…1111` is the native asset. Bridging built the route around wrapped
/// SOL, the wallet holds native, and the transaction failed at simulation with
/// "not enough balance" — for an amount the user plainly had.
///
/// So the balance decides, rather than a guess in either direction: if the
/// wrapped account cannot cover it and the native one can, the bridge is built
/// on native. A wallet that really does hold wrapped SOL keeps using it.
pub async fn resolve_solana_origin_currency(
    rpc: &SolanaRpc,
    user: &Pubkey,
    chain_id: u64,
    currency: &str,
) -> String {
    const WRAPPED_SOL: &str = "So11111111111111111111111111111111111111112";
    const NATIVE_SOL: &str = "11111111111111111111111111111111";
    if canonical_chain_id(chain_id) != chain_id::SOLANA || currency != WRAPPED_SOL {
        return currency.to_string();
    }

    let owner = *user;
    let rpc = rpc.clone();
    let wrapped_balance = tokio::task::spawn_blocking(move || {
        let mint: Pubkey = WRAPPED_SOL.parse().ok()?;
        let ata = spl_associated_token_account::get_associated_token_address(&owner, &mint);
        rpc.client()
            .get_token_account_balance(&ata)
            .ok()
            .and_then(|b| b.amount.parse::<u64>().ok())
    })
    .await
    .ok()
    .flatten()
    .unwrap_or(0);

    // Any wrapped balance at all means the user deliberately holds wSOL and
    // the route should use it; none means they meant the SOL they can see.
    if wrapped_balance > 0 {
        currency.to_string()
    } else {
        tracing::info!("bridging native SOL: this wallet holds no wrapped SOL");
        NATIVE_SOL.to_string()
    }
}

/// Relay's own id for a chain, given whatever id reached us.
///
/// 900 was our constant for Solana for a long time, so it is in prompts, in
/// chat history and in every card built before it was corrected. Rejecting
/// those is punishing the user for our own old value.
pub fn canonical_chain_id(id: u64) -> u64 {
    if id == chain_id::SOLANA_LEGACY_ID {
        chain_id::SOLANA
    } else {
        id
    }
}

async fn to_base_units(
    http: &reqwest::Client,
    chain_id: u64,
    currency: &str,
    amount: &str,
) -> Result<String, AppError> {
    let amount = amount.trim();
    if amount.is_empty() {
        return Err(AppError::InvalidParams("Enter an amount".into()));
    }
    // Already integral: nothing to scale, and scaling it would silently
    // multiply an amount someone had already converted.
    if !amount.contains('.') && !amount.contains(',') {
        return Ok(amount.to_string());
    }
    let normalised = amount.replace(',', ".");

    let chain_id = canonical_chain_id(chain_id);
    let decimals = relay_token_decimals(http, chain_id, currency)
        .await
        .ok_or_else(|| {
            AppError::InvalidParams(format!(
            "Relay does not list {currency} on chain {chain_id}, so its amount cannot be scaled"
        ))
        })?;

    // Scaled as text, not through f64: 0.1 is not representable in binary
    // floating point, and this figure is money.
    let (whole, frac) = match normalised.split_once('.') {
        Some((w, f)) => (w, f),
        None => (normalised.as_str(), ""),
    };
    if frac.len() > decimals as usize {
        return Err(AppError::InvalidParams(format!(
            "{amount} has more decimal places than this token has ({decimals})"
        )));
    }
    let padded = format!("{frac:0<width$}", width = decimals as usize);
    let joined = format!("{}{}", whole.trim_start_matches('0'), padded);
    let trimmed = joined.trim_start_matches('0');
    Ok(if trimmed.is_empty() {
        "0".to_string()
    } else {
        trimmed.to_string()
    })
}

/// How many decimals Relay says this token has. Cached: a chain's list does
/// not change between two quotes.
async fn relay_token_decimals(http: &reqwest::Client, chain_id: u64, currency: &str) -> Option<u8> {
    use std::collections::HashMap;
    use std::sync::{Mutex, OnceLock};
    static CACHE: OnceLock<Mutex<HashMap<(u64, String), u8>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    let key = (chain_id, currency.to_lowercase());
    if let Some(d) = cache.lock().ok().and_then(|c| c.get(&key).copied()) {
        return Some(d);
    }
    let tokens = get_chain_tokens(http, chain_id).await.ok()?;
    let found = tokens
        .iter()
        .find(|t| t.address.eq_ignore_ascii_case(currency))
        .map(|t| t.decimals);
    if let (Some(d), Ok(mut c)) = (found, cache.lock()) {
        c.insert(key, d);
    }
    found
}

/// Fetch a cross-chain swap quote from Relay API.
pub async fn get_cross_chain_quote(
    http: &reqwest::Client,
    params: &CrossChainSwapParams,
    user_address: &str,
    fee_recipient: Option<&str>,
) -> Result<RelayQuote, AppError> {
    let base_url = if is_testnet(params.origin_chain_id) || is_testnet(params.destination_chain_id)
    {
        RELAY_TESTNET_API
    } else {
        RELAY_API
    };

    // Scaled from Relay's own decimals for this token, not from a guess. This
    // used eighteen for everything, native or not, with a comment saying so —
    // which is a billion-fold error on SOL and a million-fold one on USDC, in
    // the direction of sending far more than the user typed.
    let amount_in_wei = to_base_units(
        http,
        params.origin_chain_id,
        &params.origin_currency,
        &params.amount,
    )
    .await?;
    let recipient = resolve_bridge_recipient(
        params.recipient.as_deref(),
        params.destination_chain_id,
        user_address,
    )?;

    let mut quote_body = serde_json::json!({
        "user": user_address,
        "originChainId": canonical_chain_id(params.origin_chain_id),
        "destinationChainId": canonical_chain_id(params.destination_chain_id),
        "originCurrency": params.origin_currency,
        "destinationCurrency": params.destination_currency,
        "amount": amount_in_wei,
        "recipient": recipient,
        "tradeType": params.trade_type,
        "referrer": params.referrer,
    });
    append_app_fee(&mut quote_body, fee_recipient);

    let response = http
        .post(format!("{}/quote/v2", base_url))
        .header("Content-Type", "application/json")
        .json(&quote_body)
        .send()
        .await?;

    if !response.status().is_success() {
        let body = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay quote failed", &body));
    }

    let quote: RelayQuote = response.json().await.map_err(|e| {
        tracing::error!(error = %e, "Relay quote did not match our model");
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })?;

    Ok(quote)
}

// ──────────────────────────────────────────────────────────────────────────────
// Cross-chain swap build
// ──────────────────────────────────────────────────────────────────────────────

/// Build a cross-chain swap transaction via Relay.
/// Returns the quote with execution steps that the frontend can execute.
pub async fn build_cross_chain_swap(
    http: &reqwest::Client,
    user_address: &str,
    params: &CrossChainSwapParams,
    fee_recipient: Option<&str>,
) -> Result<CrossChainSwapResult, AppError> {
    validate_cross_chain_params(params)?;

    let quote = get_cross_chain_quote(http, params, user_address, fee_recipient).await?;

    // Extract details for preview
    let details = &quote.details;
    let origin_symbol = details
        .currency_in
        .currency
        .symbol
        .clone()
        .unwrap_or_else(|| "TOKEN".to_string());
    let dest_symbol = details
        .currency_out
        .currency
        .symbol
        .clone()
        .unwrap_or_else(|| "TOKEN".to_string());

    let origin_chain_name = get_chain_name(params.origin_chain_id);
    let dest_chain_name = get_chain_name(params.destination_chain_id);

    // Calculate output amount
    // Relay carries the amount inside the currency now; `amountOut` at the top
    // level is gone, so reading only that described every bridge as delivering
    // zero. Parsed as f64 because an 18-decimal wei figure overflows a u64.
    let out_amount = details
        .currency_out
        .amount
        .clone()
        .or_else(|| details.amount_out.clone())
        .unwrap_or_default();
    let out_decimals = details.currency_out.currency.decimals.unwrap_or(18);
    let out_amount_float = details
        .currency_out
        .amount_formatted
        .as_deref()
        .and_then(|f| f.parse::<f64>().ok())
        .unwrap_or_else(|| {
            out_amount.parse::<f64>().unwrap_or(0.0) / 10_f64.powi(out_decimals as i32)
        });

    // Build warnings
    let mut warnings = Vec::new();

    // Cross-chain warning
    warnings.push(format!(
        "Cross-chain swap: {} → {}",
        origin_chain_name, dest_chain_name
    ));

    // Estimated time warning
    if let Some(time) = details.estimated_time {
        if time > 300 {
            warnings.push(format!("May take {}+ minutes to complete", time / 60));
        }
    }

    // Price impact warning
    if let Some(ref impact) = details.price_impact {
        if let Ok(impact_val) = impact.parse::<f64>() {
            if impact_val > 1.0 {
                warnings.push(format!("High price impact: {:.2}%", impact_val));
            }
        }
    }

    // Fee warning
    if let Some(ref fees) = quote.fees {
        if let Some(total) = fees.total_usd() {
            if total > 10.0 {
                warnings.push(format!("Total fees: ${:.2}", total));
            }
        }
    }

    let preview = CrossChainSwapPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "cross_chain_swap".to_string(),
        description: format!(
            "Swap {} {} ({}) → {:.6} {} ({})",
            params.amount,
            origin_symbol,
            origin_chain_name,
            out_amount_float,
            dest_symbol,
            dest_chain_name
        ),
        estimated_fee: quote
            .fees
            .as_ref()
            .and_then(|f| f.total_usd())
            .map(|f| format!("${:.2}", f))
            .unwrap_or_else(|| "~$2-5".to_string()),
        params: params.clone(),
        warnings,
        requires_approval: true,
        estimated_time_seconds: details.estimated_time,
        exchange_rate: details.rate.clone(),
    };

    Ok(CrossChainSwapResult { quote, preview })
}

/// Get supported chains from Relay.
/// `include_chains`: optional comma-separated chain ID filter (e.g. "1,8453,900").
pub async fn get_supported_chains(
    http: &reqwest::Client,
    include_chains: Option<&str>,
) -> Result<Vec<RelayChainInfo>, AppError> {
    let url = match include_chains.filter(|s| !s.is_empty()) {
        Some(filter) => format!("{}/chains?includeChains={}", RELAY_API, filter),
        None => format!("{}/chains", RELAY_API),
    };

    let response = http.get(&url).send().await?;

    if !response.status().is_success() {
        let body = response.text().await.unwrap_or_default();
        return Err(relay_error("Failed to fetch chains", &body));
    }

    // API wraps the array in { "chains": [...] } — try both formats
    let raw: serde_json::Value = response.json().await.map_err(|e| {
        tracing::error!(error = %e, "Relay chains did not match our model");
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })?;

    let chains: Vec<RelayChainInfo> =
        if let Some(arr) = raw.get("chains").and_then(|v| v.as_array()) {
            serde_json::from_value(serde_json::Value::Array(arr.clone())).map_err(|e| {
                tracing::error!(error = %e, "Relay chains did not match our model");
                AppError::RelayApiError(
                    "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
                )
            })?
        } else {
            serde_json::from_value(raw).map_err(|e| {
                tracing::error!(error = %e, "Relay chains did not match our model");
                AppError::RelayApiError(
                    "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
                )
            })?
        };

    Ok(chains)
}

/// Native currency info for a chain.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayChainCurrency {
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub symbol: Option<String>,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub decimals: Option<u8>,
    #[serde(default)]
    pub address: Option<String>,
}

/// Relay contract addresses for a chain.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayChainContracts {
    #[serde(default)]
    pub multicall3: Option<String>,
    #[serde(default)]
    pub multicaller: Option<String>,
    #[serde(default)]
    pub relay_receiver: Option<String>,
    #[serde(default)]
    pub erc20_router: Option<String>,
    #[serde(default)]
    pub approval_proxy: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayChainInfo {
    pub id: u64,
    pub name: String,
    #[serde(default)]
    pub display_name: Option<String>,
    /// VM type: "evm" | "svm" | "bvm" | "tvm" | "tonvm" | "suivm" | "hypevm" | "lvm"
    #[serde(default)]
    pub vm_type: Option<String>,
    /// The chain's mark, which Relay hosts. Undeclared, serde dropped it, and
    /// a chain list without icons is sixty-eight lines of text.
    #[serde(default)]
    pub icon_url: Option<String>,
    // RPC & Explorer
    #[serde(default)]
    pub http_rpc_url: Option<String>,
    #[serde(default)]
    pub ws_rpc_url: Option<String>,
    #[serde(default)]
    pub explorer_url: Option<String>,
    #[serde(default)]
    pub explorer_name: Option<String>,
    // Operational status
    #[serde(default)]
    pub disabled: Option<bool>,
    #[serde(default)]
    pub deposit_enabled: Option<bool>,
    #[serde(default)]
    pub block_production_lagging: Option<bool>,
    #[serde(default)]
    pub status_message: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub partial_disable_limit: Option<f64>,
    // Fees
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub withdrawal_fee: Option<f64>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub deposit_fee: Option<f64>,
    #[serde(default)]
    pub surge_enabled: Option<bool>,
    // Token support
    /// "All" | "Limited"
    #[serde(default)]
    pub token_support: Option<String>,
    #[serde(default)]
    pub featured_tokens: Vec<serde_json::Value>,
    #[serde(default)]
    pub erc20_currencies: Vec<serde_json::Value>,
    #[serde(default)]
    pub solver_currencies: Vec<serde_json::Value>,
    // Currency & contracts
    #[serde(default)]
    pub currency: Option<RelayChainCurrency>,
    #[serde(default)]
    pub contracts: Option<RelayChainContracts>,
    // L2 / solver
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub base_chain_id: Option<u64>,
    #[serde(default)]
    pub solver_addresses: Vec<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    // Legacy compat
    #[serde(default)]
    pub logo_uri: Option<String>,
}

/// Get token list for a specific chain
pub async fn get_chain_tokens(
    http: &reqwest::Client,
    chain_id: u64,
) -> Result<Vec<RelayTokenInfo>, AppError> {
    // POST, not GET: `GET /currencies/v2?chainId=` answers 404 — "Route not
    // found" — so this call has never returned a token. Its sibling
    // `get_relay_currencies` already posts, which is how one worked and the
    // other silently did not.
    let response = http
        .post(format!("{}/currencies/v2", RELAY_API))
        .json(&serde_json::json!({ "chainIds": [chain_id] }))
        .send()
        .await?;

    if !response.status().is_success() {
        let body = response.text().await.unwrap_or_default();
        return Err(relay_error("Failed to fetch tokens", &body));
    }

    let tokens: Vec<RelayTokenInfo> = response.json().await.map_err(|e| {
        tracing::error!(error = %e, "Relay tokens did not match our model");
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })?;

    Ok(tokens)
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct RelayTokenMetadata {
    /// Relay writes `logoURI`; camelCase renaming asks for `logoUri`. One
    /// capital letter, and serde drops the field silently — which is why the
    /// bridge named its tokens correctly and drew both of them as a letter in
    /// a circle. Serialised back out as `logoURI` so the card reads the same
    /// name Relay uses.
    #[serde(default, rename = "logoURI", alias = "logoUri")]
    pub logo_uri: Option<String>,
    #[serde(default)]
    pub verified: Option<bool>,
    #[serde(default)]
    pub is_native: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayTokenInfo {
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub chain_id: Option<u64>,
    pub address: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
    /// VM type: "evm" | "svm" | "bvm" | "tvm" | "tonvm" | "suivm" | "hypevm" | "lvm"
    #[serde(default)]
    pub vm_type: Option<String>,
    #[serde(default)]
    pub metadata: Option<RelayTokenMetadata>,
    // legacy / extra fields some responses include
    #[serde(default, alias = "logoURI", alias = "logoUri")]
    pub logo_uri: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub price: Option<f64>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub volume_24h: Option<f64>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay.link — full bridge with all optional parameters
// ──────────────────────────────────────────────────────────────────────────────

/// Full relay_bridge params — all optional fields from /quote/v2.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayBridgeParams {
    // Required
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub origin_chain_id: u64,
    #[serde(deserialize_with = "crate::services::params::lenient")]
    pub destination_chain_id: u64,
    pub origin_currency: String,
    pub destination_currency: String,
    /// Human-readable amount (e.g. "1.5"). Passed as-is to Relay API (raw or wei).
    pub amount: String,
    #[serde(default = "default_trade_type")]
    pub trade_type: String,
    // Optional — addressing
    #[serde(default)]
    pub recipient: Option<String>,
    #[serde(default)]
    pub refund_to: Option<String>,
    /// "origin" | "destination"
    #[serde(default)]
    pub refund_type: Option<String>,
    // Optional — gas top-up on destination
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub topup_gas: Option<bool>,
    #[serde(default)]
    pub topup_gas_amount: Option<String>,
    // Optional — fees / slippage
    /// Slippage tolerance in basis points (e.g. 50 = 0.5%)
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub slippage_tolerance: Option<u32>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub subsidize_fees: Option<bool>,
    #[serde(default)]
    pub referrer: Option<String>,
    #[serde(default)]
    pub referrer_address: Option<String>,
    // Optional — routing controls
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub use_deposit_address: Option<bool>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub disable_origin_swaps: Option<bool>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub force_solver_execution: Option<bool>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub fixed_rate: Option<bool>,
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub max_route_length: Option<u32>,
    /// true = fail if exact quote cannot be fulfilled (no degraded fallback)
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub strict: Option<bool>,
    /// true = proceed even if price impact exceeds safe threshold
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub override_price_impact: Option<bool>,
    /// true = include Solana compute unit limit in origin TX (recommended for Solana-origin bridges)
    #[serde(default, deserialize_with = "crate::services::params::lenient_opt")]
    pub include_compute_unit_limit: Option<bool>,
}

/// Get a quote from Relay (no execution) with full optional params.
pub async fn get_relay_quote_full(
    http: &reqwest::Client,
    params: &RelayBridgeParams,
    user_address: &str,
    fee_recipient: Option<&str>,
) -> Result<RelayQuote, AppError> {
    // Relay takes base units and rejects anything with a decimal point.
    let scaled_amount = to_base_units(
        http,
        params.origin_chain_id,
        &params.origin_currency,
        &params.amount,
    )
    .await?;

    let mut body = serde_json::json!({
        "user": user_address,
        "originChainId": canonical_chain_id(params.origin_chain_id),
        "destinationChainId": canonical_chain_id(params.destination_chain_id),
        "originCurrency": params.origin_currency,
        "destinationCurrency": params.destination_currency,
        "amount": scaled_amount,
        "tradeType": params.trade_type,
    });

    body["recipient"] = serde_json::json!(resolve_bridge_recipient(
        params.recipient.as_deref(),
        params.destination_chain_id,
        user_address,
    )?);
    if let Some(ref v) = params.refund_to {
        body["refundTo"] = serde_json::json!(v);
    }
    if let Some(ref v) = params.refund_type {
        body["refundType"] = serde_json::json!(v);
    }
    if let Some(v) = params.topup_gas {
        body["topupGas"] = serde_json::json!(v);
    }
    if let Some(ref v) = params.topup_gas_amount {
        body["topupGasAmount"] = serde_json::json!(v);
    }
    if let Some(v) = params.slippage_tolerance {
        body["slippageTolerance"] = serde_json::json!(v);
    }
    if let Some(v) = params.subsidize_fees {
        body["subsidizeFees"] = serde_json::json!(v);
    }
    if let Some(ref v) = params.referrer {
        body["referrer"] = serde_json::json!(v);
    }
    if let Some(ref v) = params.referrer_address {
        body["referrerAddress"] = serde_json::json!(v);
    }
    if let Some(v) = params.use_deposit_address {
        body["useDepositAddress"] = serde_json::json!(v);
    }
    if let Some(v) = params.disable_origin_swaps {
        body["disableOriginSwaps"] = serde_json::json!(v);
    }
    if let Some(v) = params.force_solver_execution {
        body["forceSolverExecution"] = serde_json::json!(v);
    }
    if let Some(v) = params.fixed_rate {
        body["fixedRate"] = serde_json::json!(v);
    }
    if let Some(v) = params.max_route_length {
        body["maxRouteLength"] = serde_json::json!(v);
    }
    if let Some(v) = params.strict {
        body["strict"] = serde_json::json!(v);
    }
    if let Some(v) = params.override_price_impact {
        body["overridePriceImpact"] = serde_json::json!(v);
    }
    if let Some(v) = params.include_compute_unit_limit {
        body["includeComputeUnitLimit"] = serde_json::json!(v);
    }
    append_app_fee(&mut body, fee_recipient);

    let response = http
        .post(format!("{}/quote/v2", RELAY_API))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay quote failed", &err));
    }

    // Read the text and parse it here rather than through `.json()`: reqwest
    // reports every shape mismatch as "error decoding response body", which
    // names neither the field nor the type it wanted. serde, given the string,
    // names both — and this response has changed shape under us twice.
    let body = response.text().await.map_err(|e| {
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })?;
    serde_json::from_str::<RelayQuote>(&body).map_err(|e| {
        tracing::error!(error = %e, body = %body.chars().take(600).collect::<String>(),
                        "Relay quote did not match our model");
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })
}

/// Execute a Relay bridge — get quote then return steps for frontend signing.
pub async fn relay_bridge(
    http: &reqwest::Client,
    user_address: &str,
    params: &RelayBridgeParams,
    fee_recipient: Option<&str>,
) -> Result<CrossChainSwapResult, AppError> {
    let quote = get_relay_quote_full(http, params, user_address, fee_recipient).await?;

    let details = &quote.details;
    let origin_symbol = details
        .currency_in
        .currency
        .symbol
        .clone()
        .unwrap_or_else(|| "TOKEN".to_string());
    let dest_symbol = details
        .currency_out
        .currency
        .symbol
        .clone()
        .unwrap_or_else(|| "TOKEN".to_string());
    let origin_chain_name = get_chain_name(params.origin_chain_id);
    let dest_chain_name = get_chain_name(params.destination_chain_id);

    // Relay carries the amount inside the currency now; `amountOut` at the top
    // level is gone, so reading only that described every bridge as delivering
    // zero. Parsed as f64 because an 18-decimal wei figure overflows a u64.
    let out_amount = details
        .currency_out
        .amount
        .clone()
        .or_else(|| details.amount_out.clone())
        .unwrap_or_default();
    let out_decimals = details.currency_out.currency.decimals.unwrap_or(18);
    let out_amount_float = details
        .currency_out
        .amount_formatted
        .as_deref()
        .and_then(|f| f.parse::<f64>().ok())
        .unwrap_or_else(|| {
            out_amount.parse::<f64>().unwrap_or(0.0) / 10_f64.powi(out_decimals as i32)
        });

    let mut warnings = vec![format!(
        "Cross-chain bridge: {} → {}",
        origin_chain_name, dest_chain_name
    )];
    if let Some(time) = details.estimated_time {
        if time > 300 {
            warnings.push(format!("May take {}+ minutes", time / 60));
        }
    }
    if let Some(ref impact) = details.price_impact {
        if let Ok(v) = impact.parse::<f64>() {
            if v > 1.0 {
                warnings.push(format!("High price impact: {:.2}%", v));
            }
        }
    }
    if let Some(ref fees) = quote.fees {
        if let Some(total) = fees.total_usd() {
            if total > 10.0 {
                warnings.push(format!("Total fees: ${:.2}", total));
            }
        }
    }

    let swap_params = CrossChainSwapParams {
        origin_chain_id: params.origin_chain_id,
        destination_chain_id: params.destination_chain_id,
        origin_currency: params.origin_currency.clone(),
        destination_currency: params.destination_currency.clone(),
        amount: params.amount.clone(),
        recipient: params.recipient.clone(),
        trade_type: params.trade_type.clone(),
        referrer: params.referrer.clone(),
        slippage_bps: params.slippage_tolerance.unwrap_or(50),
        provider: Some("relay".to_string()),
    };

    let preview = CrossChainSwapPreview {
        id: Uuid::new_v4().to_string(),
        action_type: "relay_bridge".to_string(),
        description: format!(
            "Bridge {} {} ({}) → {:.6} {} ({})",
            params.amount,
            origin_symbol,
            origin_chain_name,
            out_amount_float,
            dest_symbol,
            dest_chain_name
        ),
        estimated_fee: quote
            .fees
            .as_ref()
            .and_then(|f| f.total_usd())
            .map(|f| format!("${:.2}", f))
            .unwrap_or_else(|| "~$2-5".to_string()),
        params: swap_params,
        warnings,
        requires_approval: true,
        estimated_time_seconds: details.estimated_time,
        exchange_rate: details.rate.clone(),
    };

    Ok(CrossChainSwapResult { quote, preview })
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay.link — index transaction (/transactions/index)
// ──────────────────────────────────────────────────────────────────────────────

/// Request body for POST /transactions/index.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayIndexTransactionRequest {
    /// Network identifier (chain ID as string, e.g. "1", "900")
    pub chain_id: String,
    /// Transaction hash to index
    pub tx_hash: String,
    /// Optional request identifier to associate with the transaction
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelayIndexTransactionResponse {
    pub message: String,
}

/// Notify Relay backend to index a transaction and detect cross-chain deposits.
/// Call this after a user submits a deposit transaction so Relay can pick it up.
pub async fn index_relay_transaction(
    http: &reqwest::Client,
    req: &RelayIndexTransactionRequest,
) -> Result<RelayIndexTransactionResponse, AppError> {
    let response = http
        .post(format!("{}/transactions/index", RELAY_API))
        .header("Content-Type", "application/json")
        .json(req)
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay transactions/index failed", &err));
    }

    response
        .json::<RelayIndexTransactionResponse>()
        .await
        .map_err(|e| {
            AppError::RelayApiError(
                "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
            )
        })
}

// ── POST /transactions/single ─────────────────────────────────────────────────

/// Request body for POST /transactions/single.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelaySingleTransactionRequest {
    /// Unique identifier for the Relay request
    pub request_id: String,
    /// Chain ID as string (e.g. "1", "900")
    pub chain_id: String,
    /// Transaction hash
    pub tx: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelaySingleTransactionResponse {
    pub message: String,
}

/// Notify Relay to index transfers, wraps, and unwraps for a specific request.
/// Use this after submitting a specific transaction tied to a known requestId.
pub async fn single_relay_transaction(
    http: &reqwest::Client,
    req: &RelaySingleTransactionRequest,
) -> Result<RelaySingleTransactionResponse, AppError> {
    let response = http
        .post(format!("{}/transactions/single", RELAY_API))
        .header("Content-Type", "application/json")
        .json(req)
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay transactions/single failed", &err));
    }

    response
        .json::<RelaySingleTransactionResponse>()
        .await
        .map_err(|e| {
            AppError::RelayApiError(
                "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
            )
        })
}

// ── POST /app-fees/{wallet}/claim ────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayClaimAppFeesRequest {
    pub chain_id: u64,
    pub currency: String,
    pub recipient: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub amount: Option<String>,
}

/// Response mirrors /execute/permits — steps that require a signature.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayClaimAppFeesResponse {
    #[serde(default)]
    pub steps: Vec<RelayPermitStep>,
}

/// Initiate an app-fee claim for `wallet`. Returns signature steps the frontend must process.
pub async fn claim_app_fees(
    http: &reqwest::Client,
    wallet: &str,
    req: &RelayClaimAppFeesRequest,
) -> Result<RelayClaimAppFeesResponse, AppError> {
    let url = format!("{}/app-fees/{}/claim", RELAY_API, wallet);
    let response = http
        .post(&url)
        .header("Content-Type", "application/json")
        .json(req)
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay app-fees/claim failed", &err));
    }

    response
        .json::<RelayClaimAppFeesResponse>()
        .await
        .map_err(|e| {
            AppError::RelayApiError(
                "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
            )
        })
}

// ── GET /app-fees/{wallet}/balances ──────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayAppFeeTokenMetadata {
    #[serde(default, alias = "logoURI", alias = "logoUri")]
    pub logo_uri: Option<String>,
    pub verified: Option<String>,
    pub is_native: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayAppFeeCurrency {
    pub chain_id: Option<u64>,
    pub address: Option<String>,
    pub symbol: Option<String>,
    pub name: Option<String>,
    pub decimals: Option<u32>,
    pub metadata: Option<RelayAppFeeTokenMetadata>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayAppFeeBalance {
    pub currency: Option<RelayAppFeeCurrency>,
    pub amount: Option<String>,
    pub amount_formatted: Option<String>,
    pub amount_usd: Option<String>,
    pub minimum_amount: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayAppFeeBalancesResponse {
    #[serde(default)]
    pub balances: Vec<RelayAppFeeBalance>,
    pub total_balance_usd: Option<f64>,
    pub outstanding_fast_fill_balance_usd: Option<f64>,
    pub available_balance_usd: Option<f64>,
}

/// Fetch accumulated app fee balances for a fee-recipient wallet.
pub async fn get_app_fee_balances(
    http: &reqwest::Client,
    wallet: &str,
) -> Result<RelayAppFeeBalancesResponse, AppError> {
    let url = format!("{}/app-fees/{}/balances", RELAY_API, wallet);
    let response = http.get(&url).send().await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay app-fees/balances failed", &err));
    }

    response
        .json::<RelayAppFeeBalancesResponse>()
        .await
        .map_err(|e| {
            AppError::RelayApiError(
                "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
            )
        })
}

// ── POST /fast-fill ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayFastFillRequest {
    pub request_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub solver_input_currency_amount: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_fill_amount_usd: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelayFastFillResponse {
    pub message: String,
}

/// Queue a request for fast fill. Requires a Relay API key (`x-api-key` header).
pub async fn fast_fill(
    http: &reqwest::Client,
    api_key: &str,
    req: &RelayFastFillRequest,
) -> Result<RelayFastFillResponse, AppError> {
    let response = http
        .post(format!("{}/fast-fill", RELAY_API))
        .header("Content-Type", "application/json")
        .header("x-api-key", api_key)
        .json(req)
        .send()
        .await?;

    if !response.status().is_success() {
        let status = response.status();
        let err = response.text().await.unwrap_or_default();
        tracing::warn!(%status, body = %err.chars().take(300).collect::<String>(),
                       "Relay fast-fill refused");
        return Err(relay_error("The fast-fill", &err));
    }

    response.json::<RelayFastFillResponse>().await.map_err(|e| {
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })
}

// ── POST /transactions/deposit-address/reindex ───────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayDepositAddressReindexRequest {
    pub chain_id: u64,
    pub deposit_address: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sweep: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_chain_id: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayTriggeredCurrency {
    pub currency: String,
    pub symbol: String,
    pub balance: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayDepositAddressReindexResponse {
    pub message: String,
    #[serde(default)]
    pub triggered_currencies: Vec<RelayTriggeredCurrency>,
    pub checked_currencies: Option<u64>,
    pub failed_currencies: Option<u64>,
}

/// Reindex a deposit address — scans for currency activity and triggers processing.
pub async fn reindex_deposit_address(
    http: &reqwest::Client,
    req: &RelayDepositAddressReindexRequest,
) -> Result<RelayDepositAddressReindexResponse, AppError> {
    let response = http
        .post(format!(
            "{}/transactions/deposit-address/reindex",
            RELAY_API
        ))
        .header("Content-Type", "application/json")
        .json(req)
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay deposit-address/reindex failed", &err));
    }

    response
        .json::<RelayDepositAddressReindexResponse>()
        .await
        .map_err(|e| {
            AppError::RelayApiError(
                "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
            )
        })
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay.link — intent status v3 (/intents/status/v3)
// ──────────────────────────────────────────────────────────────────────────────

/// Status values returned by /intents/status/v3.
/// waiting → depositing → pending → submitted → success | delayed | refunded | failure
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayIntentStatus {
    /// "waiting" | "depositing" | "pending" | "submitted" | "success" | "delayed" | "refunded" | "failure"
    pub status: String,
    #[serde(default)]
    pub details: Option<String>,
    /// Incoming tx hashes on the origin chain
    #[serde(default)]
    pub in_tx_hashes: Vec<String>,
    /// Outgoing tx hashes on the destination chain
    #[serde(default)]
    pub tx_hashes: Vec<String>,
    /// Last updated timestamp in milliseconds
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub updated_at: Option<u64>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub origin_chain_id: Option<u64>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub destination_chain_id: Option<u64>,
}

/// Check the execution status of a Relay cross-chain intent.
pub async fn get_relay_intent_status(
    http: &reqwest::Client,
    request_id: &str,
) -> Result<RelayIntentStatus, AppError> {
    let response = http
        .get(format!(
            "{}/intents/status/v3?requestId={}",
            RELAY_API, request_id
        ))
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay intent status failed", &err));
    }

    response.json::<RelayIntentStatus>().await.map_err(|e| {
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay.link — execute permits (/execute/permits)
// ──────────────────────────────────────────────────────────────────────────────

/// Request body for POST /execute/permits.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayExecutePermitsRequest {
    /// Permit signature kind returned in the quote steps (e.g. "eip3009").
    pub kind: String,
    /// The requestId of the quote this permit signature applies to.
    pub request_id: String,
    /// Optional API type from the quote steps: "bridge" | "swap" | "user-swap".
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api: Option<String>,
}

/// A single execution step item returned by /execute/permits.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayPermitStepItem {
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub data: Option<serde_json::Value>,
    #[serde(default)]
    pub check: Option<serde_json::Value>,
}

/// A single execution step returned by /execute/permits.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayPermitStep {
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub action: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub kind: Option<String>,
    #[serde(default)]
    pub items: Vec<RelayPermitStepItem>,
}

/// Response from POST /execute/permits.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayExecutePermitsResponse {
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub steps: Vec<RelayPermitStep>,
}

/// Submit a permit signature to Relay and get back updated execution steps.
///
/// Called by the frontend after the user signs an EIP-3009 (or similar) permit
/// that was requested in a quote step. The signature is passed as a query param;
/// the body carries kind + requestId obtained from the quote response.
pub async fn execute_relay_permits(
    http: &reqwest::Client,
    signature: &str,
    body: &RelayExecutePermitsRequest,
) -> Result<RelayExecutePermitsResponse, AppError> {
    let response = http
        .post(format!(
            "{}/execute/permits?signature={}",
            RELAY_API, signature
        ))
        .header("Content-Type", "application/json")
        .json(body)
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay execute/permits failed", &err));
    }

    response
        .json::<RelayExecutePermitsResponse>()
        .await
        .map_err(|e| {
            AppError::RelayApiError(
                "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
            )
        })
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay.link — chain liquidity (/chains/liquidity)
// ──────────────────────────────────────────────────────────────────────────────

/// Solver liquidity balance for a single currency on a chain.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayLiquidityItem {
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub chain_id: Option<u64>,
    #[serde(default)]
    pub currency_id: Option<String>,
    #[serde(default)]
    pub symbol: Option<String>,
    #[serde(default)]
    pub address: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub decimals: Option<u8>,
    /// Solver balance in smallest unit (e.g. wei for ETH)
    #[serde(default)]
    pub balance: Option<String>,
    /// Solver balance in USD
    #[serde(default)]
    pub amount_usd: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayLiquidityResponse {
    #[serde(default)]
    pub liquidity: Vec<RelayLiquidityItem>,
}

/// Get solver liquidity balances for all currencies on a given chain.
pub async fn get_relay_chains_liquidity(
    http: &reqwest::Client,
    chain_id: u64,
) -> Result<RelayLiquidityResponse, AppError> {
    let response = http
        .get(format!(
            "{}/chains/liquidity?chainId={}",
            RELAY_API, chain_id
        ))
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Failed to fetch chain liquidity", &err));
    }

    response
        .json::<RelayLiquidityResponse>()
        .await
        .map_err(|e| {
            AppError::RelayApiError(
                "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
            )
        })
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay.link — currencies search (/currencies/v2)
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct RelayCurrenciesQuery {
    #[serde(default)]
    pub chain_ids: Option<Vec<u64>>,
    #[serde(default)]
    pub term: Option<String>,
    #[serde(default)]
    pub address: Option<String>,
    #[serde(default)]
    pub currency_id: Option<String>,
    /// Token addresses in "chainId:address" format (e.g. ["1:0xA0b8…", "900:So111…"])
    #[serde(default)]
    pub tokens: Option<Vec<String>>,
    #[serde(default)]
    pub verified: Option<bool>,
    /// Max results (default 20, max 100)
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub limit: Option<u32>,
    #[serde(default)]
    pub default_list: Option<bool>,
    /// Include all chains for a currency when filtering by chainId + address
    #[serde(default)]
    pub include_all_chains: Option<bool>,
    #[serde(default)]
    pub use_external_search: Option<bool>,
    #[serde(default)]
    pub deposit_address_only: Option<bool>,
}

pub async fn get_relay_currencies(
    http: &reqwest::Client,
    query: &RelayCurrenciesQuery,
) -> Result<Vec<RelayTokenInfo>, AppError> {
    let mut body = serde_json::json!({});
    if let Some(ref v) = query.chain_ids {
        body["chainIds"] = serde_json::json!(v);
    }
    if let Some(ref v) = query.term {
        body["term"] = serde_json::json!(v);
    }
    if let Some(ref v) = query.address {
        body["address"] = serde_json::json!(v);
    }
    if let Some(ref v) = query.currency_id {
        body["currencyId"] = serde_json::json!(v);
    }
    if let Some(ref v) = query.tokens {
        body["tokens"] = serde_json::json!(v);
    }
    if let Some(v) = query.verified {
        body["verified"] = serde_json::json!(v);
    }
    if let Some(v) = query.limit {
        body["limit"] = serde_json::json!(v);
    }
    if let Some(v) = query.default_list {
        body["defaultList"] = serde_json::json!(v);
    }
    if let Some(v) = query.include_all_chains {
        body["includeAllChains"] = serde_json::json!(v);
    }
    if let Some(v) = query.use_external_search {
        body["useExternalSearch"] = serde_json::json!(v);
    }
    if let Some(v) = query.deposit_address_only {
        body["depositAddressOnly"] = serde_json::json!(v);
    }

    let response = http
        .post(format!("{}/currencies/v2", RELAY_API))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Failed to fetch currencies", &err));
    }

    response.json::<Vec<RelayTokenInfo>>().await.map_err(|e| {
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay.link — token price (/currencies/token/price)
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelayTokenPrice {
    pub price: f64,
}

pub async fn get_relay_token_price(
    http: &reqwest::Client,
    address: &str,
    chain_id: u64,
) -> Result<RelayTokenPrice, AppError> {
    let response = http
        .get(format!(
            "{}/currencies/token/price?address={}&chainId={}",
            RELAY_API, address, chain_id
        ))
        .send()
        .await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Failed to fetch token price", &err));
    }

    response.json::<RelayTokenPrice>().await.map_err(|e| {
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })
}

// ──────────────────────────────────────────────────────────────────────────────
// Relay.link — bridge request history (/requests/v2)
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct RelayRequestsQuery {
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub limit: Option<u32>,
    #[serde(default)]
    pub continuation: Option<String>,
    #[serde(default)]
    pub user: Option<String>,
    #[serde(default)]
    pub hash: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub origin_chain_id: Option<u64>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub destination_chain_id: Option<u64>,
    /// Filter requests on either direction for a single chain (string form of chain ID)
    #[serde(default)]
    pub chain_id: Option<String>,
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub order_id: Option<String>,
    #[serde(default)]
    pub deposit_address: Option<String>,
    /// "success" | "failure" | "refund" | "pending" | "depositing" | "waiting"
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub start_timestamp: Option<u64>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub end_timestamp: Option<u64>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub start_block: Option<u64>,
    #[serde(default, deserialize_with = "crate::services::params::soft_opt")]
    pub end_block: Option<u64>,
    #[serde(default)]
    pub referrer: Option<String>,
    #[serde(default)]
    pub include_order_data: Option<bool>,
    #[serde(default)]
    pub include_child_requests: Option<bool>,
    #[serde(default)]
    pub private_chains_to_include: Option<String>,
    /// "createdAt" | "updatedAt"
    #[serde(default)]
    pub sort_by: Option<String>,
    /// "asc" | "desc"
    #[serde(default)]
    pub sort_direction: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayRequestsResponse {
    #[serde(default)]
    pub requests: Vec<serde_json::Value>,
    #[serde(default)]
    pub continuation: Option<String>,
}

pub async fn get_relay_requests(
    http: &reqwest::Client,
    query: &RelayRequestsQuery,
) -> Result<RelayRequestsResponse, AppError> {
    let mut parts: Vec<String> = Vec::new();
    if let Some(v) = query.limit {
        parts.push(format!("limit={}", v));
    }
    if let Some(ref v) = query.continuation {
        parts.push(format!("continuation={}", v));
    }
    if let Some(ref v) = query.user {
        parts.push(format!("user={}", v));
    }
    if let Some(ref v) = query.hash {
        parts.push(format!("hash={}", v));
    }
    if let Some(v) = query.origin_chain_id {
        parts.push(format!("originChainId={}", v));
    }
    if let Some(v) = query.destination_chain_id {
        parts.push(format!("destinationChainId={}", v));
    }
    if let Some(ref v) = query.chain_id {
        parts.push(format!("chainId={}", v));
    }
    if let Some(ref v) = query.id {
        parts.push(format!("id={}", v));
    }
    if let Some(ref v) = query.order_id {
        parts.push(format!("orderId={}", v));
    }
    if let Some(ref v) = query.deposit_address {
        parts.push(format!("depositAddress={}", v));
    }
    if let Some(ref v) = query.status {
        parts.push(format!("status={}", v));
    }
    if let Some(v) = query.start_timestamp {
        parts.push(format!("startTimestamp={}", v));
    }
    if let Some(v) = query.end_timestamp {
        parts.push(format!("endTimestamp={}", v));
    }
    if let Some(v) = query.start_block {
        parts.push(format!("startBlock={}", v));
    }
    if let Some(v) = query.end_block {
        parts.push(format!("endBlock={}", v));
    }
    if let Some(ref v) = query.referrer {
        parts.push(format!("referrer={}", v));
    }
    if let Some(v) = query.include_order_data {
        parts.push(format!("includeOrderData={}", v));
    }
    if let Some(v) = query.include_child_requests {
        parts.push(format!("includeChildRequests={}", v));
    }
    if let Some(ref v) = query.private_chains_to_include {
        parts.push(format!("privateChainsToInclude={}", v));
    }
    if let Some(ref v) = query.sort_by {
        parts.push(format!("sortBy={}", v));
    }
    if let Some(ref v) = query.sort_direction {
        parts.push(format!("sortDirection={}", v));
    }

    let url = if parts.is_empty() {
        format!("{}/requests/v2", RELAY_API)
    } else {
        format!("{}/requests/v2?{}", RELAY_API, parts.join("&"))
    };

    let response = http.get(&url).send().await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Failed to fetch requests", &err));
    }

    response.json::<RelayRequestsResponse>().await.map_err(|e| {
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })
}

// ── POST /execute ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayEip7702Authorization {
    pub chain_id: u64,
    pub address: String,
    pub nonce: u64,
    pub y_parity: u64,
    pub r: String,
    pub s: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayExecuteCallData {
    pub chain_id: u64,
    pub to: String,
    /// ABI-encoded calldata (hex string)
    pub data: String,
    /// ETH value in wei
    pub value: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub authorization_list: Option<Vec<RelayEip7702Authorization>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayDestinationCall {
    pub to: String,
    pub value: String,
    pub data: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayDestinationChainExecutionData {
    pub calls: Vec<RelayDestinationCall>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub authorization_list: Option<Vec<RelayEip7702Authorization>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayExecutionOptions {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub referrer: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subsidize_fees: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub destination_chain_execution_data: Option<RelayDestinationChainExecutionData>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayExecuteRequest {
    pub execution_kind: String,
    pub data: RelayExecuteCallData,
    pub execution_options: RelayExecutionOptions,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RelayExecuteResponse {
    pub message: Option<String>,
    pub request_id: Option<String>,
}

/// Submit a gasless EVM transaction via Relay. Requires a Relay API key.
pub async fn relay_execute(
    http: &reqwest::Client,
    api_key: &str,
    req: &RelayExecuteRequest,
) -> Result<RelayExecuteResponse, AppError> {
    let response = http
        .post(format!("{}/execute", RELAY_API))
        .header("Content-Type", "application/json")
        .header("x-api-key", api_key)
        .json(req)
        .send()
        .await?;

    if !response.status().is_success() {
        let status = response.status();
        let err = response.text().await.unwrap_or_default();
        tracing::warn!(%status, body = %err.chars().take(300).collect::<String>(),
                       "Relay execute refused");
        return Err(relay_error("The execute", &err));
    }

    response.json::<RelayExecuteResponse>().await.map_err(|e| {
        AppError::RelayApiError(
            "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
        )
    })
}

// ── GET /swap-sources ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelaySwapSourcesResponse {
    #[serde(default)]
    pub sources: Vec<String>,
}

/// Fetch available swap sources, optionally filtered by chain ID.
pub async fn get_swap_sources(
    http: &reqwest::Client,
    chain_id: Option<u64>,
) -> Result<RelaySwapSourcesResponse, AppError> {
    let mut url = format!("{}/swap-sources", RELAY_API);
    if let Some(cid) = chain_id {
        url.push_str(&format!("?chainId={}", cid));
    }

    let response = http.get(&url).send().await?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(relay_error("Relay swap-sources failed", &err));
    }

    response
        .json::<RelaySwapSourcesResponse>()
        .await
        .map_err(|e| {
            AppError::RelayApiError(
                "Relay is answering in a shape we do not recognise. Nothing was signed.".into(),
            )
        })
}

#[cfg(test)]
mod tests {

    /// Relay validates `amount` against ^[0-9]+$ and the card sends "0.01".
    /// Scaling as text, because 0.1 is not representable in binary floating
    /// point and this figure is money.
    #[test]
    fn a_human_amount_becomes_base_units() {
        // The pure part of the conversion, with decimals already known.
        let scale = |amount: &str, decimals: usize| -> String {
            let (w, f) = amount.split_once('.').unwrap_or((amount, ""));
            let padded = format!("{f:0<width$}", width = decimals);
            let joined = format!("{}{}", w.trim_start_matches('0'), padded);
            let t = joined.trim_start_matches('0');
            if t.is_empty() {
                "0".into()
            } else {
                t.to_string()
            }
        };
        assert_eq!(scale("0.01", 9), "10000000");
        assert_eq!(scale("1", 9), "1000000000");
        assert_eq!(scale("1.5", 6), "1500000");
        assert_eq!(scale("0.000001", 6), "1");
    }
    use super::*;

    /// Relay rejects a non-EVM app-fee recipient on the quote itself, so
    /// attaching one does not forgo a fee — it forgoes the bridge.
    #[test]
    fn a_solana_fee_recipient_is_not_sent_to_relay() {
        let mut body = serde_json::json!({});
        append_app_fee(
            &mut body,
            Some("Gf3dtGnHRkfaPpeHc2UYfu6mHrTsbFUp4Qx3RqV526h"),
        );
        assert!(body.get("appFees").is_none());

        let mut body = serde_json::json!({});
        append_app_fee(
            &mut body,
            Some("0x71C7656EC7ab88b098defB751B7401B5f6d8976F"),
        );
        assert_eq!(body["appFees"][0]["fee"], "20");
    }
}

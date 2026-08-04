use base64::Engine;
use serde::{Deserialize, Serialize};
use solana_sdk::{pubkey::Pubkey, transaction::Transaction};
use uuid::Uuid;

use crate::error::AppError;
use crate::services::{
    burn, dca, debridge, helius, jito, jupiter_lend, jupiter_perp, jupiter_query, jupsol, kamino,
    limit_order, magic_eden, marginfi, marinade, meteora, native_stake, orca, protocol_reads,
    pumpfun, raydium, relay, sns, solend, squid, streamflow, swap, tensor, token_safety,
    transfer,
};
use crate::solana::connection::SolanaRpc;

// ──────────────────────────────────────────────────────────────────────────────
// Build request / response types
// ──────────────────────────────────────────────────────────────────────────────

/// Incoming build request body from the client.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildRequest {
    #[serde(rename = "type")]
    pub action_type: String,
    pub params: serde_json::Value,
}

/// Unified preview returned for any action type.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ActionPreview {
    pub id: String,
    #[serde(rename = "type")]
    pub action_type: String,
    pub description: String,
    pub estimated_fee: String,
    /// Estimated SOL refund from rent reclamation (burn/close_accounts only).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub estimated_refund: Option<String>,
    pub params: serde_json::Value,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
    pub requires_approval: bool,
}

/// The response returned by POST /actions/build.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BuildResponse {
    pub preview: ActionPreview,
    /// Base64-encoded serialised transaction (for Solana actions).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub transaction: Option<String>,
    /// Additional signers required (for Solana actions).
    #[serde(default)]
    pub additional_signers_required: usize,
    /// Execution steps from Relay (for cross-chain actions).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_steps: Option<serde_json::Value>,
    /// Full quote data (for cross-chain actions).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quote: Option<serde_json::Value>,
    /// Whether this is a cross-chain action.
    #[serde(default)]
    pub is_cross_chain: bool,
    /// Query result data for read-only actions (GET queries that fetch on-chain
    /// or API data without building a transaction). Present when transaction is None.
    /// For batch actions (streamflow_create_multiple), data.transactions contains
    /// the array of base64-encoded transactions.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Orchestrator
// ──────────────────────────────────────────────────────────────────────────────

/// Validate that the action type is known and the params decode correctly.
pub fn validate_action(action_type: &str, params: &serde_json::Value) -> Result<(), AppError> {
    match action_type {
        "transfer" => {
            let p: transfer::TransferParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid transfer params: {e}")))?;
            transfer::validate_transfer_params(&p)
        }
        "swap" => {
            let p: swap::SwapParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid swap params: {e}")))?;
            swap::validate_swap_params(&p)
        }
        "launch_token" | "pumpfun_launch" => {
            let p: pumpfun::LaunchTokenParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid launch_token params: {e}"))
                })?;
            pumpfun::validate_launch_params(&p)
        }
        "stake" | "unstake" => {
            // Basic validation: amount must parse.
            let p: marinade::StakeParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid stake params: {e}")))?;
            let amount: f64 = p
                .amount
                .parse()
                .map_err(|_| AppError::InvalidParams("Amount must be a positive number".into()))?;
            if amount <= 0.0 {
                return Err(AppError::InvalidParams(
                    "Amount must be a positive number".into(),
                ));
            }
            Ok(())
        }
        "cross_chain_swap" | "bridge" => {
            // bridge is an alias for cross_chain_swap
            let p: relay::CrossChainSwapParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid cross_chain_swap params: {e}"))
                })?;
            relay::validate_cross_chain_params(&p)
        }
        "debridge" => {
            let p: debridge::DebridgeParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid debridge params: {e}")))?;
            debridge::validate_debridge_params(&p)
        }
        "squid" | "squid_bridge" => {
            let p: squid::SquidParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid squid params: {e}")))?;
            squid::validate_squid_params(&p)
        }
        "cross_chain_quote" => {
            let _p: squid::SquidQuoteParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid cross_chain_quote params: {e}"))
                })?;
            Ok(())
        }
        "cross_chain_chains" => Ok(()),
        "cross_chain_tokens" => Ok(()),
        // ── SNS — Solana Name Service ──────────────────────────────────────────────
        "sns_resolve" => {
            let _p: sns::SnsResolveParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid sns_resolve params: {e}")))?;
            sns::validate_domain_name(params.get("domain").and_then(|v| v.as_str()).unwrap_or(""))
        }
        "sns_reverse_lookup" => {
            let _p: sns::SnsReverseParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_reverse_lookup params: {e}"))
                })?;
            Ok(())
        }
        "sns_domains" => {
            let _p: sns::SnsDomainsParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid sns_domains params: {e}")))?;
            Ok(())
        }
        "sns_record" => {
            let _p: sns::SnsRecordParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid sns_record params: {e}")))?;
            Ok(())
        }
        "sns_domain_info" => {
            let _p: sns::SnsDomainInfoParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_domain_info params: {e}"))
                })?;
            sns::validate_domain_name(params.get("domain").and_then(|v| v.as_str()).unwrap_or(""))
        }
        "sns_check_available" => {
            let _p: sns::SnsCheckAvailableParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_check_available params: {e}"))
                })?;
            sns::validate_domain_name(params.get("domain").and_then(|v| v.as_str()).unwrap_or(""))
        }
        "sns_primary_domain" => {
            let _p: sns::SnsPrimaryDomainParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_primary_domain params: {e}"))
                })?;
            Ok(())
        }
        "sns_register" => {
            let p: sns::SnsRegisterParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_register params: {e}"))
                })?;
            sns::validate_domain_name(&p.domain)
        }
        "sns_transfer" => {
            let p: sns::SnsTransferParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_transfer params: {e}"))
                })?;
            sns::validate_domain_name(&p.domain)
        }
        "sns_set_record" => {
            let p: sns::SnsSetRecordParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_set_record params: {e}"))
                })?;
            if p.record.is_empty() {
                return Err(AppError::InvalidParams("record type is required".into()));
            }
            if p.value.is_empty() {
                return Err(AppError::InvalidParams("record value is required".into()));
            }
            sns::validate_domain_name(&p.domain)
        }
        "sns_delete" => {
            let p: sns::SnsDeleteParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid sns_delete params: {e}")))?;
            sns::validate_domain_name(&p.domain)
        }
        "sns_create_subdomain" => {
            let _p: sns::SnsCreateSubdomainParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_create_subdomain params: {e}"))
                })?;
            Ok(())
        }
        "sns_list" => {
            let p: sns::SnsListParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid sns_list params: {e}")))?;
            if p.price <= 0.0 {
                return Err(AppError::InvalidParams("price must be positive".into()));
            }
            sns::validate_domain_name(&p.domain)
        }
        "sns_buy" => {
            let p: sns::SnsBuyParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid sns_buy params: {e}")))?;
            sns::validate_domain_name(&p.domain)
        }
        "sns_make_offer" => {
            let p: sns::SnsMakeOfferParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_make_offer params: {e}"))
                })?;
            if p.amount <= 0.0 {
                return Err(AppError::InvalidParams("amount must be positive".into()));
            }
            sns::validate_domain_name(&p.domain)
        }
        "sns_accept_offer" => {
            let p: sns::SnsAcceptOfferParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_accept_offer params: {e}"))
                })?;
            if p.offer_key.is_empty() {
                return Err(AppError::InvalidParams("offer_key is required".into()));
            }
            sns::validate_domain_name(&p.domain)
        }
        "sns_cancel_offer" => {
            let p: sns::SnsCancelOfferParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_cancel_offer params: {e}"))
                })?;
            if p.offer_key.is_empty() {
                return Err(AppError::InvalidParams("offer_key is required".into()));
            }
            sns::validate_domain_name(&p.domain)
        }
        "sns_p2p_create" => {
            let p: sns::SnsP2pCreateParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_p2p_create params: {e}"))
                })?;
            if p.base_domains.is_empty() {
                return Err(AppError::InvalidParams("base_domains is required".into()));
            }
            if p.counter_party.is_empty() {
                return Err(AppError::InvalidParams("counter_party is required".into()));
            }
            Ok(())
        }
        "sns_p2p_accept" | "sns_p2p_cancel" => Ok(()),
        "sns_set_favorite" => {
            let p: sns::SnsSetFavoriteParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_set_favorite params: {e}"))
                })?;
            sns::validate_domain_name(&p.domain)
        }
        "sns_subdomains" => {
            let p: sns::SnsSubdomainsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_subdomains params: {e}"))
                })?;
            sns::validate_domain_name(&p.parent_domain)
        }
        "sns_realloc" => {
            let p: sns::SnsReallocParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid sns_realloc params: {e}")))?;
            if p.new_space > 10_240 {
                return Err(AppError::InvalidParams(
                    "new_space cannot exceed 10240".into(),
                ));
            }
            sns::validate_domain_name(&p.domain)
        }
        "sns_transfer_subdomain" => {
            let p: sns::SnsTransferSubdomainParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_transfer_subdomain params: {e}"))
                })?;
            if !p.subdomain.contains('.') {
                return Err(AppError::InvalidParams(
                    "subdomain must contain a dot (e.g. sub.parent)".into(),
                ));
            }
            if p.new_owner.is_empty() {
                return Err(AppError::InvalidParams("new_owner is required".into()));
            }
            Ok(())
        }
        "sns_create_record" => {
            let p: sns::SnsCreateRecordParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_create_record params: {e}"))
                })?;
            if p.record.is_empty() {
                return Err(AppError::InvalidParams("record type is required".into()));
            }
            sns::validate_domain_name(&p.domain)
        }
        "sns_domain_key" => {
            let p: sns::SnsDomainKeyParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_domain_key params: {e}"))
                })?;
            sns::validate_domain_name(&p.domain)
        }
        "sns_record_key" => {
            let p: sns::SnsRecordKeyParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_record_key params: {e}"))
                })?;
            if p.record.is_empty() {
                return Err(AppError::InvalidParams("record type is required".into()));
            }
            sns::validate_domain_name(&p.domain)
        }
        "sns_twitter_handle" => {
            let p: sns::SnsTwitterHandleParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid sns_twitter_handle params: {e}"))
                })?;
            if p.input.is_empty() {
                return Err(AppError::InvalidParams("input is required".into()));
            }
            Ok(())
        }
        "limit_order" => {
            let p: limit_order::LimitOrderParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid limit_order params: {e}")))?;
            limit_order::validate_limit_order_params(&p)
        }
        "cancel_limit_order" => {
            let _p: limit_order::CancelLimitOrderParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid cancel_limit_order params: {e}"))
                })?;
            Ok(())
        }
        "cancel_all_limit_orders" => Ok(()),
        "dca" => {
            let p: dca::DcaParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid dca params: {e}")))?;
            dca::validate_dca_params(&p)
        }
        "cancel_dca" => {
            let _p: dca::CancelDcaParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid cancel_dca params: {e}")))?;
            Ok(())
        }
        "jup_dca_orders" => {
            let p: dca::JupDcaOrdersParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_dca_orders params: {e}"))
                })?;
            dca::validate_jup_dca_orders_params(&p)
        }
        "jup_limit_orders" => {
            let p: limit_order::JupLimitOrdersParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_limit_orders params: {e}"))
                })?;
            limit_order::validate_jup_limit_orders_params(&p)
        }
        "jup_price" => {
            let p: jupiter_query::JupPriceParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid jup_price params: {e}")))?;
            jupiter_query::validate_jup_price_params(&p)
        }
        "jup_token_search" => {
            let p: jupiter_query::JupTokenSearchParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_token_search params: {e}"))
                })?;
            jupiter_query::validate_jup_token_search_params(&p)
        }
        "jup_tokens_tag" => {
            let p: jupiter_query::JupTokensTagParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_tokens_tag params: {e}"))
                })?;
            jupiter_query::validate_jup_tokens_tag_params(&p)
        }
        "jup_tokens_recent" => {
            let p: jupiter_query::JupTokensRecentParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_tokens_recent params: {e}"))
                })?;
            jupiter_query::validate_jup_tokens_recent_params(&p)
        }
        "jup_tokens_trending" => {
            let p: jupiter_query::JupTokensTrendingParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_tokens_trending params: {e}"))
                })?;
            jupiter_query::validate_jup_tokens_trending_params(&p)
        }
        "jup_portfolio_positions" => {
            let p: jupiter_query::JupPortfolioPositionsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_portfolio_positions params: {e}"))
                })?;
            jupiter_query::validate_jup_portfolio_positions_params(&p)
        }
        "jup_staked_jup" => {
            let p: jupiter_query::JupStakedJupParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_staked_jup params: {e}"))
                })?;
            jupiter_query::validate_jup_staked_jup_params(&p)
        }
        "jup_lend_positions" => {
            let p: jupiter_query::JupLendPositionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid jup_lend_positions params: {e}"))
            })?;
            jupiter_query::validate_jup_lend_positions_params(&p)
        }
        "jup_lend_earnings" => {
            let p: jupiter_query::JupLendEarningsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_lend_earnings params: {e}"))
                })?;
            jupiter_query::validate_jup_lend_earnings_params(&p)
        }
        "jup_pending_invites" => {
            let p: jupiter_query::JupPendingInvitesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_pending_invites params: {e}"))
                })?;
            jupiter_query::validate_jup_pending_invites_params(&p)
        }
        "jup_platforms" => {
            let p: jupiter_query::JupPlatformsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_platforms params: {e}"))
                })?;
            jupiter_query::validate_jup_platforms_params(&p)
        }
        "helius_tx_history" => {
            let p: helius::HeliusTxHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_tx_history params: {e}"))
                })?;
            helius::validate_helius_tx_history_params(&p)
        }
        "helius_parse_transactions" => {
            let p: helius::HeliusParseTransactionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid helius_parse_transactions params: {e}"))
            })?;
            helius::validate_helius_parse_transactions_params(&p)
        }
        "helius_get_assets" => {
            let p: helius::HeliusGetAssetsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_get_assets params: {e}"))
                })?;
            helius::validate_helius_get_assets_params(&p)
        }
        "helius_get_asset" => {
            let p: helius::HeliusGetAssetParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_get_asset params: {e}"))
                })?;
            helius::validate_helius_get_asset_params(&p)
        }
        "helius_search_assets" => {
            let p: helius::HeliusSearchAssetsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_search_assets params: {e}"))
                })?;
            helius::validate_helius_search_assets_params(&p)
        }
        "helius_nft_editions" => {
            let p: helius::HeliusNftEditionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_nft_editions params: {e}"))
                })?;
            helius::validate_helius_nft_editions_params(&p)
        }
        "helius_get_token_accounts" => {
            let p: helius::HeliusGetTokenAccountsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_get_token_accounts params: {e}"
                    ))
                })?;
            helius::validate_helius_get_token_accounts_params(&p)
        }
        "helius_asset_signatures" => {
            let p: helius::HeliusAssetSignaturesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_asset_signatures params: {e}"))
                })?;
            helius::validate_helius_asset_signatures_params(&p)
        }
        "helius_priority_fee" => {
            let p: helius::HeliusPriorityFeeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_priority_fee params: {e}"))
                })?;
            helius::validate_helius_priority_fee_params(&p)
        }
        "helius_wallet_identity" => {
            let p: helius::HeliusWalletIdentityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_wallet_identity params: {e}"))
                })?;
            helius::validate_helius_wallet_identity_params(&p)
        }
        "helius_batch_identity" => {
            let p: helius::HeliusBatchIdentityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_batch_identity params: {e}"))
                })?;
            helius::validate_helius_batch_identity_params(&p)
        }
        "helius_wallet_balances" => {
            let p: helius::HeliusWalletBalancesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_wallet_balances params: {e}"))
                })?;
            helius::validate_helius_wallet_balances_params(&p)
        }
        "helius_wallet_history" => {
            let p: helius::HeliusWalletHistoryParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_wallet_history params: {e}"))
                })?;
            helius::validate_helius_wallet_history_params(&p)
        }
        "helius_wallet_transfers" => {
            let p: helius::HeliusWalletTransfersParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_wallet_transfers params: {e}"))
                })?;
            helius::validate_helius_wallet_transfers_params(&p)
        }
        "helius_wallet_funded_by" => {
            let p: helius::HeliusWalletFundedByParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_wallet_funded_by params: {e}"))
                })?;
            helius::validate_helius_wallet_funded_by_params(&p)
        }
        "helius_get_asset_batch" => {
            let p: helius::HeliusGetAssetBatchParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_get_asset_batch params: {e}"))
                })?;
            helius::validate_helius_get_asset_batch_params(&p)
        }
        "helius_get_assets_by_creator" => {
            let p: helius::HeliusGetAssetsByCreatorParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_get_assets_by_creator params: {e}"
                    ))
                })?;
            helius::validate_helius_get_assets_by_creator_params(&p)
        }
        "helius_get_assets_by_authority" => {
            let p: helius::HeliusGetAssetsByAuthorityParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_get_assets_by_authority params: {e}"
                    ))
                })?;
            helius::validate_helius_get_assets_by_authority_params(&p)
        }
        "helius_get_assets_by_group" => {
            let p: helius::HeliusGetAssetsByGroupParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_get_assets_by_group params: {e}"
                    ))
                })?;
            helius::validate_helius_get_assets_by_group_params(&p)
        }
        "helius_get_asset_proof" => {
            let p: helius::HeliusGetAssetProofParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_get_asset_proof params: {e}"))
                })?;
            helius::validate_helius_get_asset_proof_params(&p)
        }
        "helius_get_asset_proof_batch" => {
            let p: helius::HeliusGetAssetProofBatchParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_get_asset_proof_batch params: {e}"
                    ))
                })?;
            helius::validate_helius_get_asset_proof_batch_params(&p)
        }
        "helius_create_webhook" => {
            let p: helius::HeliusCreateWebhookParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_create_webhook params: {e}"))
                })?;
            helius::validate_helius_create_webhook_params(&p)
        }
        "helius_list_webhooks" => {
            let p: helius::HeliusListWebhooksParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_list_webhooks params: {e}"))
                })?;
            helius::validate_helius_list_webhooks_params(&p)
        }
        "helius_get_webhook" => {
            let p: helius::HeliusGetWebhookParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_get_webhook params: {e}"))
                })?;
            helius::validate_helius_get_webhook_params(&p)
        }
        "helius_update_webhook" => {
            let p: helius::HeliusUpdateWebhookParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_update_webhook params: {e}"))
                })?;
            helius::validate_helius_update_webhook_params(&p)
        }
        "helius_toggle_webhook" => {
            let p: helius::HeliusToggleWebhookParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_toggle_webhook params: {e}"))
                })?;
            helius::validate_helius_toggle_webhook_params(&p)
        }
        "helius_delete_webhook" => {
            let p: helius::HeliusDeleteWebhookParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_delete_webhook params: {e}"))
                })?;
            helius::validate_helius_delete_webhook_params(&p)
        }
        "helius_send_transaction" => {
            let p: helius::HeliusSendTransactionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_send_transaction params: {e}"))
                })?;
            helius::validate_helius_send_transaction_params(&p)
        }
        "helius_zk_compressed_account" => {
            let p: helius::HeliusZkAccountParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_compressed_account params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_account_params(&p)
        }
        "helius_zk_multiple_compressed_accounts" => {
            let p: helius::HeliusZkMultipleAccountsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_multiple_compressed_accounts params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_multiple_accounts_params(&p)
        }
        "helius_zk_compressed_balance_by_owner" => {
            let p: helius::HeliusZkOwnerParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_compressed_balance_by_owner params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_owner_params(&p)
        }
        "helius_zk_token_accounts_by_owner" => {
            let p: helius::HeliusZkOwnerParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_token_accounts_by_owner params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_owner_params(&p)
        }
        "helius_zk_token_balances_by_owner" => {
            let p: helius::HeliusZkOwnerParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_token_balances_by_owner params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_owner_params(&p)
        }
        "helius_zk_mint_token_holders" => {
            let p: helius::HeliusZkMintParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_mint_token_holders params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_mint_params(&p)
        }
        "helius_zk_compression_signatures_for_owner" => {
            let p: helius::HeliusZkSignaturesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_compression_signatures_for_owner params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_signatures_params(&p)
        }
        "helius_zk_transaction_with_compression" => {
            let p: helius::HeliusZkSignaturesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_transaction_with_compression params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_signatures_params(&p)
        }
        "helius_zk_indexer_health" => {
            let p: helius::HeliusZkIndexerParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_zk_indexer_health params: {e}"))
                })?;
            helius::validate_helius_zk_indexer_params(&p)
        }
        "helius_zk_indexer_slot" => {
            let p: helius::HeliusZkIndexerParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_zk_indexer_slot params: {e}"))
                })?;
            helius::validate_helius_zk_indexer_params(&p)
        }
        "helius_zk_validity_proof" => {
            let p: helius::HeliusZkAccountParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_zk_validity_proof params: {e}"))
                })?;
            helius::validate_helius_zk_account_params(&p)
        }
        "helius_zk_new_address_proofs" => {
            let p: helius::HeliusZkNewAddressProofsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid helius_zk_new_address_proofs params: {e}"
                    ))
                })?;
            helius::validate_helius_zk_new_address_proofs_params(&p)
        }
        "helius_smart_send" => {
            let p: helius::HeliusSmartSendParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid helius_smart_send params: {e}"))
                })?;
            helius::validate_helius_smart_send_params(&p)
        }
        "jupsol_stake" => {
            let p: jupsol::JupSolParams = serde_json::from_value(params.clone()).map_err(|e| {
                AppError::InvalidParams(format!("Invalid jupsol_stake params: {e}"))
            })?;
            jupsol::validate_jupsol_params(&p)
        }
        "jupsol_unstake" => {
            let p: jupsol::JupSolParams = serde_json::from_value(params.clone()).map_err(|e| {
                AppError::InvalidParams(format!("Invalid jupsol_unstake params: {e}"))
            })?;
            jupsol::validate_jupsol_params(&p)
        }
        // Generic lend — routes by protocol field (default: jupiter)
        "lend" | "withdraw_lend" => {
            let protocol = params
                .get("protocol")
                .and_then(|v| v.as_str())
                .unwrap_or("jupiter");
            match protocol {
                "marginfi" => {
                    let p: marginfi::MarginfiDepositParams = serde_json::from_value(params.clone())
                        .map_err(|e| {
                            AppError::InvalidParams(format!("Invalid marginfi lend params: {e}"))
                        })?;
                    marginfi::validate_marginfi_deposit_params(&p)
                }
                "solend" => {
                    let p: solend::SolendDepositParams = serde_json::from_value(params.clone())
                        .map_err(|e| {
                            AppError::InvalidParams(format!("Invalid solend lend params: {e}"))
                        })?;
                    solend::validate_solend_deposit_params(&p)
                }
                "kamino" => Err(AppError::InvalidParams(
                    "Kamino requires a reserve address — use kamino_deposit instead".into(),
                )),
                _ => {
                    let p: jupiter_lend::JupiterLendParams = serde_json::from_value(params.clone())
                        .map_err(|e| {
                            AppError::InvalidParams(format!("Invalid lend params: {e}"))
                        })?;
                    jupiter_lend::validate_lend_params(&p)
                }
            }
        }
        "borrow" | "repay" => {
            let protocol = params
                .get("protocol")
                .and_then(|v| v.as_str())
                .unwrap_or("jupiter");
            match protocol {
                "marginfi" => {
                    let p: marginfi::MarginfiBorrowParams = serde_json::from_value(params.clone())
                        .map_err(|e| {
                            AppError::InvalidParams(format!("Invalid marginfi borrow params: {e}"))
                        })?;
                    marginfi::validate_marginfi_borrow_params(&p)
                }
                "solend" => {
                    let p: solend::SolendBorrowParams = serde_json::from_value(params.clone())
                        .map_err(|e| {
                            AppError::InvalidParams(format!("Invalid solend borrow params: {e}"))
                        })?;
                    solend::validate_solend_borrow_params(&p)
                }
                "kamino" => Err(AppError::InvalidParams(
                    "Kamino requires a reserve address — use kamino_borrow instead".into(),
                )),
                _ => {
                    let p: jupiter_lend::JupiterBorrowParams =
                        serde_json::from_value(params.clone()).map_err(|e| {
                            AppError::InvalidParams(format!("Invalid borrow params: {e}"))
                        })?;
                    jupiter_lend::validate_borrow_params(&p)
                }
            }
        }
        "jup_lend_markets" => {
            let p: jupiter_lend::JupLendMarketsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jup_lend_markets params: {e}"))
                })?;
            jupiter_lend::validate_jup_lend_markets_params(&p)
        }
        // Jupiter Perp
        "perp_open" | "perp_close" => {
            let mut p_val = params.clone();
            // Inject operation from action_type so frontend doesn't need to send it
            p_val["operation"] = serde_json::json!(if action_type == "perp_open" {
                "open"
            } else {
                "close"
            });
            let p: jupiter_perp::JupiterPerpParams = serde_json::from_value(p_val)
                .map_err(|e| AppError::InvalidParams(format!("Invalid perp params: {e}")))?;
            jupiter_perp::validate_perp_params(&p)
        }
        "perp_positions" => Ok(()),
        "jlp_add" | "jlp_remove" => {
            let mut p_val = params.clone();
            p_val["operation"] = serde_json::json!(if action_type == "jlp_add" {
                "add"
            } else {
                "remove"
            });
            let p: jupiter_perp::JupiterPerpLiquidityParams = serde_json::from_value(p_val)
                .map_err(|e| AppError::InvalidParams(format!("Invalid jlp params: {e}")))?;
            jupiter_perp::validate_liquidity_params(&p)
        }
        "pumpfun_buy" | "pumpfun_sell" | "pumpfun_initial_buy" => {
            let p: pumpfun::PumpFunTradeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid pumpfun trade params: {e}"))
                })?;
            pumpfun::validate_pumpfun_trade_params(&p)
        }
        "pumpfun_token_info"
        | "pumpfun_comments"
        | "pumpfun_bonding_curve"
        | "pumpswap_pool_info" => {
            let p: pumpfun::PumpFunMintParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid params: {e}")))?;
            pumpfun::validate_pumpfun_mint_params(&p)
        }
        "pumpfun_new"
        | "pumpfun_graduating"
        | "pumpfun_koth"
        | "pumpfun_trending"
        | "pumpfun_curve_global" => Ok(()),
        "pumpfun_search" => {
            let p: pumpfun::PumpFunSearchParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid pumpfun search params: {e}"))
                })?;
            if p.query.as_deref().unwrap_or("").trim().is_empty() {
                return Err(AppError::InvalidParams("search query is required".into()));
            }
            Ok(())
        }
        "pumpfun_user" => Ok(()),
        "relay_bridge" | "relay_get_quote" => {
            let p: relay::RelayBridgeParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid relay params: {e}")))?;
            if p.origin_chain_id == p.destination_chain_id {
                return Err(AppError::InvalidParams(
                    "relay_bridge: origin and destination chain must differ".into(),
                ));
            }
            if p.amount.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "relay_bridge: amount is required".into(),
                ));
            }
            Ok(())
        }
        "relay_get_chains"
        | "relay_get_currencies"
        | "relay_get_token_price"
        | "relay_get_requests"
        | "relay_get_chains_liquidity"
        | "relay_get_app_fee_balances"
        | "relay_get_swap_sources" => Ok(()),
        "relay_intent_status" => {
            let id = params
                .get("requestId")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if id.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "requestId is required for relay_intent_status".into(),
                ));
            }
            Ok(())
        }
        "squid_status" => {
            let tx_id = params
                .get("transactionId")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let quote_id = params.get("quoteId").and_then(|v| v.as_str()).unwrap_or("");
            if tx_id.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "transactionId is required for squid_status".into(),
                ));
            }
            if quote_id.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "quoteId is required for squid_status".into(),
                ));
            }
            Ok(())
        }
        "relay_index_transaction" => {
            let chain_id = params.get("chainId").and_then(|v| v.as_str()).unwrap_or("");
            let tx_hash = params.get("txHash").and_then(|v| v.as_str()).unwrap_or("");
            if chain_id.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "chainId is required for relay_index_transaction".into(),
                ));
            }
            if tx_hash.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "txHash is required for relay_index_transaction".into(),
                ));
            }
            Ok(())
        }
        "relay_single_transaction" => {
            let request_id = params
                .get("requestId")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let chain_id = params.get("chainId").and_then(|v| v.as_str()).unwrap_or("");
            let tx = params.get("tx").and_then(|v| v.as_str()).unwrap_or("");
            if request_id.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "requestId is required for relay_single_transaction".into(),
                ));
            }
            if chain_id.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "chainId is required for relay_single_transaction".into(),
                ));
            }
            if tx.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "tx is required for relay_single_transaction".into(),
                ));
            }
            Ok(())
        }
        "relay_deposit_address_reindex" => {
            let deposit_address = params
                .get("depositAddress")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let chain_id = params.get("chainId").and_then(|v| v.as_u64());
            if deposit_address.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "depositAddress is required for relay_deposit_address_reindex".into(),
                ));
            }
            if chain_id.is_none() {
                return Err(AppError::InvalidParams(
                    "chainId is required for relay_deposit_address_reindex".into(),
                ));
            }
            Ok(())
        }
        "relay_claim_app_fees" => {
            let currency = params
                .get("currency")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let recipient = params
                .get("recipient")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let chain_id = params.get("chainId").and_then(|v| v.as_u64());
            if chain_id.is_none() {
                return Err(AppError::InvalidParams(
                    "chainId is required for relay_claim_app_fees".into(),
                ));
            }
            if currency.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "currency is required for relay_claim_app_fees".into(),
                ));
            }
            if recipient.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "recipient is required for relay_claim_app_fees".into(),
                ));
            }
            Ok(())
        }
        "relay_fast_fill" => {
            let request_id = params
                .get("requestId")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if request_id.trim().is_empty() {
                return Err(AppError::InvalidParams(
                    "requestId is required for relay_fast_fill".into(),
                ));
            }
            Ok(())
        }
        "relay_execute" => {
            let data = params.get("data");
            let execution_options = params.get("executionOptions");
            if data.is_none() {
                return Err(AppError::InvalidParams(
                    "data is required for relay_execute".into(),
                ));
            }
            if execution_options.is_none() {
                return Err(AppError::InvalidParams(
                    "executionOptions is required for relay_execute".into(),
                ));
            }
            Ok(())
        }
        "pumpswap_buy" | "pumpswap_sell" => {
            let p: pumpfun::PumpFunTradeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid pumpswap trade params: {e}"))
                })?;
            pumpfun::validate_pumpfun_trade_params(&p)
        }
        "raydium_swap" => {
            let p: raydium::RaydiumSwapParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_swap params: {e}"))
                })?;
            raydium::validate_raydium_swap_params(&p)
        }
        "raydium_add_liquidity" => {
            let p: raydium::RaydiumAddLiquidityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_add_liquidity params: {e}"))
                })?;
            raydium::validate_raydium_add_liquidity_params(&p)
        }
        "raydium_remove_liquidity" => {
            let p: raydium::RaydiumRemoveLiquidityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid raydium_remove_liquidity params: {e}"))
            })?;
            raydium::validate_raydium_remove_liquidity_params(&p)
        }
        "raydium_create_pool" => {
            let p: raydium::RaydiumCreatePoolParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_create_pool params: {e}"))
                })?;
            raydium::validate_raydium_create_pool_params(&p)
        }
        "raydium_open_position" => {
            let p: raydium::RaydiumOpenPositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_open_position params: {e}"))
                })?;
            raydium::validate_raydium_open_position_params(&p)
        }
        "raydium_close_position" => {
            let p: raydium::RaydiumClosePositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_close_position params: {e}"))
                })?;
            raydium::validate_raydium_close_position_params(&p)
        }
        "raydium_increase_position" => {
            let p: raydium::RaydiumIncreasePositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid raydium_increase_position params: {e}"
                    ))
                })?;
            raydium::validate_raydium_increase_position_params(&p)
        }
        "raydium_decrease_position" => {
            let p: raydium::RaydiumDecreasePositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid raydium_decrease_position params: {e}"
                    ))
                })?;
            raydium::validate_raydium_decrease_position_params(&p)
        }
        "raydium_get_pools" => {
            let p: raydium::RaydiumGetPoolsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_pools params: {e}"))
                })?;
            raydium::validate_raydium_get_pools_params(&p)
        }
        "raydium_search_pools" => {
            let p: raydium::RaydiumSearchPoolsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_search_pools params: {e}"))
                })?;
            raydium::validate_raydium_search_pools_params(&p)
        }
        "raydium_get_pool_info" => {
            let p: raydium::RaydiumGetPoolInfoParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_pool_info params: {e}"))
                })?;
            raydium::validate_raydium_get_pool_info_params(&p)
        }
        "raydium_get_user_positions" => {
            let p: raydium::RaydiumGetUserPositionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid raydium_get_user_positions params: {e}"
                    ))
                })?;
            raydium::validate_raydium_get_user_positions_params(&p)
        }
        "raydium_get_clmm_positions" => {
            let p: raydium::RaydiumGetClmmPositionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid raydium_get_clmm_positions params: {e}"
                    ))
                })?;
            raydium::validate_raydium_get_clmm_positions_params(&p)
        }
        "raydium_get_token_info" => {
            let p: raydium::RaydiumGetTokenInfoParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_token_info params: {e}"))
                })?;
            raydium::validate_raydium_get_token_info_params(&p)
        }
        "raydium_get_platform_stats" => {
            let p: raydium::RaydiumGetPlatformStatsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid raydium_get_platform_stats params: {e}"
                    ))
                })?;
            raydium::validate_raydium_get_platform_stats_params(&p)
        }
        "raydium_get_clmm_configs" => {
            let p: raydium::RaydiumGetClmmConfigsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_clmm_configs params: {e}"))
                })?;
            raydium::validate_raydium_get_clmm_configs_params(&p)
        }
        "raydium_swap_quote" => {
            let p: raydium::RaydiumSwapQuoteParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_swap_quote params: {e}"))
                })?;
            raydium::validate_raydium_swap_quote_params(&p)
        }
        "raydium_get_pools_by_lp" => {
            let p: raydium::RaydiumGetPoolsByLpParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_pools_by_lp params: {e}"))
                })?;
            raydium::validate_raydium_get_pools_by_lp_params(&p)
        }
        "raydium_get_pools_v2" => {
            let p: raydium::RaydiumGetPoolsV2Params = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_pools_v2 params: {e}"))
                })?;
            raydium::validate_raydium_get_pools_v2_params(&p)
        }
        "raydium_get_pool_keys" => {
            let p: raydium::RaydiumGetPoolKeysParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_pool_keys params: {e}"))
                })?;
            raydium::validate_raydium_get_pool_keys_params(&p)
        }
        "raydium_get_pool_liquidity_history" => {
            let p: raydium::RaydiumGetPoolLiquidityHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid raydium_get_pool_liquidity_history params: {e}"
                    ))
                })?;
            raydium::validate_raydium_get_pool_liquidity_history_params(&p)
        }
        "raydium_get_pool_position_history" => {
            let p: raydium::RaydiumGetPoolPositionHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid raydium_get_pool_position_history params: {e}"
                    ))
                })?;
            raydium::validate_raydium_get_pool_position_history_params(&p)
        }
        "raydium_get_token_list" => {
            let p: raydium::RaydiumGetTokenListParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_token_list params: {e}"))
                })?;
            raydium::validate_raydium_get_token_list_params(&p)
        }
        "raydium_get_token_prices" => {
            let p: raydium::RaydiumGetTokenPricesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_token_prices params: {e}"))
                })?;
            raydium::validate_raydium_get_token_prices_params(&p)
        }
        "raydium_get_farm_info" => {
            let p: raydium::RaydiumGetFarmInfoParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_farm_info params: {e}"))
                })?;
            raydium::validate_raydium_get_farm_info_params(&p)
        }
        "raydium_get_farm_by_lp" => {
            let p: raydium::RaydiumGetFarmByLpParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_farm_by_lp params: {e}"))
                })?;
            raydium::validate_raydium_get_farm_by_lp_params(&p)
        }
        "raydium_get_farm_keys" => {
            let p: raydium::RaydiumGetFarmKeysParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_farm_keys params: {e}"))
                })?;
            raydium::validate_raydium_get_farm_keys_params(&p)
        }
        "raydium_get_ido_keys" => {
            let p: raydium::RaydiumGetIdoKeysParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_ido_keys params: {e}"))
                })?;
            raydium::validate_raydium_get_ido_keys_params(&p)
        }
        "raydium_get_main_version" => {
            let p: raydium::RaydiumGetMainVersionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_main_version params: {e}"))
                })?;
            raydium::validate_raydium_get_main_version_params(&p)
        }
        "raydium_get_rpcs" => {
            let p: raydium::RaydiumGetRpcsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_rpcs params: {e}"))
                })?;
            raydium::validate_raydium_get_rpcs_params(&p)
        }
        "raydium_get_chain_time" => {
            let p: raydium::RaydiumGetChainTimeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_chain_time params: {e}"))
                })?;
            raydium::validate_raydium_get_chain_time_params(&p)
        }
        "raydium_get_stake_pools" => {
            let p: raydium::RaydiumGetStakePoolsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_stake_pools params: {e}"))
                })?;
            raydium::validate_raydium_get_stake_pools_params(&p)
        }
        "raydium_get_migrate_lp" => {
            let p: raydium::RaydiumGetMigrateLpParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_migrate_lp params: {e}"))
                })?;
            raydium::validate_raydium_get_migrate_lp_params(&p)
        }
        "raydium_get_auto_fee" => {
            let p: raydium::RaydiumGetAutoFeeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_auto_fee params: {e}"))
                })?;
            raydium::validate_raydium_get_auto_fee_params(&p)
        }
        "raydium_get_cpmm_configs" => {
            let p: raydium::RaydiumGetCpmmConfigsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid raydium_get_cpmm_configs params: {e}"))
                })?;
            raydium::validate_raydium_get_cpmm_configs_params(&p)
        }
        // ── Orca Whirlpools Actions ─────────────────────────────────────────────────
        "orca_swap" => {
            let p: orca::OrcaSwapParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid orca_swap params: {e}")))?;
            orca::validate_orca_swap_params(&p)
        }
        "orca_add_liquidity" => {
            let p: orca::OrcaAddLiquidityParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_add_liquidity params: {e}"))
                })?;
            orca::validate_orca_add_liquidity_params(&p)
        }
        "orca_remove_liquidity" => {
            let p: orca::OrcaRemoveLiquidityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_remove_liquidity params: {e}"))
                })?;
            orca::validate_orca_remove_liquidity_params(&p)
        }
        "orca_open_position" => {
            let p: orca::OrcaOpenPositionParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_open_position params: {e}"))
                })?;
            orca::validate_orca_open_position_params(&p)
        }
        "orca_close_position" => {
            let p: orca::OrcaClosePositionParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_close_position params: {e}"))
                })?;
            orca::validate_orca_close_position_params(&p)
        }
        "orca_increase_position" => {
            let p: orca::OrcaIncreasePositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_increase_position params: {e}"))
                })?;
            orca::validate_orca_increase_position_params(&p)
        }
        "orca_decrease_position" => {
            let p: orca::OrcaDecreasePositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_decrease_position params: {e}"))
                })?;
            orca::validate_orca_decrease_position_params(&p)
        }
        "orca_collect_fees" => {
            let p: orca::OrcaCollectFeesParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_collect_fees params: {e}"))
                })?;
            orca::validate_orca_collect_fees_params(&p)
        }
        "orca_collect_rewards" => {
            let p: orca::OrcaCollectRewardsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_collect_rewards params: {e}"))
                })?;
            orca::validate_orca_collect_rewards_params(&p)
        }
        "orca_create_pool" => {
            let p: orca::OrcaCreatePoolParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_create_pool params: {e}"))
                })?;
            orca::validate_orca_create_pool_params(&p)
        }
        "orca_get_pools" => {
            let p: orca::OrcaGetPoolsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_pools params: {e}"))
                })?;
            orca::validate_orca_get_pools_params(&p)
        }
        "orca_search_pools" => {
            let p: orca::OrcaSearchPoolsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_search_pools params: {e}"))
                })?;
            orca::validate_orca_search_pools_params(&p)
        }
        "orca_get_pool" => {
            let p: orca::OrcaGetPoolParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_pool params: {e}"))
                })?;
            orca::validate_orca_get_pool_params(&p)
        }
        "orca_get_locked_liquidity" => {
            let p: orca::OrcaGetLockedLiquidityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid orca_get_locked_liquidity params: {e}"
                    ))
                })?;
            orca::validate_orca_get_locked_liquidity_params(&p)
        }
        "orca_get_protocol_stats" => {
            let p: orca::OrcaGetProtocolStatsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_protocol_stats params: {e}"))
                })?;
            orca::validate_orca_get_protocol_stats_params(&p)
        }
        "orca_get_orca_token" => {
            let p: orca::OrcaGetOrcaTokenParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_orca_token params: {e}"))
                })?;
            orca::validate_orca_get_orca_token_params(&p)
        }
        "orca_get_circulating_supply" => {
            let p: orca::OrcaGetCirculatingSupplyParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid orca_get_circulating_supply params: {e}"
                    ))
                })?;
            orca::validate_orca_get_circulating_supply_params(&p)
        }
        "orca_get_total_supply" => {
            let p: orca::OrcaGetTotalSupplyParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_total_supply params: {e}"))
                })?;
            orca::validate_orca_get_total_supply_params(&p)
        }
        "orca_get_tokens" => {
            let p: orca::OrcaGetTokensParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_tokens params: {e}"))
                })?;
            orca::validate_orca_get_tokens_params(&p)
        }
        "orca_search_tokens" => {
            let p: orca::OrcaSearchTokensParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_search_tokens params: {e}"))
                })?;
            orca::validate_orca_search_tokens_params(&p)
        }
        "orca_get_token" => {
            let p: orca::OrcaGetTokenParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_token params: {e}"))
                })?;
            orca::validate_orca_get_token_params(&p)
        }
        "orca_get_user_positions" => {
            let p: orca::OrcaGetUserPositionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_user_positions params: {e}"))
                })?;
            orca::validate_orca_get_user_positions_params(&p)
        }
        "orca_get_pool_positions" => {
            let p: orca::OrcaGetPoolPositionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid orca_get_pool_positions params: {e}"))
                })?;
            orca::validate_orca_get_pool_positions_params(&p)
        }
        // ── Kamino Finance Actions ───────────────────────────────────────────────────
        "kamino_deposit" => {
            let p: kamino::KaminoDepositParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_deposit params: {e}"))
                })?;
            kamino::validate_kamino_deposit_params(&p)
        }
        "kamino_withdraw" => {
            let p: kamino::KaminoWithdrawParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_withdraw params: {e}"))
                })?;
            kamino::validate_kamino_withdraw_params(&p)
        }
        "kamino_borrow" => {
            let p: kamino::KaminoBorrowParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_borrow params: {e}"))
                })?;
            kamino::validate_kamino_borrow_params(&p)
        }
        "kamino_repay" => {
            let p: kamino::KaminoRepayParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_repay params: {e}"))
                })?;
            kamino::validate_kamino_repay_params(&p)
        }
        "kamino_add_collateral" => {
            let p: kamino::KaminoAddCollateralParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_add_collateral params: {e}"))
                })?;
            kamino::validate_kamino_add_collateral_params(&p)
        }
        "kamino_withdraw_collateral" => {
            let p: kamino::KaminoWithdrawCollateralParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_withdraw_collateral params: {e}"
                    ))
                })?;
            kamino::validate_kamino_withdraw_collateral_params(&p)
        }
        "kamino_multiply_open" => {
            let p: kamino::KaminoMultiplyOpenParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_multiply_open params: {e}"))
                })?;
            kamino::validate_kamino_multiply_open_params(&p)
        }
        "kamino_multiply_add" => {
            let p: kamino::KaminoMultiplyAddParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_multiply_add params: {e}"))
                })?;
            kamino::validate_kamino_multiply_add_params(&p)
        }
        "kamino_multiply_withdraw" => {
            let p: kamino::KaminoMultiplyWithdrawParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_multiply_withdraw params: {e}"))
                })?;
            kamino::validate_kamino_multiply_withdraw_params(&p)
        }
        "kamino_multiply_close" => {
            let p: kamino::KaminoMultiplyCloseParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_multiply_close params: {e}"))
                })?;
            kamino::validate_kamino_multiply_close_params(&p)
        }
        "kamino_long_open" => {
            let p: kamino::KaminoLongOpenParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_long_open params: {e}"))
                })?;
            kamino::validate_kamino_long_open_params(&p)
        }
        "kamino_short_open" => {
            let p: kamino::KaminoShortOpenParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_short_open params: {e}"))
                })?;
            kamino::validate_kamino_short_open_params(&p)
        }
        "kamino_position_close" => {
            let p: kamino::KaminoPositionCloseParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_position_close params: {e}"))
                })?;
            kamino::validate_kamino_position_close_params(&p)
        }
        "kamino_vault_deposit" => {
            let p: kamino::KaminoVaultDepositParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_vault_deposit params: {e}"))
                })?;
            kamino::validate_kamino_vault_deposit_params(&p)
        }
        "kamino_vault_withdraw" => {
            let p: kamino::KaminoVaultWithdrawParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_vault_withdraw params: {e}"))
                })?;
            kamino::validate_kamino_vault_withdraw_params(&p)
        }
        "kamino_stake" => {
            let p: kamino::KaminoStakeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_stake params: {e}"))
                })?;
            kamino::validate_kamino_stake_params(&p)
        }
        "kamino_unstake" => {
            let p: kamino::KaminoUnstakeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_unstake params: {e}"))
                })?;
            kamino::validate_kamino_unstake_params(&p)
        }
        "kamino_vaults" => {
            let p: kamino::KaminoVaultsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_vaults params: {e}"))
                })?;
            kamino::validate_kamino_vaults_params(&p)
        }
        "kamino_user_vault_positions" => {
            let p: kamino::KaminoUserVaultPositionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_user_vault_positions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_user_vault_positions_params(&p)
        }
        "kamino_markets" => {
            let p: kamino::KaminoMarketsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_markets params: {e}"))
                })?;
            kamino::validate_kamino_markets_params(&p)
        }
        "kamino_market_reserves" => {
            let p: kamino::KaminoMarketReservesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_market_reserves params: {e}"))
                })?;
            kamino::validate_kamino_market_reserves_params(&p)
        }
        "kamino_user_obligations" => {
            let p: kamino::KaminoUserObligationsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_user_obligations params: {e}"))
                })?;
            kamino::validate_kamino_user_obligations_params(&p)
        }
        "kamino_oracle_prices" => {
            let p: kamino::KaminoOraclePricesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_oracle_prices params: {e}"))
                })?;
            kamino::validate_kamino_oracle_prices_params(&p)
        }
        "kamino_usd_benchmark_rates" => {
            let p: kamino::KaminoUsdBenchmarkRatesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_usd_benchmark_rates params: {e}"))
            })?;
            kamino::validate_kamino_usd_benchmark_rates_params(&p)
        }
        "kamino_vault_detail" => {
            let p: kamino::KaminoVaultDetailParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_vault_detail params: {e}"))
                })?;
            kamino::validate_kamino_vault_detail_params(&p)
        }
        "kamino_vault_metrics" => {
            let p: kamino::KaminoVaultMetricsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_vault_metrics params: {e}"))
                })?;
            kamino::validate_kamino_vault_metrics_params(&p)
        }
        "kamino_vault_metrics_history" => {
            let p: kamino::KaminoVaultMetricsHistoryParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_vault_metrics_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_vault_metrics_history_params(&p)
        }
        "kamino_vault_allocation_history" => {
            let p: kamino::KaminoVaultAllocationHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_vault_allocation_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_vault_allocation_history_params(&p)
        }
        "kamino_vaults_rewards" => {
            let p: kamino::KaminoVaultsRewardsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_vaults_rewards params: {e}"))
                })?;
            kamino::validate_kamino_vaults_rewards_params(&p)
        }
        "kamino_vaults_summary" => {
            let p: kamino::KaminoVaultsSummaryParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_vaults_summary params: {e}"))
                })?;
            kamino::validate_kamino_vaults_summary_params(&p)
        }
        "kamino_vault_mint_metadata" => {
            let p: kamino::KaminoVaultMintMetadataParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_vault_mint_metadata params: {e}"))
            })?;
            kamino::validate_kamino_vault_mint_metadata_params(&p)
        }
        "kamino_vault_mint_image" => {
            let p: kamino::KaminoVaultMintImageParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_vault_mint_image params: {e}"))
                })?;
            kamino::validate_kamino_vault_mint_image_params(&p)
        }
        "kamino_user_metrics_history" => {
            let p: kamino::KaminoUserMetricsHistoryParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_user_metrics_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_user_metrics_history_params(&p)
        }
        "kamino_user_transactions" => {
            let p: kamino::KaminoUserTransactionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_user_transactions params: {e}"))
                })?;
            kamino::validate_kamino_user_transactions_params(&p)
        }
        "kamino_user_kvault_rewards" => {
            let p: kamino::KaminoUserKvaultRewardsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_user_kvault_rewards params: {e}"))
            })?;
            kamino::validate_kamino_user_kvault_rewards_params(&p)
        }
        "kamino_vault_transactions" => {
            let p: kamino::KaminoVaultTransactionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_vault_transactions params: {e}"))
            })?;
            kamino::validate_kamino_vault_transactions_params(&p)
        }
        "kamino_user_vault_position" => {
            let p: kamino::KaminoUserVaultPositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_user_vault_position params: {e}"))
            })?;
            kamino::validate_kamino_user_vault_position_params(&p)
        }
        "kamino_user_vault_metrics_history" => {
            let p: kamino::KaminoUserVaultMetricsHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_user_vault_metrics_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_user_vault_metrics_history_params(&p)
        }
        "kamino_user_vault_pnl" => {
            let p: kamino::KaminoUserVaultPnlParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_user_vault_pnl params: {e}"))
                })?;
            kamino::validate_kamino_user_vault_pnl_params(&p)
        }
        "kamino_user_vault_pnl_history" => {
            let p: kamino::KaminoUserVaultPnlHistoryParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_user_vault_pnl_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_user_vault_pnl_history_params(&p)
        }
        "kamino_vault_deposit_instructions" => {
            let p: kamino::KaminoVaultDepositInstructionsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_vault_deposit_instructions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_vault_deposit_instructions_params(&p)
        }
        "kamino_vault_withdraw_instructions" => {
            let p: kamino::KaminoVaultWithdrawInstructionsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_vault_withdraw_instructions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_vault_withdraw_instructions_params(&p)
        }
        "kamino_market_detail" => {
            let p: kamino::KaminoMarketDetailParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_market_detail params: {e}"))
                })?;
            kamino::validate_kamino_market_detail_params(&p)
        }
        "kamino_market_reserve_history" => {
            let p: kamino::KaminoMarketReserveHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_market_reserve_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_market_reserve_history_params(&p)
        }
        "kamino_market_leverage_metrics" => {
            let p: kamino::KaminoMarketLeverageMetricsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_market_leverage_metrics params: {e}"
                    ))
                })?;
            kamino::validate_kamino_market_leverage_metrics_params(&p)
        }
        "kamino_market_metrics_history" => {
            let p: kamino::KaminoMarketMetricsHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_market_metrics_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_market_metrics_history_params(&p)
        }
        "kamino_reserve_borrow_apy_history" => {
            let p: kamino::KaminoReserveBorrowApyHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_reserve_borrow_apy_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_reserve_borrow_apy_history_params(&p)
        }
        "kamino_reserve_borrow_apy_median" => {
            let p: kamino::KaminoReserveBorrowApyMedianParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_reserve_borrow_apy_median params: {e}"
                    ))
                })?;
            kamino::validate_kamino_reserve_borrow_apy_median_params(&p)
        }
        "kamino_obligation_interest_earned" => {
            let p: kamino::KaminoObligationInterestEarnedParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_obligation_interest_earned params: {e}"
                    ))
                })?;
            kamino::validate_kamino_obligation_interest_earned_params(&p)
        }
        "kamino_obligation_interest_paid" => {
            let p: kamino::KaminoObligationInterestPaidParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_obligation_interest_paid params: {e}"
                    ))
                })?;
            kamino::validate_kamino_obligation_interest_paid_params(&p)
        }
        "kamino_obligation_transactions" => {
            let p: kamino::KaminoObligationTransactionsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_obligation_transactions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_obligation_transactions_params(&p)
        }
        "kamino_user_klend_transactions_all" => {
            let p: kamino::KaminoUserKlendTransactionsAllParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_user_klend_transactions_all params: {e}"
                    ))
                })?;
            kamino::validate_kamino_user_klend_transactions_all_params(&p)
        }
        "kamino_user_klend_transactions" => {
            let p: kamino::KaminoUserKlendTransactionsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_user_klend_transactions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_user_klend_transactions_params(&p)
        }
        "kamino_borrow_order_fills" => {
            let p: kamino::KaminoBorrowOrderFillsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_borrow_order_fills params: {e}"
                    ))
                })?;
            kamino::validate_kamino_borrow_order_fills_params(&p)
        }
        "kamino_open_borrow_orders" => {
            let p: kamino::KaminoOpenBorrowOrdersParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_open_borrow_orders params: {e}"
                    ))
                })?;
            kamino::validate_kamino_open_borrow_orders_params(&p)
        }
        "kamino_yield_history" => {
            let p: kamino::KaminoYieldHistoryParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_yield_history params: {e}"))
                })?;
            kamino::validate_kamino_yield_history_params(&p)
        }
        "kamino_principal_token_yields" => {
            let p: kamino::KaminoPrincipalTokenYieldsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_principal_token_yields params: {e}"
                    ))
                })?;
            kamino::validate_kamino_principal_token_yields_params(&p)
        }
        "kamino_airdrop_allocations" => {
            let p: kamino::KaminoAirdropAllocationsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_airdrop_allocations params: {e}"
                    ))
                })?;
            kamino::validate_kamino_airdrop_allocations_params(&p)
        }
        "kamino_airdrop_metrics" => {
            let p: kamino::KaminoAirdropMetricsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_airdrop_metrics params: {e}"))
                })?;
            kamino::validate_kamino_airdrop_metrics_params(&p)
        }
        "kamino_staking_yields" => {
            let p: kamino::KaminoStakingYieldsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_staking_yields params: {e}"))
                })?;
            kamino::validate_kamino_staking_yields_params(&p)
        }
        "kamino_staking_yields_median" => {
            let p: kamino::KaminoStakingYieldsMedianParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_staking_yields_median params: {e}"
                    ))
                })?;
            kamino::validate_kamino_staking_yields_median_params(&p)
        }
        "kamino_staking_yields_mean" => {
            let p: kamino::KaminoStakingYieldsMeanParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_staking_yields_mean params: {e}"))
            })?;
            kamino::validate_kamino_staking_yields_mean_params(&p)
        }
        "kamino_user_staking_boosts" => {
            let p: kamino::KaminoUserStakingBoostsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_user_staking_boosts params: {e}"))
            })?;
            kamino::validate_kamino_user_staking_boosts_params(&p)
        }
        "kamino_season_rewards_user" => {
            let p: kamino::KaminoSeasonRewardsUserParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_season_rewards_user params: {e}"))
            })?;
            kamino::validate_kamino_season_rewards_user_params(&p)
        }
        "kamino_season_rewards_vesting_pool" => {
            let p: kamino::KaminoSeasonRewardsVestingPoolParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_season_rewards_vesting_pool params: {e}"
                    ))
                })?;
            kamino::validate_kamino_season_rewards_vesting_pool_params(&p)
        }
        "kamino_private_credit_metrics" => {
            let p: kamino::KaminoPrivateCreditMetricsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_private_credit_metrics params: {e}"
                    ))
                })?;
            kamino::validate_kamino_private_credit_metrics_params(&p)
        }
        "kamino_private_credit_metrics_history" => {
            let p: kamino::KaminoPrivateCreditMetricsHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_private_credit_metrics_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_private_credit_metrics_history_params(&p)
        }
        "kamino_user_farm_transactions" => {
            let p: kamino::KaminoUserFarmTransactionsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_user_farm_transactions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_user_farm_transactions_params(&p)
        }
        "kamino_farm_transactions" => {
            let p: kamino::KaminoFarmTransactionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_farm_transactions params: {e}"))
                })?;
            kamino::validate_kamino_farm_transactions_params(&p)
        }
        "kamino_market_reserves_account" => {
            let p: kamino::KaminoMarketReservesAccountParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_market_reserves_account params: {e}"
                    ))
                })?;
            kamino::validate_kamino_market_reserves_account_params(&p)
        }
        "kamino_user_rewards" => {
            let p: kamino::KaminoUserRewardsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_user_rewards params: {e}"))
                })?;
            kamino::validate_kamino_user_rewards_params(&p)
        }
        "kamino_loan_detail" => {
            let p: kamino::KaminoLoanDetailParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_loan_detail params: {e}"))
                })?;
            kamino::validate_kamino_loan_detail_params(&p)
        }
        "kamino_obligation_pnl" => {
            let p: kamino::KaminoObligationPnlParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_obligation_pnl params: {e}"))
                })?;
            kamino::validate_kamino_obligation_pnl_params(&p)
        }
        "kamino_obligation_metrics_history" => {
            let p: kamino::KaminoObligationMetricsHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_obligation_metrics_history params: {e}"
                    ))
                })?;
            kamino::validate_kamino_obligation_metrics_history_params(&p)
        }
        "kamino_rewards_list" => {
            let p: kamino::KaminoRewardsListParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_rewards_list params: {e}"))
                })?;
            kamino::validate_kamino_rewards_list_params(&p)
        }
        "kamino_rewards_history" => {
            let p: kamino::KaminoRewardsHistoryParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_rewards_history params: {e}"))
                })?;
            kamino::validate_kamino_rewards_history_params(&p)
        }
        "kamino_borrow_instructions" => {
            let p: kamino::KaminoBorrowInstructionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_borrow_instructions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_borrow_instructions_params(&p)
        }
        "kamino_repay_instructions" => {
            let p: kamino::KaminoRepayInstructionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid kamino_repay_instructions params: {e}"))
            })?;
            kamino::validate_kamino_repay_instructions_params(&p)
        }
        "kamino_kswap" => {
            let p: kamino::KaminoKswapParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid kamino_kswap params: {e}"))
                })?;
            kamino::validate_kamino_kswap_params(&p)
        }
        "kamino_deposit_instructions" => {
            let p: kamino::KaminoDepositInstructionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_deposit_instructions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_deposit_instructions_params(&p)
        }
        "kamino_withdraw_instructions" => {
            let p: kamino::KaminoWithdrawInstructionsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid kamino_withdraw_instructions params: {e}"
                    ))
                })?;
            kamino::validate_kamino_withdraw_instructions_params(&p)
        }
        // ── Jito Finance Actions ─────────────────────────────────────────────────────
        "jito_stake" => {
            let p: jito::JitoStakeParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid jito_stake params: {e}")))?;
            jito::validate_jito_stake_params(&p)
        }
        "jito_unstake" => {
            let p: jito::JitoUnstakeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jito_unstake params: {e}"))
                })?;
            jito::validate_jito_unstake_params(&p)
        }
        "jito_tip" => {
            let p: jito::JitoTipParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid jito_tip params: {e}")))?;
            jito::validate_jito_tip_params(&p)
        }
        "jito_bundle" => {
            let p: jito::JitoBundleParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid jito_bundle params: {e}")))?;
            jito::validate_jito_bundle_params(&p)
        }
        "jito_bundle_status" => {
            let p: jito::JitoBundleStatusParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid jito_bundle_status params: {e}"))
                })?;
            jito::validate_jito_bundle_status_params(&p)
        }
        // ── Meteora Protocol Actions ─────────────────────────────────────────────────
        "meteora_swap" => {
            let p: meteora::MeteoraSwapParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_swap params: {e}"))
                })?;
            meteora::validate_meteora_swap_params(&p)
        }
        "meteora_add_liquidity" => {
            let p: meteora::MeteoraAddLiquidityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_add_liquidity params: {e}"))
                })?;
            meteora::validate_meteora_add_liquidity_params(&p)
        }
        "meteora_remove_liquidity" => {
            let p: meteora::MeteoraRemoveLiquidityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid meteora_remove_liquidity params: {e}"))
            })?;
            meteora::validate_meteora_remove_liquidity_params(&p)
        }
        "meteora_create_pool" => {
            let p: meteora::MeteoraCreatePoolParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_create_pool params: {e}"))
                })?;
            meteora::validate_meteora_create_pool_params(&p)
        }
        "meteora_open_position" => {
            let p: meteora::MeteoraOpenPositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_open_position params: {e}"))
                })?;
            meteora::validate_meteora_open_position_params(&p)
        }
        "meteora_close_position" => {
            let p: meteora::MeteoraClosePositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_close_position params: {e}"))
                })?;
            meteora::validate_meteora_close_position_params(&p)
        }
        "meteora_add_to_position" => {
            let p: meteora::MeteoraAddToPositionParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_add_to_position params: {e}"))
                })?;
            meteora::validate_meteora_add_to_position_params(&p)
        }
        "meteora_claim_fees" => {
            let p: meteora::MeteoraClaimFeesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_claim_fees params: {e}"))
                })?;
            meteora::validate_meteora_claim_fees_params(&p)
        }
        "meteora_claim_rewards" => {
            let p: meteora::MeteoraClaimRewardsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_claim_rewards params: {e}"))
                })?;
            meteora::validate_meteora_claim_rewards_params(&p)
        }
        "meteora_stake" => {
            let p: meteora::MeteoraStakeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_stake params: {e}"))
                })?;
            meteora::validate_meteora_stake_params(&p)
        }
        "meteora_unstake" => {
            let p: meteora::MeteoraUnstakeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_unstake params: {e}"))
                })?;
            meteora::validate_meteora_unstake_params(&p)
        }
        "meteora_harvest" => {
            let p: meteora::MeteoraHarvestParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_harvest params: {e}"))
                })?;
            meteora::validate_meteora_harvest_params(&p)
        }
        // ── Meteora GET Query Actions ────────────────────────────────────────────────
        "meteora_dlmm_get_pairs" => {
            let p: meteora::MeteoraDlmmGetPairsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dlmm_get_pairs params: {e}"))
                })?;
            meteora::validate_meteora_dlmm_get_pairs_params(&p)
        }
        "meteora_dlmm_get_pair" => {
            let p: meteora::MeteoraDlmmGetPairParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dlmm_get_pair params: {e}"))
                })?;
            meteora::validate_meteora_dlmm_get_pair_params(&p)
        }
        "meteora_dlmm_get_user_positions" => {
            let p: meteora::MeteoraDlmmGetUserPositionsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dlmm_get_user_positions params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dlmm_get_user_positions_params(&p)
        }
        "meteora_dlmm_get_active_bin" => {
            let p: meteora::MeteoraDlmmGetActiveBinParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dlmm_get_active_bin params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dlmm_get_active_bin_params(&p)
        }
        "meteora_dlmm_get_pool_groups" => {
            let p: meteora::MeteoraDlmmGetPoolGroupsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dlmm_get_pool_groups params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dlmm_get_pool_groups_params(&p)
        }
        "meteora_dlmm_get_pool_group" => {
            let p: meteora::MeteoraDlmmGetPoolGroupParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dlmm_get_pool_group params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dlmm_get_pool_group_params(&p)
        }
        "meteora_dlmm_get_pool_ohlcv" => {
            let p: meteora::MeteoraDlmmGetPoolOhlcvParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dlmm_get_pool_ohlcv params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dlmm_get_pool_ohlcv_params(&p)
        }
        "meteora_dlmm_get_pool_volume_history" => {
            let p: meteora::MeteoraDlmmGetPoolVolumeHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dlmm_get_pool_volume_history params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dlmm_get_pool_volume_history_params(&p)
        }
        "meteora_dlmm_get_protocol_stats" => {
            let p: meteora::MeteoraDlmmGetProtocolStatsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dlmm_get_protocol_stats params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dlmm_get_protocol_stats_params(&p)
        }
        "meteora_dammv2_get_pools" => {
            let p: meteora::MeteoraDammV2GetPoolsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dammv2_get_pools params: {e}"))
                })?;
            meteora::validate_meteora_dammv2_get_pools_params(&p)
        }
        "meteora_dammv2_get_pool_groups" => {
            let p: meteora::MeteoraDammV2GetPoolGroupsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv2_get_pool_groups params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv2_get_pool_groups_params(&p)
        }
        "meteora_dammv2_get_pool_group" => {
            let p: meteora::MeteoraDammV2GetPoolGroupParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv2_get_pool_group params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv2_get_pool_group_params(&p)
        }
        "meteora_dammv2_get_pool" => {
            let p: meteora::MeteoraDammV2GetPoolParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dammv2_get_pool params: {e}"))
                })?;
            meteora::validate_meteora_dammv2_get_pool_params(&p)
        }
        "meteora_dammv2_get_pool_ohlcv" => {
            let p: meteora::MeteoraDammV2GetPoolOhlcvParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv2_get_pool_ohlcv params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv2_get_pool_ohlcv_params(&p)
        }
        "meteora_dammv2_get_pool_volume_history" => {
            let p: meteora::MeteoraDammV2GetPoolVolumeHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv2_get_pool_volume_history params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv2_get_pool_volume_history_params(&p)
        }
        "meteora_dammv2_get_protocol_metrics" => {
            let p: meteora::MeteoraDammV2GetProtocolMetricsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv2_get_protocol_metrics params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv2_get_protocol_metrics_params(&p)
        }
        "meteora_dammv1_get_pools" => {
            let p: meteora::MeteoraDammV1GetPoolsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dammv1_get_pools params: {e}"))
                })?;
            meteora::validate_meteora_dammv1_get_pools_params(&p)
        }
        "meteora_dammv1_get_pool_configs" => {
            let p: meteora::MeteoraDammV1GetPoolConfigsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv1_get_pool_configs params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv1_get_pool_configs_params(&p)
        }
        "meteora_dammv1_search_pools" => {
            let p: meteora::MeteoraDammV1SearchPoolsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv1_search_pools params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv1_search_pools_params(&p)
        }
        "meteora_dammv1_get_farms" => {
            let p: meteora::MeteoraDammV1GetFarmsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dammv1_get_farms params: {e}"))
                })?;
            meteora::validate_meteora_dammv1_get_farms_params(&p)
        }
        "meteora_dammv1_get_pools_metrics" => {
            let p: meteora::MeteoraDammV1GetPoolsMetricsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv1_get_pools_metrics params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv1_get_pools_metrics_params(&p)
        }
        "meteora_dammv1_get_alpha_vaults" => {
            let p: meteora::MeteoraDammV1GetAlphaVaultsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv1_get_alpha_vaults params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv1_get_alpha_vaults_params(&p)
        }
        "meteora_dammv1_get_alpha_vault_configs" => {
            let p: meteora::MeteoraDammV1GetAlphaVaultConfigsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv1_get_alpha_vault_configs params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv1_get_alpha_vault_configs_params(&p)
        }
        "meteora_dammv1_get_pools_by_vault_lp" => {
            let p: meteora::MeteoraDammV1GetPoolsByVaultLpParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv1_get_pools_by_vault_lp params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv1_get_pools_by_vault_lp_params(&p)
        }
        "meteora_dammv1_get_fee_config" => {
            let p: meteora::MeteoraDammV1GetFeeConfigParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv1_get_fee_config params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv1_get_fee_config_params(&p)
        }
        "meteora_s2e_get_analytics" => {
            let p: meteora::MeteoraS2EGetAnalyticsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid meteora_s2e_get_analytics params: {e}"))
            })?;
            meteora::validate_meteora_s2e_get_analytics_params(&p)
        }
        "meteora_s2e_get_all_vaults" => {
            let p: meteora::MeteoraS2EGetAllVaultsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid meteora_s2e_get_all_vaults params: {e}"))
            })?;
            meteora::validate_meteora_s2e_get_all_vaults_params(&p)
        }
        "meteora_s2e_filter_vaults" => {
            let p: meteora::MeteoraS2EFilterVaultsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid meteora_s2e_filter_vaults params: {e}"))
            })?;
            meteora::validate_meteora_s2e_filter_vaults_params(&p)
        }
        "meteora_s2e_get_vault" => {
            let p: meteora::MeteoraS2EGetVaultParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_s2e_get_vault params: {e}"))
                })?;
            meteora::validate_meteora_s2e_get_vault_params(&p)
        }
        "meteora_vault_get_info" => {
            let p: meteora::MeteoraVaultGetInfoParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_vault_get_info params: {e}"))
                })?;
            meteora::validate_meteora_vault_get_info_params(&p)
        }
        "meteora_vault_get_addresses" => {
            let p: meteora::MeteoraVaultGetAddressesParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_vault_get_addresses params: {e}"
                    ))
                })?;
            meteora::validate_meteora_vault_get_addresses_params(&p)
        }
        "meteora_vault_get_state" => {
            let p: meteora::MeteoraVaultGetStateParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_vault_get_state params: {e}"))
                })?;
            meteora::validate_meteora_vault_get_state_params(&p)
        }
        "meteora_vault_get_apy" => {
            let p: meteora::MeteoraVaultGetApyParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_vault_get_apy params: {e}"))
                })?;
            meteora::validate_meteora_vault_get_apy_params(&p)
        }
        "meteora_vault_get_apy_history" => {
            let p: meteora::MeteoraVaultGetApyHistoryParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_vault_get_apy_history params: {e}"
                    ))
                })?;
            meteora::validate_meteora_vault_get_apy_history_params(&p)
        }
        "meteora_vault_get_virtual_price" => {
            let p: meteora::MeteoraVaultGetVirtualPriceParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_vault_get_virtual_price params: {e}"
                    ))
                })?;
            meteora::validate_meteora_vault_get_virtual_price_params(&p)
        }
        // ── Meteora TX Actions (new) ─────────────────────────────────────────────────
        "meteora_dammv1_swap" => {
            let p: meteora::MeteoraDammV1SwapParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dammv1_swap params: {e}"))
                })?;
            meteora::validate_meteora_dammv1_swap_params(&p)
        }
        "meteora_dammv1_deposit" => {
            let p: meteora::MeteoraDammV1DepositParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dammv1_deposit params: {e}"))
                })?;
            meteora::validate_meteora_dammv1_deposit_params(&p)
        }
        "meteora_dammv1_withdraw" => {
            let p: meteora::MeteoraDammV1WithdrawParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dammv1_withdraw params: {e}"))
                })?;
            meteora::validate_meteora_dammv1_withdraw_params(&p)
        }
        "meteora_dammv2_swap" => {
            let p: meteora::MeteoraDammV2SwapParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_dammv2_swap params: {e}"))
                })?;
            meteora::validate_meteora_dammv2_swap_params(&p)
        }
        "meteora_dammv2_add_liquidity" => {
            let p: meteora::MeteoraDammV2AddLiquidityParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv2_add_liquidity params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv2_add_liquidity_params(&p)
        }
        "meteora_dammv2_remove_liquidity" => {
            let p: meteora::MeteoraDammV2RemoveLiquidityParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_dammv2_remove_liquidity params: {e}"
                    ))
                })?;
            meteora::validate_meteora_dammv2_remove_liquidity_params(&p)
        }
        "meteora_vault_deposit" => {
            let p: meteora::MeteoraVaultDepositParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_vault_deposit params: {e}"))
                })?;
            meteora::validate_meteora_vault_deposit_params(&p)
        }
        "meteora_vault_withdraw" => {
            let p: meteora::MeteoraVaultWithdrawParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_vault_withdraw params: {e}"))
                })?;
            meteora::validate_meteora_vault_withdraw_params(&p)
        }
        "meteora_s2e_stake" => {
            let p: meteora::MeteoraS2EStakeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_s2e_stake params: {e}"))
                })?;
            meteora::validate_meteora_s2e_stake_params(&p)
        }
        "meteora_s2e_unstake" => {
            let p: meteora::MeteoraS2EUnstakeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_s2e_unstake params: {e}"))
                })?;
            meteora::validate_meteora_s2e_unstake_params(&p)
        }
        "meteora_s2e_claim_fee" => {
            let p: meteora::MeteoraS2EClaimFeeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_s2e_claim_fee params: {e}"))
                })?;
            meteora::validate_meteora_s2e_claim_fee_params(&p)
        }
        "meteora_s2e_cancel_unstake" => {
            let p: meteora::MeteoraS2ECancelUnstakeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid meteora_s2e_cancel_unstake params: {e}"
                    ))
                })?;
            meteora::validate_meteora_s2e_cancel_unstake_params(&p)
        }
        "meteora_s2e_withdraw" => {
            let p: meteora::MeteoraS2EWithdrawParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid meteora_s2e_withdraw params: {e}"))
                })?;
            meteora::validate_meteora_s2e_withdraw_params(&p)
        }
        // ── Marinade Finance Actions ─────────────────────────────────────────────────
        "marinade_stake" => {
            let p: marinade::MarinadeStakeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marinade_stake params: {e}"))
                })?;
            marinade::validate_marinade_stake_params(&p)
        }
        "marinade_unstake" => {
            let p: marinade::MarinadeUnstakeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marinade_unstake params: {e}"))
                })?;
            marinade::validate_marinade_unstake_params(&p)
        }
        "marinade_delayed_unstake" => {
            let p: marinade::MarinadeDelayedUnstakeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marinade_delayed_unstake params: {e}"))
                })?;
            marinade::validate_marinade_delayed_unstake_params(&p)
        }
        "marinade_claim_ticket" | "marinade_claim" => {
            let p: marinade::MarinadeClaimTicketParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marinade_claim_ticket params: {e}"))
                })?;
            marinade::validate_marinade_claim_ticket_params(&p)
        }
        // ── marginfi v2 Protocol Actions ─────────────────────────────────────────────
        "marginfi_create_account" => {
            let p: marginfi::MarginfiCreateAccountParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid marginfi_create_account params: {e}"))
            })?;
            marginfi::validate_marginfi_create_account_params(&p)
        }
        "marginfi_create_account_pda" => {
            let p: marginfi::MarginfiCreateAccountPdaParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_create_account_pda params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_create_account_pda_params(&p)
        }
        "marginfi_close_account" => {
            let p: marginfi::MarginfiCloseAccountParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_close_account params: {e}"))
                })?;
            marginfi::validate_marginfi_close_account_params(&p)
        }
        "marginfi_close_balance" => {
            let p: marginfi::MarginfiCloseBalanceParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_close_balance params: {e}"))
                })?;
            marginfi::validate_marginfi_close_balance_params(&p)
        }
        "marginfi_transfer_account" => {
            let p: marginfi::MarginfiTransferAccountParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_transfer_account params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_transfer_account_params(&p)
        }
        "marginfi_deposit" => {
            let p: marginfi::MarginfiDepositParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_deposit params: {e}"))
                })?;
            marginfi::validate_marginfi_deposit_params(&p)
        }
        "marginfi_withdraw" => {
            let p: marginfi::MarginfiWithdrawParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_withdraw params: {e}"))
                })?;
            marginfi::validate_marginfi_withdraw_params(&p)
        }
        "marginfi_borrow" => {
            let p: marginfi::MarginfiBorrowParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_borrow params: {e}"))
                })?;
            marginfi::validate_marginfi_borrow_params(&p)
        }
        "marginfi_repay" => {
            let p: marginfi::MarginfiRepayParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_repay params: {e}"))
                })?;
            marginfi::validate_marginfi_repay_params(&p)
        }
        "marginfi_liquidate" => {
            let p: marginfi::MarginfiLiquidateParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_liquidate params: {e}"))
                })?;
            marginfi::validate_marginfi_liquidate_params(&p)
        }
        "marginfi_start_liquidation" => {
            let p: marginfi::MarginfiStartLiquidationParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_start_liquidation params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_start_liquidation_params(&p)
        }
        "marginfi_end_liquidation" => {
            let p: marginfi::MarginfiEndLiquidationParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_end_liquidation params: {e}"))
                })?;
            marginfi::validate_marginfi_end_liquidation_params(&p)
        }
        "marginfi_flashloan_start" => {
            let p: marginfi::MarginfiFlashloanStartParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_flashloan_start params: {e}"))
                })?;
            marginfi::validate_marginfi_flashloan_start_params(&p)
        }
        "marginfi_flashloan_end" => {
            let p: marginfi::MarginfiFlashloanEndParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_flashloan_end params: {e}"))
                })?;
            marginfi::validate_marginfi_flashloan_end_params(&p)
        }
        "marginfi_place_order" => {
            let p: marginfi::MarginfiPlaceOrderParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_place_order params: {e}"))
                })?;
            marginfi::validate_marginfi_place_order_params(&p)
        }
        "marginfi_close_order" => {
            let p: marginfi::MarginfiCloseOrderParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_close_order params: {e}"))
                })?;
            marginfi::validate_marginfi_close_order_params(&p)
        }
        "marginfi_execute_order_start" => {
            let p: marginfi::MarginfiExecuteOrderStartParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_execute_order_start params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_execute_order_start_params(&p)
        }
        "marginfi_execute_order_end" => {
            let p: marginfi::MarginfiExecuteOrderEndParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_execute_order_end params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_execute_order_end_params(&p)
        }
        "marginfi_accrue_interest" => {
            let p: marginfi::MarginfiAccrueInterestParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_accrue_interest params: {e}"))
                })?;
            marginfi::validate_marginfi_accrue_interest_params(&p)
        }
        "marginfi_pulse_price" => {
            let p: marginfi::MarginfiPulsePriceParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_pulse_price params: {e}"))
                })?;
            marginfi::validate_marginfi_pulse_price_params(&p)
        }
        "marginfi_pulse_health" => {
            let p: marginfi::MarginfiPulseHealthParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_pulse_health params: {e}"))
                })?;
            marginfi::validate_marginfi_pulse_health_params(&p)
        }
        "marginfi_account_info" => {
            let p: marginfi::MarginfiAccountInfoParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_account_info params: {e}"))
                })?;
            marginfi::validate_marginfi_account_info_params(&p)
        }
        "marginfi_banks" => {
            let p: marginfi::MarginfiBanksParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_banks params: {e}"))
                })?;
            marginfi::validate_marginfi_banks_params(&p)
        }
        "marginfi_health" => {
            let p: marginfi::MarginfiHealthParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_health params: {e}"))
                })?;
            marginfi::validate_marginfi_health_params(&p)
        }
        "marginfi_points" => {
            let p: marginfi::MarginfiPointsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_points params: {e}"))
                })?;
            marginfi::validate_marginfi_points_params(&p)
        }
        "marginfi_bank_detail" => {
            let p: marginfi::MarginfiBankDetailParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_bank_detail params: {e}"))
                })?;
            marginfi::validate_marginfi_bank_detail_params(&p)
        }
        "marginfi_user_accounts" => {
            let p: marginfi::MarginfiUserAccountsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_user_accounts params: {e}"))
                })?;
            marginfi::validate_marginfi_user_accounts_params(&p)
        }
        "marginfi_claim_emissions" => {
            let p: marginfi::MarginfiClaimEmissionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_claim_emissions params: {e}"))
                })?;
            marginfi::validate_marginfi_claim_emissions_params(&p)
        }
        "marginfi_settle_emissions" => {
            let p: marginfi::MarginfiSettleEmissionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_settle_emissions params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_settle_emissions_params(&p)
        }
        "marginfi_withdraw_emissions_permissionless" => {
            let p: marginfi::MarginfiWithdrawEmissionsPermissionlessParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_withdraw_emissions_permissionless params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_withdraw_emissions_permissionless_params(&p)
        }
        "marginfi_set_keeper_flags" => {
            let p: marginfi::MarginfiSetKeeperFlagsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_set_keeper_flags params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_set_keeper_flags_params(&p)
        }
        "marginfi_init_liq_record" => {
            let p: marginfi::MarginfiInitLiqRecordParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid marginfi_init_liq_record params: {e}"))
            })?;
            marginfi::validate_marginfi_init_liq_record_params(&p)
        }
        "marginfi_update_emissions_destination" => {
            let p: marginfi::MarginfiUpdateEmissionsDestinationParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid marginfi_update_emissions_destination params: {e}"
                    ))
                })?;
            marginfi::validate_marginfi_update_emissions_destination_params(&p)
        }
        "marginfi_clear_emissions" => {
            let p: marginfi::MarginfiClearEmissionsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid marginfi_clear_emissions params: {e}"))
                })?;
            marginfi::validate_marginfi_clear_emissions_params(&p)
        }
        // ── Solend Protocol Actions ────────────────────────────────────────────────
        "solend_deposit" => {
            let p: solend::SolendDepositParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_deposit params: {e}"))
                })?;
            solend::validate_solend_deposit_params(&p)
        }
        "solend_withdraw" => {
            let p: solend::SolendWithdrawParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_withdraw params: {e}"))
                })?;
            solend::validate_solend_withdraw_params(&p)
        }
        "solend_borrow" => {
            let p: solend::SolendBorrowParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_borrow params: {e}"))
                })?;
            solend::validate_solend_borrow_params(&p)
        }
        "solend_repay" => {
            let p: solend::SolendRepayParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_repay params: {e}"))
                })?;
            solend::validate_solend_repay_params(&p)
        }
        "solend_add_collateral" => {
            let p: solend::SolendAddCollateralParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_add_collateral params: {e}"))
                })?;
            solend::validate_solend_add_collateral_params(&p)
        }
        "solend_withdraw_collateral" => {
            let p: solend::SolendWithdrawCollateralParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid solend_withdraw_collateral params: {e}"
                    ))
                })?;
            solend::validate_solend_withdraw_collateral_params(&p)
        }
        "solend_liquidate" => {
            let p: solend::SolendLiquidateParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_liquidate params: {e}"))
                })?;
            solend::validate_solend_liquidate_params(&p)
        }
        "solend_user_info" => {
            let p: solend::SolendUserInfoParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_user_info params: {e}"))
                })?;
            solend::validate_solend_user_info_params(&p)
        }
        "solend_market" => {
            let p: solend::SolendMarketParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_market params: {e}"))
                })?;
            solend::validate_solend_market_params(&p)
        }
        "solend_reserves" => {
            let p: solend::SolendReservesParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_reserves params: {e}"))
                })?;
            solend::validate_solend_reserves_params(&p)
        }
        "solend_stats" => {
            let _p: solend::SolendStatsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_stats params: {e}"))
                })?;
            Ok(())
        }
        "solend_lst_rates" => {
            let _p: solend::SolendLstRatesParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_lst_rates params: {e}"))
                })?;
            Ok(())
        }
        "solend_prices" => {
            let _p: solend::SolendPricesParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_prices params: {e}"))
                })?;
            Ok(())
        }
        "solend_reserves_history" => {
            let p: solend::SolendReservesHistoryParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_reserves_history params: {e}"))
                })?;
            if p.ids.is_empty() {
                return Err(AppError::InvalidParams("ids is required".into()));
            }
            Ok(())
        }
        "solend_daily_stats" => {
            let p: solend::SolendDailyStatsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_daily_stats params: {e}"))
                })?;
            if p.date.is_empty() {
                return Err(AppError::InvalidParams("date is required".into()));
            }
            Ok(())
        }
        "solend_flash_loan" => {
            let p: solend::SolendFlashLoanParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_flash_loan params: {e}"))
                })?;
            solend::validate_solend_flash_loan_params(&p)
        }
        "solend_claim_rewards" => {
            let _p: solend::SolendClaimRewardsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_claim_rewards params: {e}"))
                })?;
            Ok(())
        }
        "solend_deposit_liquidity" => {
            let p: solend::SolendDepositLiquidityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_deposit_liquidity params: {e}"))
                })?;
            solend::validate_solend_deposit_liquidity_params(&p)
        }
        "solend_deposit_obligation_collateral" => {
            let p: solend::SolendDepositObligationCollateralParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!(
                        "Invalid solend_deposit_obligation_collateral params: {e}"
                    ))
                })?;
            solend::validate_solend_deposit_obligation_collateral_params(&p)
        }
        "solend_redeem_collateral" => {
            let p: solend::SolendRedeemCollateralParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_redeem_collateral params: {e}"))
                })?;
            solend::validate_solend_redeem_collateral_params(&p)
        }
        "solend_exercise_reward" => {
            let p: solend::SolendExerciseRewardParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid solend_exercise_reward params: {e}"))
                })?;
            solend::validate_solend_exercise_reward_params(&p)
        }
        // ── Magic Eden NFT Marketplace Actions ───────────────────────────────────────
        "me_list" | "me_sell" => {
            let p: magic_eden::MeListParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid me_list params: {e}")))?;
            magic_eden::validate_me_list_params(&p)
        }
        "me_buy" | "me_buy_now" | "me_buy_instruction" | "me_buy_now_transfer_nft" => {
            let p: magic_eden::MeBuyParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid me_buy params: {e}")))?;
            magic_eden::validate_me_buy_params(&p)
        }
        "me_cancel_listing" | "me_sell_cancel" => {
            let p: magic_eden::MeCancelListingParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid me_cancel_listing params: {e}"))
                })?;
            magic_eden::validate_me_cancel_listing_params(&p)
        }
        "me_make_offer" => {
            let p: magic_eden::MeMakeOfferParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid me_make_offer params: {e}"))
                })?;
            magic_eden::validate_me_make_offer_params(&p)
        }
        "me_accept_offer" | "me_sell_now" => {
            let p: magic_eden::MeAcceptOfferParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid me_accept_offer params: {e}"))
                })?;
            magic_eden::validate_me_accept_offer_params(&p)
        }
        "me_cancel_offer" | "me_buy_cancel" => {
            let p: magic_eden::MeCancelOfferParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid me_cancel_offer params: {e}"))
                })?;
            magic_eden::validate_me_cancel_offer_params(&p)
        }
        // Repricing and escrow moves take only a mint (or nothing) plus an
        // amount; the live listing/offer supplies the rest, so there is
        // nothing here a user could get wrong beyond the number.
        // The address is validated where it is read — a mint that does not
        // parse, or is not a token at all, is named as such rather than
        // rejected as a bad parameter.
        "token_safety" | "honeypot_check" | "scam_check" | "rug_check" => Ok(()),
        "me_sell_change_price" | "me_buy_change_price" => {
            let _p: magic_eden::MeChangePriceParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid price-change params: {e}"))
                })?;
            Ok(())
        }
        "me_deposit" | "me_withdraw" => {
            let _p: magic_eden::MeEscrowParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid escrow params: {e}")))?;
            Ok(())
        }
        // MMM params are validated where they are built — the price, curve
        // and pool checks live next to the endpoint that rejects them.
        "me_mmm_create_pool" | "me_mmm_update_pool" | "me_mmm_sol_close_pool"
        | "me_mmm_sol_deposit_buy" | "me_mmm_sol_withdraw_buy"
        | "me_mmm_sol_fulfill_buy" | "me_mmm_sol_fulfill_sell" => Ok(()),
        // Reads validate themselves: `me_read_url` is where a missing symbol,
        // mint or wallet is reported, and it names which one is missing.
        "me_collections" | "me_collection_stats" | "me_collection_attributes" | "me_collection_leaderboard" | "me_collection_listings" | "me_collection_activities" | "me_collections_batch_listings" | "me_launchpad_collections" | "me_marketplace_popular" | "me_token" | "me_token_activities" | "me_token_listings" | "me_token_offers_received" | "me_wallet" | "me_wallet_tokens" | "me_wallet_activities" | "me_owner_activities" | "me_wallet_escrow_balance" | "me_wallet_offers_made" | "me_wallet_offers_received" | "me_mmm_pools" => {
            let _p: magic_eden::MeReadParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid Magic Eden query: {e}")))?;
            Ok(())
        }
        "me_collection_info" => {
            let p: magic_eden::MeCollectionInfoParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid me_collection_info params: {e}"))
                })?;
            magic_eden::validate_me_collection_info_params(&p)
        }
        "me_nft_info" => {
            let p: magic_eden::MeNFTInfoParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid me_nft_info params: {e}")))?;
            magic_eden::validate_me_nft_info_params(&p)
        }
        "me_wallet_nfts" => {
            let p: magic_eden::MeWalletNFTsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid me_wallet_nfts params: {e}"))
                })?;
            magic_eden::validate_me_wallet_nfts_params(&p)
        }
        "me_collection_activity" => {
            let p: magic_eden::MeCollectionActivityParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid me_collection_activity params: {e}"))
                })?;
            magic_eden::validate_me_collection_activity_params(&p)
        }
        "me_listings" => {
            let p: magic_eden::MeListingsParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid me_listings params: {e}")))?;
            magic_eden::validate_me_listings_params(&p)
        }
        "me_offers" => {
            let p: magic_eden::MeOffersParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid me_offers params: {e}")))?;
            magic_eden::validate_me_offers_params(&p)
        }
        "me_collection_nfts" => {
            let p: magic_eden::MeCollectionNFTsParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid me_collection_nfts params: {e}"))
                })?;
            magic_eden::validate_me_collection_nfts_params(&p)
        }
        // ── Tensor NFT ────────────────────────────────────────────────────────────────
        "tensor_buy" => {
            let p: tensor::TensorBuyParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_buy params: {e}")))?;
            if p.max_price.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
                return Err(AppError::InvalidParams("maxPrice must be positive".into()));
            }
            Ok(())
        }
        "tensor_list" => {
            let p: tensor::TensorListParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid tensor_list params: {e}")))?;
            if p.price.parse::<f64>().map(|v| v <= 0.0).unwrap_or(true) {
                return Err(AppError::InvalidParams("price must be positive".into()));
            }
            Ok(())
        }
        "tensor_cancel_listing"
        | "tensor_cancel_offer"
        | "tensor_collection_info"
        | "tensor_nft_info"
        | "tensor_wallet_nfts"
        | "tensor_listings"
        | "tensor_make_offer" => Ok(()),
        "burn" => {
            let p: burn::BurnParams = serde_json::from_value(params.clone())
                .map_err(|e| AppError::InvalidParams(format!("Invalid burn params: {e}")))?;
            burn::validate_burn_params(&p)
        }
        "close_accounts" => {
            let p: burn::CloseAccountsParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid close_accounts params: {e}"))
                })?;
            burn::validate_close_accounts_params(&p)
        }
        "scan_empty_accounts" => Ok(()),
        "streamflow_create"
        | "streamflow_cancel"
        | "streamflow_withdraw"
        | "streamflow_transfer"
        | "streamflow_list"
        | "streamflow_topup"
        | "streamflow_update"
        | "streamflow_create_multiple"
        | "streamflow_get_one" => Ok(()),
        // Read-only actions proxied wholesale to the TS service. No params
        // validation here — the TS layer reads `wallet` from the auth header.
        "marginfi_user_balances" => Ok(()),
        // pumpfun query actions validated in primary validate block above
        "native_stake" => {
            let p: native_stake::NativeStakeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid native_stake params: {e}"))
                })?;
            native_stake::validate_native_stake_params(&p)
        }
        "native_stake_deactivate" => {
            let _p: native_stake::NativeDeactivateParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid native_stake_deactivate params: {e}"))
            })?;
            Ok(())
        }
        "native_stake_withdraw" => {
            let _p: native_stake::NativeWithdrawStakeParams =
                serde_json::from_value(params.clone()).map_err(|e| {
                    AppError::InvalidParams(format!("Invalid native_stake_withdraw params: {e}"))
                })?;
            Ok(())
        }
        "native_stake_split" => {
            let p: native_stake::NativeSplitStakeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid native_stake_split params: {e}"))
                })?;
            native_stake::validate_native_split_params(&p)
        }
        "native_stake_merge" => {
            let _p: native_stake::NativeMergeStakeParams = serde_json::from_value(params.clone())
                .map_err(|e| {
                AppError::InvalidParams(format!("Invalid native_stake_merge params: {e}"))
            })?;
            Ok(())
        }
        _ => Err(AppError::InvalidParams(format!(
            "Unsupported action type: {action_type}"
        ))),
    }
}

fn streamflow_to_build_response(
    result: streamflow::StreamflowBuildResult,
) -> Result<BuildResponse, AppError> {
    Ok(BuildResponse {
        preview: ActionPreview {
            id: result.preview.id,
            action_type: result.preview.action_type,
            description: result.preview.description,
            estimated_fee: result.preview.estimated_fee,
            estimated_refund: None,
            params: serde_json::to_value(result.preview.params)?,
            warnings: result.preview.warnings,
            requires_approval: result.preview.requires_approval,
        },
        transaction: result.transaction,
        additional_signers_required: result.additional_signers_required.unwrap_or(0),
        execution_steps: None,
        quote: None,
        is_cross_chain: result.is_cross_chain.unwrap_or(false),
        data: result.data,
    })
}

/// Build a transaction for the given action type.
/// Build an action's transaction, with OPRAI's name on it.
///
/// The stamping happens here, once, instead of in every builder below. Most of
/// them never assemble instructions of their own — they get a finished
/// transaction back from Jupiter, a Kamino SDK call or Magic Eden — so there
/// is no earlier point that all of them share. `memo::attach` returns the
/// transaction untouched whenever stamping it would not be safe.
#[allow(clippy::too_many_arguments)]
pub async fn build_action(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    jupiter_api_key: Option<&str>,
    helius_api_key: Option<&str>,
    relay_fee_recipient: Option<&str>,
    relay_api_key: Option<&str>,
    user_pubkey: &Pubkey,
    action_type: &str,
    params: serde_json::Value,
) -> Result<BuildResponse, AppError> {
    let mut built = build_action_inner(
        http,
        rpc,
        jupiter_api_key,
        helius_api_key,
        relay_fee_recipient,
        relay_api_key,
        user_pubkey,
        action_type,
        params,
    )
    .await?;
    built.transaction = built
        .transaction
        ;
    if let Some(tx) = built.transaction.take() {
        built.transaction = Some(crate::services::memo::attach(&tx).await);
    }
    Ok(built)
}

#[allow(clippy::too_many_arguments)]
async fn build_action_inner(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    jupiter_api_key: Option<&str>,
    helius_api_key: Option<&str>,
    relay_fee_recipient: Option<&str>,
    relay_api_key: Option<&str>,
    user_pubkey: &Pubkey,
    action_type: &str,
    params: serde_json::Value,
) -> Result<BuildResponse, AppError> {
    // Before validation, because validation is what rejects a Magic Eden write
    // for the missing mint this fills in.
    let params = magic_eden::resolve_me_action_mint(http, action_type, params).await;
    validate_action(action_type, &params)?;

    match action_type {
        "transfer" => {
            let mut p: transfer::TransferParams = serde_json::from_value(params)?;
            // Auto-resolve .sol domain → pubkey before entering the blocking RPC call.
            if p.to.to_lowercase().ends_with(".sol") {
                let domain = p.to[..p.to.len() - 4].to_lowercase();
                let original = p.to.clone();
                p.to = sns::resolve_domain(http, &domain).await.map_err(|_| {
                    AppError::InvalidParams(format!(
                        "Could not resolve '{}' to a Solana address",
                        original
                    ))
                })?;
            }
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result = actix_web::web::block(move || {
                transfer::build_transfer_transaction(&rpc, &pubkey, &p)
            })
            .await
            .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;
            let tx_bytes = bincode::serialize(&result.transaction)
                .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
            let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: None,
                    params: serde_json::to_value(result.preview.params)?,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: Some(tx_b64),
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }
        "swap" => {
            let p: swap::SwapParams = serde_json::from_value(params)?;
            let result =
                swap::build_swap_transaction(http, jupiter_api_key, &user_pubkey.to_string(), &p)
                    .await?;

            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: None,
                    params: serde_json::to_value(result.preview.params)?,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: Some(result.transaction_base64),
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }
        "launch_token" | "pumpfun_launch" => {
            let p: pumpfun::LaunchTokenParams = serde_json::from_value(params)?;
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result = actix_web::web::block(move || {
                pumpfun::build_launch_token_transaction_blocking(&rpc, &pubkey, &p)
            })
            .await
            .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;

            // Transaction is already base64-encoded (may be versioned or legacy).
            // For launches with an initial buy, `data.initialBuy` tells the frontend to
            // perform the dev-buy as a follow-up (via `pumpfun_initial_buy`/PumpPortal)
            // once the create tx confirms — keeps the create tx under the 1232-byte
            // limit AND handles Mayhem-mode tokens the bonding-curve buy can't.
            let launch_data = result.initial_buy.map(|ib| {
                serde_json::json!({
                    "initialBuy": {
                        "mint": ib.mint,
                        "amountSol": ib.amount_sol,
                        "mayhem": ib.mayhem,
                    }
                })
            });
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: None,
                    params: serde_json::to_value(result.preview.params)?,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: Some(result.transaction_base64),
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: launch_data,
            })
        }
        "stake" => {
            let p: marinade::StakeParams = serde_json::from_value(params)?;
            let protocol = p.protocol.as_deref().unwrap_or("marinade");
            build_stake(http, rpc, user_pubkey, &p, protocol).await
        }
        "unstake" => {
            let p: marinade::StakeParams = serde_json::from_value(params)?;
            let protocol = p.protocol.as_deref().unwrap_or("marinade");
            build_unstake(http, rpc, user_pubkey, &p, protocol).await
        }
        "limit_order" => {
            let p: limit_order::LimitOrderParams = serde_json::from_value(params)?;
            limit_order::create_limit_order_transaction(
                http,
                jupiter_api_key,
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "cancel_limit_order" => {
            let p: limit_order::CancelLimitOrderParams = serde_json::from_value(params)?;
            limit_order::cancel_limit_order_transaction(
                http,
                jupiter_api_key,
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "cancel_all_limit_orders" => {
            let responses = limit_order::cancel_all_limit_orders_transactions(
                http,
                jupiter_api_key,
                &user_pubkey.to_string(),
            )
            .await?;
            Ok(limit_order::pack_cancel_all_response(responses))
        }
        "dca" => {
            let p: dca::DcaParams = serde_json::from_value(params)?;
            dca::create_dca_transaction(http, jupiter_api_key, &user_pubkey.to_string(), &p).await
        }
        "cancel_dca" => {
            let p: dca::CancelDcaParams = serde_json::from_value(params)?;
            dca::cancel_dca_transaction(http, jupiter_api_key, &user_pubkey.to_string(), &p).await
        }
        "jup_dca_orders" => {
            let p: dca::JupDcaOrdersParams = serde_json::from_value(params)?;
            dca::build_jup_dca_orders(http, jupiter_api_key, &user_pubkey.to_string(), &p).await
        }
        "jup_limit_orders" => {
            let p: limit_order::JupLimitOrdersParams = serde_json::from_value(params)?;
            limit_order::build_jup_limit_orders(http, jupiter_api_key, &user_pubkey.to_string(), &p)
                .await
        }
        "jup_price" => {
            let p: jupiter_query::JupPriceParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_price(http, &user_pubkey.to_string(), &p, jupiter_api_key)
                .await
        }
        "jup_token_search" => {
            let p: jupiter_query::JupTokenSearchParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_token_search(
                http,
                &user_pubkey.to_string(),
                &p,
                jupiter_api_key,
            )
            .await
        }
        "jup_tokens_tag" => {
            let p: jupiter_query::JupTokensTagParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_tokens_tag(http, &user_pubkey.to_string(), &p, jupiter_api_key)
                .await
        }
        "jup_tokens_recent" => {
            let p: jupiter_query::JupTokensRecentParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_tokens_recent(
                http,
                &user_pubkey.to_string(),
                &p,
                jupiter_api_key,
            )
            .await
        }
        "jup_tokens_trending" => {
            let p: jupiter_query::JupTokensTrendingParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_tokens_trending(
                http,
                &user_pubkey.to_string(),
                &p,
                jupiter_api_key,
            )
            .await
        }
        "jup_portfolio_positions" => {
            let p: jupiter_query::JupPortfolioPositionsParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_portfolio_positions(
                http,
                &user_pubkey.to_string(),
                &p,
                jupiter_api_key,
            )
            .await
        }
        "jup_staked_jup" => {
            let p: jupiter_query::JupStakedJupParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_staked_jup(http, &user_pubkey.to_string(), &p, jupiter_api_key)
                .await
        }
        "jup_lend_positions" => {
            let p: jupiter_query::JupLendPositionsParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_lend_positions(
                http,
                &user_pubkey.to_string(),
                &p,
                jupiter_api_key,
            )
            .await
        }
        "jup_lend_earnings" => {
            let p: jupiter_query::JupLendEarningsParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_lend_earnings(
                http,
                &user_pubkey.to_string(),
                &p,
                jupiter_api_key,
            )
            .await
        }
        "jup_pending_invites" => {
            let p: jupiter_query::JupPendingInvitesParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_pending_invites(
                http,
                &user_pubkey.to_string(),
                &p,
                jupiter_api_key,
            )
            .await
        }
        "jup_platforms" => {
            let p: jupiter_query::JupPlatformsParams = serde_json::from_value(params)?;
            jupiter_query::build_jup_platforms(http, &user_pubkey.to_string(), &p, jupiter_api_key)
                .await
        }
        "helius_tx_history" => {
            let p: helius::HeliusTxHistoryParams = serde_json::from_value(params)?;
            helius::build_helius_tx_history(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_parse_transactions" => {
            let p: helius::HeliusParseTransactionsParams = serde_json::from_value(params)?;
            helius::build_helius_parse_transactions(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_get_assets" => {
            let p: helius::HeliusGetAssetsParams = serde_json::from_value(params)?;
            helius::build_helius_get_assets(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_get_asset" => {
            let p: helius::HeliusGetAssetParams = serde_json::from_value(params)?;
            helius::build_helius_get_asset(http, &user_pubkey.to_string(), &p, helius_api_key).await
        }
        "helius_search_assets" => {
            let p: helius::HeliusSearchAssetsParams = serde_json::from_value(params)?;
            helius::build_helius_search_assets(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_nft_editions" => {
            let p: helius::HeliusNftEditionsParams = serde_json::from_value(params)?;
            helius::build_helius_nft_editions(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_get_token_accounts" => {
            let p: helius::HeliusGetTokenAccountsParams = serde_json::from_value(params)?;
            helius::build_helius_get_token_accounts(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_asset_signatures" => {
            let p: helius::HeliusAssetSignaturesParams = serde_json::from_value(params)?;
            helius::build_helius_asset_signatures(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_priority_fee" => {
            let p: helius::HeliusPriorityFeeParams = serde_json::from_value(params)?;
            helius::build_helius_priority_fee(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_wallet_identity" => {
            let p: helius::HeliusWalletIdentityParams = serde_json::from_value(params)?;
            helius::build_helius_wallet_identity(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_batch_identity" => {
            let p: helius::HeliusBatchIdentityParams = serde_json::from_value(params)?;
            helius::build_helius_batch_identity(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_wallet_balances" => {
            let p: helius::HeliusWalletBalancesParams = serde_json::from_value(params)?;
            helius::build_helius_wallet_balances(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_wallet_history" => {
            let p: helius::HeliusWalletHistoryParams = serde_json::from_value(params)?;
            helius::build_helius_wallet_history(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_wallet_transfers" => {
            let p: helius::HeliusWalletTransfersParams = serde_json::from_value(params)?;
            helius::build_helius_wallet_transfers(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_wallet_funded_by" => {
            let p: helius::HeliusWalletFundedByParams = serde_json::from_value(params)?;
            helius::build_helius_wallet_funded_by(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_get_asset_batch" => {
            let p: helius::HeliusGetAssetBatchParams = serde_json::from_value(params)?;
            helius::build_helius_get_asset_batch(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_get_assets_by_creator" => {
            let p: helius::HeliusGetAssetsByCreatorParams = serde_json::from_value(params)?;
            helius::build_helius_get_assets_by_creator(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_get_assets_by_authority" => {
            let p: helius::HeliusGetAssetsByAuthorityParams = serde_json::from_value(params)?;
            helius::build_helius_get_assets_by_authority(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_get_assets_by_group" => {
            let p: helius::HeliusGetAssetsByGroupParams = serde_json::from_value(params)?;
            helius::build_helius_get_assets_by_group(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_get_asset_proof" => {
            let p: helius::HeliusGetAssetProofParams = serde_json::from_value(params)?;
            helius::build_helius_get_asset_proof(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_get_asset_proof_batch" => {
            let p: helius::HeliusGetAssetProofBatchParams = serde_json::from_value(params)?;
            helius::build_helius_get_asset_proof_batch(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_create_webhook" => {
            let p: helius::HeliusCreateWebhookParams = serde_json::from_value(params)?;
            helius::build_helius_create_webhook(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_list_webhooks" => {
            let p: helius::HeliusListWebhooksParams = serde_json::from_value(params)?;
            helius::build_helius_list_webhooks(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_get_webhook" => {
            let p: helius::HeliusGetWebhookParams = serde_json::from_value(params)?;
            helius::build_helius_get_webhook(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_update_webhook" => {
            let p: helius::HeliusUpdateWebhookParams = serde_json::from_value(params)?;
            helius::build_helius_update_webhook(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_toggle_webhook" => {
            let p: helius::HeliusToggleWebhookParams = serde_json::from_value(params)?;
            helius::build_helius_toggle_webhook(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_delete_webhook" => {
            let p: helius::HeliusDeleteWebhookParams = serde_json::from_value(params)?;
            helius::build_helius_delete_webhook(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_send_transaction" => {
            let p: helius::HeliusSendTransactionParams = serde_json::from_value(params)?;
            helius::build_helius_send_transaction(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_compressed_account" => {
            let p: helius::HeliusZkAccountParams = serde_json::from_value(params)?;
            helius::build_helius_zk_compressed_account(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_multiple_compressed_accounts" => {
            let p: helius::HeliusZkMultipleAccountsParams = serde_json::from_value(params)?;
            helius::build_helius_zk_multiple_compressed_accounts(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_compressed_balance_by_owner" => {
            let p: helius::HeliusZkOwnerParams = serde_json::from_value(params)?;
            helius::build_helius_zk_compressed_balance_by_owner(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_token_accounts_by_owner" => {
            let p: helius::HeliusZkOwnerParams = serde_json::from_value(params)?;
            helius::build_helius_zk_token_accounts_by_owner(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_token_balances_by_owner" => {
            let p: helius::HeliusZkOwnerParams = serde_json::from_value(params)?;
            helius::build_helius_zk_token_balances_by_owner(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_mint_token_holders" => {
            let p: helius::HeliusZkMintParams = serde_json::from_value(params)?;
            helius::build_helius_zk_mint_token_holders(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_compression_signatures_for_owner" => {
            let p: helius::HeliusZkSignaturesParams = serde_json::from_value(params)?;
            helius::build_helius_zk_compression_signatures_for_owner(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_transaction_with_compression" => {
            let p: helius::HeliusZkSignaturesParams = serde_json::from_value(params)?;
            helius::build_helius_zk_transaction_with_compression(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_indexer_health" => {
            let p: helius::HeliusZkIndexerParams = serde_json::from_value(params)?;
            helius::build_helius_zk_indexer_health(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_indexer_slot" => {
            let p: helius::HeliusZkIndexerParams = serde_json::from_value(params)?;
            helius::build_helius_zk_indexer_slot(http, &user_pubkey.to_string(), &p, helius_api_key)
                .await
        }
        "helius_zk_validity_proof" => {
            let p: helius::HeliusZkAccountParams = serde_json::from_value(params)?;
            helius::build_helius_zk_validity_proof(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_zk_new_address_proofs" => {
            let p: helius::HeliusZkNewAddressProofsParams = serde_json::from_value(params)?;
            helius::build_helius_zk_new_address_proofs(
                http,
                &user_pubkey.to_string(),
                &p,
                helius_api_key,
            )
            .await
        }
        "helius_smart_send" => {
            let p: helius::HeliusSmartSendParams = serde_json::from_value(params)?;
            helius::build_helius_smart_send(http, rpc, &p, helius_api_key).await
        }
        "jupsol_stake" => {
            let p: jupsol::JupSolParams = serde_json::from_value(params)?;
            jupsol::build_jupsol_stake_transaction(
                http,
                jupiter_api_key,
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "jupsol_unstake" => {
            let p: jupsol::JupSolParams = serde_json::from_value(params)?;
            jupsol::build_jupsol_unstake_transaction(
                http,
                jupiter_api_key,
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        // Generic lend — routes by protocol field (default: jupiter)
        "lend" | "withdraw_lend" => {
            let protocol = params
                .get("protocol")
                .and_then(|v| v.as_str())
                .unwrap_or("jupiter")
                .to_string();
            match protocol.as_str() {
                "marginfi" => {
                    let p: marginfi::MarginfiDepositParams = serde_json::from_value(params)?;
                    marginfi::build_marginfi_deposit(
                        http,
                        rpc.endpoint(),
                        &user_pubkey.to_string(),
                        &p,
                    )
                    .await
                }
                "solend" => {
                    let p: solend::SolendDepositParams = serde_json::from_value(params)?;
                    solend::build_solend_deposit(http, &user_pubkey.to_string(), &p).await
                }
                "kamino" => Err(AppError::InvalidParams(
                    "Kamino requires a reserve address — use kamino_deposit instead".into(),
                )),
                _ => {
                    let p: jupiter_lend::JupiterLendParams = serde_json::from_value(params)?;
                    jupiter_lend::build_lend_transaction(http, &user_pubkey.to_string(), &p).await
                }
            }
        }
        "borrow" | "repay" => {
            let protocol = params
                .get("protocol")
                .and_then(|v| v.as_str())
                .unwrap_or("jupiter")
                .to_string();
            match protocol.as_str() {
                "marginfi" => {
                    let p: marginfi::MarginfiBorrowParams = serde_json::from_value(params)?;
                    marginfi::build_marginfi_borrow(
                        http,
                        rpc.endpoint(),
                        &user_pubkey.to_string(),
                        &p,
                    )
                    .await
                }
                "solend" => {
                    let p: solend::SolendBorrowParams = serde_json::from_value(params)?;
                    solend::build_solend_borrow(http, &user_pubkey.to_string(), &p).await
                }
                "kamino" => Err(AppError::InvalidParams(
                    "Kamino requires a reserve address — use kamino_borrow instead".into(),
                )),
                _ => {
                    let p: jupiter_lend::JupiterBorrowParams = serde_json::from_value(params)?;
                    jupiter_lend::build_borrow_transaction(http, &user_pubkey.to_string(), &p).await
                }
            }
        }
        "jup_lend_markets" => {
            let p: jupiter_lend::JupLendMarketsParams = serde_json::from_value(params)?;
            jupiter_lend::build_jup_lend_markets(http, &user_pubkey.to_string(), &p).await
        }
        // Jupiter Perp
        "perp_open" | "perp_close" => {
            let mut p_val = params;
            p_val["operation"] = serde_json::json!(if action_type == "perp_open" {
                "open"
            } else {
                "close"
            });
            let p: jupiter_perp::JupiterPerpParams = serde_json::from_value(p_val)?;
            jupiter_perp::build_perp_transaction(
                http,
                jupiter_api_key,
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "perp_positions" => {
            jupiter_perp::build_perp_positions(http, jupiter_api_key, &user_pubkey.to_string())
                .await
        }
        "jlp_add" | "jlp_remove" => {
            let mut p_val = params;
            p_val["operation"] = serde_json::json!(if action_type == "jlp_add" {
                "add"
            } else {
                "remove"
            });
            let p: jupiter_perp::JupiterPerpLiquidityParams = serde_json::from_value(p_val)?;
            jupiter_perp::build_perp_liquidity_transaction(
                http,
                jupiter_api_key,
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "pumpfun_buy" => {
            let p: pumpfun::PumpFunTradeParams = serde_json::from_value(params)?;
            pumpfun::build_pumpfun_buy(http, rpc, &user_pubkey.to_string(), &p).await
        }
        // Token-launch initial dev-buy, called by the frontend once the create
        // transaction confirms. Same builder as any other bonding-curve buy —
        // it reads the curve account, which exists the moment create lands.
        "pumpfun_initial_buy" => {
            let p: pumpfun::PumpFunTradeParams = serde_json::from_value(params)?;
            pumpfun::build_pumpfun_buy(http, rpc, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_sell" => {
            let p: pumpfun::PumpFunTradeParams = serde_json::from_value(params)?;
            pumpfun::build_pumpfun_sell(http, rpc, &user_pubkey.to_string(), &p).await
        }
        "pumpswap_buy" => {
            let p: pumpfun::PumpFunTradeParams = serde_json::from_value(params)?;
            pumpfun::build_pumpswap_buy(http, rpc, &user_pubkey.to_string(), &p).await
        }
        "pumpswap_sell" => {
            let p: pumpfun::PumpFunTradeParams = serde_json::from_value(params)?;
            pumpfun::build_pumpswap_sell(http, rpc, &user_pubkey.to_string(), &p).await
        }
        "raydium_swap" => {
            let p: raydium::RaydiumSwapParams = serde_json::from_value(params)?;
            raydium::build_raydium_swap(http, rpc, &user_pubkey.to_string(), &p).await
        }
        "raydium_add_liquidity" => {
            let p: raydium::RaydiumAddLiquidityParams = serde_json::from_value(params)?;
            raydium::build_raydium_add_liquidity(http, &user_pubkey.to_string(), &p).await
        }
        "raydium_remove_liquidity" => {
            let p: raydium::RaydiumRemoveLiquidityParams = serde_json::from_value(params)?;
            raydium::build_raydium_remove_liquidity(http, &user_pubkey.to_string(), &p).await
        }
        "raydium_create_pool" => {
            let p: raydium::RaydiumCreatePoolParams = serde_json::from_value(params)?;
            raydium::build_raydium_create_pool(http, &user_pubkey.to_string(), &p).await
        }
        "raydium_open_position" => {
            let p: raydium::RaydiumOpenPositionParams = serde_json::from_value(params)?;
            raydium::build_raydium_open_position(http, rpc, &user_pubkey.to_string(), &p).await
        }
        "raydium_close_position" => {
            let p: raydium::RaydiumClosePositionParams = serde_json::from_value(params)?;
            raydium::build_raydium_close_position(http, &user_pubkey.to_string(), &p).await
        }
        "raydium_increase_position" => {
            let p: raydium::RaydiumIncreasePositionParams = serde_json::from_value(params)?;
            raydium::build_raydium_increase_position(http, &user_pubkey.to_string(), &p).await
        }
        "raydium_decrease_position" => {
            let p: raydium::RaydiumDecreasePositionParams = serde_json::from_value(params)?;
            raydium::build_raydium_decrease_position(http, &user_pubkey.to_string(), &p).await
        }
        "raydium_get_pools" => {
            let p: raydium::RaydiumGetPoolsParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_pools(http, &p).await
        }
        "raydium_search_pools" => {
            let p: raydium::RaydiumSearchPoolsParams = serde_json::from_value(params)?;
            raydium::build_raydium_search_pools(http, &p).await
        }
        "raydium_get_pool_info" => {
            let p: raydium::RaydiumGetPoolInfoParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_pool_info(http, &p).await
        }
        "raydium_get_user_positions" => {
            let p: raydium::RaydiumGetUserPositionsParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_user_positions(http, &user_pubkey.to_string(), &p).await
        }
        "raydium_get_clmm_positions" => {
            let p: raydium::RaydiumGetClmmPositionsParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_clmm_positions(http, &user_pubkey.to_string(), &p).await
        }
        "raydium_get_token_info" => {
            let p: raydium::RaydiumGetTokenInfoParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_token_info(http, &p).await
        }
        "raydium_get_platform_stats" => {
            let p: raydium::RaydiumGetPlatformStatsParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_platform_stats(http, &p).await
        }
        "raydium_get_clmm_configs" => {
            let p: raydium::RaydiumGetClmmConfigsParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_clmm_configs(http, &p).await
        }
        "raydium_swap_quote" => {
            let p: raydium::RaydiumSwapQuoteParams = serde_json::from_value(params)?;
            raydium::build_raydium_swap_quote(http, &p).await
        }
        "raydium_get_pools_by_lp" => {
            let p: raydium::RaydiumGetPoolsByLpParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_pools_by_lp(http, &p).await
        }
        "raydium_get_pools_v2" => {
            let p: raydium::RaydiumGetPoolsV2Params = serde_json::from_value(params)?;
            raydium::build_raydium_get_pools_v2(http, &p).await
        }
        "raydium_get_pool_keys" => {
            let p: raydium::RaydiumGetPoolKeysParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_pool_keys(http, &p).await
        }
        "raydium_get_pool_liquidity_history" => {
            let p: raydium::RaydiumGetPoolLiquidityHistoryParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_pool_liquidity_history(http, &p).await
        }
        "raydium_get_pool_position_history" => {
            let p: raydium::RaydiumGetPoolPositionHistoryParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_pool_position_history(http, &p).await
        }
        "raydium_get_token_list" => {
            let p: raydium::RaydiumGetTokenListParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_token_list(http, &p).await
        }
        "raydium_get_token_prices" => {
            let p: raydium::RaydiumGetTokenPricesParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_token_prices(http, &p).await
        }
        "raydium_get_farm_info" => {
            let p: raydium::RaydiumGetFarmInfoParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_farm_info(http, &p).await
        }
        "raydium_get_farm_by_lp" => {
            let p: raydium::RaydiumGetFarmByLpParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_farm_by_lp(http, &p).await
        }
        "raydium_get_farm_keys" => {
            let p: raydium::RaydiumGetFarmKeysParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_farm_keys(http, &p).await
        }
        "raydium_get_ido_keys" => {
            let p: raydium::RaydiumGetIdoKeysParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_ido_keys(http, &p).await
        }
        "raydium_get_main_version" => {
            let p: raydium::RaydiumGetMainVersionParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_main_version(http, &p).await
        }
        "raydium_get_rpcs" => {
            let p: raydium::RaydiumGetRpcsParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_rpcs(http, &p).await
        }
        "raydium_get_chain_time" => {
            let p: raydium::RaydiumGetChainTimeParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_chain_time(http, &p).await
        }
        "raydium_get_stake_pools" => {
            let p: raydium::RaydiumGetStakePoolsParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_stake_pools(http, &p).await
        }
        "raydium_get_migrate_lp" => {
            let p: raydium::RaydiumGetMigrateLpParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_migrate_lp(http, &p).await
        }
        "raydium_get_auto_fee" => {
            let p: raydium::RaydiumGetAutoFeeParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_auto_fee(http, &p).await
        }
        "raydium_get_cpmm_configs" => {
            let p: raydium::RaydiumGetCpmmConfigsParams = serde_json::from_value(params)?;
            raydium::build_raydium_get_cpmm_configs(http, &p).await
        }
        // ── Orca Whirlpools Actions ─────────────────────────────────────────────────
        "orca_swap" => {
            let p: orca::OrcaSwapParams = serde_json::from_value(params)?;
            orca::build_orca_swap(
                http,
                rpc.endpoint(),
                jupiter_api_key,
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "orca_add_liquidity" => {
            let p: orca::OrcaAddLiquidityParams = serde_json::from_value(params)?;
            orca::build_orca_add_liquidity(http, rpc.endpoint(), &user_pubkey.to_string(), &p).await
        }
        "orca_remove_liquidity" => {
            let p: orca::OrcaRemoveLiquidityParams = serde_json::from_value(params)?;
            orca::build_orca_remove_liquidity(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "orca_open_position" => {
            let p: orca::OrcaOpenPositionParams = serde_json::from_value(params)?;
            orca::build_orca_open_position(http, rpc.endpoint(), &user_pubkey.to_string(), &p).await
        }
        "orca_close_position" => {
            let p: orca::OrcaClosePositionParams = serde_json::from_value(params)?;
            orca::build_orca_close_position(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "orca_increase_position" => {
            let p: orca::OrcaIncreasePositionParams = serde_json::from_value(params)?;
            orca::build_orca_increase_position(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "orca_decrease_position" => {
            let p: orca::OrcaDecreasePositionParams = serde_json::from_value(params)?;
            orca::build_orca_decrease_position(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "orca_collect_fees" => {
            let p: orca::OrcaCollectFeesParams = serde_json::from_value(params)?;
            orca::build_orca_collect_fees(http, rpc.endpoint(), &user_pubkey.to_string(), &p).await
        }
        "orca_collect_rewards" => {
            let p: orca::OrcaCollectRewardsParams = serde_json::from_value(params)?;
            orca::build_orca_collect_rewards(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "orca_create_pool" => {
            let p: orca::OrcaCreatePoolParams = serde_json::from_value(params)?;
            orca::build_orca_create_pool(http, rpc.endpoint(), &user_pubkey.to_string(), &p).await
        }
        "orca_get_pools" => {
            let p: orca::OrcaGetPoolsParams = serde_json::from_value(params)?;
            orca::build_orca_get_pools(http, &p).await
        }
        "orca_search_pools" => {
            let p: orca::OrcaSearchPoolsParams = serde_json::from_value(params)?;
            orca::build_orca_search_pools(http, &p).await
        }
        "orca_get_pool" => {
            let p: orca::OrcaGetPoolParams = serde_json::from_value(params)?;
            orca::build_orca_get_pool(http, &p).await
        }
        "orca_get_locked_liquidity" => {
            let p: orca::OrcaGetLockedLiquidityParams = serde_json::from_value(params)?;
            orca::build_orca_get_locked_liquidity(http, &p).await
        }
        "orca_get_protocol_stats" => {
            let p: orca::OrcaGetProtocolStatsParams = serde_json::from_value(params)?;
            orca::build_orca_get_protocol_stats(http, &p).await
        }
        "orca_get_orca_token" => {
            let p: orca::OrcaGetOrcaTokenParams = serde_json::from_value(params)?;
            orca::build_orca_get_orca_token(http, &p).await
        }
        "orca_get_circulating_supply" => {
            let p: orca::OrcaGetCirculatingSupplyParams = serde_json::from_value(params)?;
            orca::build_orca_get_circulating_supply(http, &p).await
        }
        "orca_get_total_supply" => {
            let p: orca::OrcaGetTotalSupplyParams = serde_json::from_value(params)?;
            orca::build_orca_get_total_supply(http, &p).await
        }
        "orca_get_tokens" => {
            let p: orca::OrcaGetTokensParams = serde_json::from_value(params)?;
            orca::build_orca_get_tokens(http, &p).await
        }
        "orca_search_tokens" => {
            let p: orca::OrcaSearchTokensParams = serde_json::from_value(params)?;
            orca::build_orca_search_tokens(http, &p).await
        }
        "orca_get_token" => {
            let p: orca::OrcaGetTokenParams = serde_json::from_value(params)?;
            orca::build_orca_get_token(http, &p).await
        }
        "orca_get_user_positions" => {
            let p: orca::OrcaGetUserPositionsParams = serde_json::from_value(params)?;
            orca::build_orca_get_user_positions(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "orca_get_pool_positions" => {
            let p: orca::OrcaGetPoolPositionsParams = serde_json::from_value(params)?;
            orca::build_orca_get_pool_positions(http, rpc.endpoint(), &p).await
        }
        // ── Kamino Finance Actions ───────────────────────────────────────────────────
        "kamino_deposit" => {
            let p: kamino::KaminoDepositParams = serde_json::from_value(params)?;
            kamino::build_kamino_deposit(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_withdraw" => {
            let p: kamino::KaminoWithdrawParams = serde_json::from_value(params)?;
            kamino::build_kamino_withdraw(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_borrow" => {
            let p: kamino::KaminoBorrowParams = serde_json::from_value(params)?;
            kamino::build_kamino_borrow(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_repay" => {
            let p: kamino::KaminoRepayParams = serde_json::from_value(params)?;
            kamino::build_kamino_repay(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_add_collateral" => {
            let p: kamino::KaminoAddCollateralParams = serde_json::from_value(params)?;
            kamino::build_kamino_add_collateral(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_withdraw_collateral" => {
            let p: kamino::KaminoWithdrawCollateralParams = serde_json::from_value(params)?;
            kamino::build_kamino_withdraw_collateral(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_multiply_open" => {
            let p: kamino::KaminoMultiplyOpenParams = serde_json::from_value(params)?;
            kamino::build_kamino_multiply_open(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_multiply_add" => {
            let p: kamino::KaminoMultiplyAddParams = serde_json::from_value(params)?;
            kamino::build_kamino_multiply_add(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_multiply_withdraw" => {
            let p: kamino::KaminoMultiplyWithdrawParams = serde_json::from_value(params)?;
            kamino::build_kamino_multiply_withdraw(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_multiply_close" => {
            let p: kamino::KaminoMultiplyCloseParams = serde_json::from_value(params)?;
            kamino::build_kamino_multiply_close(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_long_open" => {
            let p: kamino::KaminoLongOpenParams = serde_json::from_value(params)?;
            kamino::build_kamino_long_open(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_short_open" => {
            let p: kamino::KaminoShortOpenParams = serde_json::from_value(params)?;
            kamino::build_kamino_short_open(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_position_close" => {
            let p: kamino::KaminoPositionCloseParams = serde_json::from_value(params)?;
            kamino::build_kamino_position_close(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_deposit" => {
            let p: kamino::KaminoVaultDepositParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_deposit(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_withdraw" => {
            let p: kamino::KaminoVaultWithdrawParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_withdraw(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_stake" => {
            let p: kamino::KaminoStakeParams = serde_json::from_value(params)?;
            kamino::build_kamino_stake(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_unstake" => {
            let p: kamino::KaminoUnstakeParams = serde_json::from_value(params)?;
            kamino::build_kamino_unstake(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vaults" => {
            let p: kamino::KaminoVaultsParams = serde_json::from_value(params)?;
            kamino::build_kamino_vaults(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_vault_positions" => {
            let p: kamino::KaminoUserVaultPositionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_vault_positions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_markets" => {
            let p: kamino::KaminoMarketsParams = serde_json::from_value(params)?;
            kamino::build_kamino_markets(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_market_reserves" => {
            let p: kamino::KaminoMarketReservesParams = serde_json::from_value(params)?;
            kamino::build_kamino_market_reserves(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_obligations" => {
            let p: kamino::KaminoUserObligationsParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_obligations(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_oracle_prices" => {
            let p: kamino::KaminoOraclePricesParams = serde_json::from_value(params)?;
            kamino::build_kamino_oracle_prices(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_usd_benchmark_rates" => {
            let p: kamino::KaminoUsdBenchmarkRatesParams = serde_json::from_value(params)?;
            kamino::build_kamino_usd_benchmark_rates(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_detail" => {
            let p: kamino::KaminoVaultDetailParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_detail(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_metrics" => {
            let p: kamino::KaminoVaultMetricsParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_metrics(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_metrics_history" => {
            let p: kamino::KaminoVaultMetricsHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_metrics_history(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_allocation_history" => {
            let p: kamino::KaminoVaultAllocationHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_allocation_history(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vaults_rewards" => {
            let p: kamino::KaminoVaultsRewardsParams = serde_json::from_value(params)?;
            kamino::build_kamino_vaults_rewards(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vaults_summary" => {
            let p: kamino::KaminoVaultsSummaryParams = serde_json::from_value(params)?;
            kamino::build_kamino_vaults_summary(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_mint_metadata" => {
            let p: kamino::KaminoVaultMintMetadataParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_mint_metadata(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_mint_image" => {
            let p: kamino::KaminoVaultMintImageParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_mint_image(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_metrics_history" => {
            let p: kamino::KaminoUserMetricsHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_metrics_history(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_transactions" => {
            let p: kamino::KaminoUserTransactionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_transactions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_kvault_rewards" => {
            let p: kamino::KaminoUserKvaultRewardsParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_kvault_rewards(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_transactions" => {
            let p: kamino::KaminoVaultTransactionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_transactions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_vault_position" => {
            let p: kamino::KaminoUserVaultPositionParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_vault_position(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_vault_metrics_history" => {
            let p: kamino::KaminoUserVaultMetricsHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_vault_metrics_history(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_user_vault_pnl" => {
            let p: kamino::KaminoUserVaultPnlParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_vault_pnl(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_vault_pnl_history" => {
            let p: kamino::KaminoUserVaultPnlHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_vault_pnl_history(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_vault_deposit_instructions" => {
            let p: kamino::KaminoVaultDepositInstructionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_deposit_instructions(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_vault_withdraw_instructions" => {
            let p: kamino::KaminoVaultWithdrawInstructionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_vault_withdraw_instructions(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_market_detail" => {
            let p: kamino::KaminoMarketDetailParams = serde_json::from_value(params)?;
            kamino::build_kamino_market_detail(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_market_reserve_history" => {
            let p: kamino::KaminoMarketReserveHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_market_reserve_history(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_market_leverage_metrics" => {
            let p: kamino::KaminoMarketLeverageMetricsParams = serde_json::from_value(params)?;
            kamino::build_kamino_market_leverage_metrics(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_market_metrics_history" => {
            let p: kamino::KaminoMarketMetricsHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_market_metrics_history(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_reserve_borrow_apy_history" => {
            let p: kamino::KaminoReserveBorrowApyHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_reserve_borrow_apy_history(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_reserve_borrow_apy_median" => {
            let p: kamino::KaminoReserveBorrowApyMedianParams = serde_json::from_value(params)?;
            kamino::build_kamino_reserve_borrow_apy_median(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_obligation_interest_earned" => {
            let p: kamino::KaminoObligationInterestEarnedParams = serde_json::from_value(params)?;
            kamino::build_kamino_obligation_interest_earned(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_obligation_interest_paid" => {
            let p: kamino::KaminoObligationInterestPaidParams = serde_json::from_value(params)?;
            kamino::build_kamino_obligation_interest_paid(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_obligation_transactions" => {
            let p: kamino::KaminoObligationTransactionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_obligation_transactions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_klend_transactions_all" => {
            let p: kamino::KaminoUserKlendTransactionsAllParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_klend_transactions_all(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_user_klend_transactions" => {
            let p: kamino::KaminoUserKlendTransactionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_klend_transactions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_borrow_order_fills" => {
            let p: kamino::KaminoBorrowOrderFillsParams = serde_json::from_value(params)?;
            kamino::build_kamino_borrow_order_fills(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_open_borrow_orders" => {
            let p: kamino::KaminoOpenBorrowOrdersParams = serde_json::from_value(params)?;
            kamino::build_kamino_open_borrow_orders(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_yield_history" => {
            let p: kamino::KaminoYieldHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_yield_history(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_principal_token_yields" => {
            let p: kamino::KaminoPrincipalTokenYieldsParams = serde_json::from_value(params)?;
            kamino::build_kamino_principal_token_yields(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_airdrop_allocations" => {
            let p: kamino::KaminoAirdropAllocationsParams = serde_json::from_value(params)?;
            kamino::build_kamino_airdrop_allocations(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_airdrop_metrics" => {
            let p: kamino::KaminoAirdropMetricsParams = serde_json::from_value(params)?;
            kamino::build_kamino_airdrop_metrics(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_staking_yields" => {
            let p: kamino::KaminoStakingYieldsParams = serde_json::from_value(params)?;
            kamino::build_kamino_staking_yields(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_staking_yields_median" => {
            let p: kamino::KaminoStakingYieldsMedianParams = serde_json::from_value(params)?;
            kamino::build_kamino_staking_yields_median(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_staking_yields_mean" => {
            let p: kamino::KaminoStakingYieldsMeanParams = serde_json::from_value(params)?;
            kamino::build_kamino_staking_yields_mean(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_staking_boosts" => {
            let p: kamino::KaminoUserStakingBoostsParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_staking_boosts(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_season_rewards_user" => {
            let p: kamino::KaminoSeasonRewardsUserParams = serde_json::from_value(params)?;
            kamino::build_kamino_season_rewards_user(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_season_rewards_vesting_pool" => {
            let p: kamino::KaminoSeasonRewardsVestingPoolParams = serde_json::from_value(params)?;
            kamino::build_kamino_season_rewards_vesting_pool(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_private_credit_metrics" => {
            let p: kamino::KaminoPrivateCreditMetricsParams = serde_json::from_value(params)?;
            kamino::build_kamino_private_credit_metrics(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_private_credit_metrics_history" => {
            let p: kamino::KaminoPrivateCreditMetricsHistoryParams =
                serde_json::from_value(params)?;
            kamino::build_kamino_private_credit_metrics_history(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_user_farm_transactions" => {
            let p: kamino::KaminoUserFarmTransactionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_farm_transactions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_farm_transactions" => {
            let p: kamino::KaminoFarmTransactionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_farm_transactions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_market_reserves_account" => {
            let p: kamino::KaminoMarketReservesAccountParams = serde_json::from_value(params)?;
            kamino::build_kamino_market_reserves_account(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_user_rewards" => {
            let p: kamino::KaminoUserRewardsParams = serde_json::from_value(params)?;
            kamino::build_kamino_user_rewards(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_loan_detail" => {
            let p: kamino::KaminoLoanDetailParams = serde_json::from_value(params)?;
            kamino::build_kamino_loan_detail(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_obligation_pnl" => {
            let p: kamino::KaminoObligationPnlParams = serde_json::from_value(params)?;
            kamino::build_kamino_obligation_pnl(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_obligation_metrics_history" => {
            let p: kamino::KaminoObligationMetricsHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_obligation_metrics_history(http, &user_pubkey.to_string(), &p)
                .await
        }
        "kamino_rewards_list" => {
            let p: kamino::KaminoRewardsListParams = serde_json::from_value(params)?;
            kamino::build_kamino_rewards_list(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_rewards_history" => {
            let p: kamino::KaminoRewardsHistoryParams = serde_json::from_value(params)?;
            kamino::build_kamino_rewards_history(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_borrow_instructions" => {
            let p: kamino::KaminoBorrowInstructionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_borrow_instructions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_repay_instructions" => {
            let p: kamino::KaminoRepayInstructionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_repay_instructions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_kswap" => {
            let p: kamino::KaminoKswapParams = serde_json::from_value(params)?;
            kamino::build_kamino_kswap(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_deposit_instructions" => {
            let p: kamino::KaminoDepositInstructionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_deposit_instructions(http, &user_pubkey.to_string(), &p).await
        }
        "kamino_withdraw_instructions" => {
            let p: kamino::KaminoWithdrawInstructionsParams = serde_json::from_value(params)?;
            kamino::build_kamino_withdraw_instructions(http, &user_pubkey.to_string(), &p).await
        }
        // ── Jito Finance Actions ─────────────────────────────────────────────────────
        "jito_stake" => {
            let p: jito::JitoStakeParams = serde_json::from_value(params)?;
            jito::build_jito_stake_action(http, rpc, user_pubkey, &p).await
        }
        "jito_unstake" => {
            let p: jito::JitoUnstakeParams = serde_json::from_value(params)?;
            jito::build_jito_unstake_action(http, rpc, user_pubkey, &p).await
        }
        "jito_tip" => {
            let p: jito::JitoTipParams = serde_json::from_value(params)?;
            jito::build_jito_tip(http, rpc, user_pubkey, &p).await
        }
        "jito_bundle" => {
            let p: jito::JitoBundleParams = serde_json::from_value(params)?;
            jito::build_jito_bundle(http, &user_pubkey.to_string(), &p).await
        }
        "jito_bundle_status" => {
            let p: jito::JitoBundleStatusParams = serde_json::from_value(params)?;
            let result = jito::get_jito_bundle_status(http, &p.bundle_id).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: p.bundle_id.clone(),
                    action_type: "jito_bundle_status".to_string(),
                    description: format!("Bundle {} status: {}", p.bundle_id, result.status),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::to_value(&result)?,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }
        // ── Meteora Protocol Actions ─────────────────────────────────────────────────
        "meteora_swap" => {
            let p: meteora::MeteoraSwapParams = serde_json::from_value(params)?;
            meteora::build_meteora_swap(http, rpc.endpoint(), &user_pubkey.to_string(), &p).await
        }
        "meteora_add_liquidity" => {
            let p: meteora::MeteoraAddLiquidityParams = serde_json::from_value(params)?;
            meteora::build_meteora_add_liquidity(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_remove_liquidity" => {
            let p: meteora::MeteoraRemoveLiquidityParams = serde_json::from_value(params)?;
            meteora::build_meteora_remove_liquidity(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_create_pool" => {
            let p: meteora::MeteoraCreatePoolParams = serde_json::from_value(params)?;
            meteora::build_meteora_create_pool(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_open_position" => {
            let p: meteora::MeteoraOpenPositionParams = serde_json::from_value(params)?;
            meteora::build_meteora_open_position(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_close_position" => {
            let p: meteora::MeteoraClosePositionParams = serde_json::from_value(params)?;
            meteora::build_meteora_close_position(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_add_to_position" => {
            let p: meteora::MeteoraAddToPositionParams = serde_json::from_value(params)?;
            meteora::build_meteora_add_to_position(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_claim_fees" => {
            let p: meteora::MeteoraClaimFeesParams = serde_json::from_value(params)?;
            meteora::build_meteora_claim_fees(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_claim_rewards" => {
            let p: meteora::MeteoraClaimRewardsParams = serde_json::from_value(params)?;
            meteora::build_meteora_claim_rewards(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_stake" => {
            let p: meteora::MeteoraStakeParams = serde_json::from_value(params)?;
            meteora::build_meteora_stake(http, rpc.endpoint(), &user_pubkey.to_string(), &p).await
        }
        "meteora_unstake" => {
            let p: meteora::MeteoraUnstakeParams = serde_json::from_value(params)?;
            meteora::build_meteora_unstake(http, rpc.endpoint(), &user_pubkey.to_string(), &p).await
        }
        "meteora_harvest" => {
            let p: meteora::MeteoraHarvestParams = serde_json::from_value(params)?;
            meteora::build_meteora_harvest(http, rpc.endpoint(), &user_pubkey.to_string(), &p).await
        }
        // ── Meteora GET Query Actions ────────────────────────────────────────────────
        "meteora_dlmm_get_pairs" => {
            let p: meteora::MeteoraDlmmGetPairsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_pairs(http, &p).await
        }
        "meteora_dlmm_get_pair" => {
            let p: meteora::MeteoraDlmmGetPairParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_pair(http, &p).await
        }
        "meteora_dlmm_get_user_positions" => {
            let p: meteora::MeteoraDlmmGetUserPositionsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_user_positions(http, &user_pubkey.to_string(), &p).await
        }
        "meteora_dlmm_get_active_bin" => {
            let p: meteora::MeteoraDlmmGetActiveBinParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_active_bin(http, &p).await
        }
        "meteora_dlmm_get_pool_groups" => {
            let p: meteora::MeteoraDlmmGetPoolGroupsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_pool_groups(http, &p).await
        }
        "meteora_dlmm_get_pool_group" => {
            let p: meteora::MeteoraDlmmGetPoolGroupParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_pool_group(http, &p).await
        }
        "meteora_dlmm_get_pool_ohlcv" => {
            let p: meteora::MeteoraDlmmGetPoolOhlcvParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_pool_ohlcv(http, &p).await
        }
        "meteora_dlmm_get_pool_volume_history" => {
            let p: meteora::MeteoraDlmmGetPoolVolumeHistoryParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_pool_volume_history(http, &p).await
        }
        "meteora_dlmm_get_protocol_stats" => {
            let p: meteora::MeteoraDlmmGetProtocolStatsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dlmm_get_protocol_stats(http, &p).await
        }
        "meteora_dammv2_get_pools" => {
            let p: meteora::MeteoraDammV2GetPoolsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_get_pools(http, &p).await
        }
        "meteora_dammv2_get_pool_groups" => {
            let p: meteora::MeteoraDammV2GetPoolGroupsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_get_pool_groups(http, &p).await
        }
        "meteora_dammv2_get_pool_group" => {
            let p: meteora::MeteoraDammV2GetPoolGroupParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_get_pool_group(http, &p).await
        }
        "meteora_dammv2_get_pool" => {
            let p: meteora::MeteoraDammV2GetPoolParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_get_pool(http, &p).await
        }
        "meteora_dammv2_get_pool_ohlcv" => {
            let p: meteora::MeteoraDammV2GetPoolOhlcvParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_get_pool_ohlcv(http, &p).await
        }
        "meteora_dammv2_get_pool_volume_history" => {
            let p: meteora::MeteoraDammV2GetPoolVolumeHistoryParams =
                serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_get_pool_volume_history(http, &p).await
        }
        "meteora_dammv2_get_protocol_metrics" => {
            let p: meteora::MeteoraDammV2GetProtocolMetricsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_get_protocol_metrics(http, &p).await
        }
        "meteora_dammv1_get_pools" => {
            let p: meteora::MeteoraDammV1GetPoolsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_get_pools(http, &p).await
        }
        "meteora_dammv1_get_pool_configs" => {
            let p: meteora::MeteoraDammV1GetPoolConfigsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_get_pool_configs(http, &p).await
        }
        "meteora_dammv1_search_pools" => {
            let p: meteora::MeteoraDammV1SearchPoolsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_search_pools(http, &p).await
        }
        "meteora_dammv1_get_farms" => {
            let p: meteora::MeteoraDammV1GetFarmsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_get_farms(http, &p).await
        }
        "meteora_dammv1_get_pools_metrics" => {
            let p: meteora::MeteoraDammV1GetPoolsMetricsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_get_pools_metrics(http, &p).await
        }
        "meteora_dammv1_get_alpha_vaults" => {
            let p: meteora::MeteoraDammV1GetAlphaVaultsParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_get_alpha_vaults(http, &p).await
        }
        "meteora_dammv1_get_alpha_vault_configs" => {
            let p: meteora::MeteoraDammV1GetAlphaVaultConfigsParams =
                serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_get_alpha_vault_configs(http, &p).await
        }
        "meteora_dammv1_get_pools_by_vault_lp" => {
            let p: meteora::MeteoraDammV1GetPoolsByVaultLpParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_get_pools_by_vault_lp(http, &p).await
        }
        "meteora_dammv1_get_fee_config" => {
            let p: meteora::MeteoraDammV1GetFeeConfigParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_get_fee_config(http, &p).await
        }
        "meteora_s2e_get_analytics" => {
            let p: meteora::MeteoraS2EGetAnalyticsParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_get_analytics(http, &p).await
        }
        "meteora_s2e_get_all_vaults" => {
            let p: meteora::MeteoraS2EGetAllVaultsParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_get_all_vaults(http, &p).await
        }
        "meteora_s2e_filter_vaults" => {
            let p: meteora::MeteoraS2EFilterVaultsParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_filter_vaults(http, &p).await
        }
        "meteora_s2e_get_vault" => {
            let p: meteora::MeteoraS2EGetVaultParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_get_vault(http, &p).await
        }
        "meteora_vault_get_info" => {
            let p: meteora::MeteoraVaultGetInfoParams = serde_json::from_value(params)?;
            meteora::build_meteora_vault_get_info(http, &p).await
        }
        "meteora_vault_get_addresses" => {
            let p: meteora::MeteoraVaultGetAddressesParams = serde_json::from_value(params)?;
            meteora::build_meteora_vault_get_addresses(http, &p).await
        }
        "meteora_vault_get_state" => {
            let p: meteora::MeteoraVaultGetStateParams = serde_json::from_value(params)?;
            meteora::build_meteora_vault_get_state(http, &p).await
        }
        "meteora_vault_get_apy" => {
            let p: meteora::MeteoraVaultGetApyParams = serde_json::from_value(params)?;
            meteora::build_meteora_vault_get_apy(http, &p).await
        }
        "meteora_vault_get_apy_history" => {
            let p: meteora::MeteoraVaultGetApyHistoryParams = serde_json::from_value(params)?;
            meteora::build_meteora_vault_get_apy_history(http, &p).await
        }
        "meteora_vault_get_virtual_price" => {
            let p: meteora::MeteoraVaultGetVirtualPriceParams = serde_json::from_value(params)?;
            meteora::build_meteora_vault_get_virtual_price(http, &p).await
        }
        // ── Meteora TX Actions (new) ─────────────────────────────────────────────────
        "meteora_dammv1_swap" => {
            let p: meteora::MeteoraDammV1SwapParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_swap(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_dammv1_deposit" => {
            let p: meteora::MeteoraDammV1DepositParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_deposit(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_dammv1_withdraw" => {
            let p: meteora::MeteoraDammV1WithdrawParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv1_withdraw(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_dammv2_swap" => {
            let p: meteora::MeteoraDammV2SwapParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_swap(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_dammv2_add_liquidity" => {
            let p: meteora::MeteoraDammV2AddLiquidityParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_add_liquidity(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_dammv2_remove_liquidity" => {
            let p: meteora::MeteoraDammV2RemoveLiquidityParams = serde_json::from_value(params)?;
            meteora::build_meteora_dammv2_remove_liquidity(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_vault_deposit" => {
            let p: meteora::MeteoraVaultDepositParams = serde_json::from_value(params)?;
            meteora::build_meteora_vault_deposit(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_vault_withdraw" => {
            let p: meteora::MeteoraVaultWithdrawParams = serde_json::from_value(params)?;
            meteora::build_meteora_vault_withdraw(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_s2e_stake" => {
            let p: meteora::MeteoraS2EStakeParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_stake(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_s2e_unstake" => {
            let p: meteora::MeteoraS2EUnstakeParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_unstake(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_s2e_claim_fee" => {
            let p: meteora::MeteoraS2EClaimFeeParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_claim_fee(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        "meteora_s2e_cancel_unstake" => {
            let p: meteora::MeteoraS2ECancelUnstakeParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_cancel_unstake(
                http,
                rpc.endpoint(),
                &user_pubkey.to_string(),
                &p,
            )
            .await
        }
        "meteora_s2e_withdraw" => {
            let p: meteora::MeteoraS2EWithdrawParams = serde_json::from_value(params)?;
            meteora::build_meteora_s2e_withdraw(http, rpc.endpoint(), &user_pubkey.to_string(), &p)
                .await
        }
        // ── Marinade Finance Actions ─────────────────────────────────────────────────
        "marinade_stake" => {
            let p: marinade::MarinadeStakeParams = serde_json::from_value(params)?;
            marinade::build_marinade_stake(http, user_pubkey.to_string().as_str(), &p).await
        }
        "marinade_unstake" => {
            let p: marinade::MarinadeUnstakeParams = serde_json::from_value(params)?;
            marinade::build_marinade_unstake(http, user_pubkey.to_string().as_str(), &p).await
        }
        "marinade_delayed_unstake" => {
            let p: marinade::MarinadeDelayedUnstakeParams = serde_json::from_value(params)?;
            marinade::build_marinade_delayed_unstake(http, user_pubkey.to_string().as_str(), &p)
                .await
        }
        "marinade_claim_ticket" | "marinade_claim" => {
            let p: marinade::MarinadeClaimTicketParams = serde_json::from_value(params)?;
            marinade::build_marinade_claim_ticket(user_pubkey.to_string().as_str(), &p).await
        }
        // ── marginfi v2 Protocol Actions ─────────────────────────────────────────────
        "marginfi_create_account" => {
            let p: marginfi::MarginfiCreateAccountParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_create_account(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_create_account_pda" => {
            let p: marginfi::MarginfiCreateAccountPdaParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_create_account_pda(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_close_account" => {
            let p: marginfi::MarginfiCloseAccountParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_close_account(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_close_balance" => {
            let p: marginfi::MarginfiCloseBalanceParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_close_balance(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_transfer_account" => {
            let p: marginfi::MarginfiTransferAccountParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_transfer_account(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_deposit" => {
            let p: marginfi::MarginfiDepositParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_deposit(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_withdraw" => {
            let p: marginfi::MarginfiWithdrawParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_withdraw(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_borrow" => {
            let p: marginfi::MarginfiBorrowParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_borrow(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_repay" => {
            let p: marginfi::MarginfiRepayParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_repay(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_liquidate" => {
            let p: marginfi::MarginfiLiquidateParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_liquidate(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_start_liquidation" => {
            let p: marginfi::MarginfiStartLiquidationParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_start_liquidation(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_end_liquidation" => {
            let p: marginfi::MarginfiEndLiquidationParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_end_liquidation(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_flashloan_start" => {
            let p: marginfi::MarginfiFlashloanStartParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_flashloan_start(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_flashloan_end" => {
            let p: marginfi::MarginfiFlashloanEndParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_flashloan_end(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_place_order" => {
            let p: marginfi::MarginfiPlaceOrderParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_place_order(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_close_order" => {
            let p: marginfi::MarginfiCloseOrderParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_close_order(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_execute_order_start" => {
            let p: marginfi::MarginfiExecuteOrderStartParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_execute_order_start(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_execute_order_end" => {
            let p: marginfi::MarginfiExecuteOrderEndParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_execute_order_end(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_accrue_interest" => {
            let p: marginfi::MarginfiAccrueInterestParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_accrue_interest(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_pulse_price" => {
            let p: marginfi::MarginfiPulsePriceParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_pulse_price(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_pulse_health" => {
            let p: marginfi::MarginfiPulseHealthParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_pulse_health(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_account_info" => {
            let p: marginfi::MarginfiAccountInfoParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_account_info(http, user_pubkey.to_string().as_str(), &p).await
        }
        "marginfi_banks" => {
            let p: marginfi::MarginfiBanksParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_banks(http, user_pubkey.to_string().as_str(), &p).await
        }
        "marginfi_health" => {
            let p: marginfi::MarginfiHealthParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_health(http, user_pubkey.to_string().as_str(), &p).await
        }
        "marginfi_points" => {
            let p: marginfi::MarginfiPointsParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_points(http, user_pubkey.to_string().as_str(), &p).await
        }
        "marginfi_bank_detail" => {
            let p: marginfi::MarginfiBankDetailParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_bank_detail(http, user_pubkey.to_string().as_str(), &p).await
        }
        "marginfi_user_accounts" => {
            let p: marginfi::MarginfiUserAccountsParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_user_accounts(http, user_pubkey.to_string().as_str(), &p).await
        }
        "marginfi_claim_emissions" => {
            let p: marginfi::MarginfiClaimEmissionsParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_claim_emissions(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_settle_emissions" => {
            let p: marginfi::MarginfiSettleEmissionsParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_settle_emissions(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_withdraw_emissions_permissionless" => {
            let p: marginfi::MarginfiWithdrawEmissionsPermissionlessParams =
                serde_json::from_value(params)?;
            marginfi::build_marginfi_withdraw_emissions_permissionless(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_set_keeper_flags" => {
            let p: marginfi::MarginfiSetKeeperFlagsParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_set_keeper_flags(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_init_liq_record" => {
            let p: marginfi::MarginfiInitLiqRecordParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_init_liq_record(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_update_emissions_destination" => {
            let p: marginfi::MarginfiUpdateEmissionsDestinationParams =
                serde_json::from_value(params)?;
            marginfi::build_marginfi_update_emissions_destination(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "marginfi_clear_emissions" => {
            let p: marginfi::MarginfiClearEmissionsParams = serde_json::from_value(params)?;
            marginfi::build_marginfi_clear_emissions(
                http,
                rpc.endpoint(),
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        // ── Solend Protocol Actions ────────────────────────────────────────────────
        "solend_deposit" => {
            let p: solend::SolendDepositParams = serde_json::from_value(params)?;
            solend::build_solend_deposit(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_withdraw" => {
            let p: solend::SolendWithdrawParams = serde_json::from_value(params)?;
            solend::build_solend_withdraw(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_borrow" => {
            let p: solend::SolendBorrowParams = serde_json::from_value(params)?;
            solend::build_solend_borrow(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_repay" => {
            let p: solend::SolendRepayParams = serde_json::from_value(params)?;
            solend::build_solend_repay(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_add_collateral" => {
            let p: solend::SolendAddCollateralParams = serde_json::from_value(params)?;
            solend::build_solend_add_collateral(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_withdraw_collateral" => {
            let p: solend::SolendWithdrawCollateralParams = serde_json::from_value(params)?;
            solend::build_solend_withdraw_collateral(http, user_pubkey.to_string().as_str(), &p)
                .await
        }
        "solend_liquidate" => {
            let p: solend::SolendLiquidateParams = serde_json::from_value(params)?;
            solend::build_solend_liquidate(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_user_info" => {
            let p: solend::SolendUserInfoParams = serde_json::from_value(params)?;
            solend::build_solend_user_info(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_market" => {
            let p: solend::SolendMarketParams = serde_json::from_value(params)?;
            solend::build_solend_market(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_reserves" => {
            let p: solend::SolendReservesParams = serde_json::from_value(params)?;
            solend::build_solend_reserves(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_stats" => {
            let p: solend::SolendStatsParams = serde_json::from_value(params)?;
            solend::build_solend_stats(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_lst_rates" => {
            let p: solend::SolendLstRatesParams = serde_json::from_value(params)?;
            solend::build_solend_lst_rates(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_prices" => {
            let p: solend::SolendPricesParams = serde_json::from_value(params)?;
            solend::build_solend_prices(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_reserves_history" => {
            let p: solend::SolendReservesHistoryParams = serde_json::from_value(params)?;
            solend::build_solend_reserves_history(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_daily_stats" => {
            let p: solend::SolendDailyStatsParams = serde_json::from_value(params)?;
            solend::build_solend_daily_stats(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_flash_loan" => {
            let p: solend::SolendFlashLoanParams = serde_json::from_value(params)?;
            solend::build_solend_flash_loan(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_claim_rewards" => {
            let p: solend::SolendClaimRewardsParams = serde_json::from_value(params)?;
            solend::build_solend_claim_rewards(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_deposit_liquidity" => {
            let p: solend::SolendDepositLiquidityParams = serde_json::from_value(params)?;
            solend::build_solend_deposit_liquidity(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_deposit_obligation_collateral" => {
            let p: solend::SolendDepositObligationCollateralParams =
                serde_json::from_value(params)?;
            solend::build_solend_deposit_obligation_collateral(
                http,
                user_pubkey.to_string().as_str(),
                &p,
            )
            .await
        }
        "solend_redeem_collateral" => {
            let p: solend::SolendRedeemCollateralParams = serde_json::from_value(params)?;
            solend::build_solend_redeem_collateral(http, user_pubkey.to_string().as_str(), &p).await
        }
        "solend_exercise_reward" => {
            let p: solend::SolendExerciseRewardParams = serde_json::from_value(params)?;
            solend::build_solend_exercise_reward(http, user_pubkey.to_string().as_str(), &p).await
        }
        // What can be checked about a token before someone spends money on it.
        // Reads the mint account for the facts that decide whether money can
        // be taken, then enriches with what the indexers know.
        "token_safety" | "honeypot_check" | "scam_check" | "rug_check" => {
            let p: token_safety::TokenSafetyParams = serde_json::from_value(params)?;
            // People ask about a ticker, not an address — "is BONK a scam".
            // Resolving through the verified-only path also means a symbol
            // that resolves to nothing is refused rather than guessed at,
            // which on a safety check is the only acceptable failure.
            let mint =
                crate::services::mint_security::resolve_action_mint(http, &p.mint_address).await?;
            let mut safety = token_safety::inspect_mint(rpc, &mint).await?;
            token_safety::enrich(http, &mut safety).await;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: Uuid::new_v4().to_string(),
                    action_type: action_type.to_string(),
                    description: "Token safety check".to_string(),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::json!({}),
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::to_value(&safety)?),
            })
        }
        // ── Magic Eden NFT Marketplace Actions ───────────────────────────────────────
        // Magic Eden's own API names the two sides of every trade separately
        // (`sell` vs `sell_cancel` vs `sell_now`), and the tool catalogue grew
        // both those names and our plainer ones. They are the same action;
        // aliasing here beats making the model guess which spelling works.
        "me_list" | "me_sell" => {
            let p: magic_eden::MeListParams = serde_json::from_value(params)?;
            magic_eden::build_me_list(http, rpc, user_pubkey, &p).await
        }
        "me_buy" | "me_buy_now" | "me_buy_instruction" | "me_buy_now_transfer_nft" => {
            let p: magic_eden::MeBuyParams = serde_json::from_value(params)?;
            magic_eden::build_me_buy(http, rpc, user_pubkey, &p).await
        }
        "me_cancel_listing" | "me_sell_cancel" => {
            let p: magic_eden::MeCancelListingParams = serde_json::from_value(params)?;
            magic_eden::build_me_cancel_listing(http, rpc, user_pubkey, &p).await
        }
        "me_sell_change_price" => {
            let p: magic_eden::MeChangePriceParams = serde_json::from_value(params)?;
            magic_eden::build_me_change_listing_price(http, rpc, user_pubkey, &p).await
        }
        "me_make_offer" => {
            let p: magic_eden::MeMakeOfferParams = serde_json::from_value(params)?;
            magic_eden::build_me_make_offer(http, rpc, user_pubkey, &p).await
        }
        "me_accept_offer" | "me_sell_now" => {
            let p: magic_eden::MeAcceptOfferParams = serde_json::from_value(params)?;
            magic_eden::build_me_accept_offer(http, rpc, user_pubkey, &p).await
        }
        "me_cancel_offer" | "me_buy_cancel" => {
            let p: magic_eden::MeCancelOfferParams = serde_json::from_value(params)?;
            magic_eden::build_me_cancel_offer(http, rpc, user_pubkey, &p).await
        }
        "me_buy_change_price" => {
            let p: magic_eden::MeChangePriceParams = serde_json::from_value(params)?;
            magic_eden::build_me_change_offer_price(http, rpc, user_pubkey, &p).await
        }
        "me_deposit" => {
            let p: magic_eden::MeEscrowParams = serde_json::from_value(params)?;
            magic_eden::build_me_deposit(http, rpc, user_pubkey, &p).await
        }
        // MMM — Magic Eden's NFT AMM. A pool quotes both sides of a
        // collection; these are its whole lifecycle.
        "me_mmm_create_pool" => {
            let p: magic_eden::MeMmmCreatePoolParams = serde_json::from_value(params)?;
            magic_eden::build_me_mmm_create_pool(http, user_pubkey, &p).await
        }
        "me_mmm_update_pool" => {
            let p: magic_eden::MeMmmUpdatePoolParams = serde_json::from_value(params)?;
            magic_eden::build_me_mmm_update_pool(http, user_pubkey, &p).await
        }
        "me_mmm_sol_close_pool" => {
            let p: magic_eden::MeMmmPoolParams = serde_json::from_value(params)?;
            magic_eden::build_me_mmm_close_pool(http, user_pubkey, &p).await
        }
        "me_mmm_sol_deposit_buy" => {
            let p: magic_eden::MeMmmFundParams = serde_json::from_value(params)?;
            magic_eden::build_me_mmm_deposit_buy(http, user_pubkey, &p).await
        }
        "me_mmm_sol_withdraw_buy" => {
            let p: magic_eden::MeMmmFundParams = serde_json::from_value(params)?;
            magic_eden::build_me_mmm_withdraw_buy(http, user_pubkey, &p).await
        }
        "me_mmm_sol_fulfill_buy" => {
            let p: magic_eden::MeMmmFulfillBuyParams = serde_json::from_value(params)?;
            magic_eden::build_me_mmm_fulfill_buy(http, user_pubkey, &p).await
        }
        "me_mmm_sol_fulfill_sell" => {
            let p: magic_eden::MeMmmFulfillSellParams = serde_json::from_value(params)?;
            magic_eden::build_me_mmm_fulfill_sell(http, user_pubkey, &p).await
        }
        "me_withdraw" => {
            let p: magic_eden::MeEscrowParams = serde_json::from_value(params)?;
            magic_eden::build_me_withdraw(http, rpc, user_pubkey, &p).await
        }
        "me_collection_info" => {
            let p: magic_eden::MeCollectionInfoParams = serde_json::from_value(params)?;
            magic_eden::build_me_collection_info(http, &p).await
        }
        "me_nft_info" => {
            let p: magic_eden::MeNFTInfoParams = serde_json::from_value(params)?;
            magic_eden::build_me_nft_info(http, &p).await
        }
        "me_wallet_nfts" => {
            let p: magic_eden::MeWalletNFTsParams = serde_json::from_value(params)?;
            magic_eden::build_me_wallet_nfts(http, &p).await
        }
        "me_collection_activity" => {
            let p: magic_eden::MeCollectionActivityParams = serde_json::from_value(params)?;
            magic_eden::build_me_collection_activity(http, &p).await
        }
        "me_listings" => {
            let p: magic_eden::MeListingsParams = serde_json::from_value(params)?;
            magic_eden::build_me_listings(http, &p).await
        }
        "me_offers" => {
            let p: magic_eden::MeOffersParams = serde_json::from_value(params)?;
            magic_eden::build_me_offers(http, &p).await
        }
        "me_collection_nfts" => {
            let p: magic_eden::MeCollectionNFTsParams = serde_json::from_value(params)?;
            magic_eden::build_me_collection_nfts(http, &p).await
        }
        // Every remaining Magic Eden read, through one table. See
        // `me_read_url` — the paths there were checked against the live API,
        // and the three that 404 are deliberately not among them.
        "me_collections" | "me_collection_stats" | "me_collection_attributes" | "me_collection_leaderboard" | "me_collection_listings" | "me_collection_activities" | "me_collections_batch_listings" | "me_launchpad_collections" | "me_marketplace_popular" | "me_token" | "me_token_activities" | "me_token_listings" | "me_token_offers_received" | "me_wallet" | "me_wallet_tokens" | "me_wallet_activities" | "me_owner_activities" | "me_wallet_escrow_balance" | "me_wallet_offers_made" | "me_wallet_offers_received" | "me_mmm_pools" => {
            let p: magic_eden::MeReadParams = serde_json::from_value(params)?;
            magic_eden::build_me_read(http, action_type, &p).await
        }
        // ── Cross-Chain Actions ─────────────────────────────────────────────────────
        "cross_chain_swap" | "bridge" => {
            // bridge is an alias for cross_chain_swap
            // Get provider from params, default to relay
            let provider = params
                .get("provider")
                .and_then(|v| v.as_str())
                .unwrap_or("relay")
                .to_lowercase();

            match provider.as_str() {
                "debridge" => {
                    let p: debridge::DebridgeParams = serde_json::from_value(params.clone())?;
                    let result =
                        debridge::build_debridge_swap(http, &user_pubkey.to_string(), &p).await?;

                    // For EVM source chains, surface the EVM tx data as an execution step
                    let execution_steps = result.evm_tx.as_ref().map(|evm| {
                        vec![serde_json::json!({
                            "type": "evm_tx",
                            "to": evm.to,
                            "data": evm.data,
                            "value": evm.value,
                        })]
                    });

                    Ok(BuildResponse {
                        preview: ActionPreview {
                            id: result.preview.id,
                            action_type: result.preview.action_type,
                            description: result.preview.description,
                            estimated_fee: result.preview.estimated_fee,
                            estimated_refund: None,
                            params: serde_json::to_value(result.preview.params)?,
                            warnings: result.preview.warnings,
                            requires_approval: result.preview.requires_approval,
                        },
                        transaction: result.solana_tx,
                        additional_signers_required: 0,
                        execution_steps: execution_steps
                            .map(|s| serde_json::to_value(s).unwrap_or_default()),
                        quote: Some(serde_json::to_value(&result.quote)?),
                        is_cross_chain: true,
                        data: None,
                    })
                }
                "squid" | "squid_bridge" => {
                    let mut p: squid::SquidParams = serde_json::from_value(params.clone())?;
                    // Auto-populate from_address from authenticated wallet if not provided by client
                    if p.from_address.is_empty() {
                        p.from_address = user_pubkey.to_string();
                    }
                    let result =
                        squid::build_squid_swap(http, &user_pubkey.to_string(), &p).await?;

                    let execution_steps = result.evm_tx.as_ref().map(|evm| {
                        vec![serde_json::json!({
                            "type": "evm_tx",
                            "to": evm.to,
                            "data": evm.data,
                            "value": evm.value,
                            "chainId": evm.chain_id,
                            "gasLimit": evm.gas_limit,
                            "maxFeePerGas": evm.max_fee_per_gas,
                            "maxPriorityFeePerGas": evm.max_priority_fee_per_gas,
                        })]
                    });

                    Ok(BuildResponse {
                        preview: ActionPreview {
                            id: result.preview.id,
                            action_type: result.preview.action_type,
                            description: result.preview.description,
                            estimated_fee: result.preview.estimated_fee,
                            estimated_refund: None,
                            params: serde_json::to_value(result.preview.params)?,
                            warnings: result.preview.warnings,
                            requires_approval: result.preview.requires_approval,
                        },
                        transaction: result.solana_tx,
                        additional_signers_required: 0,
                        execution_steps: execution_steps
                            .map(|s| serde_json::to_value(s).unwrap_or_default()),
                        quote: Some(serde_json::to_value(&result.quote)?),
                        is_cross_chain: true,
                        data: None,
                    })
                }
                "squid_status" => {
                    let sp: squid::SquidStatusParams = serde_json::from_value(params.clone())?;
                    let status = squid::get_squid_status(http, &sp).await?;
                    Ok(BuildResponse {
                        preview: ActionPreview {
                            id: format!("sqstatus_{}", Uuid::new_v4()),
                            action_type: "squid_status".to_string(),
                            description: format!(
                                "Squid TX {} — {}",
                                &status.transaction_id
                                    [..std::cmp::min(12, status.transaction_id.len())],
                                status.status
                            ),
                            estimated_fee: "0".to_string(),
                            estimated_refund: None,
                            params: params.clone(),
                            warnings: vec![status.status_meaning.clone()],
                            requires_approval: false,
                        },
                        transaction: None,
                        additional_signers_required: 0,
                        execution_steps: None,
                        quote: None,
                        is_cross_chain: true,
                        data: Some(serde_json::to_value(&status)?),
                    })
                }
                "relay" | _ => {
                    // Default to relay
                    let p: relay::CrossChainSwapParams = serde_json::from_value(params)?;
                    let result = relay::build_cross_chain_swap(
                        http,
                        &user_pubkey.to_string(),
                        &p,
                        relay_fee_recipient,
                    )
                    .await?;

                    // Cross-chain swaps don't return a Solana transaction,
                    // but execution steps for the frontend to execute via viem/wagmi
                    Ok(BuildResponse {
                        preview: ActionPreview {
                            id: result.preview.id,
                            action_type: result.preview.action_type,
                            description: result.preview.description,
                            estimated_fee: result.preview.estimated_fee,
                            estimated_refund: None,
                            params: serde_json::to_value(result.preview.params)?,
                            warnings: result.preview.warnings,
                            requires_approval: result.preview.requires_approval,
                        },
                        transaction: None,
                        additional_signers_required: 0,
                        execution_steps: Some(serde_json::to_value(&result.quote.steps)?),
                        quote: Some(result.quote.raw),
                        is_cross_chain: true,
                        data: None,
                    })
                }
            }
        }
        // ── Relay.link — dedicated bridge action ───────────────────────────────────
        // Direct action type aliases: LLM can emit "debridge" or "squid_bridge" without "provider"
        "debridge" => {
            let p: debridge::DebridgeParams = serde_json::from_value(params)?;
            let result = debridge::build_debridge_swap(http, &user_pubkey.to_string(), &p).await?;
            let execution_steps = result.evm_tx.as_ref().map(|evm| {
                vec![serde_json::json!({
                    "type": "evm_tx",
                    "to": evm.to, "data": evm.data, "value": evm.value,
                })]
            });
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: None,
                    params: serde_json::to_value(result.preview.params)?,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: result.solana_tx,
                additional_signers_required: 0,
                execution_steps: execution_steps
                    .map(|s| serde_json::to_value(s).unwrap_or_default()),
                quote: Some(serde_json::to_value(&result.quote)?),
                is_cross_chain: true,
                data: None,
            })
        }
        "squid" | "squid_bridge" => {
            let mut p: squid::SquidParams = serde_json::from_value(params)?;
            if p.from_address.is_empty() {
                p.from_address = user_pubkey.to_string();
            }
            let result = squid::build_squid_swap(http, &user_pubkey.to_string(), &p).await?;
            let execution_steps = result.evm_tx.as_ref().map(|evm| {
                vec![serde_json::json!({
                    "type": "evm_tx",
                    "to": evm.to, "data": evm.data, "value": evm.value,
                    "chainId": evm.chain_id, "gasLimit": evm.gas_limit,
                    "maxFeePerGas": evm.max_fee_per_gas,
                    "maxPriorityFeePerGas": evm.max_priority_fee_per_gas,
                })]
            });
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: None,
                    params: serde_json::to_value(result.preview.params)?,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: result.solana_tx,
                additional_signers_required: 0,
                execution_steps: execution_steps
                    .map(|s| serde_json::to_value(s).unwrap_or_default()),
                quote: Some(serde_json::to_value(&result.quote)?),
                is_cross_chain: true,
                data: None,
            })
        }
        "relay_bridge" => {
            let p: relay::RelayBridgeParams = serde_json::from_value(params)?;
            let result =
                relay::relay_bridge(http, &user_pubkey.to_string(), &p, relay_fee_recipient)
                    .await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: None,
                    params: serde_json::to_value(result.preview.params)?,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: Some(serde_json::to_value(&result.quote.steps)?),
                quote: Some(result.quote.raw),
                is_cross_chain: true,
                data: None,
            })
        }
        // ── Cross-Chain Query Actions ───────────────────────────────────────────────
        "cross_chain_quote" => {
            let mut p: squid::SquidQuoteParams = serde_json::from_value(params.clone())?;
            if p.from_address.is_empty() {
                p.from_address = user_pubkey.to_string();
            }
            let result = squid::get_squid_quote(http, &user_pubkey.to_string(), &p).await?;
            let from_display = squid::chain_display(&result.from_chain);
            let to_display = squid::chain_display(&result.to_chain);
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("ccq_{}", Uuid::new_v4()),
                    action_type: "cross_chain_quote".to_string(),
                    description: format!(
                        "{} {} ({}) → {} ({}) | fee ~${:.2} | ETA ~{}s",
                        p.amount,
                        result.from_token,
                        from_display,
                        result.to_token,
                        to_display,
                        result.total_fee_usd,
                        result.estimated_duration_seconds,
                    ),
                    estimated_fee: format!("~${:.2}", result.total_fee_usd),
                    estimated_refund: None,
                    params,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "cross_chain_chains" => {
            let result = squid::get_squid_chains(http).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("ccc_{}", Uuid::new_v4()),
                    action_type: "cross_chain_chains".to_string(),
                    description: format!(
                        "Squid supports {} chains for cross-chain bridging",
                        result.count
                    ),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "cross_chain_tokens" => {
            // Optional chain filter from params
            let chain_id_filter = params
                .get("chainId")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            let result = squid::get_squid_tokens(http, chain_id_filter.as_deref()).await?;
            let description = match &result.chain_filter {
                Some(cid) => format!("Squid supports {} tokens on chain {}", result.count, cid),
                None => format!("Squid supports {} tokens across all chains", result.count),
            };
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("cct_{}", Uuid::new_v4()),
                    action_type: "cross_chain_tokens".to_string(),
                    description,
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        // ── Tensor NFT Marketplace Actions ──────────────────────────────────────────
        "tensor_buy"
        | "tensor_list"
        | "tensor_cancel_listing"
        | "tensor_make_offer"
        | "tensor_cancel_offer"
        | "tensor_collection_info"
        | "tensor_nft_info"
        | "tensor_wallet_nfts"
        | "tensor_listings" => {
            tensor::build_tensor_action(http, &user_pubkey.to_string(), action_type, params).await
        }
        // ── Burn (SPL token burn / close empty account) ───────────────────────────
        "burn" => {
            let p: burn::BurnParams = serde_json::from_value(params)?;
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result =
                actix_web::web::block(move || burn::build_burn_transaction(&rpc, &pubkey, &p))
                    .await
                    .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;
            let tx_bytes = bincode::serialize(&result.transaction)
                .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
            let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: result.preview.estimated_refund,
                    params: result.preview.params,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: Some(tx_b64),
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }

        // ── Close Accounts (batch reclaim rent from empty ATAs) ───────────────────
        "close_accounts" => {
            let p: burn::CloseAccountsParams = serde_json::from_value(params)?;
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result = actix_web::web::block(move || {
                burn::build_close_accounts_transaction(&rpc, &pubkey, &p)
            })
            .await
            .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;
            let tx_bytes = bincode::serialize(&result.transaction)
                .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
            let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: result.preview.estimated_refund,
                    params: result.preview.params,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: Some(tx_b64),
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }

        // ── Scan Empty Token Accounts (read-only, no transaction) ─────────────────
        "scan_empty_accounts" => {
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let (empty, total_sol, _) =
                actix_web::web::block(move || burn::scan_empty_accounts(&rpc, &pubkey))
                    .await
                    .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;

            let count = empty.len();
            let closeable_mints: Vec<String> = empty
                .iter()
                .filter_map(|a| a["mint"].as_str().map(str::to_string))
                .collect();

            let description = if count == 0 {
                "No empty token accounts found — your wallet is already clean!".to_string()
            } else {
                format!(
                    "Found {} empty token account{} — closing them would recover ~{:.4} SOL",
                    count,
                    if count == 1 { "" } else { "s" },
                    total_sol,
                )
            };

            Ok(BuildResponse {
                preview: ActionPreview {
                    id: Uuid::new_v4().to_string(),
                    action_type: "scan_empty_accounts".to_string(),
                    description,
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: if count > 0 {
                        vec![format!(
                            "Use close_accounts with the listed mints to reclaim the SOL."
                        )]
                    } else {
                        vec![]
                    },
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::json!({
                    "emptyAccounts": empty,
                    "totalCount": count,
                    "totalRecoverableSol": format!("{:.6}", total_sol),
                    "closeableMintsForBatch": closeable_mints,
                })),
            })
        }

        // ── Native SOL Staking ────────────────────────────────────────────────────
        "native_stake" => {
            let p: native_stake::NativeStakeParams = serde_json::from_value(params)?;
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result =
                actix_web::web::block(move || native_stake::build_native_stake(&rpc, &pubkey, &p))
                    .await
                    .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: result.preview.estimated_refund,
                    params: result.preview.params,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: result.transaction,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }
        "native_stake_deactivate" => {
            let p: native_stake::NativeDeactivateParams = serde_json::from_value(params)?;
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result = actix_web::web::block(move || {
                native_stake::build_native_stake_deactivate(&rpc, &pubkey, &p)
            })
            .await
            .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: result.preview.estimated_refund,
                    params: result.preview.params,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: result.transaction,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }
        "native_stake_withdraw" => {
            let p: native_stake::NativeWithdrawStakeParams = serde_json::from_value(params)?;
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result = actix_web::web::block(move || {
                native_stake::build_native_stake_withdraw(&rpc, &pubkey, &p)
            })
            .await
            .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: result.preview.estimated_refund,
                    params: result.preview.params,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: result.transaction,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }
        "native_stake_split" => {
            let p: native_stake::NativeSplitStakeParams = serde_json::from_value(params)?;
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result = actix_web::web::block(move || {
                native_stake::build_native_stake_split(&rpc, &pubkey, &p)
            })
            .await
            .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: result.preview.estimated_refund,
                    params: result.preview.params,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: result.transaction,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }
        "native_stake_merge" => {
            let p: native_stake::NativeMergeStakeParams = serde_json::from_value(params)?;
            let rpc = rpc.clone();
            let pubkey = *user_pubkey;
            let result = actix_web::web::block(move || {
                native_stake::build_native_stake_merge(&rpc, &pubkey, &p)
            })
            .await
            .map_err(|e| AppError::Internal(format!("Blocking error: {e}")))??;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: result.preview.id,
                    action_type: result.preview.action_type,
                    description: result.preview.description,
                    estimated_fee: result.preview.estimated_fee,
                    estimated_refund: result.preview.estimated_refund,
                    params: result.preview.params,
                    warnings: result.preview.warnings,
                    requires_approval: result.preview.requires_approval,
                },
                transaction: result.transaction,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: None,
            })
        }

        // ── PumpFun Queries ──────────────────────────────────────────────────────────
        "pumpfun_token_info" => {
            let p: pumpfun::PumpFunMintParams = serde_json::from_value(params)?;
            pumpfun::build_pumpfun_token_info(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_trending" => {
            let p: pumpfun::PumpFunListParams = serde_json::from_value(params).unwrap_or_default();
            pumpfun::build_pumpfun_trending(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_new" => {
            let p: pumpfun::PumpFunListParams = serde_json::from_value(params).unwrap_or_default();
            pumpfun::build_pumpfun_new(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_graduating" => {
            let p: pumpfun::PumpFunListParams = serde_json::from_value(params).unwrap_or_default();
            pumpfun::build_pumpfun_graduating(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_koth" => {
            let p: pumpfun::PumpFunListParams = serde_json::from_value(params).unwrap_or_default();
            pumpfun::build_pumpfun_koth(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_search" => {
            let p: pumpfun::PumpFunSearchParams = serde_json::from_value(params)?;
            pumpfun::build_pumpfun_search(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_comments" => {
            let p: pumpfun::PumpFunMintParams = serde_json::from_value(params)?;
            pumpfun::build_pumpfun_comments(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_user" => {
            let p: pumpfun::PumpFunUserParams = serde_json::from_value(params).unwrap_or_default();
            pumpfun::build_pumpfun_user(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_bonding_curve" => {
            let p: pumpfun::PumpFunMintParams = serde_json::from_value(params)?;
            pumpfun::build_pumpfun_bonding_curve(http, &user_pubkey.to_string(), &p).await
        }
        "pumpfun_curve_global" => {
            // Global curve constants + optional deterministic compute paths
            // (from_mc_sol/to_mc_sol, sol_in_fresh, tokens_out_fresh, mc_to_v_sol).
            pumpfun::build_pumpfun_curve_global(rpc, &params).await
        }
        "pumpswap_pool_info" => {
            let p: pumpfun::PumpFunMintParams = serde_json::from_value(params)?;
            pumpfun::build_pumpswap_pool_info(http, &user_pubkey.to_string(), &p).await
        }
        // ── Relay.link — query actions ────────────────────────────────────────
        "relay_get_quote" => {
            let p: relay::RelayBridgeParams = serde_json::from_value(params)?;
            let quote = relay::get_relay_quote_full(
                http,
                &p,
                &user_pubkey.to_string(),
                relay_fee_recipient,
            )
            .await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayq_{}", Uuid::new_v4()),
                    action_type: "relay_get_quote".to_string(),
                    description: format!(
                        "Relay quote: {} {} (chain {}) → chain {}",
                        p.amount, p.origin_currency, p.origin_chain_id, p.destination_chain_id
                    ),
                    estimated_fee: quote
                        .fees
                        .as_ref()
                        .and_then(|f| f.total_usd)
                        .map(|f| format!("${:.2}", f))
                        .unwrap_or_else(|| "~$2-5".to_string()),
                    estimated_refund: None,
                    params: serde_json::to_value(&p)?,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: Some(quote.raw),
                is_cross_chain: true,
                data: None,
            })
        }
        "relay_get_chains" => {
            let include_chains = params.get("includeChains").and_then(|v| v.as_str());
            let chains = relay::get_supported_chains(http, include_chains).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relaychains_{}", Uuid::new_v4()),
                    action_type: "relay_get_chains".to_string(),
                    description: format!("Relay supports {} chains", chains.len()),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::to_value(&chains)?),
            })
        }
        "relay_get_chains_liquidity" => {
            let chain_id = params
                .get("chainId")
                .and_then(|v| v.as_u64())
                .ok_or_else(|| {
                    AppError::InvalidParams(
                        "chainId is required for relay_get_chains_liquidity".into(),
                    )
                })?;
            let result = relay::get_relay_chains_liquidity(http, chain_id).await?;
            let count = result.liquidity.len();
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayliq_{}", Uuid::new_v4()),
                    action_type: "relay_get_chains_liquidity".to_string(),
                    description: format!(
                        "Relay solver liquidity on chain {}: {} currencies",
                        chain_id, count
                    ),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_get_currencies" => {
            let q: relay::RelayCurrenciesQuery = serde_json::from_value(params).unwrap_or_default();
            let currencies = relay::get_relay_currencies(http, &q).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relaycur_{}", Uuid::new_v4()),
                    action_type: "relay_get_currencies".to_string(),
                    description: format!("Found {} currencies on Relay", currencies.len()),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::to_value(&currencies)?),
            })
        }
        "relay_get_token_price" => {
            let address = params
                .get("address")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let chain_id = params.get("chainId").and_then(|v| v.as_u64()).unwrap_or(1);
            let price = relay::get_relay_token_price(http, &address, chain_id).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayprice_{}", Uuid::new_v4()),
                    action_type: "relay_get_token_price".to_string(),
                    description: format!("{} price: ${:.6}", address, price.price),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::json!({"price": price.price})),
            })
        }
        "relay_get_requests" => {
            let q: relay::RelayRequestsQuery = serde_json::from_value(params).unwrap_or_default();
            let result = relay::get_relay_requests(http, &q).await?;
            let count = result.requests.len();
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayreqs_{}", Uuid::new_v4()),
                    action_type: "relay_get_requests".to_string(),
                    description: format!("Found {} Relay bridge requests", count),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_intent_status" => {
            let request_id = params
                .get("requestId")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let status = relay::get_relay_intent_status(http, &request_id).await?;
            let description = format!(
                "Relay intent {}: {} (origin chain {} → dest chain {})",
                &request_id[..request_id.len().min(12)],
                status.status,
                status.origin_chain_id.unwrap_or(0),
                status.destination_chain_id.unwrap_or(0),
            );
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relaystatus_{}", Uuid::new_v4()),
                    action_type: "relay_intent_status".to_string(),
                    description,
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&status)?),
            })
        }
        "relay_get_app_fee_balances" => {
            let wallet = params
                .get("wallet")
                .and_then(|v| v.as_str())
                .unwrap_or(&user_pubkey.to_string())
                .to_string();
            let result = relay::get_app_fee_balances(http, &wallet).await?;
            let description = format!(
                "App fee balances for {}: ${:.2} total (${:.2} available)",
                &wallet[..wallet.len().min(12)],
                result.total_balance_usd.unwrap_or(0.0),
                result.available_balance_usd.unwrap_or(0.0),
            );
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayappfees_{}", Uuid::new_v4()),
                    action_type: "relay_get_app_fee_balances".to_string(),
                    description,
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_get_swap_sources" => {
            let chain_id = params.get("chainId").and_then(|v| v.as_u64());
            let result = relay::get_swap_sources(http, chain_id).await?;
            let description = match chain_id {
                Some(cid) => format!(
                    "Relay swap sources for chain {}: {} available",
                    cid,
                    result.sources.len()
                ),
                None => format!("Relay swap sources: {} available", result.sources.len()),
            };
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayswapsrc_{}", Uuid::new_v4()),
                    action_type: "relay_get_swap_sources".to_string(),
                    description,
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: false,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_claim_app_fees" => {
            let chain_id = params.get("chainId").and_then(|v| v.as_u64()).unwrap_or(0);
            let currency = params
                .get("currency")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let recipient = params
                .get("recipient")
                .and_then(|v| v.as_str())
                .unwrap_or(&user_pubkey.to_string())
                .to_string();
            let amount = params
                .get("amount")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            let wallet = params
                .get("wallet")
                .and_then(|v| v.as_str())
                .unwrap_or(&user_pubkey.to_string())
                .to_string();
            let claim_req = relay::RelayClaimAppFeesRequest {
                chain_id,
                currency: currency.clone(),
                recipient: recipient.clone(),
                amount,
            };
            let result = relay::claim_app_fees(http, &wallet, &claim_req).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayclaim_{}", Uuid::new_v4()),
                    action_type: "relay_claim_app_fees".to_string(),
                    description: format!(
                        "Relay: claim {} fees on chain {} → {}",
                        currency,
                        chain_id,
                        &recipient[..recipient.len().min(12)],
                    ),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: true,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: Some(serde_json::to_value(&result.steps)?),
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_fast_fill" => {
            let api_key = relay_api_key.ok_or_else(|| {
                AppError::InvalidParams(
                    "RELAY_API_KEY is not configured — fast-fill unavailable".into(),
                )
            })?;
            let request_id = params
                .get("requestId")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let solver_input_currency_amount = params
                .get("solverInputCurrencyAmount")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            let max_fill_amount_usd = params.get("maxFillAmountUsd").and_then(|v| v.as_f64());
            let fast_fill_req = relay::RelayFastFillRequest {
                request_id: request_id.clone(),
                solver_input_currency_amount,
                max_fill_amount_usd,
            };
            let result = relay::fast_fill(http, api_key, &fast_fill_req).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayfastfill_{}", Uuid::new_v4()),
                    action_type: "relay_fast_fill".to_string(),
                    description: format!(
                        "Relay fast-fill queued for request {}",
                        &request_id[..request_id.len().min(16)]
                    ),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_execute" => {
            let api_key = relay_api_key.ok_or_else(|| {
                AppError::InvalidParams(
                    "RELAY_API_KEY is not configured — relay_execute unavailable".into(),
                )
            })?;
            let execute_req: relay::RelayExecuteRequest = serde_json::from_value(params.clone())
                .map_err(|e| {
                    AppError::InvalidParams(format!("Invalid relay_execute params: {e}"))
                })?;
            let chain_id = execute_req.data.chain_id;
            let to = execute_req.data.to.clone();
            let result = relay::relay_execute(http, api_key, &execute_req).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayexec_{}", Uuid::new_v4()),
                    action_type: "relay_execute".to_string(),
                    description: format!(
                        "Relay gasless execute on chain {} → {}",
                        chain_id,
                        &to[..to.len().min(16)],
                    ),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: true,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_index_transaction" => {
            let chain_id = params
                .get("chainId")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let tx_hash = params
                .get("txHash")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let request_id = params
                .get("requestId")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            let index_req = relay::RelayIndexTransactionRequest {
                chain_id,
                tx_hash: tx_hash.clone(),
                request_id,
            };
            let result = relay::index_relay_transaction(http, &index_req).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayindex_{}", Uuid::new_v4()),
                    action_type: "relay_index_transaction".to_string(),
                    description: format!(
                        "Relay: indexed transaction {}",
                        &tx_hash[..tx_hash.len().min(16)]
                    ),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_single_transaction" => {
            let request_id = params
                .get("requestId")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let chain_id = params
                .get("chainId")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let tx = params
                .get("tx")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let single_req = relay::RelaySingleTransactionRequest {
                request_id,
                chain_id,
                tx: tx.clone(),
            };
            let result = relay::single_relay_transaction(http, &single_req).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relaysingle_{}", Uuid::new_v4()),
                    action_type: "relay_single_transaction".to_string(),
                    description: format!(
                        "Relay: indexed single transaction {}",
                        &tx[..tx.len().min(16)]
                    ),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        "relay_deposit_address_reindex" => {
            let chain_id = params.get("chainId").and_then(|v| v.as_u64()).unwrap_or(0);
            let deposit_address = params
                .get("depositAddress")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let sweep = params.get("sweep").and_then(|v| v.as_bool());
            let target_chain_id = params.get("targetChainId").and_then(|v| v.as_u64());
            let reindex_req = relay::RelayDepositAddressReindexRequest {
                chain_id,
                deposit_address: deposit_address.clone(),
                sweep,
                target_chain_id,
            };
            let result = relay::reindex_deposit_address(http, &reindex_req).await?;
            let desc = format!(
                "Relay: reindexed deposit address {} — {} currencies checked, {} triggered",
                &deposit_address[..deposit_address.len().min(16)],
                result.checked_currencies.unwrap_or(0),
                result.triggered_currencies.len(),
            );
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("relayreindex_{}", Uuid::new_v4()),
                    action_type: "relay_deposit_address_reindex".to_string(),
                    description: desc,
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: serde_json::Value::Null,
                    warnings: vec![],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&result)?),
            })
        }
        // ── Streamflow ────────────────────────────────────────────────────────
        "streamflow_list" => {
            let direction = params.get("direction").and_then(|v| v.as_str());
            let result =
                streamflow::list_streams(http, &user_pubkey.to_string(), direction).await?;
            streamflow_to_build_response(result)
        }
        "streamflow_create" => {
            let p: streamflow::StreamflowCreateParams = serde_json::from_value(params)?;
            streamflow_to_build_response(
                streamflow::build_create_stream(http, &user_pubkey.to_string(), &p).await?,
            )
        }
        "streamflow_cancel" => {
            let p: streamflow::StreamflowCancelParams = serde_json::from_value(params)?;
            streamflow_to_build_response(
                streamflow::build_cancel_stream(http, &user_pubkey.to_string(), &p).await?,
            )
        }
        "streamflow_withdraw" => {
            let p: streamflow::StreamflowWithdrawParams = serde_json::from_value(params)?;
            streamflow_to_build_response(
                streamflow::build_withdraw_stream(http, &user_pubkey.to_string(), &p).await?,
            )
        }
        "streamflow_transfer" => {
            let p: streamflow::StreamflowTransferParams = serde_json::from_value(params)?;
            streamflow_to_build_response(
                streamflow::build_transfer_stream(http, &user_pubkey.to_string(), &p).await?,
            )
        }
        "streamflow_topup" => {
            let p: streamflow::StreamflowTopupParams = serde_json::from_value(params)?;
            let result = streamflow::build_topup_stream(http, &user_pubkey.to_string(), &p).await?;
            streamflow_to_build_response(result)
        }
        "streamflow_update" => {
            let p: streamflow::StreamflowUpdateParams = serde_json::from_value(params)?;
            let result =
                streamflow::build_update_stream(http, &user_pubkey.to_string(), &p).await?;
            streamflow_to_build_response(result)
        }
        "streamflow_create_multiple" => {
            let p: streamflow::StreamflowCreateMultipleParams = serde_json::from_value(params)?;
            let result =
                streamflow::build_create_multiple_streams(http, &user_pubkey.to_string(), &p)
                    .await?;
            streamflow_to_build_response(result)
        }
        "streamflow_get_one" => {
            let p: streamflow::StreamflowGetOneParams = serde_json::from_value(params)?;
            let result = streamflow::get_stream_by_id(http, &p).await?;
            streamflow_to_build_response(result)
        }

        // ── Read-only protocol queries (MarginFi balances) ─────────────────────
        "marginfi_user_balances" => {
            protocol_reads::marginfi_user_balances(http, &user_pubkey.to_string()).await
        }

        // ── Solana Name Service (SNS) ───────────────────────────────────────────────
        "sns_resolve" => {
            let p: sns::SnsResolveParams = serde_json::from_value(params)?;
            sns::build_sns_resolve(http, p).await
        }
        "sns_reverse_lookup" => {
            let p: sns::SnsReverseParams = serde_json::from_value(params)?;
            sns::build_sns_reverse_lookup(http, p).await
        }
        "sns_domains" => {
            let p: sns::SnsDomainsParams = serde_json::from_value(params)?;
            sns::build_sns_domains(http, p).await
        }
        "sns_record" => {
            let p: sns::SnsRecordParams = serde_json::from_value(params)?;
            sns::build_sns_record(http, p).await
        }
        "sns_domain_info" => {
            let p: sns::SnsDomainInfoParams = serde_json::from_value(params)?;
            sns::build_sns_domain_info(http, p).await
        }
        "sns_check_available" => {
            let p: sns::SnsCheckAvailableParams = serde_json::from_value(params)?;
            sns::build_sns_check_available(http, p).await
        }
        "sns_primary_domain" => {
            let p: sns::SnsPrimaryDomainParams = serde_json::from_value(params)?;
            sns::build_sns_primary_domain(http, p).await
        }
        "sns_register" => {
            let p: sns::SnsRegisterParams = serde_json::from_value(params)?;
            sns::build_sns_register(http, rpc, &user_pubkey.to_string(), p).await
        }
        "sns_transfer" => {
            let p: sns::SnsTransferParams = serde_json::from_value(params)?;
            sns::build_sns_transfer(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_set_record" => {
            let p: sns::SnsSetRecordParams = serde_json::from_value(params)?;
            sns::build_sns_set_record(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_delete" => {
            let p: sns::SnsDeleteParams = serde_json::from_value(params)?;
            sns::build_sns_delete(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_create_subdomain" => {
            let p: sns::SnsCreateSubdomainParams = serde_json::from_value(params)?;
            sns::build_sns_create_subdomain(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_list" => {
            let p: sns::SnsListParams = serde_json::from_value(params)?;
            sns::build_sns_list(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_buy" => {
            let p: sns::SnsBuyParams = serde_json::from_value(params)?;
            sns::build_sns_buy(http, rpc, &user_pubkey.to_string(), p).await
        }
        "sns_make_offer" => {
            let p: sns::SnsMakeOfferParams = serde_json::from_value(params)?;
            sns::build_sns_make_offer(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_accept_offer" => {
            let p: sns::SnsAcceptOfferParams = serde_json::from_value(params)?;
            sns::build_sns_accept_offer(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_cancel_offer" => {
            let p: sns::SnsCancelOfferParams = serde_json::from_value(params)?;
            sns::build_sns_cancel_offer(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_p2p_create" => {
            let p: sns::SnsP2pCreateParams = serde_json::from_value(params)?;
            sns::build_sns_p2p_create(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_p2p_accept" => {
            let p: sns::SnsP2pAcceptParams = serde_json::from_value(params)?;
            sns::build_sns_p2p_accept(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_p2p_cancel" => {
            let p: sns::SnsP2pCancelParams = serde_json::from_value(params)?;
            sns::build_sns_p2p_cancel(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_set_favorite" => {
            let p: sns::SnsSetFavoriteParams = serde_json::from_value(params)?;
            sns::build_sns_set_favorite(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_subdomains" => {
            let p: sns::SnsSubdomainsParams = serde_json::from_value(params)?;
            sns::build_sns_subdomains(http, p).await
        }
        "sns_realloc" => {
            let p: sns::SnsReallocParams = serde_json::from_value(params)?;
            sns::build_sns_realloc(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_transfer_subdomain" => {
            let p: sns::SnsTransferSubdomainParams = serde_json::from_value(params)?;
            sns::build_sns_transfer_subdomain(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_create_record" => {
            let p: sns::SnsCreateRecordParams = serde_json::from_value(params)?;
            sns::build_sns_create_record(rpc, &user_pubkey.to_string(), p).await
        }
        "sns_domain_key" => {
            let p: sns::SnsDomainKeyParams = serde_json::from_value(params)?;
            sns::build_sns_domain_key(http, p).await
        }
        "sns_record_key" => {
            let p: sns::SnsRecordKeyParams = serde_json::from_value(params)?;
            sns::build_sns_record_key(http, p).await
        }
        "sns_twitter_handle" => {
            let p: sns::SnsTwitterHandleParams = serde_json::from_value(params)?;
            sns::build_sns_twitter_handle(http, p).await
        }

        "squid_status" => {
            let sp: squid::SquidStatusParams = serde_json::from_value(params.clone())?;
            let status = squid::get_squid_status(http, &sp).await?;
            Ok(BuildResponse {
                preview: ActionPreview {
                    id: format!("sqstatus_{}", Uuid::new_v4()),
                    action_type: "squid_status".to_string(),
                    description: format!(
                        "Squid TX {} — {}",
                        &status.transaction_id[..std::cmp::min(12, status.transaction_id.len())],
                        status.status
                    ),
                    estimated_fee: "0".to_string(),
                    estimated_refund: None,
                    params: params.clone(),
                    warnings: vec![status.status_meaning.clone()],
                    requires_approval: false,
                },
                transaction: None,
                additional_signers_required: 0,
                execution_steps: None,
                quote: None,
                is_cross_chain: true,
                data: Some(serde_json::to_value(&status)?),
            })
        }

        _ => Err(AppError::InvalidParams(format!(
            "Unsupported action type: {action_type}"
        ))),
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// Stake / Unstake dispatch
// ──────────────────────────────────────────────────────────────────────────────

async fn build_stake(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &marinade::StakeParams,
    protocol: &str,
) -> Result<BuildResponse, AppError> {
    match protocol {
        "jito" => {
            let jito_params = jito::JitoStakeParams {
                amount: params.amount.clone(),
                slippage_bps: None,
            };
            jito::build_jito_stake_action(http, rpc, user_pubkey, &jito_params).await
        }
        _ => {
            // Default to Marinade using new async function
            let marinade_params = marinade::MarinadeStakeParams {
                amount: params.amount.clone(),
                slippage_bps: None,
            };
            marinade::build_marinade_stake(http, &user_pubkey.to_string(), &marinade_params).await
        }
    }
}

async fn build_unstake(
    http: &reqwest::Client,
    rpc: &SolanaRpc,
    user_pubkey: &Pubkey,
    params: &marinade::StakeParams,
    protocol: &str,
) -> Result<BuildResponse, AppError> {
    match protocol {
        "jito" => {
            let jito_params = jito::JitoUnstakeParams {
                amount: params.amount.clone(),
                instant: params.instant_unstake,
                slippage_bps: None,
            };
            jito::build_jito_unstake_action(http, rpc, user_pubkey, &jito_params).await
        }
        _ => {
            // Default to Marinade using new async function
            let instant = params.instant_unstake.unwrap_or(false);
            if instant {
                let marinade_params = marinade::MarinadeUnstakeParams {
                    amount: params.amount.clone(),
                    slippage_bps: None,
                };
                marinade::build_marinade_unstake(http, &user_pubkey.to_string(), &marinade_params)
                    .await
            } else {
                let marinade_params = marinade::MarinadeDelayedUnstakeParams {
                    amount: params.amount.clone(),
                };
                marinade::build_marinade_delayed_unstake(
                    http,
                    &user_pubkey.to_string(),
                    &marinade_params,
                )
                .await
            }
        }
    }
}

fn serialize_stake_response(
    transaction: Transaction,
    id: String,
    action_type: String,
    description: String,
    estimated_fee: String,
    params: serde_json::Value,
    warnings: Vec<String>,
    requires_approval: bool,
) -> Result<BuildResponse, AppError> {
    let tx_bytes = bincode::serialize(&transaction)
        .map_err(|e| AppError::Internal(format!("Serialization error: {e}")))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(BuildResponse {
        preview: ActionPreview {
            id,
            action_type,
            description,
            estimated_fee,
            estimated_refund: None,
            params,
            warnings,
            requires_approval,
        },
        transaction: Some(tx_b64),
        additional_signers_required: 0,
        execution_steps: None,
        quote: None,
        is_cross_chain: false,
        data: None,
    })
}

// Allow serde_json::Error -> AppError for convenience
impl From<serde_json::Error> for AppError {
    fn from(e: serde_json::Error) -> Self {
        AppError::InvalidParams(format!("JSON error: {e}"))
    }
}

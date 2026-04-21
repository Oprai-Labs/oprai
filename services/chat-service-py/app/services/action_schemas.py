"""
Action schemas for OPRAI Chat Service.

Single source of truth for:
  - All valid action / query types (ActionType, QueryType enums)
  - OpenAI function-calling tool definitions (OPRAI_TOOLS)
  - Pydantic output models for validated results
  - validate_tool_call() — validates LLM tool-call output before forwarding to frontend

Prompt injection defence:
  The LLM is instructed to call these tools instead of emitting [ACTION:...] text blocks.
  Even if a malicious user message contains fake [ACTION:...] syntax, the LLM's tool-call
  output is validated against this schema before it reaches the client, so unknown action
  types and bad parameter values are rejected at the boundary.
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    # Core Solana
    TRANSFER = "transfer"
    SWAP = "swap"
    STAKE = "stake"
    UNSTAKE = "unstake"
    BURN = "burn"
    CLOSE_ACCOUNTS = "close_accounts"
    SCAN_EMPTY_ACCOUNTS = "scan_empty_accounts"
    CLAIM = "claim"
    VOTE = "vote"
    LAUNCH_TOKEN = "launch_token"
    CROSS_CHAIN_SWAP = "cross_chain_swap"
    BRIDGE = "bridge"
    NFT_BUY = "nft_buy"
    NFT_LIST = "nft_list"
    NFT_MINT = "nft_mint"
    # Jupiter
    LIMIT_ORDER = "limit_order"
    CANCEL_LIMIT_ORDER = "cancel_limit_order"
    CANCEL_ALL_LIMIT_ORDERS = "cancel_all_limit_orders"
    DCA = "dca"
    CANCEL_DCA = "cancel_dca"
    JUPSOL_STAKE = "jupsol_stake"
    JUPSOL_UNSTAKE = "jupsol_unstake"
    LEND = "lend"
    WITHDRAW_LEND = "withdraw_lend"
    BORROW = "borrow"
    REPAY = "repay"
    PERP_OPEN = "perp_open"
    PERP_CLOSE = "perp_close"
    JLP_ADD = "jlp_add"
    JLP_REMOVE = "jlp_remove"
    # Jupiter — data queries
    JUP_DCA_ORDERS = "jup_dca_orders"
    JUP_LIMIT_ORDERS = "jup_limit_orders"
    JUP_PRICE = "jup_price"
    JUP_TOKEN_SEARCH = "jup_token_search"
    JUP_TOKENS_TAG = "jup_tokens_tag"
    JUP_TOKENS_RECENT = "jup_tokens_recent"
    JUP_TOKENS_TRENDING = "jup_tokens_trending"
    JUP_PORTFOLIO_POSITIONS = "jup_portfolio_positions"
    JUP_STAKED_JUP = "jup_staked_jup"
    JUP_LEND_POSITIONS = "jup_lend_positions"
    JUP_LEND_EARNINGS = "jup_lend_earnings"
    JUP_PENDING_INVITES = "jup_pending_invites"
    JUP_LEND_MARKETS = "jup_lend_markets"
    # pump.fun — bonding curve actions
    PUMPFUN_BUY = "pumpfun_buy"
    PUMPFUN_SELL = "pumpfun_sell"
    # pump.fun — data queries
    PUMPFUN_TOKEN_INFO = "pumpfun_token_info"
    PUMPFUN_TRENDING = "pumpfun_trending"
    PUMPFUN_NEW = "pumpfun_new"
    PUMPFUN_GRADUATING = "pumpfun_graduating"
    PUMPFUN_KOTH = "pumpfun_koth"
    PUMPFUN_SEARCH = "pumpfun_search"
    PUMPFUN_COMMENTS = "pumpfun_comments"
    PUMPFUN_USER = "pumpfun_user"
    PUMPFUN_BONDING_CURVE = "pumpfun_bonding_curve"
    # PumpSwap AMM — graduated tokens
    PUMPSWAP_BUY = "pumpswap_buy"
    PUMPSWAP_SELL = "pumpswap_sell"
    PUMPSWAP_POOL_INFO = "pumpswap_pool_info"
    # Raydium
    RAYDIUM_SWAP = "raydium_swap"
    RAYDIUM_ADD_LIQUIDITY = "raydium_add_liquidity"
    RAYDIUM_REMOVE_LIQUIDITY = "raydium_remove_liquidity"
    RAYDIUM_CREATE_POOL = "raydium_create_pool"
    RAYDIUM_OPEN_POSITION = "raydium_open_position"
    RAYDIUM_CLOSE_POSITION = "raydium_close_position"
    RAYDIUM_INCREASE_POSITION = "raydium_increase_position"
    RAYDIUM_DECREASE_POSITION = "raydium_decrease_position"
    # Raydium — data queries
    RAYDIUM_GET_POOLS = "raydium_get_pools"
    RAYDIUM_SEARCH_POOLS = "raydium_search_pools"
    RAYDIUM_SWAP_QUOTE = "raydium_swap_quote"
    RAYDIUM_GET_POOL_INFO = "raydium_get_pool_info"
    RAYDIUM_GET_USER_POSITIONS = "raydium_get_user_positions"
    RAYDIUM_GET_CLMM_POSITIONS = "raydium_get_clmm_positions"
    RAYDIUM_GET_TOKEN_INFO = "raydium_get_token_info"
    RAYDIUM_GET_PLATFORM_STATS = "raydium_get_platform_stats"
    RAYDIUM_GET_CLMM_CONFIGS = "raydium_get_clmm_configs"
    RAYDIUM_GET_POOLS_BY_LP = "raydium_get_pools_by_lp"
    RAYDIUM_GET_POOLS_V2 = "raydium_get_pools_v2"
    RAYDIUM_GET_POOL_KEYS = "raydium_get_pool_keys"
    RAYDIUM_GET_POOL_LIQUIDITY_HISTORY = "raydium_get_pool_liquidity_history"
    RAYDIUM_GET_POOL_POSITION_HISTORY = "raydium_get_pool_position_history"
    RAYDIUM_GET_TOKEN_LIST = "raydium_get_token_list"
    RAYDIUM_GET_TOKEN_PRICES = "raydium_get_token_prices"
    RAYDIUM_GET_FARM_INFO = "raydium_get_farm_info"
    RAYDIUM_GET_FARM_BY_LP = "raydium_get_farm_by_lp"
    RAYDIUM_GET_FARM_KEYS = "raydium_get_farm_keys"
    RAYDIUM_GET_IDO_KEYS = "raydium_get_ido_keys"
    RAYDIUM_GET_MAIN_VERSION = "raydium_get_main_version"
    RAYDIUM_GET_RPCS = "raydium_get_rpcs"
    RAYDIUM_GET_CHAIN_TIME = "raydium_get_chain_time"
    RAYDIUM_GET_STAKE_POOLS = "raydium_get_stake_pools"
    RAYDIUM_GET_MIGRATE_LP = "raydium_get_migrate_lp"
    RAYDIUM_GET_AUTO_FEE = "raydium_get_auto_fee"
    RAYDIUM_GET_CPMM_CONFIGS = "raydium_get_cpmm_configs"
    # Orca
    ORCA_SWAP = "orca_swap"
    ORCA_ADD_LIQUIDITY = "orca_add_liquidity"
    ORCA_REMOVE_LIQUIDITY = "orca_remove_liquidity"
    ORCA_OPEN_POSITION = "orca_open_position"
    ORCA_CLOSE_POSITION = "orca_close_position"
    ORCA_INCREASE_POSITION = "orca_increase_position"
    ORCA_DECREASE_POSITION = "orca_decrease_position"
    ORCA_COLLECT_FEES = "orca_collect_fees"
    ORCA_COLLECT_REWARDS = "orca_collect_rewards"
    # Orca — pool creation + data queries
    ORCA_CREATE_POOL = "orca_create_pool"
    ORCA_GET_POOLS = "orca_get_pools"
    ORCA_SEARCH_POOLS = "orca_search_pools"
    ORCA_GET_POOL = "orca_get_pool"
    ORCA_GET_LOCKED_LIQUIDITY = "orca_get_locked_liquidity"
    ORCA_GET_PROTOCOL_STATS = "orca_get_protocol_stats"
    ORCA_GET_ORCA_TOKEN = "orca_get_orca_token"
    ORCA_GET_CIRCULATING_SUPPLY = "orca_get_circulating_supply"
    ORCA_GET_TOTAL_SUPPLY = "orca_get_total_supply"
    ORCA_GET_TOKENS = "orca_get_tokens"
    ORCA_SEARCH_TOKENS = "orca_search_tokens"
    ORCA_GET_TOKEN = "orca_get_token"
    ORCA_GET_USER_POSITIONS = "orca_get_user_positions"
    ORCA_GET_POOL_POSITIONS = "orca_get_pool_positions"
    # Meteora
    METEORA_SWAP = "meteora_swap"
    METEORA_ADD_LIQUIDITY = "meteora_add_liquidity"
    METEORA_REMOVE_LIQUIDITY = "meteora_remove_liquidity"
    METEORA_CREATE_POOL = "meteora_create_pool"
    METEORA_OPEN_POSITION = "meteora_open_position"
    METEORA_CLOSE_POSITION = "meteora_close_position"
    METEORA_ADD_TO_POSITION = "meteora_add_to_position"
    METEORA_CLAIM_FEES = "meteora_claim_fees"
    METEORA_CLAIM_REWARDS = "meteora_claim_rewards"
    METEORA_STAKE = "meteora_stake"
    METEORA_UNSTAKE = "meteora_unstake"
    METEORA_HARVEST = "meteora_harvest"
    # Meteora — DLMM GET queries
    METEORA_DLMM_GET_PAIRS = "meteora_dlmm_get_pairs"
    METEORA_DLMM_GET_PAIR = "meteora_dlmm_get_pair"
    METEORA_DLMM_GET_USER_POSITIONS = "meteora_dlmm_get_user_positions"
    METEORA_DLMM_GET_ACTIVE_BIN = "meteora_dlmm_get_active_bin"
    METEORA_DLMM_GET_POOL_GROUPS = "meteora_dlmm_get_pool_groups"
    METEORA_DLMM_GET_POOL_GROUP = "meteora_dlmm_get_pool_group"
    METEORA_DLMM_GET_POOL_OHLCV = "meteora_dlmm_get_pool_ohlcv"
    METEORA_DLMM_GET_POOL_VOLUME_HISTORY = "meteora_dlmm_get_pool_volume_history"
    METEORA_DLMM_GET_PROTOCOL_STATS = "meteora_dlmm_get_protocol_stats"
    # Meteora — DAMM v2 GET queries
    METEORA_DAMMV2_GET_POOLS = "meteora_dammv2_get_pools"
    METEORA_DAMMV2_GET_POOL_GROUPS = "meteora_dammv2_get_pool_groups"
    METEORA_DAMMV2_GET_POOL_GROUP = "meteora_dammv2_get_pool_group"
    METEORA_DAMMV2_GET_POOL = "meteora_dammv2_get_pool"
    METEORA_DAMMV2_GET_POOL_OHLCV = "meteora_dammv2_get_pool_ohlcv"
    METEORA_DAMMV2_GET_POOL_VOLUME_HISTORY = "meteora_dammv2_get_pool_volume_history"
    METEORA_DAMMV2_GET_PROTOCOL_METRICS = "meteora_dammv2_get_protocol_metrics"
    # Meteora — DAMM v2 TX
    METEORA_DAMMV2_SWAP = "meteora_dammv2_swap"
    METEORA_DAMMV2_ADD_LIQUIDITY = "meteora_dammv2_add_liquidity"
    METEORA_DAMMV2_REMOVE_LIQUIDITY = "meteora_dammv2_remove_liquidity"
    # Meteora — DAMM v1 GET queries
    METEORA_DAMMV1_GET_POOLS = "meteora_dammv1_get_pools"
    METEORA_DAMMV1_GET_POOL_CONFIGS = "meteora_dammv1_get_pool_configs"
    METEORA_DAMMV1_SEARCH_POOLS = "meteora_dammv1_search_pools"
    METEORA_DAMMV1_GET_FARMS = "meteora_dammv1_get_farms"
    METEORA_DAMMV1_GET_POOLS_METRICS = "meteora_dammv1_get_pools_metrics"
    METEORA_DAMMV1_GET_ALPHA_VAULTS = "meteora_dammv1_get_alpha_vaults"
    METEORA_DAMMV1_GET_ALPHA_VAULT_CONFIGS = "meteora_dammv1_get_alpha_vault_configs"
    METEORA_DAMMV1_GET_POOLS_BY_VAULT_LP = "meteora_dammv1_get_pools_by_vault_lp"
    METEORA_DAMMV1_GET_FEE_CONFIG = "meteora_dammv1_get_fee_config"
    # Meteora — DAMM v1 TX
    METEORA_DAMMV1_SWAP = "meteora_dammv1_swap"
    METEORA_DAMMV1_DEPOSIT = "meteora_dammv1_deposit"
    METEORA_DAMMV1_WITHDRAW = "meteora_dammv1_withdraw"
    # Meteora — Dynamic Vault GET queries
    METEORA_VAULT_GET_INFO = "meteora_vault_get_info"
    METEORA_VAULT_GET_ADDRESSES = "meteora_vault_get_addresses"
    METEORA_VAULT_GET_STATE = "meteora_vault_get_state"
    METEORA_VAULT_GET_APY = "meteora_vault_get_apy"
    METEORA_VAULT_GET_APY_HISTORY = "meteora_vault_get_apy_history"
    METEORA_VAULT_GET_VIRTUAL_PRICE = "meteora_vault_get_virtual_price"
    # Meteora — Dynamic Vault TX
    METEORA_VAULT_DEPOSIT = "meteora_vault_deposit"
    METEORA_VAULT_WITHDRAW = "meteora_vault_withdraw"
    # Meteora — Stake2Earn (m3m3) GET queries
    METEORA_S2E_GET_ANALYTICS = "meteora_s2e_get_analytics"
    METEORA_S2E_GET_ALL_VAULTS = "meteora_s2e_get_all_vaults"
    METEORA_S2E_FILTER_VAULTS = "meteora_s2e_filter_vaults"
    METEORA_S2E_GET_VAULT = "meteora_s2e_get_vault"
    # Meteora — Stake2Earn (m3m3) TX
    METEORA_S2E_STAKE = "meteora_s2e_stake"
    METEORA_S2E_UNSTAKE = "meteora_s2e_unstake"
    METEORA_S2E_CLAIM_FEE = "meteora_s2e_claim_fee"
    METEORA_S2E_CANCEL_UNSTAKE = "meteora_s2e_cancel_unstake"
    METEORA_S2E_WITHDRAW = "meteora_s2e_withdraw"
    # Kamino
    KAMINO_DEPOSIT = "kamino_deposit"
    KAMINO_WITHDRAW = "kamino_withdraw"
    KAMINO_BORROW = "kamino_borrow"
    KAMINO_REPAY = "kamino_repay"
    KAMINO_ADD_COLLATERAL = "kamino_add_collateral"
    KAMINO_WITHDRAW_COLLATERAL = "kamino_withdraw_collateral"
    KAMINO_MULTIPLY_OPEN = "kamino_multiply_open"
    KAMINO_MULTIPLY_ADD = "kamino_multiply_add"
    KAMINO_MULTIPLY_WITHDRAW = "kamino_multiply_withdraw"
    KAMINO_MULTIPLY_CLOSE = "kamino_multiply_close"
    KAMINO_LONG_OPEN = "kamino_long_open"
    KAMINO_SHORT_OPEN = "kamino_short_open"
    KAMINO_POSITION_CLOSE = "kamino_position_close"
    KAMINO_VAULT_DEPOSIT = "kamino_vault_deposit"
    KAMINO_VAULT_WITHDRAW = "kamino_vault_withdraw"
    KAMINO_STAKE = "kamino_stake"
    KAMINO_UNSTAKE = "kamino_unstake"
    # Kamino — data queries
    KAMINO_VAULTS = "kamino_vaults"
    KAMINO_MARKETS = "kamino_markets"
    KAMINO_MARKET_RESERVES = "kamino_market_reserves"
    KAMINO_USER_VAULT_POSITIONS = "kamino_user_vault_positions"
    KAMINO_USER_OBLIGATIONS = "kamino_user_obligations"
    KAMINO_ORACLE_PRICES = "kamino_oracle_prices"
    KAMINO_USD_BENCHMARK_RATES = "kamino_usd_benchmark_rates"
    # Kamino — market metrics history + reserve APY history + obligation interest
    KAMINO_MARKET_METRICS_HISTORY = "kamino_market_metrics_history"
    KAMINO_RESERVE_BORROW_APY_HISTORY = "kamino_reserve_borrow_apy_history"
    KAMINO_RESERVE_BORROW_APY_MEDIAN = "kamino_reserve_borrow_apy_median"
    KAMINO_OBLIGATION_INTEREST_EARNED = "kamino_obligation_interest_earned"
    KAMINO_OBLIGATION_INTEREST_PAID = "kamino_obligation_interest_paid"
    # Kamino — K-Lend transactions + borrow orders
    KAMINO_OBLIGATION_TRANSACTIONS = "kamino_obligation_transactions"
    KAMINO_USER_KLEND_TRANSACTIONS_ALL = "kamino_user_klend_transactions_all"
    KAMINO_USER_KLEND_TRANSACTIONS = "kamino_user_klend_transactions"
    KAMINO_BORROW_ORDER_FILLS = "kamino_borrow_order_fills"
    KAMINO_OPEN_BORROW_ORDERS = "kamino_open_borrow_orders"
    # Kamino — yields
    KAMINO_YIELD_HISTORY = "kamino_yield_history"
    KAMINO_PRINCIPAL_TOKEN_YIELDS = "kamino_principal_token_yields"
    # Kamino — airdrop
    KAMINO_AIRDROP_ALLOCATIONS = "kamino_airdrop_allocations"
    KAMINO_AIRDROP_METRICS = "kamino_airdrop_metrics"
    # Kamino — staking yields
    KAMINO_STAKING_YIELDS = "kamino_staking_yields"
    KAMINO_STAKING_YIELDS_MEDIAN = "kamino_staking_yields_median"
    KAMINO_STAKING_YIELDS_MEAN = "kamino_staking_yields_mean"
    # Kamino — season rewards + staking boosts
    KAMINO_USER_STAKING_BOOSTS = "kamino_user_staking_boosts"
    KAMINO_SEASON_REWARDS_USER = "kamino_season_rewards_user"
    KAMINO_SEASON_REWARDS_VESTING_POOL = "kamino_season_rewards_vesting_pool"
    # Kamino — private credit
    KAMINO_PRIVATE_CREDIT_METRICS = "kamino_private_credit_metrics"
    KAMINO_PRIVATE_CREDIT_METRICS_HISTORY = "kamino_private_credit_metrics_history"
    # Kamino — farms
    KAMINO_USER_FARM_TRANSACTIONS = "kamino_user_farm_transactions"
    KAMINO_FARM_TRANSACTIONS = "kamino_farm_transactions"
    # Kamino — extended earn vault data
    KAMINO_VAULT_DETAIL = "kamino_vault_detail"
    KAMINO_VAULT_METRICS = "kamino_vault_metrics"
    KAMINO_VAULT_METRICS_HISTORY = "kamino_vault_metrics_history"
    KAMINO_VAULT_ALLOCATION_HISTORY = "kamino_vault_allocation_history"
    KAMINO_VAULTS_REWARDS = "kamino_vaults_rewards"
    KAMINO_VAULTS_SUMMARY = "kamino_vaults_summary"
    KAMINO_VAULT_MINT_METADATA = "kamino_vault_mint_metadata"
    KAMINO_VAULT_MINT_IMAGE = "kamino_vault_mint_image"
    # Kamino — extended earn user data
    KAMINO_USER_METRICS_HISTORY = "kamino_user_metrics_history"
    KAMINO_USER_TRANSACTIONS = "kamino_user_transactions"
    KAMINO_USER_KVAULT_REWARDS = "kamino_user_kvault_rewards"
    KAMINO_USER_VAULT_POSITION = "kamino_user_vault_position"
    KAMINO_USER_VAULT_METRICS_HISTORY = "kamino_user_vault_metrics_history"
    KAMINO_USER_VAULT_PNL = "kamino_user_vault_pnl"
    KAMINO_USER_VAULT_PNL_HISTORY = "kamino_user_vault_pnl_history"
    KAMINO_VAULT_TRANSACTIONS = "kamino_vault_transactions"
    # Kamino — vault action instructions variants
    KAMINO_VAULT_DEPOSIT_INSTRUCTIONS = "kamino_vault_deposit_instructions"
    KAMINO_VAULT_WITHDRAW_INSTRUCTIONS = "kamino_vault_withdraw_instructions"
    # Kamino — extended borrow market data
    KAMINO_MARKET_DETAIL = "kamino_market_detail"
    KAMINO_MARKET_RESERVE_HISTORY = "kamino_market_reserve_history"
    KAMINO_MARKET_LEVERAGE_METRICS = "kamino_market_leverage_metrics"
    KAMINO_MARKET_RESERVES_ACCOUNT = "kamino_market_reserves_account"
    # Kamino — extended borrow user/loan data
    KAMINO_USER_REWARDS = "kamino_user_rewards"
    KAMINO_LOAN_DETAIL = "kamino_loan_detail"
    KAMINO_OBLIGATION_PNL = "kamino_obligation_pnl"
    KAMINO_OBLIGATION_METRICS_HISTORY = "kamino_obligation_metrics_history"
    KAMINO_REWARDS_LIST = "kamino_rewards_list"
    KAMINO_REWARDS_HISTORY = "kamino_rewards_history"
    # Kamino — borrow/repay instruction variants
    KAMINO_BORROW_INSTRUCTIONS = "kamino_borrow_instructions"
    KAMINO_REPAY_INSTRUCTIONS = "kamino_repay_instructions"
    # Kamino — KSwap
    KAMINO_KSWAP = "kamino_kswap"
    # Kamino — K-Lend deposit/withdraw instruction variants
    KAMINO_DEPOSIT_INSTRUCTIONS = "kamino_deposit_instructions"
    KAMINO_WITHDRAW_INSTRUCTIONS = "kamino_withdraw_instructions"
    # Jito
    JITO_STAKE = "jito_stake"
    JITO_UNSTAKE = "jito_unstake"
    JITO_TIP = "jito_tip"
    JITO_BUNDLE = "jito_bundle"
    JITO_BUNDLE_STATUS = "jito_bundle_status"
    # Marinade
    MARINADE_STAKE = "marinade_stake"
    MARINADE_UNSTAKE = "marinade_unstake"
    MARINADE_DELAYED_UNSTAKE = "marinade_delayed_unstake"
    # marginfi v2 — Account Management
    MARGINFI_CREATE_ACCOUNT = "marginfi_create_account"
    MARGINFI_CREATE_ACCOUNT_PDA = "marginfi_create_account_pda"
    MARGINFI_CLOSE_ACCOUNT = "marginfi_close_account"
    MARGINFI_CLOSE_BALANCE = "marginfi_close_balance"
    MARGINFI_TRANSFER_ACCOUNT = "marginfi_transfer_account"
    # marginfi v2 — Core Lending
    MARGINFI_DEPOSIT = "marginfi_deposit"
    MARGINFI_WITHDRAW = "marginfi_withdraw"
    MARGINFI_BORROW = "marginfi_borrow"
    MARGINFI_REPAY = "marginfi_repay"
    # marginfi v2 — Liquidation
    MARGINFI_LIQUIDATE = "marginfi_liquidate"
    MARGINFI_START_LIQUIDATION = "marginfi_start_liquidation"
    MARGINFI_END_LIQUIDATION = "marginfi_end_liquidation"
    # marginfi v2 — Flash Loans
    MARGINFI_FLASHLOAN_START = "marginfi_flashloan_start"
    MARGINFI_FLASHLOAN_END = "marginfi_flashloan_end"
    # marginfi v2 — Borrow Orders
    MARGINFI_PLACE_ORDER = "marginfi_place_order"
    MARGINFI_CLOSE_ORDER = "marginfi_close_order"
    MARGINFI_EXECUTE_ORDER_START = "marginfi_execute_order_start"
    MARGINFI_EXECUTE_ORDER_END = "marginfi_execute_order_end"
    # marginfi v2 — Emissions / Rewards
    MARGINFI_CLAIM_EMISSIONS = "marginfi_claim_emissions"
    MARGINFI_UPDATE_EMISSIONS_DESTINATION = "marginfi_update_emissions_destination"
    MARGINFI_SETTLE_EMISSIONS = "marginfi_settle_emissions"
    MARGINFI_WITHDRAW_EMISSIONS_PERMISSIONLESS = "marginfi_withdraw_emissions_permissionless"
    MARGINFI_CLEAR_EMISSIONS = "marginfi_clear_emissions"
    # marginfi v2 — Liquidation Setup
    MARGINFI_SET_KEEPER_FLAGS = "marginfi_set_keeper_flags"
    MARGINFI_INIT_LIQ_RECORD = "marginfi_init_liq_record"
    # marginfi v2 — Permissionless
    MARGINFI_ACCRUE_INTEREST = "marginfi_accrue_interest"
    MARGINFI_PULSE_PRICE = "marginfi_pulse_price"
    MARGINFI_PULSE_HEALTH = "marginfi_pulse_health"
    # marginfi v2 — Queries
    MARGINFI_ACCOUNT_INFO = "marginfi_account_info"
    MARGINFI_BANKS = "marginfi_banks"
    MARGINFI_HEALTH = "marginfi_health"
    MARGINFI_POINTS = "marginfi_points"
    MARGINFI_BANK_DETAIL = "marginfi_bank_detail"
    MARGINFI_USER_ACCOUNTS = "marginfi_user_accounts"
    # Solend
    SOLEND_DEPOSIT = "solend_deposit"
    SOLEND_WITHDRAW = "solend_withdraw"
    SOLEND_BORROW = "solend_borrow"
    SOLEND_REPAY = "solend_repay"
    SOLEND_ADD_COLLATERAL = "solend_add_collateral"
    SOLEND_WITHDRAW_COLLATERAL = "solend_withdraw_collateral"
    SOLEND_USER_INFO = "solend_user_info"
    SOLEND_RESERVES = "solend_reserves"
    SOLEND_MARKET = "solend_market"
    # Magic Eden
    ME_BUY = "me_buy"
    ME_LIST = "me_list"
    ME_CANCEL_LISTING = "me_cancel_listing"
    ME_MAKE_OFFER = "me_make_offer"
    ME_ACCEPT_OFFER = "me_accept_offer"
    ME_CANCEL_OFFER = "me_cancel_offer"
    ME_COLLECTION_INFO = "me_collection_info"
    ME_NFT_INFO = "me_nft_info"
    ME_WALLET_NFTS = "me_wallet_nfts"
    ME_COLLECTION_ACTIVITY = "me_collection_activity"
    ME_LISTINGS = "me_listings"
    ME_OFFERS = "me_offers"
    ME_COLLECTION_NFTS = "me_collection_nfts"
    # Tensor
    TENSOR_BUY = "tensor_buy"
    TENSOR_LIST = "tensor_list"
    TENSOR_CANCEL_LISTING = "tensor_cancel_listing"
    TENSOR_MAKE_OFFER = "tensor_make_offer"
    TENSOR_CANCEL_OFFER = "tensor_cancel_offer"
    TENSOR_COLLECTION_INFO = "tensor_collection_info"
    TENSOR_NFT_INFO = "tensor_nft_info"
    TENSOR_WALLET_NFTS = "tensor_wallet_nfts"
    TENSOR_LISTINGS = "tensor_listings"
    # Streamflow — token streaming / vesting (full SDK)
    STREAMFLOW_CREATE = "streamflow_create"
    STREAMFLOW_CREATE_MULTIPLE = "streamflow_create_multiple"
    STREAMFLOW_CANCEL = "streamflow_cancel"
    STREAMFLOW_WITHDRAW = "streamflow_withdraw"
    STREAMFLOW_TRANSFER = "streamflow_transfer"
    STREAMFLOW_TOPUP = "streamflow_topup"
    STREAMFLOW_UPDATE = "streamflow_update"
    STREAMFLOW_GET_ONE = "streamflow_get_one"
    STREAMFLOW_LIST = "streamflow_list"
    # Relay.link — full cross-chain bridge (all optional params)
    RELAY_BRIDGE = "relay_bridge"
    RELAY_INDEX_TRANSACTION = "relay_index_transaction"
    RELAY_SINGLE_TRANSACTION = "relay_single_transaction"
    RELAY_DEPOSIT_ADDRESS_REINDEX = "relay_deposit_address_reindex"
    RELAY_CLAIM_APP_FEES = "relay_claim_app_fees"
    RELAY_FAST_FILL = "relay_fast_fill"
    RELAY_EXECUTE = "relay_execute"
    # Cross-chain bridges
    DEBRIDGE = "debridge"
    SQUID = "squid"
    SQUID_BRIDGE = "squid_bridge"
    # Automation / local
    SET_ALERT = "set_alert"
    COPY_TRADE = "copy_trade"
    CREATE_SCHEDULE = "create_schedule"
    CANCEL_SCHEDULE = "cancel_schedule"
    LIST_SCHEDULES = "list_schedules"
    CREATE_WORKFLOW = "create_workflow"
    SET_TRIGGER = "set_trigger"
    AUTO_COMPOUND = "auto_compound"
    REBALANCE_PORTFOLIO = "rebalance_portfolio"


class QueryType(str, Enum):
    BALANCE = "balance"
    PRICE = "price"
    PORTFOLIO = "portfolio"
    POSITIONS = "positions"
    TRANSACTIONS = "transactions"
    TOKEN_INFO = "token_info"
    TRENDING = "trending"
    NETWORK = "network"
    RISK = "risk"
    YIELD = "yield"
    ANALYTICS = "analytics"
    NFT_COLLECTION = "nft_collection"
    AIRDROPS = "airdrops"
    GAS = "gas"
    WALLET_INFO = "wallet_info"
    TAX_REPORT = "tax_report"
    LIMIT_ORDERS = "limit_orders"
    DCA = "dca"
    LEND_POSITIONS = "lend_positions"
    PERP_POSITIONS = "perp_positions"
    SIMULATE = "simulate"
    WHALE = "whale"
    SMART_MONEY = "smart_money"
    # Pump.fun / PumpSwap read-only queries
    PUMPFUN_TOKEN_INFO = "pumpfun_token_info"
    PUMPFUN_TRENDING = "pumpfun_trending"
    PUMPFUN_NEW = "pumpfun_new"
    PUMPFUN_GRADUATING = "pumpfun_graduating"
    PUMPFUN_KOTH = "pumpfun_koth"
    PUMPFUN_SEARCH = "pumpfun_search"
    PUMPFUN_BONDING_CURVE = "pumpfun_bonding_curve"
    PUMPFUN_COMMENTS = "pumpfun_comments"
    PUMPFUN_USER = "pumpfun_user"
    PUMPSWAP_POOL_INFO = "pumpswap_pool_info"
    # Relay.link — cross-chain data queries
    RELAY_GET_QUOTE = "relay_get_quote"
    RELAY_GET_CHAINS = "relay_get_chains"
    RELAY_GET_CHAINS_LIQUIDITY = "relay_get_chains_liquidity"
    RELAY_GET_CURRENCIES = "relay_get_currencies"
    RELAY_GET_TOKEN_PRICE = "relay_get_token_price"
    RELAY_GET_REQUESTS = "relay_get_requests"
    RELAY_INTENT_STATUS = "relay_intent_status"
    RELAY_GET_APP_FEE_BALANCES = "relay_get_app_fee_balances"
    RELAY_GET_SWAP_SOURCES = "relay_get_swap_sources"


# ---------------------------------------------------------------------------
# Parameter validation helpers
# ---------------------------------------------------------------------------

_SOLANA_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Addresses that must NEVER be used as transfer destinations.
# Includes the system program, zero-equivalent accounts, and known honey-pot patterns.
_BLOCKED_DESTINATION_ADDRESSES: frozenset[str] = frozenset({
    "11111111111111111111111111111111",           # System Program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token Program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe8bXh",  # Associated Token Program
    "ComputeBudget111111111111111111111111111111",  # Compute Budget
    "Vote111111111111111111111111111111111111111p",  # Vote Program
    "Stake11111111111111111111111111111111111111",   # Stake Program
    "BPFLoaderUpgradeab1e11111111111111111111111",  # BPF Loader
    "Sysvar1111111111111111111111111111111111111",   # Sysvar
    "SysvarC1ock11111111111111111111111111111111",   # Clock Sysvar
    "SysvarRent111111111111111111111111111111111",   # Rent Sysvar
})

# Actions that move user funds — extra validation applied.
_FUND_MOVING_ACTIONS: frozenset[str] = frozenset({
    "transfer", "swap", "stake", "unstake", "bridge",
    "lend", "borrow", "deposit", "withdraw", "liquidate",
    "pumpfun_buy", "pumpfun_sell", "pumpfun_launch",
    "pumpswap_buy", "pumpswap_sell", "launch_token",
    "relay_bridge", "relay_claim_app_fees", "squid_bridge", "squid",
})

# Token symbols accepted as-is (not required to be mint addresses).
# All entries MUST be uppercase — _is_token_ref() calls .upper() before checking.
_KNOWN_SYMBOLS: frozenset[str] = frozenset({
    # Native & stablecoins
    "SOL", "USDC", "USDT", "PYUSD",
    # Liquid staking
    "MSOL", "JSOL", "JITOSOL", "BSOL", "JUPSOL", "INF", "MNDE",
    # Major DeFi
    "JUP", "RAY", "ORCA", "KMNO", "DRIFT", "PYTH", "JTO",
    # Bridged assets
    "WBTC", "WETH", "STRK", "W",
    # Infrastructure / data
    "RENDER", "HNT", "MOBILE", "IOT", "NEON", "HXRO",
    # Memecoins (popular on Solana)
    "BONK", "WIF", "POPCAT", "MOODENG", "BOME", "MEW", "SLERF",
    "FARTCOIN", "TRUMP", "MELANIA", "WEN", "SAMO", "BOOK", "SEND",
    # Gaming / social
    "ATLAS", "POLIS", "GMT", "GST", "GENE", "SHDW", "STARS",
    # Legacy / older DeFi
    "MNGO", "SRM", "FIDA", "STEP", "TNSR", "TNSR",
    # Special sentinel — frontend resolves "all" → full balance
    "ALL",
})

# Params that must be Solana addresses
_ADDR_ONLY_PARAMS: frozenset[str] = frozenset({"to", "wallet", "mint"})
# Params that can be symbol OR mint address
_TOKEN_REF_PARAMS: frozenset[str] = frozenset({"inputMint", "outputMint", "token"})

# Special token values allowed for the `burn` action that aren't valid symbols/addresses
# e.g. `token=dust` means "burn all dust tokens", `token=empty_accounts` means close accounts
_BURN_SPECIAL_TOKENS: frozenset[str] = frozenset({"dust", "empty_accounts"})
# Params that must be numeric (or the special string "all" / "auto").
# Add every param that the prompt uses as a number — covers perp, LP, DCA, pools.
_NUMERIC_PARAMS: frozenset[str] = frozenset({
    # Core amounts
    "amount", "totalAmount", "inputAmount",
    # Liquidity pool amounts
    "amountA", "amountB", "amountX", "amountY",
    # DAMM v1/v2 deposit/withdraw amounts
    "tokenAAmount", "tokenBAmount",
    "maxAmountA", "maxAmountB",
    "minAmountA", "minAmountB",
    "minAAmount", "minBAmount",
    "lpAmount", "unmintAmount",
    # Perpetuals / leverage
    "collateralAmount", "leverage", "leverageX", "sizeUsd",
    # Price / range
    "minPrice", "maxPrice", "targetPrice", "initialPrice",
    "minBinId", "maxBinId",
    # DCA
    "numberOfOrders", "minOutPerCycle", "maxOutPerCycle",
    # Slippage / fees
    "slippageBps", "baseFee", "binStep", "bpsToRemove",
    # Pagination / listing
    "page", "pageSize", "offset", "limit",
    # Timing
    "time", "expirySeconds", "startAt",
    "startTimestamp", "endTimestamp", "fromTime", "toTime",
    # Streamflow
    "period", "amountPerPeriod", "start", "cliff", "cliffAmount",
    # Bridge / cross-chain
    "originChainId", "destinationChainId", "slippage",
    # Raydium / Orca pool creation
    "feeRate", "tickSpacing", "size",
    # Limits
    "maxTrades", "collateralRatio",
    # LP / position amounts
    "liquidity", "rewardIndex",
    # Set alert threshold
    "value",
})

_MAX_PARAM_VALUE_LEN = 512


def _is_solana_address(v: str) -> bool:
    return bool(_SOLANA_ADDR_RE.match(v))


def _is_token_ref(v: str) -> bool:
    return v.upper() in _KNOWN_SYMBOLS or _is_solana_address(v)


def validate_action_params(
    action_type: str,
    params: dict[str, Any],
) -> dict[str, str]:
    """
    Validate and sanitise params for *action_type*.

    Rules:
    - All keys and values must be str (others are skipped).
    - Values may not exceed _MAX_PARAM_VALUE_LEN characters.
    - Address-only params must be valid base58 Solana pubkeys.
    - Token-ref params must be a known symbol or valid mint address.
    - Numeric params must parse as float (or be "all" / "auto").

    Returns sanitised dict[str, str].
    Raises ValueError describing the first violation found.
    """
    sanitised: dict[str, str] = {}

    for raw_key, raw_val in params.items():
        if not isinstance(raw_key, str):
            continue
        # Allow lists for known list params (e.g. mints=[...]) — join to comma-separated string
        if isinstance(raw_val, list):
            raw_val = ",".join(str(v) for v in raw_val if isinstance(v, (str, int, float)))
        if not isinstance(raw_val, (str, int, float)):
            continue
        key = raw_key.strip()
        val = str(raw_val).strip()

        if not key:
            continue
        if len(val) > _MAX_PARAM_VALUE_LEN:
            raise ValueError(
                f"Parameter '{key}' value exceeds maximum length "
                f"({len(val)} > {_MAX_PARAM_VALUE_LEN})"
            )

        if key in _ADDR_ONLY_PARAMS and val not in ("self", "auto"):
            # For burn, `mint` can be a token symbol (e.g. "BONK") or a mint address.
            is_burn_mint = (action_type == "burn" and key == "mint")
            if is_burn_mint:
                if not _is_token_ref(val):
                    raise ValueError(
                        f"'mint' must be a known token symbol or valid mint address, got: {val!r}"
                    )
            elif not _is_solana_address(val):
                raise ValueError(
                    f"'{key}' must be a valid base58 Solana address, got: {val!r}"
                )
            # Block known system/program addresses as fund-moving destinations.
            if key == "to" and val in _BLOCKED_DESTINATION_ADDRESSES:
                raise ValueError(
                    f"Transfer destination '{val[:8]}…' is a system program address "
                    f"and cannot receive user funds."
                )

        if key in _TOKEN_REF_PARAMS and val not in ("self", "auto"):
            # Allow special burn-only values (dust, empty_accounts) when action is burn
            is_burn_special = (
                action_type == "burn"
                and key == "token"
                and val.lower() in _BURN_SPECIAL_TOKENS
            )
            if not is_burn_special and not _is_token_ref(val):
                raise ValueError(
                    f"'{key}' must be a known token symbol or valid mint address, "
                    f"got: {val!r}"
                )

        if key in _NUMERIC_PARAMS and val not in ("all", "auto"):
            try:
                num = float(val)
            except ValueError:
                raise ValueError(
                    f"'{key}' must be numeric (or 'all'/'auto'), got: {val!r}"
                )
            if num < 0:
                raise ValueError(
                    f"'{key}' must be non-negative, got: {val!r}"
                )
            if num > 1e15:
                raise ValueError(
                    f"'{key}' exceeds maximum allowed value (1e15), got: {val!r}"
                )

        sanitised[key] = val

    return sanitised


# ---------------------------------------------------------------------------
# Pydantic output models
# ---------------------------------------------------------------------------

class ValidatedAction(BaseModel):
    type: ActionType
    params: dict[str, str]
    chain_from_previous: bool = False
    # Set by _validate_execute_action when the action moves funds to an unrecognized address.
    warn_unverified_destination: bool = False

    def to_frontend_dict(self) -> dict:
        d: dict = {
            "type": self.type.value,
            "params": self.params,
            "chainFromPrevious": self.chain_from_previous,
        }
        if self.warn_unverified_destination:
            d["warnUnverifiedDestination"] = True
        return d


class ValidatedQuery(BaseModel):
    type: QueryType
    params: dict[str, str]

    def to_frontend_dict(self) -> dict:
        return {"type": self.type.value, "params": self.params}


class ClarifyOption(BaseModel):
    label: str
    sublabel: str | None = None
    action: str
    params: dict[str, str]

    @field_validator("action")
    @classmethod
    def action_must_be_known(cls, v: str) -> str:
        try:
            ActionType(v)
        except ValueError:
            raise ValueError(f"Unknown action type in clarify option: {v!r}")
        return v

    @model_validator(mode="after")
    def params_must_be_valid(self) -> "ClarifyOption":
        """Run params through the same validation as a real action call."""
        try:
            sanitised = validate_action_params(self.action, self.params)
            self.params = sanitised
        except ValueError as exc:
            raise ValueError(
                f"Invalid params for clarify option '{self.action}': {exc}"
            ) from exc
        return self


class ValidatedClarify(BaseModel):
    category: str
    question: str
    options: list[ClarifyOption]

    def to_frontend_dict(self) -> dict:
        return {
            "category": self.category,
            "question": self.question,
            "options": [
                {
                    "label": o.label,
                    **({"sublabel": o.sublabel} if o.sublabel else {}),
                    "action": o.action,
                    "params": o.params,
                }
                for o in self.options
            ],
        }


# ---------------------------------------------------------------------------
# OpenAI tool definitions
# ---------------------------------------------------------------------------

_ACTION_ENUM = [e.value for e in ActionType]
_QUERY_ENUM = [e.value for e in QueryType]

OPRAI_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "execute_action",
            "description": (
                "Execute an on-chain action for the user on Solana. "
                "Call this when the user wants to perform a blockchain transaction: "
                "swap, transfer, stake, lend, borrow, buy/list NFT, DCA, limit order, etc. "
                "Use the action_type enum value that matches the protocol and operation. "
                "Refer to the system prompt parameter documentation for required params per action."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": _ACTION_ENUM,
                        "description": "The specific action to execute.",
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Action parameters as string key-value pairs. "
                            "Use 'all' for amount when the user says full balance/max. "
                            "All values must be strings."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "chain_from_previous": {
                        "type": "boolean",
                        "description": (
                            "True when this action should use the output of the previous "
                            "action as its input (e.g. swap → immediately stake the result)."
                        ),
                    },
                },
                "required": ["action_type", "params"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_onchain",
            "description": (
                "Query on-chain data for the user (read-only, no wallet signing). "
                "Call this when the user asks about balances, token prices, positions, "
                "transaction history, portfolio analytics, trending tokens, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": _QUERY_ENUM,
                        "description": "Type of data to query.",
                    },
                    "params": {
                        "type": "object",
                        "description": "Query parameters as string key-value pairs.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["query_type", "params"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_clarification",
            "description": (
                "Request clarification from the user when their intent is ambiguous — "
                "for example when they say 'stake' without specifying a protocol, "
                "or 'swap' without specifying which DEX. "
                "Present 2-4 concrete options the user can choose from."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category of the ambiguity (e.g. 'stake', 'dex', 'nft').",
                    },
                    "question": {
                        "type": "string",
                        "description": "The clarifying question to show the user.",
                    },
                    "options": {
                        "type": "array",
                        "description": "Options the user can choose from.",
                        "minItems": 2,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "description": "Short display label (e.g. 'Jito').",
                                },
                                "sublabel": {
                                    "type": "string",
                                    "description": "Optional subtitle (e.g. '~7.8% APY (MEV)').",
                                },
                                "action": {
                                    "type": "string",
                                    "enum": _ACTION_ENUM,
                                    "description": "The action type that will be triggered.",
                                },
                                "params": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                            },
                            "required": ["label", "action", "params"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["category", "question", "options"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "text_response",
            "description": (
                "Send a plain conversational reply. Use ONLY for greetings, general explanations, "
                "or when no DeFi action or query is needed. "
                "Do NOT use instead of execute_action or query_onchain."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The text message to send to the user.",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool call validation entry point
# ---------------------------------------------------------------------------

# Query types that return non-public computed data (off-chain analytics, tax data,
# strategy positions). Requests for these types targeting a *different* wallet are
# blocked — the LLM should only query private data for the authenticated user.
_PRIVATE_QUERY_TYPES: frozenset[str] = frozenset({
    "tax_report",
    "analytics",
    "positions",
    "lend_positions",
    "perp_positions",
    "limit_orders",
    "dca",
    "airdrops",
})


def validate_tool_call(
    tool_name: str,
    arguments_json: str,
    authenticated_wallet: str | None = None,
) -> ValidatedAction | ValidatedQuery | ValidatedClarify | None:
    """Parse and validate a tool call produced by the LLM.

    authenticated_wallet: the gateway-verified wallet of the current user.
    Used to enforce that private query types cannot target other wallets.

    Returns a validated model on success, or None if validation fails.
    """
    try:
        args: dict = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Tool call '%s' has invalid JSON arguments: %s | raw=%r",
            tool_name, exc, arguments_json[:200],
        )
        return None

    if tool_name == "execute_action":
        return _validate_execute_action(args, chain_depth=int(args.get("_chain_depth", 0)))
    if tool_name == "query_onchain":
        return _validate_query_onchain(args, authenticated_wallet)
    if tool_name == "request_clarification":
        return _validate_request_clarification(args)

    logger.warning("Unknown tool name: %r", tool_name)
    return None


def _validate_execute_action(
    args: dict,
    chain_depth: int = 0,
) -> ValidatedAction | None:
    # Prevent LLM from constructing arbitrarily deep fund-movement chains.
    # Legitimate workflows rarely need more than 3 chained steps.
    _MAX_CHAIN_DEPTH = 3
    if chain_depth >= _MAX_CHAIN_DEPTH:
        logger.warning(
            "execute_action: chain depth %d >= max %d — dropping action",
            chain_depth, _MAX_CHAIN_DEPTH,
        )
        return None

    try:
        action_type = ActionType(args.get("action_type", ""))
    except ValueError:
        logger.warning("execute_action: unknown action_type %r", args.get("action_type"))
        return None

    raw_params = args.get("params", {})
    if not isinstance(raw_params, dict):
        logger.warning("execute_action: params is not a dict")
        return None

    # Normalize: LLM sometimes emits params at the top level instead of
    # nested under the "params" key (observed with pumpfun actions).
    if not raw_params:
        _schema_keys = {"action_type", "params", "chain_from_previous", "_chain_depth"}
        raw_params = {k: v for k, v in args.items() if k not in _schema_keys}

    try:
        sanitised = validate_action_params(action_type.value, raw_params)
    except ValueError as exc:
        logger.warning("execute_action '%s' param validation failed: %s", action_type.value, exc)
        return None

    # Flag fund-moving actions whose destination is not the authenticated wallet
    # and not a known safe address — surfaces warning in the frontend confirmation dialog.
    warn_dest = False
    if action_type.value in _FUND_MOVING_ACTIONS:
        dest = sanitised.get("to", "")
        if dest and dest not in ("self", "auto") and _is_solana_address(dest):
            warn_dest = True

    return ValidatedAction(
        type=action_type,
        params=sanitised,
        chain_from_previous=bool(args.get("chain_from_previous", False)),
        warn_unverified_destination=warn_dest,
    )


def _validate_query_onchain(
    args: dict,
    authenticated_wallet: str | None = None,
) -> ValidatedQuery | None:
    try:
        query_type = QueryType(args.get("query_type", ""))
    except ValueError:
        logger.warning("query_onchain: unknown query_type %r", args.get("query_type"))
        return None

    raw_params = args.get("params", {})
    if not isinstance(raw_params, dict):
        return None

    # Normalize: LLM sometimes emits params at the top level (e.g. pumpfun queries).
    if not raw_params:
        _schema_keys = {"query_type", "params", "_chain_depth"}
        raw_params = {k: v for k, v in args.items() if k not in _schema_keys}

    str_params = {str(k): str(v) for k, v in raw_params.items()
                  if isinstance(k, str) and isinstance(v, (str, int, float))}

    # Normalize param aliases: LLM sometimes uses 'token' instead of 'mint'
    # for pump.fun queries (e.g. pumpfun_token_info, pumpfun_bonding_curve).
    _PUMPFUN_MINT_QUERIES = {
        "pumpfun_token_info", "pumpfun_bonding_curve", "pumpfun_comments",
        "pumpswap_pool_info",
    }
    if query_type.value in _PUMPFUN_MINT_QUERIES and "mint" not in str_params:
        for alias in ("token", "token_mint", "address", "mint_address"):
            if alias in str_params:
                str_params["mint"] = str_params.pop(alias)
                break

    # Enforce wallet ownership for private (non-public) query types.
    # "self" and "auto" are always allowed; an explicit third-party address
    # is blocked for data types that include off-chain computed results.
    if (
        authenticated_wallet
        and query_type.value in _PRIVATE_QUERY_TYPES
    ):
        wallet_param = str_params.get("wallet", "self")
        if wallet_param not in ("self", "auto", authenticated_wallet):
            logger.warning(
                "query_onchain '%s' blocked: wallet=%r does not match authenticated=%r",
                query_type.value, wallet_param, authenticated_wallet[:8] + "…",
            )
            return None

    return ValidatedQuery(type=query_type, params=str_params)


def _validate_request_clarification(args: dict) -> ValidatedClarify | None:
    try:
        options: list[ClarifyOption] = []
        for opt in args.get("options", []):
            action_type = ActionType(opt.get("action", ""))
            raw_params = opt.get("params", {})
            str_params = {str(k): str(v) for k, v in raw_params.items()
                          if isinstance(k, str)}
            options.append(ClarifyOption(
                label=str(opt.get("label", "")),
                sublabel=opt.get("sublabel") or None,
                action=action_type.value,
                params=str_params,
            ))
        return ValidatedClarify(
            category=str(args.get("category", "")),
            question=str(args.get("question", "")),
            options=options,
        )
    except (ValueError, Exception) as exc:
        logger.warning("request_clarification validation failed: %s", exc)
        return None

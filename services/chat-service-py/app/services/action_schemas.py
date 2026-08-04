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
    # Native validator staking (direct SPL stake, not LST)
    NATIVE_STAKE = "native_stake"
    NATIVE_STAKE_DEACTIVATE = "native_stake_deactivate"
    NATIVE_STAKE_WITHDRAW = "native_stake_withdraw"
    NATIVE_STAKE_SPLIT = "native_stake_split"
    NATIVE_STAKE_MERGE = "native_stake_merge"
    BURN = "burn"
    CLOSE_ACCOUNTS = "close_accounts"
    LAUNCH_TOKEN = "launch_token"
    PUMPFUN_LAUNCH = "pumpfun_launch"   # alias accepted by Rust alongside launch_token
    CROSS_CHAIN_SWAP = "cross_chain_swap"
    BRIDGE = "bridge"
    # Solana Name Service — TX actions only (data queries moved to QueryType)
    SNS_REGISTER = "sns_register"
    SNS_TRANSFER = "sns_transfer"
    SNS_BUY = "sns_buy"
    SNS_MAKE_OFFER = "sns_make_offer"
    SNS_ACCEPT_OFFER = "sns_accept_offer"
    SNS_CANCEL_OFFER = "sns_cancel_offer"
    SNS_SET_RECORD = "sns_set_record"
    SNS_DELETE = "sns_delete"
    SNS_CREATE_SUBDOMAIN = "sns_create_subdomain"
    SNS_SET_FAVORITE = "sns_set_favorite"
    SNS_TRANSFER_SUBDOMAIN = "sns_transfer_subdomain"
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
    JUP_PORTFOLIO_PLATFORMS = "jup_portfolio_platforms"
    JUP_STAKED_JUP = "jup_staked_jup"
    JUP_LEND_POSITIONS = "jup_lend_positions"
    JUP_LEND_EARNINGS = "jup_lend_earnings"
    JUP_PENDING_INVITES = "jup_pending_invites"
    JUP_LEND_MARKETS = "jup_lend_markets"
    JUP_PLATFORMS = "jup_platforms"
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
    PUMPFUN_CURVE_GLOBAL = "pumpfun_curve_global"  # global curve constants (no params)
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
    METEORA_DAMMV2_GET_USER_POSITIONS = "meteora_dammv2_get_user_positions"
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
    METEORA_DAMMV2_CLAIM_FEE = "meteora_dammv2_claim_fee"
    METEORA_DAMMV2_CLOSE_POSITION = "meteora_dammv2_close_position"
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
    KAMINO_LIQUIDITY_DEPOSIT = "kamino_liquidity_deposit"
    KAMINO_LIQUIDITY_WITHDRAW = "kamino_liquidity_withdraw"
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
    MARINADE_CLAIM_TICKET = "marinade_claim_ticket"
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
    SQUID_STATUS = "squid_status"


class QueryType(str, Enum):
    BALANCE = "balance"
    # ── Protocol Guidance (handled inline, no action card) ─────────────────
    CLAIM = "claim"
    VOTE = "vote"
    # ── Wallet Utility Queries (no action card, result interpreted inline) ──
    SCAN_EMPTY_ACCOUNTS = "scan_empty_accounts"  # finds zero-balance token accounts → user recovers rent with close_accounts
    MY_STAKE_ACCOUNTS = "my_stake_accounts"      # lists all native stake accounts for the wallet
    # ── SNS (Bonfida Name Service) — domain lookups, no action card ─────────
    SNS_RESOLVE = "sns_resolve"
    SNS_REVERSE_LOOKUP = "sns_reverse_lookup"
    SNS_CHECK_AVAILABLE = "sns_check_available"
    SNS_DOMAINS = "sns_domains"
    SNS_PRIMARY_DOMAIN = "sns_primary_domain"
    SNS_RECORD = "sns_record"
    SNS_DOMAIN_INFO = "sns_domain_info"
    SNS_LIST = "sns_list"
    SNS_SUBDOMAINS = "sns_subdomains"
    # ── Market Data Queries (handled directly by chat-service-py, no action card) ──
    # Validator discovery
    TOP_VALIDATORS = "top_validators"
    # Birdeye
    BIRDEYE_PRICE = "birdeye_price"
    BIRDEYE_MULTI_PRICE = "birdeye_multi_price"
    BIRDEYE_TOKEN_OVERVIEW = "birdeye_token_overview"
    BIRDEYE_TOKEN_METADATA = "birdeye_token_metadata"
    BIRDEYE_TOKEN_SECURITY = "birdeye_token_security"
    BIRDEYE_OHLCV = "birdeye_ohlcv"
    BIRDEYE_TOKEN_TRENDING = "birdeye_token_trending"
    BIRDEYE_NEW_LISTINGS = "birdeye_new_listings"
    BIRDEYE_TOKEN_HOLDERS = "birdeye_token_holders"
    BIRDEYE_HOLDER_DISTRIBUTION = "birdeye_holder_distribution"
    BIRDEYE_HOLDER_POSITIONS = "birdeye_holder_positions"
    BIRDEYE_HOLDER_PROFILE = "birdeye_holder_profile"
    BIRDEYE_WALLET_PORTFOLIO = "birdeye_wallet_portfolio"
    BIRDEYE_WALLET_PNL = "birdeye_wallet_pnl"
    BIRDEYE_WALLET_PNL_DETAILS = "birdeye_wallet_pnl_details"
    BIRDEYE_WALLET_FIRST_FUNDED = "birdeye_wallet_first_funded"
    BIRDEYE_WALLET_NET_WORTH_HISTORY = "birdeye_wallet_net_worth_history"
    BIRDEYE_TOKEN_TRADE_DATA = "birdeye_token_trade_data"
    BIRDEYE_SMART_MONEY = "birdeye_smart_money"
    BIRDEYE_TOKEN_TOP_TRADERS = "birdeye_token_top_traders"
    TOKEN_DEEP_ANALYSIS = "token_deep_analysis"
    BUNDLE_RING_ANALYSIS = "bundle_ring_analysis"
    KOL_DISCOVERY_FEED = "kol_discovery_feed"
    BIRDEYE_SEARCH = "birdeye_search"
    BIRDEYE_PRICE_HISTORY = "birdeye_price_history"
    # DexScreener
    DEX_TOKEN = "dex_token"
    DEX_SEARCH = "dex_search"
    DEX_TRENDING = "dex_trending"
    DEX_LATEST_PAIRS = "dex_latest_pairs"
    # Helius
    HELIUS_TOKEN_HOLDERS = "helius_token_holders"
    HELIUS_TOKEN_SUPPLY = "helius_token_supply"
    HELIUS_WALLET_TOKENS = "helius_wallet_tokens"
    HELIUS_WALLET_TXS = "helius_wallet_txs"
    # Jupiter data
    JUP_PRICE = "jup_price"
    JUP_TOKEN_SEARCH = "jup_token_search"
    JUP_TRENDING = "jup_trending"
    # Robust fallback chains — preferred when accuracy matters more than provenance
    PRICE_ROBUST = "price_robust"
    HOLDERS_ROBUST = "holders_robust"
    TVL_ROBUST = "tvl_robust"
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
    # Relay.link — cross-chain data queries (dispatched via market_data.py, result interpreted inline)
    RELAY_GET_QUOTE = "relay_get_quote"
    RELAY_GET_CHAINS = "relay_get_chains"
    RELAY_GET_CHAINS_LIQUIDITY = "relay_get_chains_liquidity"
    RELAY_GET_CURRENCIES = "relay_get_currencies"
    RELAY_GET_TOKEN_PRICE = "relay_get_token_price"
    RELAY_GET_REQUESTS = "relay_get_requests"
    RELAY_INTENT_STATUS = "relay_intent_status"
    RELAY_GET_APP_FEE_BALANCES = "relay_get_app_fee_balances"
    RELAY_GET_SWAP_SOURCES = "relay_get_swap_sources"
    # Magic Eden — NFT marketplace queries
    ME_WALLET = "me_wallet"
    ME_WALLET_ACTIVITIES = "me_wallet_activities"
    ME_WALLET_OFFERS_MADE = "me_wallet_offers_made"
    ME_WALLET_OFFERS_RECEIVED = "me_wallet_offers_received"
    ME_WALLET_ESCROW_BALANCE = "me_wallet_escrow_balance"
    ME_COLLECTION_ACTIVITIES = "me_collection_activities"
    ME_COLLECTION_STATS = "me_collection_stats"
    ME_COLLECTIONS = "me_collections"
    # Token safety — the honeypot / tax / scam check.
    TOKEN_SAFETY = "token_safety"
    HONEYPOT_CHECK = "honeypot_check"
    SCAM_CHECK = "scam_check"
    RUG_CHECK = "rug_check"
    ME_COLLECTION_LISTINGS = "me_collection_listings"
    ME_COLLECTIONS_BATCH_LISTINGS = "me_collections_batch_listings"
    ME_COLLECTION_LEADERBOARD = "me_collection_leaderboard"
    ME_LAUNCHPAD_COLLECTIONS = "me_launchpad_collections"
    ME_BUY_INSTRUCTION = "me_buy_instruction"
    ME_BUY_NOW_TRANSFER_NFT = "me_buy_now_transfer_nft"
    ME_BUY_NOW = "me_buy_now"
    ME_BUY_CANCEL = "me_buy_cancel"
    ME_BUY_CHANGE_PRICE = "me_buy_change_price"
    ME_SELL = "me_sell"
    ME_SELL_CHANGE_PRICE = "me_sell_change_price"
    ME_SELL_NOW = "me_sell_now"
    ME_SELL_CANCEL = "me_sell_cancel"
    ME_DEPOSIT = "me_deposit"
    ME_WITHDRAW = "me_withdraw"
    ME_COLLECTION_ATTRIBUTES = "me_collection_attributes"
    ME_OWNER_ACTIVITIES = "me_owner_activities"
    ME_WALLET_TOKENS = "me_wallet_tokens"
    ME_TOKEN = "me_token"
    ME_TOKEN_LISTINGS = "me_token_listings"
    ME_TOKEN_OFFERS_RECEIVED = "me_token_offers_received"
    ME_TOKEN_ACTIVITIES = "me_token_activities"
    ME_MMM_POOLS = "me_mmm_pools"
    ME_MMM_TOKEN_POOLS = "me_mmm_token_pools"
    ME_MMM_CREATE_POOL = "me_mmm_create_pool"
    ME_MMM_UPDATE_POOL = "me_mmm_update_pool"
    ME_MMM_SOL_DEPOSIT_BUY = "me_mmm_sol_deposit_buy"
    ME_MMM_SOL_WITHDRAW_BUY = "me_mmm_sol_withdraw_buy"
    ME_MMM_SOL_CLOSE_POOL = "me_mmm_sol_close_pool"
    ME_MMM_SOL_FULFILL_BUY = "me_mmm_sol_fulfill_buy"
    ME_MMM_SOL_FULFILL_SELL = "me_mmm_sol_fulfill_sell"
    ME_MARKETPLACE_POPULAR = "me_marketplace_popular"
    # RAG knowledge base queries
    KNOWLEDGE = "knowledge"
    # Portfolio strategy generation
    STRATEGY = "strategy"
    # ── Protocol-native data queries (dispatched to Rust solana-service via
    # gateway /actions/build). Same string values as the corresponding
    # ActionType members; routing them through query_onchain prevents the LLM
    # from treating them as transaction-signing actions when the user is
    # asking a read-only question.
    # Raydium ───────────────────────────────────────────────────────────────
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
    # Orca ─────────────────────────────────────────────────────────────────
    ORCA_GET_POOLS = "orca_get_pools"
    ORCA_GET_POOL = "orca_get_pool"
    ORCA_SEARCH_POOLS = "orca_search_pools"
    ORCA_GET_USER_POSITIONS = "orca_get_user_positions"
    ORCA_GET_POOL_POSITIONS = "orca_get_pool_positions"
    ORCA_SEARCH_TOKENS = "orca_search_tokens"
    ORCA_GET_TOKEN = "orca_get_token"
    ORCA_GET_TOKENS = "orca_get_tokens"
    ORCA_GET_PROTOCOL_STATS = "orca_get_protocol_stats"
    ORCA_GET_ORCA_TOKEN = "orca_get_orca_token"
    ORCA_GET_CIRCULATING_SUPPLY = "orca_get_circulating_supply"
    ORCA_GET_TOTAL_SUPPLY = "orca_get_total_supply"
    ORCA_GET_LOCKED_LIQUIDITY = "orca_get_locked_liquidity"
    # Meteora ──────────────────────────────────────────────────────────────
    METEORA_DLMM_GET_PAIRS = "meteora_dlmm_get_pairs"
    METEORA_DLMM_GET_PAIR = "meteora_dlmm_get_pair"
    METEORA_DLMM_GET_USER_POSITIONS = "meteora_dlmm_get_user_positions"
    METEORA_DLMM_GET_ACTIVE_BIN = "meteora_dlmm_get_active_bin"
    METEORA_DLMM_GET_POOL_GROUP = "meteora_dlmm_get_pool_group"
    METEORA_DLMM_GET_POOL_GROUPS = "meteora_dlmm_get_pool_groups"
    METEORA_DLMM_GET_POOL_OHLCV = "meteora_dlmm_get_pool_ohlcv"
    METEORA_DLMM_GET_POOL_VOLUME_HISTORY = "meteora_dlmm_get_pool_volume_history"
    METEORA_DLMM_GET_PROTOCOL_STATS = "meteora_dlmm_get_protocol_stats"
    METEORA_DAMMV2_GET_POOL = "meteora_dammv2_get_pool"
    METEORA_DAMMV2_GET_POOL_GROUP = "meteora_dammv2_get_pool_group"
    METEORA_DAMMV2_GET_POOL_GROUPS = "meteora_dammv2_get_pool_groups"
    METEORA_DAMMV2_GET_POOL_OHLCV = "meteora_dammv2_get_pool_ohlcv"
    METEORA_DAMMV2_GET_POOL_VOLUME_HISTORY = "meteora_dammv2_get_pool_volume_history"
    METEORA_DAMMV2_GET_POOLS = "meteora_dammv2_get_pools"
    METEORA_DAMMV2_GET_USER_POSITIONS = "meteora_dammv2_get_user_positions"
    METEORA_DAMMV2_GET_PROTOCOL_METRICS = "meteora_dammv2_get_protocol_metrics"
    METEORA_DAMMV1_GET_ALPHA_VAULT_CONFIGS = "meteora_dammv1_get_alpha_vault_configs"
    METEORA_DAMMV1_GET_ALPHA_VAULTS = "meteora_dammv1_get_alpha_vaults"
    METEORA_DAMMV1_GET_FARMS = "meteora_dammv1_get_farms"
    METEORA_DAMMV1_GET_FEE_CONFIG = "meteora_dammv1_get_fee_config"
    METEORA_DAMMV1_GET_POOL_CONFIGS = "meteora_dammv1_get_pool_configs"
    METEORA_DAMMV1_GET_POOLS = "meteora_dammv1_get_pools"
    METEORA_DAMMV1_GET_POOLS_BY_VAULT_LP = "meteora_dammv1_get_pools_by_vault_lp"
    METEORA_DAMMV1_GET_POOLS_METRICS = "meteora_dammv1_get_pools_metrics"
    METEORA_DAMMV1_SEARCH_POOLS = "meteora_dammv1_search_pools"
    METEORA_S2E_FILTER_VAULTS = "meteora_s2e_filter_vaults"
    METEORA_S2E_GET_ALL_VAULTS = "meteora_s2e_get_all_vaults"
    METEORA_S2E_GET_ANALYTICS = "meteora_s2e_get_analytics"
    METEORA_S2E_GET_VAULT = "meteora_s2e_get_vault"
    METEORA_VAULT_GET_ADDRESSES = "meteora_vault_get_addresses"
    METEORA_VAULT_GET_APY = "meteora_vault_get_apy"
    METEORA_VAULT_GET_APY_HISTORY = "meteora_vault_get_apy_history"
    METEORA_VAULT_GET_INFO = "meteora_vault_get_info"
    METEORA_VAULT_GET_STATE = "meteora_vault_get_state"
    METEORA_VAULT_GET_VIRTUAL_PRICE = "meteora_vault_get_virtual_price"
    # Kamino ───────────────────────────────────────────────────────────────
    KAMINO_LOAN_DETAIL = "kamino_loan_detail"
    KAMINO_MARKET_DETAIL = "kamino_market_detail"
    KAMINO_MARKETS = "kamino_markets"
    KAMINO_MARKET_METRICS_HISTORY = "kamino_market_metrics_history"
    KAMINO_MARKET_RESERVE_HISTORY = "kamino_market_reserve_history"
    KAMINO_MARKET_RESERVES = "kamino_market_reserves"
    KAMINO_MARKET_RESERVES_ACCOUNT = "kamino_market_reserves_account"
    KAMINO_MARKET_LEVERAGE_METRICS = "kamino_market_leverage_metrics"
    KAMINO_OBLIGATION_INTEREST_EARNED = "kamino_obligation_interest_earned"
    KAMINO_OBLIGATION_INTEREST_PAID = "kamino_obligation_interest_paid"
    KAMINO_OBLIGATION_METRICS_HISTORY = "kamino_obligation_metrics_history"
    KAMINO_OBLIGATION_PNL = "kamino_obligation_pnl"
    KAMINO_OBLIGATION_TRANSACTIONS = "kamino_obligation_transactions"
    KAMINO_OPEN_BORROW_ORDERS = "kamino_open_borrow_orders"
    KAMINO_BORROW_ORDER_FILLS = "kamino_borrow_order_fills"
    KAMINO_ORACLE_PRICES = "kamino_oracle_prices"
    KAMINO_PRINCIPAL_TOKEN_YIELDS = "kamino_principal_token_yields"
    KAMINO_PRIVATE_CREDIT_METRICS = "kamino_private_credit_metrics"
    KAMINO_PRIVATE_CREDIT_METRICS_HISTORY = "kamino_private_credit_metrics_history"
    KAMINO_RESERVE_BORROW_APY_HISTORY = "kamino_reserve_borrow_apy_history"
    KAMINO_RESERVE_BORROW_APY_MEDIAN = "kamino_reserve_borrow_apy_median"
    KAMINO_REWARDS_HISTORY = "kamino_rewards_history"
    KAMINO_REWARDS_LIST = "kamino_rewards_list"
    KAMINO_SEASON_REWARDS_USER = "kamino_season_rewards_user"
    KAMINO_SEASON_REWARDS_VESTING_POOL = "kamino_season_rewards_vesting_pool"
    KAMINO_STAKING_YIELDS = "kamino_staking_yields"
    KAMINO_STAKING_YIELDS_MEAN = "kamino_staking_yields_mean"
    KAMINO_STAKING_YIELDS_MEDIAN = "kamino_staking_yields_median"
    KAMINO_USD_BENCHMARK_RATES = "kamino_usd_benchmark_rates"
    KAMINO_USER_FARM_TRANSACTIONS = "kamino_user_farm_transactions"
    KAMINO_USER_KLEND_TRANSACTIONS = "kamino_user_klend_transactions"
    KAMINO_USER_KLEND_TRANSACTIONS_ALL = "kamino_user_klend_transactions_all"
    KAMINO_USER_KVAULT_REWARDS = "kamino_user_kvault_rewards"
    KAMINO_USER_METRICS_HISTORY = "kamino_user_metrics_history"
    KAMINO_USER_OBLIGATIONS = "kamino_user_obligations"
    KAMINO_USER_REWARDS = "kamino_user_rewards"
    KAMINO_USER_STAKING_BOOSTS = "kamino_user_staking_boosts"
    KAMINO_USER_TRANSACTIONS = "kamino_user_transactions"
    KAMINO_USER_VAULT_METRICS_HISTORY = "kamino_user_vault_metrics_history"
    KAMINO_USER_VAULT_PNL = "kamino_user_vault_pnl"
    KAMINO_USER_VAULT_PNL_HISTORY = "kamino_user_vault_pnl_history"
    KAMINO_USER_VAULT_POSITION = "kamino_user_vault_position"
    KAMINO_USER_VAULT_POSITIONS = "kamino_user_vault_positions"
    KAMINO_VAULT_ALLOCATION_HISTORY = "kamino_vault_allocation_history"
    KAMINO_VAULT_DETAIL = "kamino_vault_detail"
    KAMINO_VAULT_METRICS = "kamino_vault_metrics"
    KAMINO_VAULT_METRICS_HISTORY = "kamino_vault_metrics_history"
    KAMINO_VAULT_MINT_IMAGE = "kamino_vault_mint_image"
    KAMINO_VAULT_MINT_METADATA = "kamino_vault_mint_metadata"
    KAMINO_VAULT_TRANSACTIONS = "kamino_vault_transactions"
    KAMINO_VAULTS = "kamino_vaults"
    KAMINO_VAULTS_REWARDS = "kamino_vaults_rewards"
    KAMINO_VAULTS_SUMMARY = "kamino_vaults_summary"
    KAMINO_LIQUIDITY_STRATEGIES = "kamino_liquidity_strategies"
    KAMINO_MULTIPLY_MARKETS = "kamino_multiply_markets"
    KAMINO_YIELD_HISTORY = "kamino_yield_history"
    KAMINO_AIRDROP_ALLOCATIONS = "kamino_airdrop_allocations"
    KAMINO_AIRDROP_METRICS = "kamino_airdrop_metrics"
    KAMINO_FARM_TRANSACTIONS = "kamino_farm_transactions"
    # MarginFi ─────────────────────────────────────────────────────────────
    MARGINFI_ACCOUNT_INFO = "marginfi_account_info"
    MARGINFI_BANK_DETAIL = "marginfi_bank_detail"
    MARGINFI_BANKS = "marginfi_banks"
    MARGINFI_HEALTH = "marginfi_health"
    MARGINFI_POINTS = "marginfi_points"
    MARGINFI_USER_ACCOUNTS = "marginfi_user_accounts"
    # Jupiter data (currently in ActionType, exposed here as queries) ───────
    JUP_DCA_ORDERS = "jup_dca_orders"
    JUP_LIMIT_ORDERS = "jup_limit_orders"
    JUP_TOKENS_TAG = "jup_tokens_tag"
    JUP_TOKENS_RECENT = "jup_tokens_recent"
    JUP_TOKENS_TRENDING = "jup_tokens_trending"
    JUP_PORTFOLIO_POSITIONS = "jup_portfolio_positions"
    JUP_PORTFOLIO_PLATFORMS = "jup_portfolio_platforms"
    JUP_STAKED_JUP = "jup_staked_jup"
    JUP_LEND_POSITIONS = "jup_lend_positions"
    JUP_LEND_EARNINGS = "jup_lend_earnings"
    JUP_PENDING_INVITES = "jup_pending_invites"
    JUP_LEND_MARKETS = "jup_lend_markets"
    JUP_PLATFORMS = "jup_platforms"
    # Pump.fun ─────────────────────────────────────────────────────────────
    # Live on-chain fetch of pump.fun bonding-curve global constants
    # (initial virtual reserves, fee bps, total supply, graduation target).
    # No params. Used for analytical/hypothetical bonding-curve math so the
    # model has authoritative numbers instead of stale KB values.
    PUMPFUN_CURVE_GLOBAL = "pumpfun_curve_global"
    # Pump.fun read-only queries. These live in ActionType too (the Rust
    # builder handles them via /actions/build with tx=None), but the LLM
    # reaches them through query_onchain, so they MUST be QueryType members
    # or _validate_query_onchain drops them as "unknown query_type". Kept in
    # sync with the pump.fun block of SOLANA_ACTION_DATA_TYPES in
    # clients/market_data.py.
    PUMPFUN_TOKEN_INFO = "pumpfun_token_info"
    PUMPFUN_TRENDING = "pumpfun_trending"
    PUMPFUN_NEW = "pumpfun_new"
    PUMPFUN_GRADUATING = "pumpfun_graduating"
    PUMPFUN_KOTH = "pumpfun_koth"
    PUMPFUN_SEARCH = "pumpfun_search"
    PUMPFUN_COMMENTS = "pumpfun_comments"
    PUMPFUN_USER = "pumpfun_user"
    PUMPFUN_BONDING_CURVE = "pumpfun_bonding_curve"
    PUMPSWAP_POOL_INFO = "pumpswap_pool_info"


# ---------------------------------------------------------------------------
# Parameter validation helpers
# ---------------------------------------------------------------------------

_SOLANA_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_EVM_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

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
    # Native validator staking
    "native_stake", "native_stake_deactivate", "native_stake_withdraw",
    "native_stake_split", "native_stake_merge",
    # LST staking
    "jupsol_stake", "jupsol_unstake",
    "jito_stake", "jito_unstake", "jito_tip", "jito_bundle",
    "marinade_stake", "marinade_unstake", "marinade_delayed_unstake", "marinade_claim_ticket",
    # Jupiter Perpetuals (open/close leveraged positions)
    "perp_open", "perp_close", "jlp_add", "jlp_remove",
    # Protocol-specific lending/borrowing (direct fund movement)
    "kamino_deposit", "kamino_withdraw", "kamino_borrow", "kamino_repay",
    "kamino_multiply_open", "kamino_multiply_add", "kamino_multiply_withdraw", "kamino_multiply_close",
    "kamino_long_open", "kamino_short_open", "kamino_position_close",
    "kamino_vault_deposit", "kamino_vault_withdraw",
    "marginfi_deposit", "marginfi_withdraw", "marginfi_borrow", "marginfi_repay",
    "marginfi_liquidate",
    "solend_deposit", "solend_withdraw", "solend_borrow", "solend_repay",
    "solend_add_collateral", "solend_withdraw_collateral",
    # Kamino KSwap (token swap via Kamino router)
    "kamino_kswap",
    # deBridge cross-chain (direct action, not through bridge alias)
    "debridge",
    # Streamflow token streaming & vesting
    "streamflow_create", "streamflow_create_multiple",
    "streamflow_cancel", "streamflow_transfer",
    "streamflow_topup", "streamflow_withdraw",
    # Magic Eden NFT marketplace (buy/sell/offer moves SOL)
    "me_buy", "me_list", "me_make_offer", "me_accept_offer",
    "me_cancel_listing", "me_cancel_offer",
    # SNS marketplace (pay SOL/USDC to buy or make offer on a domain)
    "sns_buy", "sns_make_offer",
    # SNS domain transfer (moves domain ownership to newOwner)
    "sns_transfer",
    # SNS subdomain transfer (moves subdomain ownership)
    "sns_transfer_subdomain",
    # Tensor NFT marketplace (buy moves SOL; list/make_offer lock tokens)
    "tensor_buy", "tensor_list", "tensor_make_offer",
    # Cross-chain Squid swap (may specify recipient on destination chain)
    "cross_chain_swap",
    # Jupiter Trigger/Recurring orders (lock input tokens in protocol escrow)
    "limit_order", "dca", "cancel_limit_order", "cancel_all_limit_orders", "cancel_dca",
    # Kamino stake/collateral operations
    "kamino_stake", "kamino_unstake", "kamino_add_collateral", "kamino_withdraw_collateral",
    # Relay direct execution steps
    "relay_execute", "relay_fast_fill",
    # Raydium swaps and LP operations
    "raydium_swap", "raydium_add_liquidity", "raydium_remove_liquidity",
    "raydium_create_pool", "raydium_open_position", "raydium_close_position",
    "raydium_increase_position", "raydium_decrease_position",
    # Orca Whirlpool swap and LP operations
    "orca_swap", "orca_add_liquidity", "orca_remove_liquidity",
    "orca_create_pool", "orca_open_position", "orca_close_position",
    "orca_increase_position", "orca_decrease_position",
    # Meteora DLMM base operations
    "meteora_swap", "meteora_add_liquidity", "meteora_remove_liquidity",
    "meteora_open_position", "meteora_close_position", "meteora_add_to_position",
    "meteora_stake", "meteora_unstake", "meteora_harvest",
    # Meteora DAMM v1 / DAMM v2 (swaps + liquidity)
    "meteora_dammv1_swap", "meteora_dammv1_deposit", "meteora_dammv1_withdraw",
    "meteora_dammv2_swap", "meteora_dammv2_add_liquidity", "meteora_dammv2_remove_liquidity",
    "meteora_dammv2_claim_fee", "meteora_dammv2_close_position",
    # Meteora Dynamic Vault
    "meteora_vault_deposit", "meteora_vault_withdraw",
    # Meteora Stake-to-Earn (m3m3)
    "meteora_s2e_stake", "meteora_s2e_unstake", "meteora_s2e_withdraw",
})

# Token symbols accepted as-is (not required to be mint addresses).
# All entries MUST be uppercase — _is_token_ref() calls .upper() before checking.
_KNOWN_SYMBOLS: frozenset[str] = frozenset({
    # Native & stablecoins (must stay in sync with Rust `STABLE_SYMBOLS` in
    # solana-service-rs/src/services/swap.rs — that list drives stable-pair
    # routing on the backend; this set gates whether the validator forwards
    # the symbol to it. Dropping a symbol here means the action is silently
    # rejected before Rust ever sees it.)
    "SOL", "WSOL", "USDC", "USDT", "PYUSD", "USDS", "DAI", "FDUSD", "USDE", "SUSDE",
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
_ADDR_ONLY_PARAMS: frozenset[str] = frozenset({
    "to", "wallet", "mint", "ticketAccount",
    # Native stake accounts
    "stakeAccount", "validatorVoteAccount", "destinationStakeAccount", "sourceStakeAccount",
    # SNS transactional
    "newOwner", "pubkey",
    # Streamflow streaming & vesting
    "streamId", "recipient", "newRecipient",
    # NFT marketplace (ME + Tensor)
    "tokenMint", "seller", "buyer", "tokenAccount", "tokenATA",
    "destinationATA", "destinationOwner", "pool",
    # Raydium CLMM pool / position accounts
    "poolId", "positionId",
    # Orca Whirlpool position / pool accounts
    "position", "whirlpool",
    # Magic Eden v2 API address params
    "auctionHouseAddress", "buyerReferral", "sellerReferral", "allowlistAuxAccount",
})
# Params that can be symbol OR mint address
_TOKEN_REF_PARAMS: frozenset[str] = frozenset({
    "inputMint", "outputMint", "token", "bank",
    # Kamino K-Lend reserve param (token symbol or mint address)
    "reserve",
    # kamino_kswap uses tokenIn/tokenOut
    "tokenIn", "tokenOut",
    # Raydium / Meteora pool token mints (LP pairs)
    "tokenA", "tokenB", "mintA", "mintB",
})

# Special token values allowed for the `burn` action that aren't valid symbols/addresses
# e.g. `token=dust` means "burn all dust tokens", `token=empty_accounts` means close accounts
_BURN_SPECIAL_TOKENS: frozenset[str] = frozenset({"dust", "empty_accounts"})
# Percentage sentinel ("32%", "50%") — resolved against the live balance at
# signing, same class as the "all" sentinel.
_PERCENT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")

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
    # Limit order / DCA timing
    "expirySeconds",
    # Slippage / fees
    "slippageBps", "baseFee", "binStep", "bpsToRemove",
    # Pagination / listing
    "page", "pageSize", "offset", "limit",
    # Timing
    "time", "expirySeconds", "startAt", "intervalSeconds",
    "startTimestamp", "endTimestamp", "fromTime", "toTime",
    "time_from", "time_to",
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
    # Kamino specific
    "percent", "ktokenAmount",
    # kamino_kswap
    "amountIn", "maxSlippageBps",
    # NFT marketplace pricing (ME + Tensor)
    "price", "maxPaymentAmount", "minPaymentAmount", "newPrice",
    "solAmount", "solDeposit", "spreadFee", "mmFeeBps", "lpFeeBp",
    "curveDelta", "spotPrice", "buysideCreatorRoyaltyBp",
    # Magic Eden v2 fulfill operations
    "assetAmount",
    # Priority fees
    "priorityFee",
})

_MAX_PARAM_VALUE_LEN = 512

# LLMs (especially gpt-5.4-nano) sometimes emit snake_case param names instead of
# the camelCase format the frontend expects. Normalize before validation so the
# frontend always receives consistent camelCase keys.
_SNAKE_TO_CAMEL: dict[str, str] = {
    "input_mint":  "inputMint",
    "output_mint": "outputMint",
    "token_in":    "tokenIn",
    "token_out":   "tokenOut",
    "token_a":     "tokenA",
    "token_b":     "tokenB",
    "mint_a":      "mintA",
    "mint_b":      "mintB",
    "slippage_bps": "slippageBps",
    "chain_from_previous": "chainFromPrevious",
}


def _is_solana_address(v: str) -> bool:
    return bool(_SOLANA_ADDR_RE.match(v))


def _is_token_ref(v: str) -> bool:
    return v.upper() in _KNOWN_SYMBOLS or _is_solana_address(v)


def validate_action_params(
    action_type: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and sanitise params for *action_type*.

    Rules:
    - All keys must be str (others are skipped).
    - String values may not exceed _MAX_PARAM_VALUE_LEN characters.
    - Native int/float values are preserved as-is (not stringified) so Rust
      can deserialize typed fields (u32, f64, etc.) without conversion.
    - String values that represent addresses or token refs are validated.
    - Values may not exceed _MAX_PARAM_VALUE_LEN characters (string length).
    - Address-only params must be valid base58 Solana pubkeys.
    - Token-ref params must be a known symbol or valid mint address.
    - Numeric params must parse as float (or be "all" / "auto").

    Returns sanitised dict[str, Any] — native types (int/float/bool) preserved for
    Rust serde compatibility; strings are trimmed.
    Raises ValueError describing the first violation found.
    """
    sanitised: dict[str, Any] = {}

    # Normalize snake_case keys to camelCase before validation (LLM sometimes uses either)
    params = {_SNAKE_TO_CAMEL.get(k, k): v for k, v in params.items()}

    # Enforce minimum required params per action so the LLM can't emit
    # `execute_action({"action_type":"swap"})` with no params and produce
    # an empty / useless card. gpt-5.4-mini reliably forgets to fill
    # params under tool_choice=required; without this guard the validator
    # accepted empty {} and the frontend rendered a blank swap UI.
    _REQUIRED = {
        "swap":             ("amount", "inputMint", "outputMint"),
        "transfer":         ("amount", "recipient"),
        "burn":             ("amount", "token"),
        "stake":            ("amount",),
        "unstake":          ("amount",),
        "jito_stake":       ("amount",),
        "jito_unstake":     ("amount",),
        "marinade_stake":   ("amount",),
        "marinade_unstake": ("amount",),
        "jupsol_stake":     ("amount",),
        "jupsol_unstake":   ("amount",),
    }
    if (req := _REQUIRED.get(action_type)):
        missing = [k for k in req if not params.get(k) and not params.get(_SNAKE_TO_CAMEL.get(k, k))]
        if missing:
            raise ValueError(
                f"{action_type} missing required param(s): {', '.join(missing)}"
            )

    for raw_key, raw_val in params.items():
        if not isinstance(raw_key, str):
            continue
        # Allow lists for known list params
        if isinstance(raw_val, list):
            if any(isinstance(v, dict) for v in raw_val):
                # List of objects (e.g. streamflow recipients) — preserve as JSON array string
                import json as _json
                raw_val = _json.dumps(raw_val)
            else:
                # List of scalars (e.g. close_accounts mints) — join as comma-separated
                raw_val = ",".join(str(v) for v in raw_val if isinstance(v, (str, int, float)))
        if not isinstance(raw_val, (str, int, float)):
            continue
        key = raw_key.strip()
        # Use str representation only for validation; preserve native type for output
        val = str(raw_val).strip()

        if not key:
            continue
        # JSON-serialized arrays can legitimately be long; apply a higher limit for them
        effective_max = _MAX_PARAM_VALUE_LEN * 16 if val.startswith("[") else _MAX_PARAM_VALUE_LEN
        if len(val) > effective_max:
            raise ValueError(
                f"Parameter '{key}' value exceeds maximum length "
                f"({len(val)} > {effective_max})"
            )

        if key in _ADDR_ONLY_PARAMS and val not in ("self", "auto"):
            # For burn, `mint` can be a token symbol, a special sentinel, or a mint address.
            is_burn_mint = (action_type == "burn" and key == "mint")
            if is_burn_mint:
                if val.lower() not in _BURN_SPECIAL_TOKENS and not _is_token_ref(val):
                    raise ValueError(
                        f"'mint' must be a known token symbol or valid mint address, got: {val!r}"
                    )
            elif key == "to" and val.endswith(".sol"):
                pass  # Allow .sol domain names as transfer destinations
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
                and key == "mint"
                and val.lower() in _BURN_SPECIAL_TOKENS
            )
            if not is_burn_special and not _is_token_ref(val):
                raise ValueError(
                    f"'{key}' must be a known token symbol or valid mint address, "
                    f"got: {val!r}"
                )

        if key in _NUMERIC_PARAMS and val not in ("all", "auto"):
            # Percentage sentinel ("32%", "50%") — the frontend resolves it
            # against the live balance at signing, same as "all". Accept 0<p<=100.
            _pct = _PERCENT_RE.match(str(val).strip()) if isinstance(val, str) else None
            if _pct:
                p = float(_pct.group(1))
                if not (0 < p <= 100):
                    raise ValueError(f"'{key}' percentage must be between 0 and 100, got: {val!r}")
                sanitised[key] = str(val).strip()
                continue
            try:
                num = float(val)
            except ValueError:
                raise ValueError(
                    f"'{key}' must be numeric (or 'all'/'auto'/'N%'), got: {val!r}"
                )
            if num < 0:
                raise ValueError(
                    f"'{key}' must be non-negative, got: {val!r}"
                )
            if num > 1e15:
                raise ValueError(
                    f"'{key}' exceeds maximum allowed value (1e15), got: {val!r}"
                )

        # Meteora DLMM strategy must be one of the three valid bin distribution types
        if key == "strategy" and action_type.startswith("meteora_"):
            _METEORA_STRATEGIES = {"uniform", "spot", "curve"}
            if val.lower() not in _METEORA_STRATEGIES:
                raise ValueError(
                    f"'strategy' must be one of {sorted(_METEORA_STRATEGIES)}, got: {val!r}"
                )
            # Store the normalised lowercase string
            sanitised[key] = val.lower()
            continue

        # Preserve native int/float types so Rust typed fields (u32, f64, etc.)
        # can deserialize without conversion errors. Strings are trimmed.
        if isinstance(raw_val, bool):
            sanitised[key] = raw_val
        elif isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
            sanitised[key] = raw_val
        else:
            sanitised[key] = val

    return sanitised


# ---------------------------------------------------------------------------
# Pydantic output models
# ---------------------------------------------------------------------------

class ValidatedAction(BaseModel):
    type: ActionType
    params: dict[str, Any]
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
    params: dict[str, Any]

    def to_frontend_dict(self) -> dict:
        return {"type": self.type.value, "params": self.params}


class ClarifyOption(BaseModel):
    label: str
    sublabel: str | None = None
    action: str
    params: dict[str, Any]

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
                "Refer to the system prompt parameter documentation for required params per action. "
                "For launch_token: only name and symbol are required. "
                "pump.fun ticker rules: letters and numbers only (A-Z, 0-9), max 10 chars — no underscores, hyphens, or special chars. "
                "Set symbol = ticker uppercased with all invalid chars stripped (e.g. ZORT1234_ → ZORT1234). "
                "If you stripped characters, mention it briefly after calling the tool. Never ask for the ticker. "
                "Optional params: description (only if user gave one — never auto-generate), "
                "initialBuyAmount (SOL), twitter/telegram/website, "
                "cashback='true'. "
                "Never ask for description or image — the UI handles them."
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
    # Wallet-specific on-chain scans — always restricted to authenticated wallet
    "scan_empty_accounts",
    "my_stake_accounts",
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

    # Accept both "action_type" (schema field) and "type" (LLM sometimes
    # uses this name instead, especially with Turkish/non-English prompts).
    raw_action_type = args.get("action_type") or args.get("type") or ""
    try:
        action_type = ActionType(raw_action_type)
    except ValueError:
        logger.warning("execute_action: unknown action_type %r", raw_action_type)
        return None

    raw_params = args.get("params", {})
    if not isinstance(raw_params, dict):
        logger.warning("execute_action: params is not a dict")
        return None

    # Normalize: LLM sometimes emits params at the top level instead of
    # nested under the "params" key (observed with pumpfun actions).
    if not raw_params:
        _schema_keys = {"action_type", "type", "params", "chain_from_previous", "_chain_depth"}
        raw_params = {k: v for k, v in args.items() if k not in _schema_keys}

    try:
        sanitised = validate_action_params(action_type.value, raw_params)
    except ValueError as exc:
        logger.warning("execute_action '%s' param validation failed: %s", action_type.value, exc)
        return None

    # For launch_token: if the model omitted symbol, derive it from name.
    # Always strip non-alphanumeric chars — pump.fun rejects symbols like "ZORT1234_".
    if action_type in (ActionType.LAUNCH_TOKEN, ActionType.PUMPFUN_LAUNCH):
        name = sanitised.get("name", "")
        sym = sanitised.get("symbol") or name
        sanitised["symbol"] = re.sub(r"[^A-Z0-9]", "", sym.upper())[:10]

    # Pool-required guard: Raydium liquidity actions need EITHER
    #   (a) poolId / positionId          — direct reference, OR
    #   (b) tokenA + tokenB              — Rust auto-resolves to a pool via
    #                                       `lookup_raydium_clmm_pool(ta, tb)`.
    # The Rust builder (see raydium.rs:996) already does this resolution
    # safely: invalid symbols / non-existent pairs return clean errors that
    # surface to the user. Forcing the LLM to manually call
    # `raydium_search_pools` first is unnecessary friction and produces the
    # "I can't respond" UX users hit when poolId is missing but tokenA+B
    # are present. We still drop when NONE of {poolId, positionId, tokenA+B}
    # are present — that case is genuinely unresolvable.
    _POOL_ID_ACTIONS_WITH_TOKEN_FALLBACK = {
        ActionType.RAYDIUM_OPEN_POSITION,
        ActionType.RAYDIUM_ADD_LIQUIDITY,
    }
    _POOL_ID_STRICT_ACTIONS = {
        ActionType.RAYDIUM_INCREASE_POSITION,
        ActionType.RAYDIUM_DECREASE_POSITION,
        ActionType.RAYDIUM_REMOVE_LIQUIDITY,
        ActionType.RAYDIUM_CLOSE_POSITION,
    }
    if action_type in (_POOL_ID_ACTIONS_WITH_TOKEN_FALLBACK | _POOL_ID_STRICT_ACTIONS):
        pool_id = sanitised.get("poolId") or sanitised.get("pool_id") or sanitised.get("pool")
        position_id = sanitised.get("positionId") or sanitised.get("position_id")
        has_pool = bool(pool_id and str(pool_id).strip())
        has_position = bool(position_id and str(position_id).strip())
        if not has_pool and not has_position:
            if action_type in _POOL_ID_ACTIONS_WITH_TOKEN_FALLBACK:
                token_a = sanitised.get("tokenA") or sanitised.get("token_a") or sanitised.get("inputMint")
                token_b = sanitised.get("tokenB") or sanitised.get("token_b") or sanitised.get("outputMint")
                has_token_pair = bool(
                    token_a and str(token_a).strip()
                    and token_b and str(token_b).strip()
                )
                if not has_token_pair:
                    logger.warning(
                        "execute_action '%s' dropped: need poolId, positionId, or tokenA+tokenB.",
                        action_type.value,
                    )
                    return None
            else:
                logger.warning(
                    "execute_action '%s' dropped: poolId or positionId required (existing-position action).",
                    action_type.value,
                )
                return None

    # Flag actions whose destination is an unrecognised program/protocol address.
    # Transfer is excluded: the recipient is always a user wallet, not a protocol,
    # so the "not a known protocol" warning is meaningless and misleading there.
    _PROTOCOL_DEST_ACTIONS = _FUND_MOVING_ACTIONS - {"transfer"}
    warn_dest = False
    if action_type.value in _PROTOCOL_DEST_ACTIONS:
        _DEST_KEYS = ("to", "recipient", "newRecipient", "newOwner")
        for _dk in _DEST_KEYS:
            dest = sanitised.get(_dk, "")
            # Flag EVM addresses (0x...) for cross-chain actions.
            # For Solana addresses only warn on non-transfer actions (protocol interactions).
            if isinstance(dest, str) and dest and dest not in ("self", "auto") and (
                bool(_EVM_ADDR_RE.match(dest))
            ):
                warn_dest = True
                break

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

    # `nft_collection` had no backend — the card invented four NFTs and their
    # floor prices. Magic Eden answers the same question with the wallet's real
    # holdings and their real listing state, so the query becomes that one.
    if query_type is QueryType.NFT_COLLECTION:
        query_type = QueryType.ME_WALLET_TOKENS
        wallet = (
            str_params.pop("wallet", "")
            or str_params.pop("address", "")
            or str_params.pop("owner", "")
            or "self"
        )
        str_params["walletAddress"] = wallet

    # jup_token_search resolves a SINGLE token symbol/name/mint to its mint
    # address. The Rust handler does not understand pair-shaped queries, so a
    # call like "JupSOL-SOL" returns 0 results AND its description text
    # ("0 token(s) found for ...") leaks into the user-facing stream when the
    # LLM narrates it on the way to a corrected protocol query. Block here so
    # the model is forced to pick the correct pool-list tool directly.
    if query_type.value == "jup_token_search":
        q = str_params.get("query", "").strip()
        # Pair indicators that single-token searches never contain. Single
        # token names with spaces (e.g. "Pyth Network") still pass; "X-Y",
        # "X/Y", "X,Y", "X vs Y" all fail. Hyphen check is `-` between two
        # word-chars to avoid blocking legitimate names that use a hyphen as
        # part of a single token (rare but possible).
        is_pair = (
            "/" in q
            or "," in q
            or re.search(r"\b(vs|VS)\b", q) is not None
            or re.search(r"\w-\w", q) is not None
        )
        if is_pair:
            logger.warning(
                "jup_token_search rejected: pair-shaped query %r — model "
                "should call the protocol's pool-list tool instead.", q,
            )
            return None

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

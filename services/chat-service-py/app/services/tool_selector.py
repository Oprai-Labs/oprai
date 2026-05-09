"""
Tool selector for OPRAI Chat Service.

Builds the execute_action / query_onchain schema passed to the LLM:
  - Protocol selected by user → only that protocol's tools (focused)
  - No protocol selected     → all tools (LLM decides intent)
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.action_schemas import ActionType, QueryType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag taxonomy
# ---------------------------------------------------------------------------
# Each action / query value maps to a frozenset of tags.
# ToolSelector unions the tags from active signals then filters both enums.

_A = frozenset  # shorthand

ACTION_TAGS: dict[str, frozenset[str]] = {
    # ── Always (core, universal) ────────────────────────────────────────────
    "transfer":    _A({"always", "core"}),
    "swap":        _A({"always", "core", "dex"}),
    "burn":        _A({"core"}),
    "close_accounts": _A({"core"}),

    # ── DEX / Jupiter ───────────────────────────────────────────────────────
    "limit_order":              _A({"dex", "jupiter"}),
    "cancel_limit_order":       _A({"dex", "jupiter"}),
    "cancel_all_limit_orders":  _A({"dex", "jupiter"}),
    "dca":                      _A({"dex", "jupiter"}),
    "cancel_dca":               _A({"dex", "jupiter"}),
    "jlp_add":                  _A({"dex", "jupiter"}),
    "jlp_remove":               _A({"dex", "jupiter"}),
    "perp_open":                _A({"perp", "jupiter"}),
    "perp_close":               _A({"perp", "jupiter"}),
    "jupsol_stake":             _A({"staking", "jupiter"}),
    "jupsol_unstake":           _A({"staking", "jupiter"}),
    # Jupiter data-query action types
    "jup_dca_orders":           _A({"dex", "jupiter", "analysis"}),
    "jup_limit_orders":         _A({"dex", "jupiter", "analysis"}),
    "jup_price":                _A({"dex", "jupiter", "price", "always"}),
    "jup_token_search":         _A({"dex", "jupiter", "price"}),
    "jup_tokens_tag":           _A({"dex", "jupiter"}),
    "jup_tokens_recent":        _A({"dex", "jupiter"}),
    "jup_tokens_trending":      _A({"dex", "jupiter", "analysis"}),
    "jup_portfolio_positions":  _A({"dex", "jupiter", "portfolio", "analysis"}),
    "jup_staked_jup":           _A({"dex", "jupiter", "staking"}),
    "jup_lend_positions":       _A({"lending", "jupiter"}),
    "jup_lend_earnings":        _A({"lending", "jupiter"}),
    "jup_pending_invites":      _A({"jupiter"}),
    "jup_lend_markets":         _A({"lending", "jupiter"}),
    "jup_platforms":            _A({"jupiter", "analysis"}),

    # ── Raydium ─────────────────────────────────────────────────────────────
    "raydium_swap":                    _A({"dex", "raydium"}),
    "raydium_add_liquidity":           _A({"dex", "raydium", "liquidity"}),
    "raydium_remove_liquidity":        _A({"dex", "raydium", "liquidity"}),
    "raydium_create_pool":             _A({"dex", "raydium", "liquidity"}),
    "raydium_open_position":           _A({"dex", "raydium", "liquidity"}),
    "raydium_close_position":          _A({"dex", "raydium", "liquidity"}),
    "raydium_increase_position":       _A({"dex", "raydium", "liquidity"}),
    "raydium_decrease_position":       _A({"dex", "raydium", "liquidity"}),
    "raydium_get_pools":               _A({"dex", "raydium", "analysis", "liquidity"}),
    "raydium_search_pools":            _A({"dex", "raydium", "analysis"}),
    "raydium_swap_quote":              _A({"dex", "raydium", "price"}),
    "raydium_get_pool_info":           _A({"dex", "raydium", "analysis"}),
    "raydium_get_user_positions":      _A({"dex", "raydium", "portfolio"}),
    "raydium_get_clmm_positions":      _A({"dex", "raydium", "portfolio"}),
    "raydium_get_token_info":          _A({"dex", "raydium", "price"}),
    "raydium_get_platform_stats":      _A({"dex", "raydium", "analysis"}),
    "raydium_get_clmm_configs":        _A({"dex", "raydium"}),
    "raydium_get_pools_by_lp":         _A({"dex", "raydium", "liquidity"}),
    "raydium_get_pools_v2":            _A({"dex", "raydium"}),
    "raydium_get_pool_keys":           _A({"dex", "raydium"}),
    "raydium_get_pool_liquidity_history": _A({"dex", "raydium", "analysis"}),
    "raydium_get_pool_position_history":  _A({"dex", "raydium", "analysis"}),
    "raydium_get_token_list":          _A({"dex", "raydium"}),
    "raydium_get_token_prices":        _A({"dex", "raydium", "price"}),
    "raydium_get_farm_info":           _A({"dex", "raydium"}),
    "raydium_get_farm_by_lp":          _A({"dex", "raydium"}),
    "raydium_get_farm_keys":           _A({"dex", "raydium"}),
    "raydium_get_ido_keys":            _A({"dex", "raydium"}),
    "raydium_get_main_version":        _A({"dex", "raydium"}),
    "raydium_get_rpcs":                _A({"dex", "raydium"}),
    "raydium_get_chain_time":          _A({"dex", "raydium"}),
    "raydium_get_stake_pools":         _A({"dex", "raydium", "staking"}),
    "raydium_get_migrate_lp":          _A({"dex", "raydium"}),
    "raydium_get_auto_fee":            _A({"dex", "raydium"}),
    "raydium_get_cpmm_configs":        _A({"dex", "raydium"}),

    # ── Orca ─────────────────────────────────────────────────────────────────
    "orca_swap":               _A({"dex", "orca"}),
    "orca_add_liquidity":      _A({"dex", "orca", "liquidity"}),
    "orca_remove_liquidity":   _A({"dex", "orca", "liquidity"}),
    "orca_open_position":      _A({"dex", "orca", "liquidity"}),
    "orca_close_position":     _A({"dex", "orca", "liquidity"}),
    "orca_increase_position":  _A({"dex", "orca", "liquidity"}),
    "orca_decrease_position":  _A({"dex", "orca", "liquidity"}),
    "orca_collect_fees":       _A({"dex", "orca", "liquidity"}),
    "orca_collect_rewards":    _A({"dex", "orca", "liquidity"}),
    "orca_create_pool":        _A({"dex", "orca", "liquidity"}),
    "orca_get_pools":          _A({"dex", "orca", "analysis", "liquidity"}),
    "orca_search_pools":       _A({"dex", "orca", "analysis"}),
    "orca_get_pool":           _A({"dex", "orca", "analysis"}),
    "orca_get_locked_liquidity":   _A({"dex", "orca", "liquidity"}),
    "orca_get_protocol_stats":     _A({"dex", "orca", "analysis"}),
    "orca_get_orca_token":         _A({"dex", "orca", "price"}),
    "orca_get_circulating_supply": _A({"dex", "orca"}),
    "orca_get_total_supply":       _A({"dex", "orca"}),
    "orca_get_tokens":             _A({"dex", "orca"}),
    "orca_search_tokens":          _A({"dex", "orca", "price"}),
    "orca_get_token":              _A({"dex", "orca", "price"}),
    "orca_get_user_positions":     _A({"dex", "orca", "portfolio"}),
    "orca_get_pool_positions":     _A({"dex", "orca", "liquidity"}),

    # ── Meteora ──────────────────────────────────────────────────────────────
    "meteora_swap":              _A({"dex", "meteora"}),
    "meteora_add_liquidity":     _A({"dex", "meteora", "liquidity"}),
    "meteora_remove_liquidity":  _A({"dex", "meteora", "liquidity"}),
    "meteora_create_pool":       _A({"dex", "meteora", "liquidity"}),
    "meteora_open_position":     _A({"dex", "meteora", "liquidity"}),
    "meteora_close_position":    _A({"dex", "meteora", "liquidity"}),
    "meteora_add_to_position":   _A({"dex", "meteora", "liquidity"}),
    "meteora_claim_fees":        _A({"dex", "meteora", "liquidity"}),
    "meteora_claim_rewards":     _A({"dex", "meteora", "liquidity"}),
    "meteora_stake":             _A({"dex", "meteora", "staking"}),
    "meteora_unstake":           _A({"dex", "meteora", "staking"}),
    "meteora_harvest":           _A({"dex", "meteora"}),
    "meteora_dammv1_swap":       _A({"dex", "meteora"}),
    "meteora_dammv1_deposit":    _A({"dex", "meteora", "liquidity"}),
    "meteora_dammv1_withdraw":   _A({"dex", "meteora", "liquidity"}),
    "meteora_dammv2_swap":       _A({"dex", "meteora"}),
    "meteora_dammv2_add_liquidity":    _A({"dex", "meteora", "liquidity"}),
    "meteora_dammv2_remove_liquidity": _A({"dex", "meteora", "liquidity"}),
    "meteora_vault_deposit":     _A({"dex", "meteora"}),
    "meteora_vault_withdraw":    _A({"dex", "meteora"}),
    "meteora_s2e_stake":         _A({"dex", "meteora", "staking"}),
    "meteora_s2e_unstake":       _A({"dex", "meteora", "staking"}),
    "meteora_s2e_claim_fee":     _A({"dex", "meteora"}),
    "meteora_s2e_cancel_unstake":_A({"dex", "meteora"}),
    "meteora_s2e_withdraw":      _A({"dex", "meteora"}),
    # Meteora queries
    "meteora_dlmm_get_pairs":          _A({"dex", "meteora", "analysis"}),
    "meteora_dlmm_get_pair":           _A({"dex", "meteora", "analysis"}),
    "meteora_dlmm_get_user_positions": _A({"dex", "meteora", "portfolio"}),
    "meteora_dlmm_get_active_bin":     _A({"dex", "meteora"}),
    "meteora_dlmm_get_pool_groups":    _A({"dex", "meteora"}),
    "meteora_dlmm_get_pool_group":     _A({"dex", "meteora"}),
    "meteora_dlmm_get_pool_ohlcv":     _A({"dex", "meteora", "analysis"}),
    "meteora_dlmm_get_pool_volume_history": _A({"dex", "meteora", "analysis"}),
    "meteora_dlmm_get_protocol_stats": _A({"dex", "meteora", "analysis"}),
    "meteora_dammv2_get_pools":        _A({"dex", "meteora", "analysis"}),
    "meteora_dammv2_get_pool_groups":  _A({"dex", "meteora"}),
    "meteora_dammv2_get_pool_group":   _A({"dex", "meteora"}),
    "meteora_dammv2_get_pool":         _A({"dex", "meteora", "analysis"}),
    "meteora_dammv2_get_pool_ohlcv":   _A({"dex", "meteora", "analysis"}),
    "meteora_dammv2_get_pool_volume_history": _A({"dex", "meteora", "analysis"}),
    "meteora_dammv2_get_protocol_metrics":    _A({"dex", "meteora", "analysis"}),
    "meteora_dammv1_get_pools":        _A({"dex", "meteora"}),
    "meteora_dammv1_get_pool_configs": _A({"dex", "meteora"}),
    "meteora_dammv1_search_pools":     _A({"dex", "meteora", "analysis"}),
    "meteora_dammv1_get_farms":        _A({"dex", "meteora"}),
    "meteora_dammv1_get_pools_metrics":_A({"dex", "meteora", "analysis"}),
    "meteora_dammv1_get_alpha_vaults": _A({"dex", "meteora"}),
    "meteora_dammv1_get_alpha_vault_configs": _A({"dex", "meteora"}),
    "meteora_dammv1_get_pools_by_vault_lp":  _A({"dex", "meteora"}),
    "meteora_dammv1_get_fee_config":   _A({"dex", "meteora"}),
    "meteora_vault_get_info":          _A({"dex", "meteora"}),
    "meteora_vault_get_addresses":     _A({"dex", "meteora"}),
    "meteora_vault_get_state":         _A({"dex", "meteora"}),
    "meteora_vault_get_apy":           _A({"dex", "meteora", "analysis"}),
    "meteora_vault_get_apy_history":   _A({"dex", "meteora", "analysis"}),
    "meteora_vault_get_virtual_price": _A({"dex", "meteora"}),
    "meteora_s2e_get_analytics":       _A({"dex", "meteora", "analysis"}),
    "meteora_s2e_get_all_vaults":      _A({"dex", "meteora"}),
    "meteora_s2e_filter_vaults":       _A({"dex", "meteora"}),
    "meteora_s2e_get_vault":           _A({"dex", "meteora"}),

    # ── Staking ───────────────────────────────────────────────────────────────
    "stake":                    _A({"staking"}),
    "unstake":                  _A({"staking"}),
    "native_stake":             _A({"staking", "native_stake"}),
    "native_stake_deactivate":  _A({"staking", "native_stake"}),
    "native_stake_withdraw":    _A({"staking", "native_stake"}),
    "native_stake_split":       _A({"staking", "native_stake"}),
    "native_stake_merge":       _A({"staking", "native_stake"}),
    "jito_stake":               _A({"staking", "jito"}),
    "jito_unstake":             _A({"staking", "jito"}),
    "jito_tip":                 _A({"staking", "jito"}),
    "jito_bundle":              _A({"staking", "jito"}),
    "jito_bundle_status":       _A({"staking", "jito"}),
    "marinade_stake":           _A({"staking", "marinade"}),
    "marinade_unstake":         _A({"staking", "marinade"}),
    "marinade_delayed_unstake": _A({"staking", "marinade"}),
    "marinade_claim_ticket":    _A({"staking", "marinade"}),
    "kamino_stake":             _A({"staking", "kamino"}),
    "kamino_unstake":           _A({"staking", "kamino"}),

    # ── Lending / Kamino ─────────────────────────────────────────────────────
    "lend":          _A({"lending"}),
    "withdraw_lend": _A({"lending"}),
    "borrow":        _A({"lending"}),
    "repay":         _A({"lending"}),
    "kamino_deposit":             _A({"lending", "kamino"}),
    "kamino_withdraw":            _A({"lending", "kamino"}),
    "kamino_borrow":              _A({"lending", "kamino"}),
    "kamino_repay":               _A({"lending", "kamino"}),
    "kamino_add_collateral":      _A({"lending", "kamino"}),
    "kamino_withdraw_collateral": _A({"lending", "kamino"}),
    "kamino_multiply_open":       _A({"lending", "kamino"}),
    "kamino_multiply_add":        _A({"lending", "kamino"}),
    "kamino_multiply_withdraw":   _A({"lending", "kamino"}),
    "kamino_multiply_close":      _A({"lending", "kamino"}),
    "kamino_long_open":           _A({"lending", "kamino", "perp"}),
    "kamino_short_open":          _A({"lending", "kamino", "perp"}),
    "kamino_position_close":      _A({"lending", "kamino"}),
    "kamino_vault_deposit":       _A({"lending", "kamino"}),
    "kamino_vault_withdraw":      _A({"lending", "kamino"}),
    "kamino_kswap":               _A({"dex", "kamino"}),
    "kamino_borrow_instructions":         _A({"lending", "kamino"}),
    "kamino_repay_instructions":          _A({"lending", "kamino"}),
    "kamino_deposit_instructions":        _A({"lending", "kamino"}),
    "kamino_withdraw_instructions":       _A({"lending", "kamino"}),
    "kamino_vault_deposit_instructions":  _A({"lending", "kamino"}),
    "kamino_vault_withdraw_instructions": _A({"lending", "kamino"}),
    # Kamino queries
    "kamino_vaults":              _A({"lending", "kamino", "analysis"}),
    "kamino_markets":             _A({"lending", "kamino", "analysis"}),
    "kamino_market_reserves":     _A({"lending", "kamino", "analysis"}),
    "kamino_user_vault_positions":_A({"lending", "kamino", "portfolio"}),
    "kamino_user_obligations":    _A({"lending", "kamino", "portfolio"}),
    "kamino_oracle_prices":       _A({"lending", "kamino", "price"}),
    "kamino_usd_benchmark_rates": _A({"lending", "kamino", "analysis"}),
    "kamino_market_metrics_history":    _A({"lending", "kamino", "analysis"}),
    "kamino_reserve_borrow_apy_history":_A({"lending", "kamino", "analysis"}),
    "kamino_reserve_borrow_apy_median": _A({"lending", "kamino", "analysis"}),
    "kamino_obligation_interest_earned":_A({"lending", "kamino", "portfolio"}),
    "kamino_obligation_interest_paid":  _A({"lending", "kamino", "portfolio"}),
    "kamino_obligation_transactions":   _A({"lending", "kamino", "portfolio"}),
    "kamino_user_klend_transactions_all":_A({"lending", "kamino", "portfolio"}),
    "kamino_user_klend_transactions":   _A({"lending", "kamino", "portfolio"}),
    "kamino_borrow_order_fills":  _A({"lending", "kamino"}),
    "kamino_open_borrow_orders":  _A({"lending", "kamino"}),
    "kamino_yield_history":       _A({"lending", "kamino", "analysis"}),
    "kamino_principal_token_yields":_A({"lending", "kamino", "analysis"}),
    "kamino_airdrop_allocations": _A({"kamino"}),
    "kamino_airdrop_metrics":     _A({"kamino"}),
    "kamino_staking_yields":      _A({"lending", "staking", "kamino", "analysis"}),
    "kamino_staking_yields_median":_A({"lending", "staking", "kamino", "analysis"}),
    "kamino_staking_yields_mean": _A({"lending", "staking", "kamino", "analysis"}),
    "kamino_user_staking_boosts": _A({"lending", "staking", "kamino", "portfolio"}),
    "kamino_season_rewards_user": _A({"kamino", "portfolio"}),
    "kamino_season_rewards_vesting_pool":_A({"kamino"}),
    "kamino_private_credit_metrics":      _A({"lending", "kamino", "analysis"}),
    "kamino_private_credit_metrics_history":_A({"lending", "kamino", "analysis"}),
    "kamino_user_farm_transactions":_A({"kamino", "portfolio"}),
    "kamino_farm_transactions":   _A({"kamino"}),
    "kamino_vault_detail":        _A({"lending", "kamino", "analysis"}),
    "kamino_vault_metrics":       _A({"lending", "kamino", "analysis"}),
    "kamino_vault_metrics_history":_A({"lending", "kamino", "analysis"}),
    "kamino_vault_allocation_history":_A({"lending", "kamino", "analysis"}),
    "kamino_vaults_rewards":      _A({"lending", "kamino"}),
    "kamino_vaults_summary":      _A({"lending", "kamino", "analysis"}),
    "kamino_vault_mint_metadata": _A({"lending", "kamino"}),
    "kamino_vault_mint_image":    _A({"lending", "kamino"}),
    "kamino_user_metrics_history":_A({"lending", "kamino", "portfolio"}),
    "kamino_user_transactions":   _A({"kamino", "portfolio"}),
    "kamino_user_kvault_rewards": _A({"kamino", "portfolio"}),
    "kamino_user_vault_position": _A({"lending", "kamino", "portfolio"}),
    "kamino_user_vault_metrics_history":_A({"lending", "kamino", "portfolio"}),
    "kamino_user_vault_pnl":      _A({"lending", "kamino", "portfolio"}),
    "kamino_user_vault_pnl_history":_A({"lending", "kamino", "portfolio"}),
    "kamino_vault_transactions":  _A({"kamino"}),
    "kamino_market_detail":       _A({"lending", "kamino", "analysis"}),
    "kamino_market_reserve_history":_A({"lending", "kamino", "analysis"}),
    "kamino_market_leverage_metrics":_A({"lending", "kamino", "analysis"}),
    "kamino_market_reserves_account":_A({"lending", "kamino"}),
    "kamino_user_rewards":        _A({"kamino", "portfolio"}),
    "kamino_loan_detail":         _A({"lending", "kamino", "portfolio"}),
    "kamino_obligation_pnl":      _A({"lending", "kamino", "portfolio"}),
    "kamino_obligation_metrics_history":_A({"lending", "kamino", "portfolio"}),
    "kamino_rewards_list":        _A({"kamino"}),
    "kamino_rewards_history":     _A({"kamino", "portfolio"}),

    # ── MarginFi ──────────────────────────────────────────────────────────────
    "marginfi_create_account":     _A({"lending", "marginfi"}),
    "marginfi_create_account_pda": _A({"lending", "marginfi"}),
    "marginfi_close_account":      _A({"lending", "marginfi"}),
    "marginfi_close_balance":      _A({"lending", "marginfi"}),
    "marginfi_transfer_account":   _A({"lending", "marginfi"}),
    "marginfi_deposit":            _A({"lending", "marginfi"}),
    "marginfi_withdraw":           _A({"lending", "marginfi"}),
    "marginfi_borrow":             _A({"lending", "marginfi"}),
    "marginfi_repay":              _A({"lending", "marginfi"}),
    "marginfi_liquidate":          _A({"lending", "marginfi"}),
    "marginfi_start_liquidation":  _A({"lending", "marginfi"}),
    "marginfi_end_liquidation":    _A({"lending", "marginfi"}),
    "marginfi_flashloan_start":    _A({"lending", "marginfi"}),
    "marginfi_flashloan_end":      _A({"lending", "marginfi"}),
    "marginfi_place_order":        _A({"lending", "marginfi"}),
    "marginfi_close_order":        _A({"lending", "marginfi"}),
    "marginfi_execute_order_start":_A({"lending", "marginfi"}),
    "marginfi_execute_order_end":  _A({"lending", "marginfi"}),
    "marginfi_claim_emissions":    _A({"lending", "marginfi"}),
    "marginfi_update_emissions_destination":_A({"lending", "marginfi"}),
    "marginfi_settle_emissions":   _A({"lending", "marginfi"}),
    "marginfi_withdraw_emissions_permissionless":_A({"lending", "marginfi"}),
    "marginfi_clear_emissions":    _A({"lending", "marginfi"}),
    "marginfi_set_keeper_flags":   _A({"lending", "marginfi"}),
    "marginfi_init_liq_record":    _A({"lending", "marginfi"}),
    "marginfi_accrue_interest":    _A({"lending", "marginfi"}),
    "marginfi_pulse_price":        _A({"lending", "marginfi"}),
    "marginfi_pulse_health":       _A({"lending", "marginfi"}),
    "marginfi_account_info":       _A({"lending", "marginfi", "portfolio"}),
    "marginfi_banks":              _A({"lending", "marginfi", "analysis"}),
    "marginfi_health":             _A({"lending", "marginfi", "portfolio"}),
    "marginfi_points":             _A({"marginfi", "portfolio"}),
    "marginfi_bank_detail":        _A({"lending", "marginfi", "analysis"}),
    "marginfi_user_accounts":      _A({"lending", "marginfi", "portfolio"}),

    # ── Solend ────────────────────────────────────────────────────────────────
    "solend_deposit":            _A({"lending", "solend"}),
    "solend_withdraw":           _A({"lending", "solend"}),
    "solend_borrow":             _A({"lending", "solend"}),
    "solend_repay":              _A({"lending", "solend"}),
    "solend_add_collateral":     _A({"lending", "solend"}),
    "solend_withdraw_collateral":_A({"lending", "solend"}),
    "solend_user_info":          _A({"lending", "solend", "portfolio"}),
    "solend_reserves":           _A({"lending", "solend", "analysis"}),
    "solend_market":             _A({"lending", "solend", "analysis"}),

    # ── NFT / PumpFun ─────────────────────────────────────────────────────────
    "launch_token":         _A({"nft", "token_launch", "pumpfun"}),
    "pumpfun_launch":       _A({"nft", "token_launch", "pumpfun"}),
    "pumpfun_buy":          _A({"nft", "pumpfun"}),
    "pumpfun_sell":         _A({"nft", "pumpfun"}),
    "pumpswap_buy":         _A({"nft", "pumpfun", "dex"}),
    "pumpswap_sell":        _A({"nft", "pumpfun", "dex"}),
    "pumpfun_token_info":   _A({"nft", "pumpfun", "price", "analysis"}),
    "pumpfun_trending":     _A({"nft", "pumpfun", "analysis"}),
    "pumpfun_new":          _A({"nft", "pumpfun"}),
    "pumpfun_graduating":   _A({"nft", "pumpfun"}),
    "pumpfun_koth":         _A({"nft", "pumpfun", "analysis"}),
    "pumpfun_search":       _A({"nft", "pumpfun", "analysis"}),
    "pumpfun_comments":     _A({"nft", "pumpfun"}),
    "pumpfun_user":         _A({"nft", "pumpfun"}),
    "pumpfun_bonding_curve":_A({"nft", "pumpfun", "analysis"}),
    "pumpswap_pool_info":   _A({"dex", "pumpfun", "analysis"}),

    # ── Magic Eden ───────────────────────────────────────────────────────────
    "me_buy":             _A({"nft", "magic_eden"}),
    "me_list":            _A({"nft", "magic_eden"}),
    "me_cancel_listing":  _A({"nft", "magic_eden"}),
    "me_make_offer":      _A({"nft", "magic_eden"}),
    "me_accept_offer":    _A({"nft", "magic_eden"}),
    "me_cancel_offer":    _A({"nft", "magic_eden"}),
    "me_collection_info": _A({"nft", "magic_eden"}),
    "me_nft_info":        _A({"nft", "magic_eden"}),
    "me_wallet_nfts":     _A({"nft", "magic_eden", "portfolio"}),
    "me_collection_activity":_A({"nft", "magic_eden", "analysis"}),
    "me_listings":        _A({"nft", "magic_eden"}),
    "me_offers":          _A({"nft", "magic_eden"}),
    "me_collection_nfts": _A({"nft", "magic_eden"}),

    # ── Tensor ────────────────────────────────────────────────────────────────
    "tensor_buy":           _A({"nft", "tensor"}),
    "tensor_list":          _A({"nft", "tensor"}),
    "tensor_cancel_listing":_A({"nft", "tensor"}),
    "tensor_make_offer":    _A({"nft", "tensor"}),
    "tensor_cancel_offer":  _A({"nft", "tensor"}),
    "tensor_collection_info":_A({"nft", "tensor", "analysis"}),
    "tensor_nft_info":      _A({"nft", "tensor"}),
    "tensor_wallet_nfts":   _A({"nft", "tensor", "portfolio"}),
    "tensor_listings":      _A({"nft", "tensor"}),

    # ── Bridge ────────────────────────────────────────────────────────────────
    "cross_chain_swap":              _A({"bridge"}),
    "bridge":                        _A({"bridge"}),
    "relay_bridge":                  _A({"bridge", "relay"}),
    "relay_index_transaction":       _A({"bridge", "relay"}),
    "relay_single_transaction":      _A({"bridge", "relay"}),
    "relay_deposit_address_reindex": _A({"bridge", "relay"}),
    "relay_claim_app_fees":          _A({"bridge", "relay"}),
    "relay_fast_fill":               _A({"bridge", "relay"}),
    "relay_execute":                 _A({"bridge", "relay"}),
    "debridge":                      _A({"bridge", "debridge"}),
    "squid":                         _A({"bridge", "squid"}),
    "squid_bridge":                  _A({"bridge", "squid"}),
    "squid_status":                  _A({"bridge", "squid"}),

    # ── Streamflow ────────────────────────────────────────────────────────────
    "streamflow_create":          _A({"streaming", "streamflow"}),
    "streamflow_create_multiple": _A({"streaming", "streamflow"}),
    "streamflow_cancel":          _A({"streaming", "streamflow"}),
    "streamflow_withdraw":        _A({"streaming", "streamflow"}),
    "streamflow_transfer":        _A({"streaming", "streamflow"}),
    "streamflow_topup":           _A({"streaming", "streamflow"}),
    "streamflow_update":          _A({"streaming", "streamflow"}),
    "streamflow_get_one":         _A({"streaming", "streamflow"}),
    "streamflow_list":            _A({"streaming", "streamflow"}),

    # ── SNS ───────────────────────────────────────────────────────────────────
    "sns_resolve":           _A({"sns"}),
    "sns_register":          _A({"sns"}),
    "sns_transfer":          _A({"sns"}),
    "sns_reverse_lookup":    _A({"sns"}),
    "sns_check_available":   _A({"sns"}),
    "sns_domains":           _A({"sns"}),
    "sns_record":            _A({"sns"}),
    "sns_domain_info":       _A({"sns"}),
    "sns_primary_domain":    _A({"sns"}),
    "sns_list":              _A({"sns"}),
    "sns_buy":               _A({"sns"}),
    "sns_make_offer":        _A({"sns"}),
    "sns_accept_offer":      _A({"sns"}),
    "sns_cancel_offer":      _A({"sns"}),
    "sns_set_record":        _A({"sns"}),
    "sns_delete":            _A({"sns"}),
    "sns_create_subdomain":  _A({"sns"}),
    "sns_set_favorite":      _A({"sns"}),
    "sns_subdomains":        _A({"sns"}),
    "sns_transfer_subdomain":_A({"sns"}),
}

QUERY_TAGS: dict[str, frozenset[str]] = {
    "balance":              _A({"always", "core", "portfolio"}),
    "claim":                _A({"core"}),
    "vote":                 _A({"core"}),
    "scan_empty_accounts":  _A({"core", "portfolio"}),
    "my_stake_accounts":    _A({"staking", "portfolio"}),
    "top_validators":       _A({"staking", "analysis"}),
    # Birdeye
    "birdeye_price":            _A({"always", "price"}),
    "birdeye_multi_price":      _A({"price", "analysis"}),
    "birdeye_token_overview":   _A({"price", "analysis"}),
    "birdeye_token_metadata":   _A({"price", "analysis"}),
    "birdeye_token_security":   _A({"analysis"}),
    "birdeye_ohlcv":            _A({"price", "analysis"}),
    "birdeye_token_trending":   _A({"analysis"}),
    "birdeye_new_listings":     _A({"analysis"}),
    "birdeye_token_holders":    _A({"analysis"}),
    "birdeye_wallet_portfolio": _A({"always", "portfolio", "analysis"}),
    "birdeye_wallet_pnl":       _A({"portfolio", "analysis"}),
    "birdeye_smart_money":      _A({"analysis"}),
    "birdeye_token_top_traders":_A({"analysis"}),
    "birdeye_search":           _A({"price", "analysis"}),
    "birdeye_price_history":    _A({"price", "analysis"}),
    # DexScreener
    "dex_token":        _A({"dex", "price", "analysis"}),
    "dex_search":       _A({"dex", "analysis"}),
    "dex_trending":     _A({"dex", "analysis"}),
    "dex_latest_pairs": _A({"dex", "analysis"}),
    # Helius
    "helius_token_holders":_A({"analysis"}),
    "helius_token_supply": _A({"analysis"}),
    "helius_wallet_tokens":_A({"always", "portfolio"}),
    "helius_wallet_txs":   _A({"portfolio", "analysis"}),
    # Jupiter
    "jup_price":        _A({"always", "dex", "jupiter", "price"}),
    "jup_token_search": _A({"dex", "jupiter", "price"}),
    "jup_trending":     _A({"dex", "jupiter", "analysis"}),
    # Robust fallback chains
    "price_robust":     _A({"always", "price"}),
    "holders_robust":   _A({"analysis"}),
    "tvl_robust":       _A({"dex", "price", "analysis"}),
    # Generic
    "price":          _A({"always", "price"}),
    "portfolio":      _A({"always", "portfolio"}),
    "positions":      _A({"portfolio", "lending", "dex"}),
    "transactions":   _A({"portfolio", "analysis"}),
    "token_info":     _A({"always", "price"}),
    "trending":       _A({"analysis"}),
    "network":        _A({"core"}),
    "risk":           _A({"analysis", "lending"}),
    "yield":          _A({"analysis", "staking", "lending"}),
    "analytics":      _A({"analysis"}),
    "nft_collection": _A({"nft", "analysis"}),
    "airdrops":       _A({"portfolio", "analysis"}),
    "gas":            _A({"always", "core"}),
    "wallet_info":    _A({"always", "core", "portfolio"}),
    "tax_report":     _A({"portfolio", "analysis"}),
    "limit_orders":   _A({"dex", "jupiter"}),
    "dca":            _A({"dex", "jupiter"}),
    "lend_positions": _A({"lending", "portfolio"}),
    "perp_positions": _A({"perp", "portfolio"}),
    "simulate":       _A({"core"}),
    "whale":          _A({"analysis"}),
    "smart_money":    _A({"analysis"}),
    # Relay
    "relay_get_quote":          _A({"bridge", "relay"}),
    "relay_get_chains":         _A({"bridge", "relay"}),
    "relay_get_chains_liquidity":_A({"bridge", "relay"}),
    "relay_get_currencies":     _A({"bridge", "relay"}),
    "relay_get_token_price":    _A({"bridge", "relay", "price"}),
    "relay_get_requests":       _A({"bridge", "relay"}),
    "relay_intent_status":      _A({"bridge", "relay"}),
    "relay_get_app_fee_balances":_A({"bridge", "relay"}),
    "relay_get_swap_sources":   _A({"bridge", "relay"}),
    # Magic Eden queries
    "me_wallet":               _A({"nft", "magic_eden", "portfolio"}),
    "me_wallet_activities":    _A({"nft", "magic_eden", "portfolio"}),
    "me_wallet_offers_made":   _A({"nft", "magic_eden", "portfolio"}),
    "me_wallet_offers_received":_A({"nft", "magic_eden", "portfolio"}),
    "me_wallet_escrow_balance":_A({"nft", "magic_eden", "portfolio"}),
    "me_collection_activities":_A({"nft", "magic_eden", "analysis"}),
    "me_collection_stats":     _A({"nft", "magic_eden", "analysis"}),
    "me_collections":          _A({"nft", "magic_eden"}),
    "me_collection_listings":  _A({"nft", "magic_eden"}),
    "me_collections_batch_listings":_A({"nft", "magic_eden"}),
    "me_collection_holder_stats":_A({"nft", "magic_eden", "analysis"}),
    "me_collection_leaderboard":_A({"nft", "magic_eden", "analysis"}),
    "me_launchpad_collections":_A({"nft", "magic_eden"}),
    "me_buy_instruction":      _A({"nft", "magic_eden"}),
    "me_buy_now_transfer_nft": _A({"nft", "magic_eden"}),
    "me_buy_now":              _A({"nft", "magic_eden"}),
    "me_buy_cancel":           _A({"nft", "magic_eden"}),
    "me_buy_change_price":     _A({"nft", "magic_eden"}),
    "me_sell":                 _A({"nft", "magic_eden"}),
    "me_sell_change_price":    _A({"nft", "magic_eden"}),
    "me_sell_now":             _A({"nft", "magic_eden"}),
    "me_sell_cancel":          _A({"nft", "magic_eden"}),
    "me_deposit":              _A({"nft", "magic_eden"}),
    "me_withdraw":             _A({"nft", "magic_eden"}),
    "me_collection_attributes":_A({"nft", "magic_eden"}),
    "me_owner_activities":     _A({"nft", "magic_eden"}),
    "me_wallet_tokens":        _A({"nft", "magic_eden", "portfolio"}),
    "me_token":                _A({"nft", "magic_eden"}),
    "me_token_listings":       _A({"nft", "magic_eden"}),
    "me_token_offers_received":_A({"nft", "magic_eden"}),
    "me_token_activities":     _A({"nft", "magic_eden"}),
    "me_mmm_pools":            _A({"nft", "magic_eden"}),
    "me_mmm_token_pools":      _A({"nft", "magic_eden"}),
    "me_mmm_create_pool":      _A({"nft", "magic_eden"}),
    "me_mmm_update_pool":      _A({"nft", "magic_eden"}),
    "me_mmm_sol_deposit_buy":  _A({"nft", "magic_eden"}),
    "me_mmm_sol_withdraw_buy": _A({"nft", "magic_eden"}),
    "me_mmm_sol_close_pool":   _A({"nft", "magic_eden"}),
    "me_mmm_sol_fulfill_buy":  _A({"nft", "magic_eden"}),
    "me_mmm_sol_fulfill_sell": _A({"nft", "magic_eden"}),
    "me_marketplace_popular":  _A({"nft", "magic_eden", "analysis"}),
    "me_magic_ticket_burns":   _A({"nft", "magic_eden"}),
    # Knowledge + Strategy
    "knowledge": _A({"always", "core"}),
    "strategy":  _A({"analysis"}),
}

# ---------------------------------------------------------------------------
# Protocol → tags
# ---------------------------------------------------------------------------

PROTOCOL_TO_TAGS: dict[str, frozenset[str]] = {
    # Protocol scope is strictly the protocol's own tag — no shared category
    # tags ("dex", "lending", "staking") here. The category tags were causing
    # cross-protocol leakage on the action side: scoping to Meteora pulled in
    # every DEX action (Raydium, Orca …) because they share the "dex" tag.
    # Queries are full-catalogue regardless, so we only need protocol-strict
    # scoping for actions, which is what these single-tag entries provide.
    #
    # The one intentional exception is `native_stake`: bare "stake X SOL"
    # without a protocol should leave Marinade / Jito / JupSOL stake actions
    # available so the prompt's default-routing rule can pick. Hence the
    # shared `staking` tag stays on `native_stake` only.
    "jupiter":     _A({"jupiter"}),
    "raydium":     _A({"raydium"}),
    "orca":        _A({"orca"}),
    "meteora":     _A({"meteora"}),
    "marinade":    _A({"marinade"}),
    "jito":        _A({"jito"}),
    "kamino":      _A({"kamino"}),
    "marginfi":    _A({"marginfi"}),
    "solend":      _A({"solend"}),
    "tensor":      _A({"tensor"}),
    "magic_eden":  _A({"magic_eden"}),
    "pumpfun":     _A({"pumpfun", "token_launch"}),
    "relay":       _A({"relay"}),
    "debridge":    _A({"debridge"}),
    "squid":       _A({"squid"}),
    "streamflow":  _A({"streamflow"}),
    "native_stake":_A({"native_stake", "staking"}),
}


# ---------------------------------------------------------------------------
# Protocol detection — delegated to the LLM IntentRouter
# ---------------------------------------------------------------------------
# This module USED to ship a regex (`_PROTOCOL_DETECT_RE`) that pattern-matched
# protocol names + venue keywords ("DLMM" → meteora, "Whirlpool" → orca, …).
# The regex was brittle: every new venue keyword had to be hand-coded, every
# language got its own blind spots ("havuz", "пул", "プール" all bypassed the
# English-only word list), and adding a protocol meant editing this file.
#
# Detection has moved to `intent_router.IntentRouter.classify()`, which runs a
# tiny LLM (gpt-5.4-nano, ~100 ms, ~$0.00003/call) per request and returns
# canonical protocol ids. The classifier is multilingual and venue-aware.
# `ToolSelector.build()` now expects an explicit list of detected protocol
# ids — there is no in-process pattern matching.

# ---------------------------------------------------------------------------
# Auto-migration: read-only "actions" → query_onchain
# ---------------------------------------------------------------------------
# Many protocol-specific data fetchers (raydium_search_pools, kamino_loan_detail,
# meteora_dlmm_get_pairs, …) historically lived in ActionType because Rust's
# /actions/build endpoint serves them. They sign no transaction, so exposing
# them under execute_action makes the model treat read-only questions as if
# they could trigger a wallet prompt — and it falls back to weaker aggregators.
# Source of truth: market_data.SOLANA_ACTION_DATA_TYPES.
from app.clients import market_data as _md  # noqa: E402

for _at in _md.SOLANA_ACTION_DATA_TYPES:
    if _at in ACTION_TAGS:
        QUERY_TAGS[_at] = ACTION_TAGS.pop(_at)
    elif _at not in QUERY_TAGS:
        # Tag-less entry (still routable by name when no protocol filter active).
        QUERY_TAGS[_at] = _A({"analysis"})

# ---------------------------------------------------------------------------
# Schema builder
# ---------------------------------------------------------------------------

_ALL_ACTION_VALUES = [e.value for e in ActionType if e.value not in _md.SOLANA_ACTION_DATA_TYPES]
_ALL_QUERY_VALUES  = [e.value for e in QueryType]


def _build_tools(action_types: list[str], query_types: list[str]) -> list[dict[str, Any]]:
    """Build the 3-function tool schema with filtered enums."""
    return [
        {
            "type": "function",
            "function": {
                "name": "execute_action",
                "description": (
                    "Execute an on-chain Solana transaction for the user. "
                    "ONLY call this when the user explicitly commands a transaction: "
                    "swap, transfer, send, stake, unstake, buy, sell, lend, borrow, "
                    "withdraw, deposit, launch, add/remove liquidity. "
                    "NEVER call execute_action for informational or analytical requests — "
                    "questions about APR, APY, price, balance, portfolio, yield, fees, "
                    "or any 'what is / how much / show me' phrasing must use query_onchain "
                    "or be answered from knowledge. "
                    "CRITICAL: Always extract ALL parameter values from the user's message — "
                    "never submit empty params if the user provided an address, amount, or token. "
                    "Examples: pumpfun_buy→{mint, amount, denominatedInSol}; "
                    "swap→{inputMint, outputMint, amount}; transfer→{to, amount, token}."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action_type": {
                            "type": "string",
                            "enum": action_types,
                            "description": "The specific action to execute.",
                        },
                        "params": {
                            "type": "object",
                            "description": (
                                "All action parameters extracted from the user's message. "
                                "NEVER leave required params empty — if user gave an address, amount, "
                                "or token, it must appear here. Use 'all' for full balance."
                            ),
                            "additionalProperties": {"type": "string"},
                        },
                        "chain_from_previous": {
                            "type": "boolean",
                            "description": "True when this action uses the output of the previous action as input.",
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
                    "Use this for any informational or analytical request: balances, token prices, "
                    "APR/APY rates, yield opportunities, staking rates (jitoSOL/mSOL/jupSOL/etc.), "
                    "positions, transaction history, portfolio analytics, trending tokens, pool data, "
                    "network status, gas fees — any 'what is / how much / show me / compare' question.\n\n"
                    "FORBIDDEN: Do NOT call query_onchain('balance', ...) as a fallback when the user "
                    "expresses a TRANSACTION intent (sell, buy, swap, send, transfer, stake, lend, "
                    "borrow, withdraw, close — in ANY language) but you cannot fully parse the params. "
                    "In that case, call `request_clarification` instead, or ask the user in one short "
                    "sentence. Substituting a balance query for an unclear action is wrong — it makes "
                    "the user repeat themselves. If the user said 'sell' or any transaction verb in "
                    "any language, the response must be either execute_action or request_clarification, "
                    "NEVER query_onchain('balance')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_type": {
                            "type": "string",
                            "enum": query_types,
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
                    "Request clarification from the user ONLY when their intent for an "
                    "ACTION (transaction that signs / moves funds) is truly ambiguous — "
                    "for example 'stake' without specifying a protocol, or 'swap' without "
                    "specifying which DEX. Present 2-4 concrete options the user can choose. "
                    "NEVER use this for any read-only data question (price, liquidity, holders, "
                    "volume, balance, portfolio, list, top, total, how much, how many). For "
                    "those, pick the most useful default — sum across pools, return USD price, "
                    "aggregate by token — and call query_onchain immediately. Asking the user "
                    "to pick a quote currency or pool slice for an info question is forbidden."
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
                                        "enum": action_types,
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
# ToolSelector
# ---------------------------------------------------------------------------

class ToolSelector:
    """
    Builds the tool schema passed to the LLM for each request.

    Hybrid filtering policy:

      • **query_onchain** — read-only, no funds at risk. When the detected
        protocols are staking-only (jito/marinade/native_stake) with no DEX
        venue named, DEX pool query tools are excluded — the model cannot
        confuse LP pool APY with native staking APR. For all other protocol
        combinations the full query catalogue is sent.

      • **execute_action** — moves funds, signs transactions. Scoped to the
        detected protocols so the model can only emit actions for the venues
        the user actually named.

      • **request_clarification** — follows the action enum automatically.
    """

    _ALWAYS = _A({"always"})

    # Protocols whose queries are staking/lending only — no DEX pool tools.
    _STAKING_PROTOCOLS: frozenset[str] = frozenset({"jito", "marinade", "native_stake"})
    # Protocols that imply DEX/LP context — always send full query catalogue.
    _DEX_PROTOCOLS: frozenset[str] = frozenset({
        "meteora", "raydium", "orca", "jupiter", "pumpfun", "tensor", "magic_eden",
    })

    def build(
        self,
        protocols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the tool catalogue for this request.

        *protocols* is the canonical-id list produced by `IntentRouter`.
        """
        active_protos: set[str] = {
            proto.lower().replace("-", "_") for proto in (protocols or [])
        }

        # Query filtering: if ALL detected protocols are staking-only and no
        # DEX venue was named, remove DEX pool query tools so the model cannot
        # route "jitoSOL APR?" to `meteora_dlmm_get_pairs` instead of `yield`.
        staking_only = (
            bool(active_protos)
            and active_protos <= self._STAKING_PROTOCOLS
            and not (active_protos & self._DEX_PROTOCOLS)
        )
        if staking_only:
            sel_queries = [
                v for v in _ALL_QUERY_VALUES
                if "dex" not in QUERY_TAGS.get(v, _A())
                # Also exclude lending-only tools that aren't staking-relevant
                # (e.g. jup_lend_markets has {"lending","jupiter"} — no "staking"
                # or "always" tag — and confuses "jitoSOL APR" with lending rates).
                and not (
                    "lending" in QUERY_TAGS.get(v, _A())
                    and "staking" not in QUERY_TAGS.get(v, _A())
                    and "always" not in QUERY_TAGS.get(v, _A())
                )
            ]
        else:
            sel_queries = _ALL_QUERY_VALUES

        # Actions: scope to the supplied protocols, or full set when empty.
        if active_protos:
            active: set[str] = set(self._ALWAYS)
            for proto in active_protos:
                active |= PROTOCOL_TO_TAGS.get(proto, set())
            sel_actions = [v for v in _ALL_ACTION_VALUES if ACTION_TAGS.get(v, _A()) & active]
        else:
            sel_actions = _ALL_ACTION_VALUES

        logger.debug(
            "ToolSelector: protocols=%s staking_only=%s → actions=%d queries=%d",
            sorted(active_protos), staking_only, len(sel_actions), len(sel_queries),
        )
        return _build_tools(sel_actions, sel_queries)

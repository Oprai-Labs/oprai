"""Single-source one-line descriptions for query_onchain tools.

WHY: the tool schema only exposes tool NAMES (the query_type enum) — it carries
no per-tool descriptions. The LLM learns what a tool does from (a) its name and
(b) the prompt fragment docs. But ~40% of tools have NO prompt doc (deep
protocol-specific analytics: kamino/raydium/orca/magic_eden/sns/jup), so the
model was working from the name alone.

This module gives every tool a concise one-liner. `describe()` resolves in
order: this map → the python function's docstring → a humanized name. The
active-protocol tool set (already scoped by ToolSelector) is rendered by
`build_query_reference()` INTO the query_onchain schema, so descriptions travel
with the offered tools and cost nothing when a protocol isn't active.

`audit_coverage()` reports tools that have no real description anywhere (map,
docstring, or prompt) — the coverage guard that makes silent orphans visible.
"""

from __future__ import annotations

import glob
import logging
import os

logger = logging.getLogger(__name__)

# Hand-authored one-liners, focused on the tools that have NO prompt doc.
# Keep each terse and faithful to the name. Protocol prefix keeps them scannable.
TOOL_DESCRIPTIONS: dict[str, str] = {
    # ── Core cross-cutting (shown in EVERY request — keep these sharp) ──
    "balance": "A wallet's SOL and SPL-token balances",
    "portfolio": "Full wallet portfolio — tokens, DeFi positions, NFTs, total USD value",
    "positions": "A wallet's open DeFi positions across all protocols",
    "lend_positions": "A wallet's lending/borrowing positions across protocols",
    "perp_positions": "A wallet's perpetual-futures positions",
    "transactions": "A wallet's recent transaction history",
    "wallet_info": "Overview of a wallet — age, activity level, labels",
    "price": "Current price of a single token",
    "token_info": "General info about a token — name, supply, price, links",
    "trending": "Trending tokens right now",
    "smart_money": "Tokens currently being accumulated by smart-money wallets",
    "whale": "Whale-wallet activity and large holders",
    "nft_collection": "NFT collection stats — floor price, volume, listings",
    "gas": "Current Solana network fees / priority-fee estimate",
    "network": "Solana network status — TPS, current slot, health",
    "analytics": "General on-chain analytics summary for a token or wallet",
    "risk": "Risk assessment for a token, wallet, or position",
    "strategy": "Suggested DeFi strategy / yield plan for the user",
    "airdrops": "Airdrop opportunities and eligibility for a wallet",
    "tax_report": "Tax report generated from a wallet's transactions",
    "knowledge": "Answer from the DeFi knowledge base (concepts, how-tos, protocol docs)",
    "pumpfun_curve_global": "pump.fun global bonding-curve constants — fees, reserves, graduation target",
    # ── Jupiter (extra portfolio / token / lend reads) ──
    "jup_dca_orders": "Jupiter — a wallet's active & past DCA (dollar-cost-average) orders",
    "jup_limit_orders": "Jupiter — a wallet's open & historical limit orders",
    "jup_tokens_tag": "Jupiter — tokens carrying a given tag (verified, lst, stablecoin, etc.)",
    "jup_tokens_recent": "Jupiter — most recently listed tokens",
    "jup_tokens_trending": "Jupiter — trending tokens by recent volume/activity",
    "jup_lend_positions": "Jupiter Lend — a wallet's lending/borrowing positions",
    "jup_lend_earnings": "Jupiter Lend — a wallet's accrued lending earnings",
    "jup_pending_invites": "Jupiter — pending referral invites for a wallet",
    "jup_platforms": "Jupiter — list of supported portfolio/DeFi platforms",
    # ── Kamino (lending/vault analytics — mostly historical/metrics reads) ──
    "kamino_market_metrics_history": "Kamino — historical metrics for a lending market",
    "kamino_market_reserve_history": "Kamino — historical data for a market reserve",
    "kamino_market_reserves_account": "Kamino — reserve account details for a market",
    "kamino_market_leverage_metrics": "Kamino — leverage/multiply metrics for a market",
    "kamino_obligation_interest_earned": "Kamino — interest earned by an obligation (position)",
    "kamino_obligation_interest_paid": "Kamino — interest paid by an obligation",
    "kamino_obligation_metrics_history": "Kamino — historical metrics for an obligation",
    "kamino_obligation_transactions": "Kamino — transaction history for an obligation",
    "kamino_open_borrow_orders": "Kamino — open borrow orders",
    "kamino_borrow_order_fills": "Kamino — fills for borrow orders",
    "kamino_principal_token_yields": "Kamino — yields for principal tokens",
    "kamino_private_credit_metrics": "Kamino — private-credit market metrics",
    "kamino_private_credit_metrics_history": "Kamino — historical private-credit metrics",
    "kamino_reserve_borrow_apy_history": "Kamino — historical borrow APY for a reserve",
    "kamino_reserve_borrow_apy_median": "Kamino — median borrow APY for a reserve",
    "kamino_rewards_history": "Kamino — rewards distribution history",
    "kamino_season_rewards_vesting_pool": "Kamino — season-rewards vesting pool details",
    "kamino_staking_yields_mean": "Kamino — mean staking yield across pools",
    "kamino_staking_yields_median": "Kamino — median staking yield across pools",
    "kamino_usd_benchmark_rates": "Kamino — USD benchmark interest rates",
    "kamino_user_farm_transactions": "Kamino — a user's farm transactions",
    "kamino_user_klend_transactions_all": "Kamino — all of a user's K-Lend transactions",
    "kamino_user_kvault_rewards": "Kamino — a user's K-Vault rewards",
    "kamino_user_metrics_history": "Kamino — historical metrics for a user",
    "kamino_user_staking_boosts": "Kamino — a user's staking boosts",
    "kamino_user_transactions": "Kamino — a user's transaction history",
    "kamino_user_vault_metrics_history": "Kamino — historical vault metrics for a user",
    "kamino_user_vault_pnl": "Kamino — a user's vault PnL",
    "kamino_user_vault_pnl_history": "Kamino — historical vault PnL for a user",
    "kamino_vault_allocation_history": "Kamino — historical allocation for a vault",
    "kamino_vault_metrics_history": "Kamino — historical metrics for a vault",
    "kamino_vault_mint_image": "Kamino — vault mint image asset",
    "kamino_vault_mint_metadata": "Kamino — vault mint metadata",
    "kamino_vault_transactions": "Kamino — a vault's transactions",
    "kamino_vaults_rewards": "Kamino — rewards across vaults",
    "kamino_yield_history": "Kamino — historical yield data",
    "kamino_airdrop_metrics": "Kamino — airdrop metrics",
    "kamino_farm_transactions": "Kamino — farm transaction history",
    # ── Magic Eden (NFT marketplace + MMM AMM; several build tx instructions) ──
    "me_launchpad_collections": "Magic Eden — launchpad collections",
    "me_buy_instruction": "Magic Eden — build a buy instruction for an NFT",
    "me_buy_now_transfer_nft": "Magic Eden — buy-now with NFT transfer",
    "me_buy_now": "Magic Eden — buy-now transaction for a listed NFT",
    "me_buy_cancel": "Magic Eden — cancel a bid/buy order",
    "me_buy_change_price": "Magic Eden — change a bid price",
    "me_sell": "Magic Eden — list an NFT for sale",
    "me_sell_change_price": "Magic Eden — change a listing price",
    "me_sell_now": "Magic Eden — accept the best offer (sell now)",
    "me_sell_cancel": "Magic Eden — cancel a listing",
    "me_deposit": "Magic Eden — deposit into marketplace escrow",
    "me_withdraw": "Magic Eden — withdraw from marketplace escrow",
    "me_collection_attributes": "Magic Eden — attribute/trait breakdown for a collection",
    "me_mmm_pools": "Magic Eden — MMM AMM pools",
    "me_mmm_token_pools": "Magic Eden — MMM pools for a token/collection",
    "me_mmm_create_pool": "Magic Eden — build a create-MMM-pool transaction",
    "me_mmm_update_pool": "Magic Eden — update an MMM pool",
    "me_mmm_sol_deposit_buy": "Magic Eden — MMM SOL deposit (buy side)",
    "me_mmm_sol_withdraw_buy": "Magic Eden — MMM SOL withdraw (buy side)",
    "me_mmm_sol_close_pool": "Magic Eden — close an MMM pool",
    "me_mmm_sol_fulfill_buy": "Magic Eden — fulfill an MMM buy",
    "me_mmm_sol_fulfill_sell": "Magic Eden — fulfill an MMM sell",
    "me_marketplace_popular": "Magic Eden — popular collections on the marketplace",
    # ── Orca (Whirlpool AMM reads) ──
    "orca_get_pools": (
        "Orca — list Whirlpool liquidity pools. Optional category=rwa | "
        "stablecoin | lst | governance | utility | meme narrows the list to "
        "pools holding that kind of asset; pass it whenever the user names a "
        "theme (\"RWA pools\", \"meme pools\"), since those pools sit far "
        "below the top of the unfiltered list"
    ),
    "orca_get_pool": "Orca — details for a specific Whirlpool",
    "orca_search_pools": (
        "Orca — search Whirlpools by token/pair; takes the same optional "
        "category as orca_get_pools"
    ),
    "orca_get_user_positions": "Orca — a wallet's LP positions",
    "orca_get_pool_positions": "Orca — positions in a specific pool",
    "orca_search_tokens": "Orca — search supported tokens",
    "orca_get_token": "Orca — details for a token",
    "orca_get_tokens": "Orca — list supported tokens",
    "orca_get_protocol_stats": "Orca — protocol-wide stats (TVL, volume)",
    "orca_get_orca_token": "Orca — ORCA governance-token info",
    "orca_get_circulating_supply": "Orca — ORCA circulating supply",
    "orca_get_total_supply": "Orca — ORCA total supply",
    "orca_get_locked_liquidity": "Orca — locked-liquidity stats",
    # ── Raydium (AMM/CLMM/farm reads) ──
    "raydium_swap_quote": "Raydium — swap quote for a token pair",
    "raydium_get_pool_info": "Raydium — pool info (reserves, price, fees)",
    "raydium_get_user_positions": "Raydium — a wallet's LP positions",
    "raydium_get_clmm_positions": "Raydium — a wallet's CLMM positions",
    "raydium_get_token_info": "Raydium — token info",
    "raydium_get_platform_stats": "Raydium — platform stats (TVL, volume)",
    "raydium_get_clmm_configs": "Raydium — CLMM config parameters",
    "raydium_get_pools_by_lp": "Raydium — pools by LP mint",
    "raydium_get_pools_v2": "Raydium — list pools (v2 API)",
    "raydium_get_pool_keys": "Raydium — on-chain account keys for a pool",
    "raydium_get_pool_liquidity_history": "Raydium — historical liquidity for a pool",
    "raydium_get_pool_position_history": "Raydium — position history for a pool",
    "raydium_get_token_list": "Raydium — supported token list",
    "raydium_get_token_prices": "Raydium — token prices",
    "raydium_get_farm_info": "Raydium — farm/staking info",
    "raydium_get_farm_by_lp": "Raydium — farm for an LP mint",
    "raydium_get_farm_keys": "Raydium — on-chain keys for a farm",
    "raydium_get_ido_keys": "Raydium — IDO/launchpad account keys",
    "raydium_get_main_version": "Raydium — current program/API version",
    "raydium_get_rpcs": "Raydium — recommended RPC endpoints",
    "raydium_get_chain_time": "Raydium — on-chain time reference",
    "raydium_get_stake_pools": "Raydium — staking pools",
    "raydium_get_migrate_lp": "Raydium — LP migration info",
    "raydium_get_auto_fee": "Raydium — recommended priority fee",
    "raydium_get_cpmm_configs": "Raydium — CPMM config parameters",
    # ── SNS (.sol domains) ──
    "sns_resolve": "SNS — resolve a .sol domain to its wallet address",
    "sns_reverse_lookup": "SNS — reverse-lookup the .sol domain(s) for a wallet",
    "sns_check_available": "SNS — check whether a .sol domain is available",
    "sns_domains": "SNS — all domains owned by a wallet",
    "sns_primary_domain": "SNS — a wallet's primary/favourite domain",
    "sns_record": "SNS — a specific record on a domain (twitter, url, etc.)",
    "sns_domain_info": "SNS — registration details for a domain",
    "sns_list": "SNS — list/browse domains",
    "sns_subdomains": "SNS — subdomains under a domain",
    # ── Misc ──
    "simulate": "Simulate a transaction to preview balance changes & errors before signing",
}


def describe(name: str) -> str:
    """One-liner for a tool: hand map → python docstring → humanized name."""
    d = TOOL_DESCRIPTIONS.get(name)
    if d:
        return d
    try:
        from app.clients import market_data as _md
        ent = _md._DISPATCH.get(name)
        if ent and ent[0].__doc__:
            return ent[0].__doc__.strip().splitlines()[0].strip()
    except Exception:
        pass
    return name.replace("_", " ")


def build_query_reference(query_types: list[str]) -> str:
    """Render a compact `- name: description` list for the (already scoped) query
    types, to embed in the query_onchain schema so the model sees what each does."""
    return "\n".join(f"- {t}: {describe(t)}" for t in sorted(query_types))


_PROMPT_GLOB = os.path.join(os.path.dirname(__file__), "..", "prompts", "*.txt")


def audit_coverage(all_query_values: list[str]) -> list[str]:
    """Return query tools with NO real description anywhere (map, docstring, or
    prompt) — true orphans. Logs a warning so the gap is visible at startup."""
    try:
        prompt_txt = "".join(open(f, encoding="utf-8").read() for f in glob.glob(_PROMPT_GLOB))
    except Exception:
        prompt_txt = ""
    try:
        from app.clients import market_data as _md
        dispatch = _md._DISPATCH
    except Exception:
        dispatch = {}
    orphans = []
    for name in all_query_values:
        if name in TOOL_DESCRIPTIONS:
            continue
        ent = dispatch.get(name)
        if ent and ent[0].__doc__:
            continue
        if name in prompt_txt:
            continue
        orphans.append(name)
    if orphans:
        logger.warning(
            "tool_descriptions: %d query tools have no description (map/docstring/prompt): %s",
            len(orphans), ", ".join(sorted(orphans)[:20]) + (" …" if len(orphans) > 20 else ""),
        )
    return orphans

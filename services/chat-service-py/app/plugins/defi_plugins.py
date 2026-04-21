"""
DeFi Protocol Plugins

Plugins for Solana DeFi protocols:
- Meteora (DLMM + Dynamic Pools + Farms)
- Marginfi (lending / borrowing / leverage)
- Orca (Whirlpools CLMM)
- Kamino Finance
- Jito Finance
- Raydium
- Marinade
- Marginfi

All action execution proxies through the gateway /actions/build endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.plugins.base import (
    BasePlugin,
    PluginAction,
    PluginContext,
    PluginResult,
)

logger = logging.getLogger(__name__)


def _coerce_param(v: Any) -> Any:
    """Preserve numeric types; coerce everything else to string.
    Rust structs expect numbers for f64/u32 fields (e.g. leverage, percent, slippageBps)
    and strings for String fields (e.g. amount, token, position).
    """
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return v
    return str(v)


async def _build_action(action_type: str, params: dict[str, Any]) -> PluginResult:
    """Send an action build request to the gateway and return the result."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.GATEWAY_URL}/actions/build",
                json={"type": action_type, "params": {k: _coerce_param(v) for k, v in params.items() if v is not None}},
                headers={"X-Internal-Api-Key": settings.OPRAI_INTERNAL_API_KEY},
            )
            resp.raise_for_status()
            return PluginResult(success=True, data=resp.json())
    except Exception as exc:
        logger.error("Action build failed [%s]: %s", action_type, exc)
        return PluginResult(success=False, error=str(exc))


class BuildableAction(PluginAction):
    """PluginAction that forwards execution to the Rust action builder via self.name."""
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action(self.name, params)


# ============================================================================
# Meteora Plugin — DLMM + Dynamic Pools + Farms
# ============================================================================

class MeteoraSwapAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_swap"
    @property
    def description(self) -> str: return "Swap tokens via Meteora DLMM"
    @property
    def aliases(self) -> list[str]: return ["swap_meteora"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint":   {"type": "string", "required": True},
            "outputMint":  {"type": "string", "required": True},
            "amount":      {"type": "number", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 50},
            "pool":        {"type": "string", "required": False},
        }


class MeteoraOpenPositionAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_open_position"
    @property
    def description(self) -> str: return "Open a DLMM concentrated liquidity position on Meteora"
    @property
    def aliases(self) -> list[str]: return ["meteora_add_clmm"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pool":        {"type": "string", "required": True, "description": "DLMM pool address"},
            "amountX":     {"type": "number", "required": True, "description": "Token X amount"},
            "amountY":     {"type": "number", "required": True, "description": "Token Y amount"},
            "minPrice":    {"type": "number", "required": False, "description": "Lower bound price (tokenY per tokenX)"},
            "maxPrice":    {"type": "number", "required": False, "description": "Upper bound price"},
            "minBinId":    {"type": "number", "required": False, "description": "Direct: lower bin ID (alternative to minPrice)"},
            "maxBinId":    {"type": "number", "required": False, "description": "Direct: upper bin ID (alternative to maxPrice)"},
            "strategy":    {"type": "string", "required": False, "description": "Distribution: uniform (default) or spot (active-bin-heavy)"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class MeteoraClosePositionAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_close_position"
    @property
    def description(self) -> str: return "Close a Meteora DLMM position and withdraw all liquidity"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":    {"type": "string", "required": True, "description": "Position address"},
            "slippageBps": {"type": "number", "required": False, "default": 50},
        }


class MeteoraAddToPositionAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_add_to_position"
    @property
    def description(self) -> str: return "Add more liquidity to an existing Meteora DLMM position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":    {"type": "string", "required": True},
            "amountX":     {"type": "number", "required": True},
            "amountY":     {"type": "number", "required": True},
            "strategy":    {"type": "string", "required": False, "description": "Distribution: uniform (default) or spot"},
            "slippageBps": {"type": "number", "required": False, "default": 50},
        }


class MeteoraAddLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_add_liquidity"
    @property
    def description(self) -> str: return "Add liquidity to a Meteora DLMM pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pool":        {"type": "string", "required": True, "description": "DLMM pool address"},
            "amountX":     {"type": "number", "required": True, "description": "Token X amount"},
            "amountY":     {"type": "number", "required": True, "description": "Token Y amount"},
            "minBinId":    {"type": "number", "required": False, "description": "Lower bin ID (alternative to minPrice)"},
            "maxBinId":    {"type": "number", "required": False, "description": "Upper bin ID (alternative to maxPrice)"},
            "minPrice":    {"type": "number", "required": False, "description": "Lower price bound in tokenY per tokenX"},
            "maxPrice":    {"type": "number", "required": False, "description": "Upper price bound in tokenY per tokenX"},
            "strategy":    {"type": "string", "required": False, "description": "Distribution: uniform (default) or spot (active-bin-heavy)"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class MeteoraRemoveLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_remove_liquidity"
    @property
    def description(self) -> str: return "Remove liquidity from a Meteora pool position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":    {"type": "string", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 50},
        }


class MeteoraCreatePoolAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_create_pool"
    @property
    def description(self) -> str: return "Create a new Meteora DLMM pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenXMint":   {"type": "string", "required": True, "description": "Token X symbol or mint address"},
            "tokenYMint":   {"type": "string", "required": True, "description": "Token Y symbol or mint address"},
            "binStep":      {"type": "number", "required": True, "description": "Bin step in bps (e.g. 25)"},
            "initialPrice": {"type": "number", "required": True, "description": "Initial price (tokenY per tokenX)"},
            "amountX":      {"type": "number", "required": True, "description": "Initial tokenX liquidity"},
            "amountY":      {"type": "number", "required": True, "description": "Initial tokenY liquidity"},
            "baseFee":      {"type": "number", "required": False, "default": 0.01, "description": "Base fee % (e.g. 0.01 = 1%)"},
        }


class MeteoraClaimFeesAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_claim_fees"
    @property
    def description(self) -> str: return "Claim accumulated trading fees from a Meteora position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"position": {"type": "string", "required": True}}


class MeteoraClaimRewardsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_claim_rewards"
    @property
    def description(self) -> str: return "Claim reward tokens from a Meteora DLMM position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":   {"type": "string", "required": True},
            "rewardIndex": {"type": "number", "required": False, "default": 0},
        }


class MeteoraStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_stake"
    @property
    def description(self) -> str: return "Stake LP tokens in a Meteora farm"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "farm":   {"type": "string", "required": True, "description": "Farm address"},
            "amount": {"type": "number", "required": True, "description": "LP token amount"},
        }


class MeteoraUnstakeAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_unstake"
    @property
    def description(self) -> str: return "Unstake LP tokens from a Meteora farm"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "farm":   {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
        }


class MeteoraHarvestAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_harvest"
    @property
    def description(self) -> str: return "Harvest pending rewards from a Meteora farm"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"farm": {"type": "string", "required": True}}


# ── DLMM GET queries ─────────────────────────────────────────────────────────

class MeteoraDlmmGetPairsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dlmm_get_pairs"
    @property
    def description(self) -> str: return "List Meteora DLMM pairs with optional filtering and sorting"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "searchTerm": {"type": "string", "required": False, "description": "Token name/symbol filter"},
            "sortKey":    {"type": "string", "required": False, "description": "Sort field (e.g. 'volume', 'fees', 'liquidity')"},
            "orderBy":    {"type": "string", "required": False, "description": "'asc' or 'desc'"},
            "offset":     {"type": "number", "required": False},
            "limit":      {"type": "number", "required": False, "default": 25},
        }


class MeteoraDlmmGetPairAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dlmm_get_pair"
    @property
    def description(self) -> str: return "Get details of a specific Meteora DLMM pair by address"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"address": {"type": "string", "required": True, "description": "DLMM pair address"}}


class MeteoraDlmmGetUserPositionsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dlmm_get_user_positions"
    @property
    def description(self) -> str: return "Get all Meteora DLMM positions for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class MeteoraDlmmGetActiveBinAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dlmm_get_active_bin"
    @property
    def description(self) -> str: return "Get the current active bin and price for a Meteora DLMM pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"address": {"type": "string", "required": True, "description": "DLMM pool address"}}


# ── DAMM v2 GET queries ───────────────────────────────────────────────────────

class MeteoraDammV2GetPoolsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_get_pools"
    @property
    def description(self) -> str: return "List Meteora DAMM v2 dynamic AMM pools"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "page":     {"type": "number", "required": False, "default": 1},
            "pageSize": {"type": "number", "required": False, "default": 20},
            "query":    {"type": "string", "required": False, "description": "Token name/mint filter"},
            "sortBy":   {"type": "string", "required": False},
            "filterBy": {"type": "string", "required": False},
        }


class MeteoraDammV2GetPoolGroupsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_get_pool_groups"
    @property
    def description(self) -> str: return "List Meteora DAMM v2 pool groups (token pairs across fee tiers)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "page":     {"type": "number", "required": False, "default": 1},
            "pageSize": {"type": "number", "required": False, "default": 20},
        }


class MeteoraDammV2GetPoolGroupAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_get_pool_group"
    @property
    def description(self) -> str: return "Get Meteora DAMM v2 pool group by token pair (lexical order)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"lexicalOrderMints": {"type": "string", "required": True, "description": "Token mints in lexical order, e.g. 'mintA-mintB'"}}


class MeteoraDammV2GetPoolAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_get_pool"
    @property
    def description(self) -> str: return "Get details of a specific Meteora DAMM v2 pool by address"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"address": {"type": "string", "required": True, "description": "DAMM v2 pool address"}}


class MeteoraDammV2GetPoolOhlcvAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_get_pool_ohlcv"
    @property
    def description(self) -> str: return "Get OHLCV candlestick data for a Meteora DAMM v2 pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "address":    {"type": "string", "required": True},
            "resolution": {"type": "string", "required": False, "description": "Candle resolution (e.g. '1h', '1d')"},
            "fromTime":   {"type": "number", "required": False, "description": "Unix timestamp start"},
            "toTime":     {"type": "number", "required": False, "description": "Unix timestamp end"},
        }


class MeteoraDammV2GetPoolVolumeHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_get_pool_volume_history"
    @property
    def description(self) -> str: return "Get volume history for a Meteora DAMM v2 pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "address":    {"type": "string", "required": True},
            "resolution": {"type": "string", "required": False},
            "fromTime":   {"type": "number", "required": False},
            "toTime":     {"type": "number", "required": False},
        }


class MeteoraDammV2GetProtocolMetricsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_get_protocol_metrics"
    @property
    def description(self) -> str: return "Get global Meteora DAMM v2 protocol metrics (TVL, volume, fees)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


# ── DAMM v2 TX ───────────────────────────────────────────────────────────────

class MeteoraDammV2SwapAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_swap"
    @property
    def description(self) -> str: return "Swap tokens via Meteora DAMM v2 (routed through Jupiter)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint":   {"type": "string", "required": True},
            "outputMint":  {"type": "string", "required": True},
            "amount":      {"type": "number", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 50},
            "pool":        {"type": "string", "required": False, "description": "Specific DAMM v2 pool address"},
        }


class MeteoraDammV2AddLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_add_liquidity"
    @property
    def description(self) -> str: return "Add liquidity to a Meteora DAMM v2 pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pool":        {"type": "string", "required": True, "description": "DAMM v2 pool address"},
            "maxAmountA":  {"type": "number", "required": True, "description": "Max token A amount"},
            "maxAmountB":  {"type": "number", "required": True, "description": "Max token B amount"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class MeteoraDammV2RemoveLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv2_remove_liquidity"
    @property
    def description(self) -> str: return "Remove liquidity from a Meteora DAMM v2 position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pool":        {"type": "string", "required": True},
            "lpAmount":    {"type": "number", "required": True, "description": "LP token amount to remove"},
            "positionNft": {"type": "string", "required": True, "description": "Position NFT mint address"},
            "minAmountA":  {"type": "number", "required": False},
            "minAmountB":  {"type": "number", "required": False},
        }


# ── DAMM v1 GET queries ───────────────────────────────────────────────────────

class MeteoraDammV1GetPoolsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_get_pools"
    @property
    def description(self) -> str: return "List Meteora DAMM v1 (Dynamic AMM) pools"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "page":             {"type": "number", "required": False, "default": 1},
            "pageSize":         {"type": "number", "required": False, "default": 20},
            "includeTokenInfo": {"type": "string", "required": False, "description": "true/false"},
        }


class MeteoraDammV1GetPoolConfigsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_get_pool_configs"
    @property
    def description(self) -> str: return "Get all Meteora DAMM v1 pool configuration presets"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class MeteoraDammV1SearchPoolsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_search_pools"
    @property
    def description(self) -> str: return "Search Meteora DAMM v1 pools by token name or mint"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"q": {"type": "string", "required": True, "description": "Search query (token name or mint address)"}}


class MeteoraDammV1GetFarmsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_get_farms"
    @property
    def description(self) -> str: return "Get Meteora DAMM v1 farms, optionally filtered by pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"poolAddress": {"type": "string", "required": False, "description": "Filter by pool address"}}


class MeteoraDammV1GetPoolsMetricsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_get_pools_metrics"
    @property
    def description(self) -> str: return "Get metrics (TVL, APY, volume) for specific Meteora DAMM v1 pools"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"poolAddresses": {"type": "string", "required": False, "description": "Comma-separated pool addresses"}}


class MeteoraDammV1GetAlphaVaultsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_get_alpha_vaults"
    @property
    def description(self) -> str: return "List all Meteora Alpha Vaults (fair launch / pre-sale vaults)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class MeteoraDammV1GetAlphaVaultConfigsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_get_alpha_vault_configs"
    @property
    def description(self) -> str: return "Get Meteora Alpha Vault configuration presets"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class MeteoraDammV1GetPoolsByVaultLpAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_get_pools_by_vault_lp"
    @property
    def description(self) -> str: return "Get Meteora DAMM v1 pools associated with a vault LP token"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"aVaultLp": {"type": "string", "required": True, "description": "A-vault LP mint address"}}


class MeteoraDammV1GetFeeConfigAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_get_fee_config"
    @property
    def description(self) -> str: return "Get Meteora DAMM v1 fee configuration by config address"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"configAddress": {"type": "string", "required": True}}


# ── DAMM v1 TX ───────────────────────────────────────────────────────────────

class MeteoraDammV1SwapAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_swap"
    @property
    def description(self) -> str: return "Swap tokens via Meteora DAMM v1 Dynamic AMM (routed through Jupiter)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint":   {"type": "string", "required": True},
            "outputMint":  {"type": "string", "required": True},
            "amount":      {"type": "number", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 50},
            "pool":        {"type": "string", "required": False, "description": "Specific DAMM v1 pool address"},
        }


class MeteoraDammV1DepositAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_deposit"
    @property
    def description(self) -> str: return "Deposit liquidity into a Meteora DAMM v1 pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pool":        {"type": "string", "required": True, "description": "DAMM v1 pool address"},
            "tokenAAmount": {"type": "number", "required": True, "description": "Token A amount"},
            "tokenBAmount": {"type": "number", "required": True, "description": "Token B amount"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class MeteoraDammV1WithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_dammv1_withdraw"
    @property
    def description(self) -> str: return "Withdraw liquidity from a Meteora DAMM v1 pool by burning LP tokens"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pool":       {"type": "string", "required": True},
            "lpAmount":   {"type": "number", "required": True, "description": "LP token amount to burn"},
            "minAAmount": {"type": "number", "required": False, "description": "Min token A out (slippage guard)"},
            "minBAmount": {"type": "number", "required": False, "description": "Min token B out (slippage guard)"},
        }


# ── Dynamic Vault GET queries ─────────────────────────────────────────────────

class MeteoraVaultGetInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_vault_get_info"
    @property
    def description(self) -> str: return "Get all Meteora Dynamic Vault info (TVL, strategies)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class MeteoraVaultGetAddressesAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_vault_get_addresses"
    @property
    def description(self) -> str: return "Get all Meteora Dynamic Vault on-chain addresses"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class MeteoraVaultGetStateAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_vault_get_state"
    @property
    def description(self) -> str: return "Get current on-chain state of a Meteora Dynamic Vault by token mint"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"tokenMint": {"type": "string", "required": True, "description": "Token mint address (e.g. USDC mint)"}}


class MeteoraVaultGetApyAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_vault_get_apy"
    @property
    def description(self) -> str: return "Get current APY for a Meteora Dynamic Vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"tokenMint": {"type": "string", "required": True}}


class MeteoraVaultGetApyHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_vault_get_apy_history"
    @property
    def description(self) -> str: return "Get historical APY data for a Meteora Dynamic Vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenMint":       {"type": "string", "required": True},
            "startTimestamp":  {"type": "number", "required": True, "description": "Unix timestamp start"},
            "endTimestamp":    {"type": "number", "required": True, "description": "Unix timestamp end"},
        }


class MeteoraVaultGetVirtualPriceAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_vault_get_virtual_price"
    @property
    def description(self) -> str: return "Get virtual price of an LP share in a Meteora Dynamic Vault strategy"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenMint": {"type": "string", "required": True},
            "strategy":  {"type": "string", "required": True, "description": "Strategy address"},
        }


# ── Dynamic Vault TX ─────────────────────────────────────────────────────────

class MeteoraVaultDepositAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_vault_deposit"
    @property
    def description(self) -> str: return "Deposit tokens into a Meteora Dynamic Vault to earn yield"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenMint":   {"type": "string", "required": True, "description": "Token mint to deposit (e.g. USDC)"},
            "amount":      {"type": "number", "required": True},
            "affiliateId": {"type": "string", "required": False},
        }


class MeteoraVaultWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_vault_withdraw"
    @property
    def description(self) -> str: return "Withdraw from a Meteora Dynamic Vault by burning LP tokens"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenMint":    {"type": "string", "required": True},
            "unmintAmount": {"type": "number", "required": True, "description": "LP token amount to burn"},
            "affiliateId":  {"type": "string", "required": False},
        }


# ── Stake2Earn (m3m3) GET queries ─────────────────────────────────────────────

class MeteoraS2EGetAnalyticsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_get_analytics"
    @property
    def description(self) -> str: return "Get global Meteora Stake2Earn (m3m3) protocol analytics"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class MeteoraS2EGetAllVaultsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_get_all_vaults"
    @property
    def description(self) -> str: return "List all Meteora Stake2Earn vaults"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class MeteoraS2EFilterVaultsAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_filter_vaults"
    @property
    def description(self) -> str: return "Search and filter Meteora Stake2Earn vaults"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "searchTerm": {"type": "string", "required": False},
            "sortKey":    {"type": "string", "required": False},
            "orderBy":    {"type": "string", "required": False, "description": "'asc' or 'desc'"},
            "offset":     {"type": "number", "required": False},
            "limit":      {"type": "number", "required": False, "default": 20},
        }


class MeteoraS2EGetVaultAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_get_vault"
    @property
    def description(self) -> str: return "Get details of a specific Meteora Stake2Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"vaultAddress": {"type": "string", "required": True}}


# ── Stake2Earn (m3m3) TX ─────────────────────────────────────────────────────

class MeteoraS2EStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_stake"
    @property
    def description(self) -> str: return "Stake tokens in a Meteora Stake2Earn (m3m3) vault to earn fees"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault":  {"type": "string", "required": True, "description": "Stake2Earn vault address"},
            "amount": {"type": "number", "required": True},
        }


class MeteoraS2EUnstakeAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_unstake"
    @property
    def description(self) -> str: return "Unstake tokens from a Meteora Stake2Earn vault (starts unlock period)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault":  {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
        }


class MeteoraS2EClaimFeeAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_claim_fee"
    @property
    def description(self) -> str: return "Claim accumulated fee rewards from a Meteora Stake2Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault":     {"type": "string", "required": True},
            "maxAmount": {"type": "number", "required": False, "description": "Cap claim amount (default: full balance)"},
        }


class MeteoraS2ECancelUnstakeAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_cancel_unstake"
    @property
    def description(self) -> str: return "Cancel a pending unstake from a Meteora Stake2Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault":  {"type": "string", "required": True},
            "escrow": {"type": "string", "required": True, "description": "Escrow account address from the unstake"},
        }


class MeteoraS2EWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "meteora_s2e_withdraw"
    @property
    def description(self) -> str: return "Withdraw unlocked tokens from a Meteora Stake2Earn escrow"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault":  {"type": "string", "required": True},
            "escrow": {"type": "string", "required": True, "description": "Escrow account address after lock period"},
        }


class MeteoraPlugin(BasePlugin):
    @property
    def id(self) -> str: return "meteora"
    @property
    def name(self) -> str: return "Meteora"
    @property
    def description(self) -> str: return "Meteora DLMM, DAMM v1/v2, Dynamic Vault, Stake2Earn — full integration"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            # DLMM TX
            MeteoraSwapAction(),
            MeteoraOpenPositionAction(),
            MeteoraClosePositionAction(),
            MeteoraAddToPositionAction(),
            MeteoraAddLiquidityAction(),
            MeteoraRemoveLiquidityAction(),
            MeteoraCreatePoolAction(),
            MeteoraClaimFeesAction(),
            MeteoraClaimRewardsAction(),
            MeteoraStakeAction(),
            MeteoraUnstakeAction(),
            MeteoraHarvestAction(),
            # DLMM GET
            MeteoraDlmmGetPairsAction(),
            MeteoraDlmmGetPairAction(),
            MeteoraDlmmGetUserPositionsAction(),
            MeteoraDlmmGetActiveBinAction(),
            # DAMM v2 GET
            MeteoraDammV2GetPoolsAction(),
            MeteoraDammV2GetPoolGroupsAction(),
            MeteoraDammV2GetPoolGroupAction(),
            MeteoraDammV2GetPoolAction(),
            MeteoraDammV2GetPoolOhlcvAction(),
            MeteoraDammV2GetPoolVolumeHistoryAction(),
            MeteoraDammV2GetProtocolMetricsAction(),
            # DAMM v2 TX
            MeteoraDammV2SwapAction(),
            MeteoraDammV2AddLiquidityAction(),
            MeteoraDammV2RemoveLiquidityAction(),
            # DAMM v1 GET
            MeteoraDammV1GetPoolsAction(),
            MeteoraDammV1GetPoolConfigsAction(),
            MeteoraDammV1SearchPoolsAction(),
            MeteoraDammV1GetFarmsAction(),
            MeteoraDammV1GetPoolsMetricsAction(),
            MeteoraDammV1GetAlphaVaultsAction(),
            MeteoraDammV1GetAlphaVaultConfigsAction(),
            MeteoraDammV1GetPoolsByVaultLpAction(),
            MeteoraDammV1GetFeeConfigAction(),
            # DAMM v1 TX
            MeteoraDammV1SwapAction(),
            MeteoraDammV1DepositAction(),
            MeteoraDammV1WithdrawAction(),
            # Dynamic Vault GET
            MeteoraVaultGetInfoAction(),
            MeteoraVaultGetAddressesAction(),
            MeteoraVaultGetStateAction(),
            MeteoraVaultGetApyAction(),
            MeteoraVaultGetApyHistoryAction(),
            MeteoraVaultGetVirtualPriceAction(),
            # Dynamic Vault TX
            MeteoraVaultDepositAction(),
            MeteoraVaultWithdrawAction(),
            # Stake2Earn GET
            MeteoraS2EGetAnalyticsAction(),
            MeteoraS2EGetAllVaultsAction(),
            MeteoraS2EFilterVaultsAction(),
            MeteoraS2EGetVaultAction(),
            # Stake2Earn TX
            MeteoraS2EStakeAction(),
            MeteoraS2EUnstakeAction(),
            MeteoraS2EClaimFeeAction(),
            MeteoraS2ECancelUnstakeAction(),
            MeteoraS2EWithdrawAction(),
        ]


# ============================================================================
# Marginfi Plugin — Lending / Borrowing / Leverage (Complete v2 Integration)
# ============================================================================
# All 28 action types wired to the Rust solana-service via /actions/build.
#
# Account Management (4):
#   marginfi_create_account, marginfi_create_account_pda,
#   marginfi_close_account,  marginfi_close_balance,
#   marginfi_transfer_account
#
# Lending (4):
#   marginfi_deposit, marginfi_withdraw, marginfi_borrow, marginfi_repay
#
# Liquidation (3):
#   marginfi_liquidate, marginfi_start_liquidation, marginfi_end_liquidation
#
# Flash Loans (2):
#   marginfi_flashloan_start, marginfi_flashloan_end
#
# Borrow Orders (4):
#   marginfi_place_order, marginfi_close_order,
#   marginfi_execute_order_start, marginfi_execute_order_end
#
# Emissions / Rewards (1):
#   marginfi_claim_emissions
#
# Permissionless (3):
#   marginfi_accrue_interest, marginfi_pulse_price, marginfi_pulse_health
#
# Queries (7):
#   marginfi_account_info, marginfi_banks, marginfi_health,
#   marginfi_points,       marginfi_bank_detail, marginfi_user_accounts
# ============================================================================

_MFI_ACCOUNT_PARAM = {
    "type": "string",
    "required": False,
    "description": "marginfi account address. Auto-resolved from wallet if omitted.",
}
_MFI_BANK_PARAM = {
    "type": "string",
    "required": True,
    "description": "Token symbol (e.g. SOL, USDC) or bank/mint address.",
}
_MFI_AMOUNT_PARAM: dict[str, Any] = {
    "type": "number",
    "required": True,
    "description": "Human-readable amount (e.g. 1.5).",
}


# ── Account Management ────────────────────────────────────────────────────────

class MarginfiCreateAccountAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_create_account"
    @property
    def description(self) -> str: return "Create a new marginfi lending account (non-PDA, requires extra signer)"
    @property
    def aliases(self) -> list[str]: return ["open_marginfi_account"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "referralCode": {
                "type": "string",
                "required": False,
                "description": "Optional referral code.",
            },
        }


class MarginfiCreateAccountPdaAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_create_account_pda"
    @property
    def description(self) -> str: return "Create a deterministic PDA marginfi account (recommended; no extra signer needed)"
    @property
    def aliases(self) -> list[str]: return ["open_marginfi_account_pda"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "accountIndex": {
                "type": "number",
                "required": False,
                "description": "Account index (0-based). Defaults to 0. Increment to create a second account.",
            },
            "thirdPartyId": {
                "type": "number",
                "required": False,
                "description": "Optional third-party identifier for referral tracking.",
            },
        }


class MarginfiCloseAccountAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_close_account"
    @property
    def description(self) -> str: return "Close a marginfi lending account and reclaim rent (account must have zero deposits and borrows)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"account": _MFI_ACCOUNT_PARAM}


class MarginfiCloseBalanceAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_close_balance"
    @property
    def description(self) -> str: return "Close a single zero-balance lending entry for a specific bank in marginfi"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank":    _MFI_BANK_PARAM,
            "account": _MFI_ACCOUNT_PARAM,
        }


class MarginfiTransferAccountAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_transfer_account"
    @property
    def description(self) -> str: return "Transfer all positions from one marginfi account to another"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "sourceAccount": {
                "type": "string",
                "required": True,
                "description": "Source marginfi account address.",
            },
            "destinationAccount": {
                "type": "string",
                "required": True,
                "description": "Destination marginfi account address.",
            },
        }


# ── Lending Operations ────────────────────────────────────────────────────────

class MarginfiDepositAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_deposit"
    @property
    def description(self) -> str: return "Deposit tokens into a marginfi bank to earn yield (all deposits are collateral in v2)"
    @property
    def aliases(self) -> list[str]: return ["marginfi_lend", "marginfi_deposit_collateral"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank":             _MFI_BANK_PARAM,
            "amount":           _MFI_AMOUNT_PARAM,
            "account":          _MFI_ACCOUNT_PARAM,
            "depositUpToLimit": {
                "type": "boolean",
                "required": False,
                "description": "If true, deposit up to the bank's deposit cap (ignores exact amount).",
            },
        }


class MarginfiWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_withdraw"
    @property
    def description(self) -> str: return "Withdraw deposited tokens from a marginfi bank"
    @property
    def aliases(self) -> list[str]: return ["marginfi_withdraw_collateral"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank":        _MFI_BANK_PARAM,
            "amount":      _MFI_AMOUNT_PARAM,
            "account":     _MFI_ACCOUNT_PARAM,
            "withdrawAll": {
                "type": "boolean",
                "required": False,
                "description": "If true, withdraw entire balance. Amount is ignored.",
            },
        }


class MarginfiBorrowAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_borrow"
    @property
    def description(self) -> str: return "Borrow tokens from a marginfi bank against deposited collateral"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank":    _MFI_BANK_PARAM,
            "amount":  _MFI_AMOUNT_PARAM,
            "account": _MFI_ACCOUNT_PARAM,
        }


class MarginfiRepayAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_repay"
    @property
    def description(self) -> str: return "Repay borrowed tokens to a marginfi bank"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank":     _MFI_BANK_PARAM,
            "amount":   _MFI_AMOUNT_PARAM,
            "account":  _MFI_ACCOUNT_PARAM,
            "repayAll": {
                "type": "boolean",
                "required": False,
                "description": "If true, repay entire outstanding debt. Amount is ignored.",
            },
        }


# ── Liquidation ────────────────────────────────────────────────────────────────

class MarginfiLiquidateAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_liquidate"
    @property
    def description(self) -> str: return "Liquidate an unhealthy marginfi account: repay its debt and seize collateral at a discount"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "liquidateeAccount": {
                "type": "string",
                "required": True,
                "description": "The unhealthy marginfi account address to liquidate.",
            },
            "assetBank": {
                "type": "string",
                "required": True,
                "description": "Token symbol or bank address for the collateral to seize.",
            },
            "liabBank": {
                "type": "string",
                "required": True,
                "description": "Token symbol or bank address for the liability to repay.",
            },
            "assetAmount": {
                "type": "number",
                "required": True,
                "description": "Amount of the liability token to repay.",
            },
            "account": _MFI_ACCOUNT_PARAM,
        }


class MarginfiStartLiquidationAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_start_liquidation"
    @property
    def description(self) -> str: return "Start receivership / administrative liquidation for a marginfi account"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "account": {
                "type": "string",
                "required": True,
                "description": "marginfi account address to place into receivership.",
            },
            "liquidationReceiver": {
                "type": "string",
                "required": True,
                "description": "Wallet address that will receive seized collateral.",
            },
        }


class MarginfiEndLiquidationAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_end_liquidation"
    @property
    def description(self) -> str: return "End receivership / administrative liquidation for a marginfi account"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "account": {
                "type": "string",
                "required": True,
                "description": "marginfi account address to remove from receivership.",
            },
        }


# ── Flash Loans ────────────────────────────────────────────────────────────────

class MarginfiFlashloanStartAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_flashloan_start"
    @property
    def description(self) -> str: return "Begin a marginfi flash loan (must be paired with marginfi_flashloan_end in the same transaction)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "account": _MFI_ACCOUNT_PARAM,
            "endIndex": {
                "type": "number",
                "required": True,
                "description": "Transaction instruction index of the paired marginfi_flashloan_end instruction. The on-chain program uses this to validate the flash loan is properly closed.",
            },
        }


class MarginfiFlashloanEndAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_flashloan_end"
    @property
    def description(self) -> str: return "End a marginfi flash loan and validate repayment (must follow marginfi_flashloan_start in the same transaction)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"account": _MFI_ACCOUNT_PARAM}


# ── Borrow Orders ──────────────────────────────────────────────────────────────

class MarginfiPlaceOrderAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_place_order"
    @property
    def description(self) -> str: return "Place a borrow order on marginfi (automated borrow at a rate limit)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "limit": {
                "type": "number",
                "required": True,
                "description": "Maximum interest rate (bps) to accept for the borrow.",
            },
            "banks": {
                "type": "array",
                "items": {"type": "string"},
                "required": True,
                "description": "List of bank addresses to attempt borrowing from (in order of preference).",
            },
            "maxDebtCoverage": {
                "type": "number",
                "required": True,
                "description": "Maximum total debt (in token units) allowed after execution.",
            },
            "orderSide": {
                "type": "number",
                "required": True,
                "description": "Order side: 0 = borrow, 1 = repay.",
            },
            "account": _MFI_ACCOUNT_PARAM,
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        # Coerce comma-separated banks string → list (LLM emits "addr1,addr2")
        if isinstance(params.get("banks"), str):
            params["banks"] = [b.strip() for b in params["banks"].split(",") if b.strip()]
        return await _build_action("marginfi_place_order", params)


class MarginfiCloseOrderAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_close_order"
    @property
    def description(self) -> str: return "Cancel an open marginfi borrow order"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "account": _MFI_ACCOUNT_PARAM,
            "order": {
                "type": "string",
                "required": True,
                "description": "Order PDA address to cancel.",
            },
            "feeRecipient": {
                "type": "string",
                "required": True,
                "description": "Wallet that receives the rent from the closed order account.",
            },
        }


class MarginfiExecuteOrderStartAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_execute_order_start"
    @property
    def description(self) -> str: return "Start execution of a placed marginfi borrow order (called by a keeper/executor bot)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "account": _MFI_ACCOUNT_PARAM,
            "executor": {
                "type": "string",
                "required": True,
                "description": "Keeper/executor wallet address that is executing the order.",
            },
            "order": {
                "type": "string",
                "required": True,
                "description": "Order PDA address to execute.",
            },
        }


class MarginfiExecuteOrderEndAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_execute_order_end"
    @property
    def description(self) -> str: return "End execution of a marginfi borrow order and finalize the borrow"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "account": _MFI_ACCOUNT_PARAM,
            "executor": {
                "type": "string",
                "required": True,
                "description": "Keeper/executor wallet (must match start_execute_order, must sign).",
            },
            "feeRecipient": {
                "type": "string",
                "required": True,
                "description": "Wallet that receives execution fees.",
            },
            "order": {
                "type": "string",
                "required": True,
                "description": "Order PDA address being executed.",
            },
        }


# ── Emissions / Rewards ────────────────────────────────────────────────────────

class MarginfiClaimEmissionsAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_claim_emissions"
    @property
    def description(self) -> str: return "Claim accumulated token emissions (yield rewards) from a marginfi lending or borrowing position"
    @property
    def aliases(self) -> list[str]: return ["marginfi_claim_rewards", "marginfi_settle_emissions"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank":          _MFI_BANK_PARAM,
            "account":       _MFI_ACCOUNT_PARAM,
            "emissionsMint": {
                "type": "string",
                "required": False,
                "description": "Mint address of the emissions token. Defaults to the bank's own token mint. "
                               "Provide explicitly if the bank emits a different token (e.g. USDC rewards on an SOL bank).",
            },
        }


class MarginfiSettleEmissionsAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_settle_emissions"
    @property
    def description(self) -> str: return "Permissionlessly accrue/settle outstanding emissions on a marginfi position without withdrawing (step before claiming)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank": _MFI_BANK_PARAM,
            "account": _MFI_ACCOUNT_PARAM,
        }


class MarginfiWithdrawEmissionsPermissionlessAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_withdraw_emissions_permissionless"
    @property
    def description(self) -> str: return "Permissionlessly withdraw emissions for any marginfi account (sent to their registered destination)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "account": {
                "type": "string",
                "required": True,
                "description": "Target marginfi account address to withdraw emissions for.",
            },
            "bank": _MFI_BANK_PARAM,
            "emissionsMint": {
                "type": "string",
                "required": False,
                "description": "Emissions mint address. Defaults to the bank's own token.",
            },
        }


class MarginfiUpdateEmissionsDestinationAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_update_emissions_destination"
    @property
    def description(self) -> str: return "Update the wallet address that receives off-chain emission distributions for a marginfi account"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "destination": {
                "type": "string",
                "required": True,
                "description": "New destination wallet address for emissions.",
            },
            "account": _MFI_ACCOUNT_PARAM,
        }


class MarginfiClearEmissionsAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_clear_emissions"
    @property
    def description(self) -> str: return "Clear stale emission balances from a marginfi position after the bank has disabled its rewards (permissionless)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank": _MFI_BANK_PARAM,
            "account": _MFI_ACCOUNT_PARAM,
        }


class MarginfiSetKeeperFlagsAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_set_keeper_flags"
    @property
    def description(self) -> str: return "Set keeper-close flags on a marginfi account so keepers can automatically close orders"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "banks": {
                "type": "array",
                "required": False,
                "description": "Optional list of bank addresses or token symbols. If omitted, clears flags on all balances.",
            },
            "account": _MFI_ACCOUNT_PARAM,
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        # Coerce comma-separated banks string → list
        if isinstance(params.get("banks"), str):
            params["banks"] = [b.strip() for b in params["banks"].split(",") if b.strip()]
        return await _build_action("marginfi_set_keeper_flags", params)


class MarginfiInitLiqRecordAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_init_liq_record"
    @property
    def description(self) -> str: return "Initialize the liquidation record PDA for a marginfi account (one-time setup required before start_liquidation)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"account": _MFI_ACCOUNT_PARAM}


# ── Permissionless Actions ─────────────────────────────────────────────────────

class MarginfiAccrueInterestAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_accrue_interest"
    @property
    def description(self) -> str: return "Permissionlessly accrue interest for a marginfi bank (anyone can call; updates on-chain state)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"bank": _MFI_BANK_PARAM}


class MarginfiPulsePriceAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_pulse_price"
    @property
    def description(self) -> str: return "Permissionlessly update the price oracle cache for a marginfi bank"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bank": _MFI_BANK_PARAM,
            "oracle": {
                "type": "string",
                "required": True,
                "description": "Oracle feed account address for this bank (Pyth or Switchboard feed).",
            },
        }


class MarginfiPulseHealthAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_pulse_health"
    @property
    def description(self) -> str: return "Permissionlessly refresh the health state of a marginfi account"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "account": {
                "type": "string",
                "required": True,
                "description": "marginfi account address to refresh.",
            },
        }


# ── Query Actions ──────────────────────────────────────────────────────────────

class MarginfiAccountInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_account_info"
    @property
    def description(self) -> str: return "Get full info for a marginfi lending account: deposits, borrows, health factor"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet":  {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)."},
            "account": {"type": "string", "required": False, "description": "marginfi account address (alternative to wallet)."},
        }


class MarginfiBanksAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_banks"
    @property
    def description(self) -> str: return "List all available marginfi lending banks with symbol, mint, and token program info"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "limit": {
                "type": "number",
                "required": False,
                "description": "Maximum number of banks to return. Returns all if omitted.",
            },
        }


class MarginfiHealthAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_health"
    @property
    def description(self) -> str: return "Get health factor, collateral value, and borrow value for a marginfi account"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet":  {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)."},
            "account": {"type": "string", "required": False, "description": "marginfi account address (alternative to wallet)."},
        }


class MarginfiPointsAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_points"
    @property
    def description(self) -> str: return "Get marginfi points balance, rank, and breakdown (deposit/borrow points) for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {
                "type": "string",
                "required": False,
                "description": "Wallet address. Defaults to connected wallet.",
            },
        }


class MarginfiBankDetailAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_bank_detail"
    @property
    def description(self) -> str: return "Get detailed info for a specific marginfi bank: address, mint, symbol, decimals, token program"
    @property
    def aliases(self) -> list[str]: return ["marginfi_bank_info"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"bank": _MFI_BANK_PARAM}


class MarginfiUserAccountsAction(BuildableAction):
    @property
    def name(self) -> str: return "marginfi_user_accounts"
    @property
    def description(self) -> str: return "List all possible marginfi PDA account addresses for a wallet (by scanning indices)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {
                "type": "string",
                "required": False,
                "description": "Wallet address. Defaults to connected wallet.",
            },
            "maxIndex": {
                "type": "number",
                "required": False,
                "description": "Max account index to scan (0–9). Default: 3.",
            },
        }


class MarginfiPlugin(BasePlugin):
    @property
    def id(self) -> str: return "marginfi"
    @property
    def name(self) -> str: return "marginfi"
    @property
    def description(self) -> str: return "marginfi v2 over-collateralized money market — lending, borrowing, liquidations, flash loans, borrow orders, emissions"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            # Account management
            MarginfiCreateAccountAction(),
            MarginfiCreateAccountPdaAction(),
            MarginfiCloseAccountAction(),
            MarginfiCloseBalanceAction(),
            MarginfiTransferAccountAction(),
            # Core lending
            MarginfiDepositAction(),
            MarginfiWithdrawAction(),
            MarginfiBorrowAction(),
            MarginfiRepayAction(),
            # Liquidation
            MarginfiLiquidateAction(),
            MarginfiStartLiquidationAction(),
            MarginfiEndLiquidationAction(),
            # Flash loans
            MarginfiFlashloanStartAction(),
            MarginfiFlashloanEndAction(),
            # Borrow orders
            MarginfiPlaceOrderAction(),
            MarginfiCloseOrderAction(),
            MarginfiExecuteOrderStartAction(),
            MarginfiExecuteOrderEndAction(),
            # Emissions / rewards
            MarginfiClaimEmissionsAction(),
            MarginfiSettleEmissionsAction(),
            MarginfiWithdrawEmissionsPermissionlessAction(),
            MarginfiUpdateEmissionsDestinationAction(),
            MarginfiClearEmissionsAction(),
            # Liquidation setup
            MarginfiSetKeeperFlagsAction(),
            MarginfiInitLiqRecordAction(),
            # Permissionless
            MarginfiAccrueInterestAction(),
            MarginfiPulsePriceAction(),
            MarginfiPulseHealthAction(),
            # Queries
            MarginfiAccountInfoAction(),
            MarginfiBanksAction(),
            MarginfiHealthAction(),
            MarginfiPointsAction(),
            MarginfiBankDetailAction(),
            MarginfiUserAccountsAction(),
        ]


# ============================================================================
# Orca Plugin
# ============================================================================

class OrcaSwapAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_swap"
    @property
    def description(self) -> str: return "Swap tokens via Orca Whirlpools"
    @property
    def aliases(self) -> list[str]: return ["whirlpool_swap"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint":  {"type": "string", "required": True},
            "outputMint": {"type": "string", "required": True},
            "amount":     {"type": "number", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 50},
            "whirlpool":  {"type": "string", "required": False, "description": "Optional: specific whirlpool address"},
        }


class OrcaLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_add_liquidity"
    @property
    def description(self) -> str: return "Add liquidity to an Orca Whirlpool (AMM-style, full range)"
    @property
    def aliases(self) -> list[str]: return ["add_liquidity_orca"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "whirlpool": {"type": "string", "required": True, "description": "Whirlpool pool address"},
            "amountA":   {"type": "number", "required": True},
            "amountB":   {"type": "number", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class OrcaOpenPositionAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_open_position"
    @property
    def description(self) -> str: return "Open a concentrated liquidity position on Orca Whirlpool"
    @property
    def aliases(self) -> list[str]: return ["orca_create_position"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenA":    {"type": "string", "required": True, "description": "Token A symbol or mint"},
            "tokenB":    {"type": "string", "required": True, "description": "Token B symbol or mint"},
            "amountA":   {"type": "number", "required": True, "description": "Amount of tokenA to deposit"},
            "minPrice":  {"type": "number", "required": True, "description": "Lower bound price (tokenB per tokenA)"},
            "maxPrice":  {"type": "number", "required": True, "description": "Upper bound price (tokenB per tokenA)"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class OrcaClosePositionAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_close_position"
    @property
    def description(self) -> str: return "Close an Orca liquidity position and withdraw all funds"
    @property
    def aliases(self) -> list[str]: return ["close_orca_position"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position": {"type": "string", "required": True, "description": "Position address (NFT mint or PDA)"},
        }


class OrcaRemoveLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_remove_liquidity"
    @property
    def description(self) -> str: return "Remove liquidity from an Orca Whirlpool (AMM-style)"
    @property
    def aliases(self) -> list[str]: return ["remove_liquidity_orca"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "whirlpool": {"type": "string", "required": True, "description": "Whirlpool pool address"},
            "liquidity":  {"type": "number", "required": True, "description": "Raw liquidity units to remove"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class OrcaIncreasePositionAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_increase_position"
    @property
    def description(self) -> str: return "Add more liquidity to an existing Orca CLMM position"
    @property
    def aliases(self) -> list[str]: return ["orca_add_to_position"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":    {"type": "string", "required": True, "description": "Position address"},
            "inputMint":   {"type": "string", "required": True, "description": "Token to deposit"},
            "inputAmount": {"type": "number", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class OrcaDecreasePositionAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_decrease_position"
    @property
    def description(self) -> str: return "Partially remove liquidity from an Orca CLMM position"
    @property
    def aliases(self) -> list[str]: return ["orca_partial_remove"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":   {"type": "string", "required": True, "description": "Position address"},
            "liquidity":  {"type": "number", "required": True, "description": "Raw liquidity units to remove"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class OrcaCollectFeesAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_collect_fees"
    @property
    def description(self) -> str: return "Collect accumulated trading fees from an Orca position"
    @property
    def aliases(self) -> list[str]: return ["orca_claim_fees"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position": {"type": "string", "required": True, "description": "Position address"},
        }


class OrcaCollectRewardsAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_collect_rewards"
    @property
    def description(self) -> str: return "Collect reward tokens from an Orca position"
    @property
    def aliases(self) -> list[str]: return ["orca_claim_rewards"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":    {"type": "string", "required": True, "description": "Position address"},
            "rewardIndex": {"type": "number", "required": False, "default": 0, "description": "Reward index (0, 1, or 2)"},
        }


class OrcaPlugin(BasePlugin):
    @property
    def id(self) -> str: return "orca"
    @property
    def name(self) -> str: return "Orca"
    @property
    def description(self) -> str: return "Orca Whirlpools concentrated liquidity protocol"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            OrcaSwapAction(),
            OrcaLiquidityAction(),
            OrcaOpenPositionAction(),
            OrcaClosePositionAction(),
            OrcaRemoveLiquidityAction(),
            OrcaIncreasePositionAction(),
            OrcaDecreasePositionAction(),
            OrcaCollectFeesAction(),
            OrcaCollectRewardsAction(),
            # Orca — pool creation + data queries
            OrcaCreatePoolAction(),
            OrcaGetPoolsAction(),
            OrcaSearchPoolsAction(),
            OrcaGetPoolAction(),
            OrcaGetLockedLiquidityAction(),
            OrcaGetProtocolStatsAction(),
            OrcaGetOrcaTokenAction(),
            OrcaGetCirculatingSupplyAction(),
            OrcaGetTotalSupplyAction(),
            OrcaGetTokensAction(),
            OrcaSearchTokensAction(),
            OrcaGetTokenAction(),
            OrcaGetUserPositionsAction(),
            OrcaGetPoolPositionsAction(),
        ]


# ── Orca query / pool-creation actions ──────────────────────────────────────

class OrcaCreatePoolAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_create_pool"
    @property
    def description(self) -> str: return "Create a new Orca Whirlpool concentrated liquidity pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenA":       {"type": "string", "required": True, "description": "Token A mint address"},
            "tokenB":       {"type": "string", "required": True, "description": "Token B mint address"},
            "initialPrice": {"type": "number", "required": True, "description": "Initial price in token B per token A"},
            "feeRate":      {"type": "number", "required": False, "description": "Fee rate in bps (e.g. 30 = 0.3%)"},
            "tickSpacing":  {"type": "number", "required": False, "description": "Tick spacing (default depends on fee tier)"},
        }


class OrcaGetPoolsAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_pools"
    @property
    def description(self) -> str: return "List Orca Whirlpool pools with optional sorting"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "sortBy":        {"type": "string", "required": False, "description": "Sort field: tvl, volume, apy"},
            "sortDirection": {"type": "string", "required": False, "description": "asc or desc"},
            "size":          {"type": "number", "required": False, "description": "Max results"},
        }


class OrcaSearchPoolsAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_search_pools"
    @property
    def description(self) -> str: return "Search Orca pools by token name, symbol, or mint"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "q":    {"type": "string", "required": True, "description": "Search query: token symbol, name, or mint"},
            "size": {"type": "number", "required": False},
        }


class OrcaGetPoolAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_pool"
    @property
    def description(self) -> str: return "Get details of a specific Orca Whirlpool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "address": {"type": "string", "required": True, "description": "Whirlpool address"},
            "stats":   {"type": "string", "required": False, "description": "Stats period: 24h, 7d, 30d"},
        }


class OrcaGetLockedLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_locked_liquidity"
    @property
    def description(self) -> str: return "Get locked liquidity positions for an Orca pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"address": {"type": "string", "required": True, "description": "Whirlpool address"}}


class OrcaGetProtocolStatsAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_protocol_stats"
    @property
    def description(self) -> str: return "Get global Orca protocol statistics: total TVL, volume, fees"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class OrcaGetOrcaTokenAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_orca_token"
    @property
    def description(self) -> str: return "Get ORCA token info: price, market cap, supply"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class OrcaGetCirculatingSupplyAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_circulating_supply"
    @property
    def description(self) -> str: return "Get ORCA token circulating supply"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class OrcaGetTotalSupplyAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_total_supply"
    @property
    def description(self) -> str: return "Get ORCA token total supply"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class OrcaGetTokensAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_tokens"
    @property
    def description(self) -> str: return "List tokens available in Orca pools"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "size":     {"type": "number", "required": False},
            "next":     {"type": "string", "required": False, "description": "Pagination cursor"},
            "previous": {"type": "string", "required": False},
        }


class OrcaSearchTokensAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_search_tokens"
    @property
    def description(self) -> str: return "Search tokens available in Orca pools"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"q": {"type": "string", "required": True, "description": "Token name, symbol, or mint"}}


class OrcaGetTokenAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_token"
    @property
    def description(self) -> str: return "Get details of a specific token in Orca pools"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"mintAddress": {"type": "string", "required": True, "description": "Token mint address"}}


class OrcaGetUserPositionsAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_user_positions"
    @property
    def description(self) -> str: return "Get all Orca liquidity positions for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class OrcaGetPoolPositionsAction(BuildableAction):
    @property
    def name(self) -> str: return "orca_get_pool_positions"
    @property
    def description(self) -> str: return "Get all open positions in a specific Orca Whirlpool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"whirlpool": {"type": "string", "required": True, "description": "Whirlpool address"}}


# ============================================================================
# Kamino Plugin
# ============================================================================

class KaminoDepositAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_deposit"
    @property
    def description(self) -> str: return "Deposit into a Kamino K-Lend reserve (earn interest, counts as collateral). First call kamino_market_reserves to get the reserve address for the token."
    @property
    def aliases(self) -> list[str]: return ["kamino_lend"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "KLend reserve account address (not the token mint). Use kamino_market_reserves to look up the reserve address for a token."},
            "amount":  {"type": "string", "required": True, "description": "Amount to deposit in decimal format (e.g. '1.5'), not lamports"},
            "market":  {"type": "string", "required": False, "description": "K-Lend market address (default: main market 7u3HeL2w...)"},
        }


class KaminoWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_withdraw"
    @property
    def description(self) -> str: return "Withdraw deposited assets from a Kamino K-Lend reserve. First call kamino_market_reserves to get the reserve address for the token."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "KLend reserve account address (not the token mint). Use kamino_market_reserves to look up the reserve address."},
            "amount":  {"type": "string", "required": True, "description": "Amount to withdraw in decimal format, not lamports"},
            "market":  {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


class KaminoBorrowAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_borrow"
    @property
    def description(self) -> str: return "Borrow liquidity from a Kamino K-Lend reserve against deposited collateral. First call kamino_market_reserves to get the reserve address for the token to borrow."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "KLend reserve account address for the token to borrow (not the token mint). Use kamino_market_reserves to look up."},
            "amount":  {"type": "string", "required": True, "description": "Amount to borrow in decimal format, not lamports"},
            "market":  {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


class KaminoRepayAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_repay"
    @property
    def description(self) -> str: return "Repay borrowed debt to a Kamino K-Lend reserve. First call kamino_market_reserves to get the reserve address for the token to repay."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "KLend reserve account address for the token to repay (not the token mint). Use kamino_market_reserves to look up."},
            "amount":  {"type": "string", "required": True, "description": "Amount to repay in decimal format, not lamports"},
            "market":  {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


class KaminoAddCollateralAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_add_collateral"
    @property
    def description(self) -> str: return "Add collateral to a Kamino K-Lend position (alias for deposit — all K-Lend deposits are collateral)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "KLend reserve account address for the collateral token"},
            "amount":  {"type": "string", "required": True, "description": "Amount in decimal format, not lamports"},
            "market":  {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


class KaminoWithdrawCollateralAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_withdraw_collateral"
    @property
    def description(self) -> str: return "Withdraw collateral from a Kamino K-Lend position (alias for withdraw)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "KLend reserve account address for the collateral token"},
            "amount":  {"type": "string", "required": True, "description": "Amount in decimal format, not lamports"},
            "market":  {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


class KaminoVaultDepositAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_deposit"
    @property
    def description(self) -> str: return "Deposit to an automated Kamino liquidity vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault":     {"type": "string", "required": True, "description": "Vault name or address"},
            "amount":    {"type": "number", "required": True},
            "token":     {"type": "string", "required": False, "description": "Token to deposit (e.g. SOL, USDC)"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class KaminoVaultWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_withdraw"
    @property
    def description(self) -> str: return "Withdraw from a Kamino liquidity vault by burning kTokens"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault":        {"type": "string", "required": True, "description": "Vault name or address"},
            "ktokenAmount": {"type": "number", "required": True, "description": "kToken/shares amount to redeem"},
            "slippageBps":  {"type": "number", "required": False, "default": 100},
        }


class KaminoMultiplyOpenAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_multiply_open"
    @property
    def description(self) -> str: return "Open a Kamino Multiply leveraged yield position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "strategy":  {"type": "string", "required": True, "description": "Multiply strategy address or name (e.g. SOL-mSOL)"},
            "amount":    {"type": "number", "required": True, "description": "Initial collateral amount"},
            "token":     {"type": "string", "required": True, "description": "Token to deposit (e.g. SOL)"},
            "leverage":  {"type": "number", "required": True, "description": "Target leverage (e.g. 2.5)"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class KaminoMultiplyAddAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_multiply_add"
    @property
    def description(self) -> str: return "Add more collateral to an existing Kamino Multiply position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":  {"type": "string", "required": True},
            "amount":    {"type": "number", "required": True},
            "token":     {"type": "string", "required": True, "description": "Token to add"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class KaminoMultiplyWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_multiply_withdraw"
    @property
    def description(self) -> str: return "Partially withdraw from a Kamino Multiply position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":  {"type": "string", "required": True},
            "percent":   {"type": "number", "required": True, "description": "Percentage to withdraw (0-100 or 0.0-1.0)"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class KaminoMultiplyCloseAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_multiply_close"
    @property
    def description(self) -> str: return "Close a Kamino Multiply leveraged position entirely"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":  {"type": "string", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class KaminoLongOpenAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_long_open"
    @property
    def description(self) -> str: return "Open a leveraged long position on Kamino"
    @property
    def aliases(self) -> list[str]: return ["kamino_long"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "collateralToken":  {"type": "string", "required": True, "description": "Collateral token (e.g. SOL)"},
            "collateralAmount": {"type": "number", "required": True, "description": "Collateral amount"},
            "leverage":         {"type": "number", "required": True},
            "debtToken":        {"type": "string", "required": False, "description": "Token to borrow (default: USDC)"},
            "sizeUsd":          {"type": "number", "required": False, "description": "Position size in USD (auto-computed if omitted)"},
            "slippageBps":      {"type": "number", "required": False, "default": 100},
        }


class KaminoShortOpenAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_short_open"
    @property
    def description(self) -> str: return "Open a leveraged short position on Kamino"
    @property
    def aliases(self) -> list[str]: return ["kamino_short"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "collateralToken":  {"type": "string", "required": True, "description": "Token to short (e.g. SOL)"},
            "collateralAmount": {"type": "number", "required": True, "description": "Collateral amount (typically USDC)"},
            "leverage":         {"type": "number", "required": True},
            "debtToken":        {"type": "string", "required": False, "description": "Token to borrow (default: SOL for shorts)"},
            "sizeUsd":          {"type": "number", "required": False},
            "slippageBps":      {"type": "number", "required": False, "default": 100},
        }


class KaminoPositionCloseAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_position_close"
    @property
    def description(self) -> str: return "Close a Kamino long or short leverage position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "position":  {"type": "string", "required": True, "description": "Position address"},
            "percent":   {"type": "number", "required": False, "description": "Percentage to close 0-100 (default: 100 = close all)"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class KaminoStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_stake"
    @property
    def description(self) -> str: return "Stake KMNO tokens for governance and rewards"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"amount": {"type": "number", "required": True, "description": "KMNO amount to stake"}}


class KaminoUnstakeAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_unstake"
    @property
    def description(self) -> str: return "Unstake KMNO tokens from Kamino governance"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"amount": {"type": "number", "required": True, "description": "KMNO amount to unstake"}}


class KaminoPlugin(BasePlugin):
    @property
    def id(self) -> str: return "kamino"
    @property
    def name(self) -> str: return "Kamino"
    @property
    def description(self) -> str: return "Kamino Finance lending, leverage vaults, and KMNO staking"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            KaminoDepositAction(),
            KaminoWithdrawAction(),
            KaminoBorrowAction(),
            KaminoRepayAction(),
            KaminoAddCollateralAction(),
            KaminoWithdrawCollateralAction(),
            KaminoVaultDepositAction(),
            KaminoVaultWithdrawAction(),
            KaminoMultiplyOpenAction(),
            KaminoMultiplyAddAction(),
            KaminoMultiplyWithdrawAction(),
            KaminoMultiplyCloseAction(),
            KaminoLongOpenAction(),
            KaminoShortOpenAction(),
            KaminoPositionCloseAction(),
            KaminoStakeAction(),
            KaminoUnstakeAction(),
            # Kamino — data queries
            KaminoVaultsAction(),
            KaminoMarketsAction(),
            KaminoMarketReservesAction(),
            KaminoUserVaultPositionsAction(),
            KaminoUserObligationsAction(),
            KaminoOraclePricesAction(),
            KaminoUsdBenchmarkRatesAction(),
            KaminoMarketMetricsHistoryAction(),
            KaminoReserveBorrowApyHistoryAction(),
            KaminoReserveBorrowApyMedianAction(),
            KaminoObligationInterestEarnedAction(),
            KaminoObligationInterestPaidAction(),
            KaminoObligationTransactionsAction(),
            KaminoUserKlendTransactionsAllAction(),
            KaminoUserKlendTransactionsAction(),
            KaminoBorrowOrderFillsAction(),
            KaminoOpenBorrowOrdersAction(),
            KaminoYieldHistoryAction(),
            KaminoPrincipalTokenYieldsAction(),
            KaminoAirdropAllocationsAction(),
            KaminoAirdropMetricsAction(),
            KaminoStakingYieldsAction(),
            KaminoStakingYieldsMedianAction(),
            KaminoStakingYieldsMeanAction(),
            KaminoUserStakingBoostsAction(),
            KaminoSeasonRewardsUserAction(),
            KaminoSeasonRewardsVestingPoolAction(),
            KaminoPrivateCreditMetricsAction(),
            KaminoPrivateCreditMetricsHistoryAction(),
            KaminoUserFarmTransactionsAction(),
            KaminoFarmTransactionsAction(),
            # Kamino — extended earn vault data
            KaminoVaultDetailAction(),
            KaminoVaultMetricsAction(),
            KaminoVaultMetricsHistoryAction(),
            KaminoVaultAllocationHistoryAction(),
            KaminoVaultsRewardsAction(),
            KaminoVaultsSummaryAction(),
            KaminoVaultMintMetadataAction(),
            KaminoVaultMintImageAction(),
            # Kamino — extended earn user data
            KaminoUserMetricsHistoryAction(),
            KaminoUserTransactionsAction(),
            KaminoUserVaultPositionAction(),
            KaminoUserVaultMetricsHistoryAction(),
            KaminoUserVaultPnlAction(),
            KaminoUserVaultPnlHistoryAction(),
            KaminoUserKvaultRewardsAction(),
            KaminoVaultTransactionsAction(),
            # Kamino — vault action instructions
            KaminoVaultDepositInstructionsAction(),
            KaminoVaultWithdrawInstructionsAction(),
            # Kamino — extended borrow market data
            KaminoMarketDetailAction(),
            KaminoMarketReserveHistoryAction(),
            KaminoMarketLeverageMetricsAction(),
            KaminoMarketReservesAccountAction(),
            # Kamino — extended borrow user/loan data
            KaminoUserRewardsAction(),
            KaminoLoanDetailAction(),
            KaminoObligationPnlAction(),
            KaminoObligationMetricsHistoryAction(),
            KaminoRewardsListAction(),
            KaminoRewardsHistoryAction(),
            # Kamino — borrow/repay instruction variants
            KaminoBorrowInstructionsAction(),
            KaminoRepayInstructionsAction(),
            # Kamino — KSwap
            KaminoKswapAction(),
            # Kamino — K-Lend deposit/withdraw instruction variants
            KaminoDepositInstructionsAction(),
            KaminoWithdrawInstructionsAction(),
        ]


# ── Kamino data query actions ────────────────────────────────────────────────

class KaminoVaultsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vaults"
    @property
    def description(self) -> str: return "List all Kamino vaults with APY and TVL"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"limit": {"type": "number", "required": False, "description": "Max results"}}


class KaminoMarketsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_markets"
    @property
    def description(self) -> str: return "List all Kamino K-Lend lending markets"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"programId": {"type": "string", "required": False, "description": "KLend program ID filter (optional)"}}


class KaminoMarketReservesAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_market_reserves"
    @property
    def description(self) -> str: return "Get all reserves in a Kamino lending market with APY and liquidity"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": False, "description": "Market address (default: main market)"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
        }


class KaminoUserVaultPositionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_vault_positions"
    @property
    def description(self) -> str: return "Get a wallet's Kamino vault positions and balances"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class KaminoUserObligationsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_obligations"
    @property
    def description(self) -> str: return "Get a wallet's Kamino lending obligations (borrows and collateral)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"},
            "market": {"type": "string", "required": False, "description": "Market address filter"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
        }


class KaminoOraclePricesAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_oracle_prices"
    @property
    def description(self) -> str: return "Get current oracle prices used by Kamino lending markets"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class KaminoUsdBenchmarkRatesAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_usd_benchmark_rates"
    @property
    def description(self) -> str:
        return (
            "Get USD benchmark lending rates aggregated across multiple DeFi protocols on Solana. "
            "Use this to compare Kamino rates against broader market benchmarks or to show historical rate trends."
        )
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "start": {"type": "string", "required": False, "description": "Start date as ISO 8601 string (e.g. '2024-01-01') or epoch milliseconds. Defaults to 1970-01-01."},
            "end": {"type": "string", "required": False, "description": "End date as ISO 8601 string or epoch milliseconds. Defaults to now."},
            "frequency": {"type": "string", "required": False, "description": "Data granularity: 'hour' (default) or 'day'."},
        }


class KaminoMarketMetricsHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_market_metrics_history"
    @property
    def description(self) -> str:
        return "Get historical TVL and obligation count snapshots for a K-Lend market over time."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": True, "description": "K-Lend market public key (base58). Use kamino_markets to find market addresses."},
            "start": {"type": "string", "required": False, "description": "Start date as ISO 8601 string or epoch milliseconds."},
            "end": {"type": "string", "required": False, "description": "End date as ISO 8601 string or epoch milliseconds."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
        }


class KaminoReserveBorrowApyHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_reserve_borrow_apy_history"
    @property
    def description(self) -> str:
        return (
            "Get historical borrow APY and staking APY for a specific K-Lend reserve. "
            "First call kamino_market_reserves to get the reserve address."
        )
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": True, "description": "K-Lend market public key (base58)."},
            "reserve": {"type": "string", "required": True, "description": "K-Lend reserve public key (base58). Use kamino_market_reserves to look up."},
            "start": {"type": "string", "required": False, "description": "Start date as ISO 8601 string or epoch milliseconds."},
            "end": {"type": "string", "required": False, "description": "End date as ISO 8601 string or epoch milliseconds."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
        }


class KaminoReserveBorrowApyMedianAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_reserve_borrow_apy_median"
    @property
    def description(self) -> str:
        return (
            "Get median borrow APY and median staking APY history for a specific K-Lend reserve. "
            "First call kamino_market_reserves to get the reserve address."
        )
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": True, "description": "K-Lend market public key (base58)."},
            "reserve": {"type": "string", "required": True, "description": "K-Lend reserve public key (base58). Use kamino_market_reserves to look up."},
            "start": {"type": "string", "required": False, "description": "Start date as ISO 8601 string or epoch milliseconds."},
            "end": {"type": "string", "required": False, "description": "End date as ISO 8601 string or epoch milliseconds."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
        }


class KaminoObligationInterestEarnedAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_obligation_interest_earned"
    @property
    def description(self) -> str:
        return (
            "Get interest fees earned for a K-Lend obligation (deposit/lender perspective). "
            "Shows how much interest income a depositor has earned. Use kamino_user_obligations to find obligation addresses."
        )
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": True, "description": "K-Lend market public key (base58)."},
            "obligation": {"type": "string", "required": True, "description": "Obligation (loan) public key (base58). Use kamino_user_obligations to find."},
            "start": {"type": "string", "required": False, "description": "Start date as ISO 8601 string or epoch milliseconds."},
            "end": {"type": "string", "required": False, "description": "End date as ISO 8601 string or epoch milliseconds."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
            "program_id": {"type": "string", "required": False, "description": "KLend program ID. Defaults to KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD."},
        }


class KaminoObligationInterestPaidAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_obligation_interest_paid"
    @property
    def description(self) -> str:
        return (
            "Get interest fees paid for a K-Lend obligation (borrow/borrower perspective). "
            "Shows how much interest a borrower has paid. Use kamino_user_obligations to find obligation addresses."
        )
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": True, "description": "K-Lend market public key (base58)."},
            "obligation": {"type": "string", "required": True, "description": "Obligation (loan) public key (base58). Use kamino_user_obligations to find."},
            "start": {"type": "string", "required": False, "description": "Start date as ISO 8601 string or epoch milliseconds."},
            "end": {"type": "string", "required": False, "description": "End date as ISO 8601 string or epoch milliseconds."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
            "program_id": {"type": "string", "required": False, "description": "KLend program ID. Defaults to KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD."},
        }


# ── Kamino K-Lend transaction actions ─────────────────────────────────────────

class KaminoObligationTransactionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_obligation_transactions"
    @property
    def description(self) -> str:
        return "Get transaction history for a specific K-Lend obligation (loan). Use kamino_user_obligations to find obligation addresses."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": True, "description": "K-Lend market public key (base58)."},
            "obligation": {"type": "string", "required": True, "description": "Obligation public key (base58). Use kamino_user_obligations to find."},
            "sort": {"type": "string", "required": False, "description": "'asc' (oldest first, default) or 'desc' (newest first)."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
            "use_log_prices": {"type": "boolean", "required": False, "description": "Use log prices for transaction values. Default false."},
        }


class KaminoUserKlendTransactionsAllAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_klend_transactions_all"
    @property
    def description(self) -> str:
        return "Get K-Lend transaction history for a user across ALL markets. Omit wallet to use the authenticated user's wallet."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Target wallet address. Defaults to the authenticated user's wallet."},
            "sort": {"type": "string", "required": False, "description": "'asc' (oldest first, default) or 'desc' (newest first)."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
        }


class KaminoUserKlendTransactionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_klend_transactions"
    @property
    def description(self) -> str:
        return "Get K-Lend transaction history for a user in a specific market. Omit wallet to use the authenticated user's wallet."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": True, "description": "K-Lend market public key (base58). Use kamino_markets to find."},
            "wallet": {"type": "string", "required": False, "description": "Target wallet address. Defaults to the authenticated user's wallet."},
            "sort": {"type": "string", "required": False, "description": "'asc' (oldest first, default) or 'desc' (newest first)."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
        }


class KaminoBorrowOrderFillsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_borrow_order_fills"
    @property
    def description(self) -> str:
        return "Get the fill history for the active borrow order of a K-Lend obligation, in chronological order."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "obligation": {"type": "string", "required": True, "description": "Obligation public key (base58). Use kamino_user_obligations to find."},
        }


class KaminoOpenBorrowOrdersAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_open_borrow_orders"
    @property
    def description(self) -> str:
        return "Get all obligations with open borrow orders in a K-Lend market. Shows current order book for borrow demand."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": True, "description": "K-Lend market public key (base58). Use kamino_markets to find."},
            "env": {"type": "string", "required": False, "description": "Solana cluster: mainnet-beta (default), devnet, or localnet."},
            "program_id": {"type": "string", "required": False, "description": "KLend program ID. Defaults to KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD."},
        }


# ── Kamino yield actions ───────────────────────────────────────────────────────

class KaminoYieldHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_yield_history"
    @property
    def description(self) -> str:
        return (
            "Get APR/APY yield history for a specific token or farm. "
            "yield_source is a token mint address for regular yields, or 'farmAddress-rewardMint' for farm/extra yields."
        )
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "yield_source": {"type": "string", "required": True, "description": "Token mint address, or 'farmAddress-rewardMint' for farm yields."},
            "start": {"type": "string", "required": False, "description": "Start date as ISO 8601 string or epoch milliseconds."},
            "end": {"type": "string", "required": False, "description": "End date as ISO 8601 string or epoch milliseconds."},
        }


class KaminoPrincipalTokenYieldsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_principal_token_yields"
    @property
    def description(self) -> str:
        return "Get current APY yields for all principal tokens tracked by Kamino. No parameters required."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


# ── Kamino airdrop actions ────────────────────────────────────────────────────

class KaminoAirdropAllocationsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_airdrop_allocations"
    @property
    def description(self) -> str:
        return "Get KMNO airdrop token allocations for a wallet. Omit wallet to check the authenticated user's allocations."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Target wallet address. Defaults to the authenticated user's wallet."},
            "source": {"type": "string", "required": False, "description": "Airdrop season: Season1, Season2, Season3, Season4. Omit for all seasons."},
        }


class KaminoAirdropMetricsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_airdrop_metrics"
    @property
    def description(self) -> str:
        return "Get Kamino airdrop metrics: total allocation, total users, and claim date for a given season."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "source": {"type": "string", "required": False, "description": "Airdrop season: Season1, Season2, Season3, Season4. Omit for all."},
        }


# ── Kamino staking yield actions ──────────────────────────────────────────────

class KaminoStakingYieldsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_staking_yields"
    @property
    def description(self) -> str:
        return "Get current staking APY for all liquid staking tokens (LSTs) tracked by Kamino (e.g. mSOL, jitoSOL, bSOL). No parameters required."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class KaminoStakingYieldsMedianAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_staking_yields_median"
    @property
    def description(self) -> str:
        return "Get median staking APY for all liquid staking tokens tracked by Kamino. No parameters required."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class KaminoStakingYieldsMeanAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_staking_yields_mean"
    @property
    def description(self) -> str:
        return "Get mean (average) staking APY for all liquid staking tokens tracked by Kamino. No parameters required."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class KaminoUserStakingBoostsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_staking_boosts"
    @property
    def description(self) -> str:
        return "Get staking boost multipliers for a user's Kamino positions. Shows how much extra yield the user earns from staking bonuses."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "User wallet address (defaults to authenticated user)"},
            "source": {"type": "string", "required": False, "description": "Filter by source (e.g. 'jlp', 'usdc')"},
        }


class KaminoSeasonRewardsUserAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_season_rewards_user"
    @property
    def description(self) -> str:
        return "Get season rewards earned by a user on Kamino Finance. Shows KMNO token rewards allocated per season."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "User wallet address (defaults to authenticated user)"},
            "source": {"type": "string", "required": False, "description": "Filter by reward source"},
        }


class KaminoSeasonRewardsVestingPoolAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_season_rewards_vesting_pool"
    @property
    def description(self) -> str:
        return "Get the final vesting pool data for Kamino season rewards. Shows total KMNO tokens in the vesting pool."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "source": {"type": "string", "required": False, "description": "Filter by source"},
        }


class KaminoPrivateCreditMetricsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_private_credit_metrics"
    @property
    def description(self) -> str:
        return "Get aggregate metrics for Kamino private credit vaults. Shows TVL, APY, and utilization for private credit pools."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class KaminoPrivateCreditMetricsHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_private_credit_metrics_history"
    @property
    def description(self) -> str:
        return "Get historical metrics for Kamino private credit vaults over a time range."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "start": {"type": "string", "required": False, "description": "Start timestamp (ISO 8601 or Unix ms)"},
            "end": {"type": "string", "required": False, "description": "End timestamp (ISO 8601 or Unix ms)"},
        }


class KaminoUserFarmTransactionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_farm_transactions"
    @property
    def description(self) -> str:
        return "Get farm transaction history for a user across all Kamino farms. Shows stake, unstake, and harvest events."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "User wallet address (defaults to authenticated user)"},
            "limit": {"type": "integer", "required": False, "description": "Max number of transactions to return"},
            "sort": {"type": "string", "required": False, "description": "Sort order: 'asc' or 'desc'"},
            "pagination_token": {"type": "string", "required": False, "description": "Pagination cursor from previous response"},
            "no_pagination": {"type": "boolean", "required": False, "description": "Return all results without pagination"},
        }


class KaminoFarmTransactionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_farm_transactions"
    @property
    def description(self) -> str:
        return "Get all transactions for a specific Kamino farm. Shows all user stake/unstake/harvest activity in the farm."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "farm": {"type": "string", "required": True, "description": "Farm pubkey (base58 Solana address)"},
            "limit": {"type": "integer", "required": False, "description": "Max number of transactions to return"},
            "sort": {"type": "string", "required": False, "description": "Sort order: 'asc' or 'desc'"},
            "pagination_token": {"type": "string", "required": False, "description": "Pagination cursor from previous response"},
        }


# ── Kamino extended earn vault data actions ───────────────────────────────────

class KaminoVaultDetailAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_detail"
    @property
    def description(self) -> str: return "Get details for a specific Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"vault": {"type": "string", "required": True, "description": "Vault address (base58)"}}


class KaminoVaultMetricsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_metrics"
    @property
    def description(self) -> str: return "Get APY and TVL metrics for a specific Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"vault": {"type": "string", "required": True, "description": "Vault address (base58)"}}


class KaminoVaultMetricsHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_metrics_history"
    @property
    def description(self) -> str: return "Get historical APY/TVL metrics for a Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "Vault address (base58)"},
            "start": {"type": "number", "required": False, "description": "Start of range (epoch in ms or ISO 8601 string)"},
            "end": {"type": "number", "required": False, "description": "End of range (epoch in ms or ISO 8601 string)"},
        }


class KaminoVaultAllocationHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_allocation_history"
    @property
    def description(self) -> str: return "Get allocation volume history for a specific Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "Vault address (base58)"},
            "start": {"type": "number", "required": False, "description": "Start of range (epoch in ms or ISO 8601 string)"},
            "end": {"type": "number", "required": False, "description": "End of range (epoch in ms or ISO 8601 string)"},
        }


class KaminoVaultsRewardsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vaults_rewards"
    @property
    def description(self) -> str: return "Get reward APY and distribution info for Kamino Earn vaults"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"source": {"type": "string", "required": False, "description": "Points source: Season1, Season2, Season3, or Season4"}}


class KaminoVaultsSummaryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vaults_summary"
    @property
    def description(self) -> str: return "Get an all-time summary of Kamino Earn vault rewards and interest"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"vaultType": {"type": "string", "required": False, "description": "Summary type: 'default' or 'private-credit'"}}


class KaminoVaultMintMetadataAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_mint_metadata"
    @property
    def description(self) -> str: return "Get kToken metadata for a Kamino Earn vault's share token. Requires the kToken mint address (get from vault detail)."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mint": {"type": "string", "required": True, "description": "kToken mint address (base58)"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
        }


class KaminoVaultMintImageAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_mint_image"
    @property
    def description(self) -> str: return "Get the SVG image for a Kamino kToken. Requires the kToken mint address."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mint": {"type": "string", "required": True, "description": "kToken mint address (base58)"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
        }


# ── Kamino extended earn user data actions ────────────────────────────────────

class KaminoUserMetricsHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_metrics_history"
    @property
    def description(self) -> str: return "Get historical Earn metrics for a user across all Kamino vaults"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)"},
            "start": {"type": "number", "required": False, "description": "Start of range (epoch in ms or ISO 8601 string)"},
            "end": {"type": "number", "required": False, "description": "End of range (epoch in ms or ISO 8601 string)"},
        }


class KaminoUserTransactionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_transactions"
    @property
    def description(self) -> str: return "Get Earn transaction history for a user on Kamino"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)"},
            "limit": {"type": "number", "required": False, "description": "Max transactions"},
            "cursor": {"type": "string", "required": False, "description": "Pagination cursor (transaction signature)"},
        }


class KaminoUserVaultPositionAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_vault_position"
    @property
    def description(self) -> str: return "Get a user's position in a specific Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "Vault address (base58)"},
            "wallet": {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)"},
        }


class KaminoUserVaultMetricsHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_vault_metrics_history"
    @property
    def description(self) -> str: return "Get historical metrics for a user's position in a Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "Vault address (base58)"},
            "wallet": {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)"},
            "start": {"type": "number", "required": False, "description": "Start of range (epoch in ms or ISO 8601 string)"},
            "end": {"type": "number", "required": False, "description": "End of range (epoch in ms or ISO 8601 string)"},
        }


class KaminoUserVaultPnlAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_vault_pnl"
    @property
    def description(self) -> str: return "Get PnL for a user's position in a specific Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "Vault address (base58)"},
            "wallet": {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)"},
        }


class KaminoUserVaultPnlHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_vault_pnl_history"
    @property
    def description(self) -> str: return "Get PnL history for a user's position in a Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "Vault address (base58)"},
            "wallet": {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)"},
            "start": {"type": "number", "required": False, "description": "Start of range (epoch in ms or ISO 8601 string)"},
            "end": {"type": "number", "required": False, "description": "End of range (epoch in ms or ISO 8601 string)"},
        }


class KaminoUserKvaultRewardsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_kvault_rewards"
    @property
    def description(self) -> str:
        return (
            "Get Kamino Earn (KVault) season reward metrics for a user: APY, tokens earned, staking boost, USD value. "
            "Omit wallet to check the authenticated user. Filter by season with source (Season1–Season4). "
            "Note: this is for Earn vaults. For K-Lend rewards use kamino_user_rewards."
        )
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Target wallet address. Defaults to the authenticated user's wallet."},
            "source": {"type": "string", "required": False, "description": "Season identifier: Season1, Season2, Season3, Season4. Omit for all seasons."},
        }


class KaminoVaultTransactionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_transactions"
    @property
    def description(self) -> str:
        return (
            "Get transaction history for a specific Kamino Earn (KVault) vault. "
            "Filter by instruction type, date range, and paginate with pagination_token. "
            "Returns deposit, withdraw, invest, and other vault instructions."
        )
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "KVault address (base58)."},
            "instruction": {"type": "string", "required": False, "description": "Filter by instruction: buy, sell, deposit, withdraw, invest, withdrawAvailable."},
            "start": {"type": "string", "required": False, "description": "Start date as ISO 8601 string or epoch milliseconds."},
            "end": {"type": "string", "required": False, "description": "End date as ISO 8601 string or epoch milliseconds."},
            "direction": {"type": "string", "required": False, "description": "Sort: 'asc' or 'desc' (default: desc)."},
            "pagination_token": {"type": "string", "required": False, "description": "Pagination token from previous response for next page."},
        }


# ── Kamino vault action instructions variants ─────────────────────────────────

class KaminoVaultDepositInstructionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_deposit_instructions"
    @property
    def description(self) -> str: return "Get raw deposit instructions (not a full transaction) for a Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "Vault address (base58)"},
            "amount": {"type": "string", "required": True, "description": "Amount to deposit (decimal)"},
            "slippageBps": {"type": "number", "required": False, "description": "Slippage tolerance in basis points"},
        }


class KaminoVaultWithdrawInstructionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_vault_withdraw_instructions"
    @property
    def description(self) -> str: return "Get raw withdraw instructions (not a full transaction) for a Kamino Earn vault"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "vault": {"type": "string", "required": True, "description": "Vault address (base58)"},
            "ktokenAmount": {"type": "string", "required": True, "description": "kToken shares to redeem (decimal)"},
            "slippageBps": {"type": "number", "required": False, "description": "Slippage tolerance in basis points"},
        }


# ── Kamino extended borrow market data actions ────────────────────────────────

class KaminoMarketDetailAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_market_detail"
    @property
    def description(self) -> str: return "Get details for a specific Kamino K-Lend lending market"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": False, "description": "K-Lend market address (defaults to main market)"},
            "programId": {"type": "string", "required": False, "description": "KLend program ID (optional)"},
        }


class KaminoMarketReserveHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_market_reserve_history"
    @property
    def description(self) -> str: return "Get historical APY/utilization metrics for a specific reserve in a Kamino K-Lend market"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "Reserve public key (on-chain reserve account address)"},
            "market": {"type": "string", "required": False, "description": "K-Lend market address (defaults to main market)"},
            "start": {"type": "number", "required": False, "description": "Start of range (epoch in ms or ISO 8601 string)"},
            "end": {"type": "number", "required": False, "description": "End of range (epoch in ms or ISO 8601 string)"},
            "frequency": {"type": "string", "required": False, "description": "Snapshot frequency: 'hour' (default) or 'day'"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
        }


class KaminoMarketLeverageMetricsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_market_leverage_metrics"
    @property
    def description(self) -> str: return "Get leverage (Multiply/Long/Short) aggregate metrics for a Kamino K-Lend market"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market": {"type": "string", "required": False, "description": "K-Lend market address (defaults to main market)"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
        }


class KaminoMarketReservesAccountAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_market_reserves_account"
    @property
    def description(self) -> str: return "Get on-chain account data for all reserves across Kamino K-Lend markets"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "markets": {"type": "array", "required": False, "description": "List of K-Lend market addresses (defaults to main market)"},
            "programId": {"type": "string", "required": False, "description": "KLend program ID (optional)"},
        }


class KaminoUserRewardsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_user_rewards"
    @property
    def description(self) -> str: return "Get K-Lend lending/borrowing reward details for a user (APY, tokens earned, staking boosts)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"},
            "source": {"type": "string", "required": False, "description": "Points season filter: Season1, Season2, Season3, Season4"},
        }


class KaminoLoanDetailAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_loan_detail"
    @property
    def description(self) -> str: return "Get detailed info for a specific K-Lend loan/obligation (collateral, debt, LTV, leverage)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "loan": {"type": "string", "required": True, "description": "Loan (obligation) public key address"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
        }


class KaminoObligationPnlAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_obligation_pnl"
    @property
    def description(self) -> str: return "Get PnL (profit and loss) for a K-Lend obligation in USD and SOL"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "obligation": {"type": "string", "required": True, "description": "Obligation public key address"},
            "market": {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
            "programId": {"type": "string", "required": False, "description": "KLend program ID (optional)"},
            "useStakeRate": {"type": "boolean", "required": False, "description": "Use stake rate for xSOL pair PnL calculation (default: false)"},
        }


class KaminoObligationMetricsHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_obligation_metrics_history"
    @property
    def description(self) -> str: return "Get historical metrics snapshots for a K-Lend obligation (LTV, deposits, borrows over time)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "obligation": {"type": "string", "required": True, "description": "Obligation public key address"},
            "market": {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
            "env": {"type": "string", "required": False, "description": "Cluster: mainnet-beta (default), devnet, or localnet"},
            "start": {"type": "number", "required": False, "description": "Start time (ISO 8601 or epoch ms)"},
            "end": {"type": "number", "required": False, "description": "End time (ISO 8601 or epoch ms)"},
            "useStakeRateForObligation": {"type": "boolean", "required": False, "description": "Use stake rate for net SOL value calculation (default: false)"},
        }


class KaminoRewardsListAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_rewards_list"
    @property
    def description(self) -> str: return "List all active K-Lend reward programs with APY, tokens/second, and total active positions"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "source": {"type": "string", "required": False, "description": "Points season filter: Season1, Season2, Season3, Season4"},
        }


class KaminoRewardsHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_rewards_history"
    @property
    def description(self) -> str: return "Get historical reward APY for a K-Lend deposit/borrow reserve pair"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "depositReserve": {"type": "string", "required": True, "description": "Deposit reserve public key"},
            "borrowReserve": {"type": "string", "required": True, "description": "Borrow reserve public key"},
            "start": {"type": "number", "required": False, "description": "Start time (ISO 8601 or epoch ms)"},
            "end": {"type": "number", "required": False, "description": "End time (ISO 8601 or epoch ms)"},
            "frequency": {"type": "string", "required": False, "description": "Aggregation frequency: 'hour' or 'day'"},
        }


class KaminoBorrowInstructionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_borrow_instructions"
    @property
    def description(self) -> str: return "Get K-Lend borrow as raw unsigned instructions array (for custom transaction assembly)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "Reserve account address for the token to borrow (on-chain reserve, not token mint)"},
            "amount": {"type": "string", "required": True, "description": "Amount to borrow in decimal format (not lamports)"},
            "market": {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


class KaminoRepayInstructionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_repay_instructions"
    @property
    def description(self) -> str: return "Get K-Lend repay as raw unsigned instructions array (for custom transaction assembly)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "Reserve account address for the token to repay (on-chain reserve, not token mint)"},
            "amount": {"type": "string", "required": True, "description": "Amount to repay in decimal format (not lamports)"},
            "market": {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


class KaminoKswapAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_kswap"
    @property
    def description(self) -> str: return "Perform a KSwap (Kamino multi-router swap via Metis/DFlow/OKX) and get a signed-ready transaction"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenIn": {"type": "string", "required": True, "description": "Input token mint address"},
            "tokenOut": {"type": "string", "required": True, "description": "Output token mint address"},
            "amountIn": {"type": "string", "required": True, "description": "Input amount in smallest unit (lamports)"},
            "maxSlippageBps": {"type": "number", "required": True, "description": "Maximum acceptable slippage in basis points (e.g. 50 = 0.5%)"},
            "includeSetupIxs": {"type": "boolean", "required": False, "description": "Include setup instructions (create ATAs etc.) — default: true"},
            "wrapAndUnwrapSol": {"type": "boolean", "required": False, "description": "Auto wrap/unwrap native SOL — default: true"},
        }


class KaminoDepositInstructionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_deposit_instructions"
    @property
    def description(self) -> str: return "Get K-Lend deposit as raw unsigned instructions array (for custom transaction assembly). First call kamino_market_reserves to get the reserve address."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "KLend reserve account address for the token to deposit (not the token mint)"},
            "amount":  {"type": "string", "required": True, "description": "Amount in decimal format, not lamports"},
            "market":  {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


class KaminoWithdrawInstructionsAction(BuildableAction):
    @property
    def name(self) -> str: return "kamino_withdraw_instructions"
    @property
    def description(self) -> str: return "Get K-Lend withdraw as raw unsigned instructions array (for custom transaction assembly). First call kamino_market_reserves to get the reserve address."
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "reserve": {"type": "string", "required": True, "description": "KLend reserve account address for the token to withdraw (not the token mint)"},
            "amount":  {"type": "string", "required": True, "description": "Amount in decimal format, not lamports"},
            "market":  {"type": "string", "required": False, "description": "K-Lend market address (default: main market)"},
        }


# ============================================================================
# Jito Plugin
# ============================================================================

class JitoStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_stake"
    @property
    def description(self) -> str: return "Stake SOL for jitoSOL with MEV rewards"
    @property
    def aliases(self) -> list[str]: return ["stake_jito"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"amount": {"type": "number", "required": True}}


class JitoUnstakeAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_unstake"
    @property
    def description(self) -> str: return "Unstake jitoSOL to SOL. Use instant=true for immediate liquid SOL from the pool reserve (subject to liquidity); default creates a stake account requiring ~1-epoch deactivation."
    @property
    def aliases(self) -> list[str]: return ["unstake_jito"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {"type": "number", "required": True, "description": "jitoSOL amount to unstake"},
            "instant": {"type": "boolean", "required": False, "default": False, "description": "If true, uses withdraw_sol for instant liquid SOL from reserve (limited by reserve liquidity). If false (default), creates a stake account that must be deactivated before withdrawal (~1 epoch)."},
        }


class JitoTipAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_tip"
    @property
    def description(self) -> str: return "Add a Jito MEV tip to a transaction for priority inclusion"
    @property
    def aliases(self) -> list[str]: return ["jito_priority_tip", "jito_mev_tip"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {"type": "number", "required": True, "description": "Tip amount in SOL (e.g. 0.001)"},
        }


class JitoBundleAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_bundle"
    @property
    def description(self) -> str: return "Create a Jito bundle for atomic multi-transaction execution"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "transactions": {"type": "array", "required": True, "items": {"type": "string"}, "description": "List of base64-encoded transactions to bundle (1–5 max)"},
        }


class JitoBundleStatusAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_bundle_status"
    @property
    def description(self) -> str: return "Check the status of a Jito bundle"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "bundleId": {"type": "string", "required": True, "description": "Jito bundle ID to check"},
        }


class JitoGetStatsAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_get_stats"
    @property
    def description(self) -> str: return "Get current Jito Finance statistics: APY, TVL, jitoSOL supply and validator count"
    @property
    def aliases(self) -> list[str]: return ["jito_stats", "jitosol_stats"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class JitoGetExchangeRateAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_get_exchange_rate"
    @property
    def description(self) -> str: return "Get the current jitoSOL to SOL exchange rate (how many SOL per 1 jitoSOL)"
    @property
    def aliases(self) -> list[str]: return ["jitosol_price", "jitosol_rate"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class JitoGetValidatorsAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_get_validators"
    @property
    def description(self) -> str: return "List validators in the JitoSOL stake pool with MEV commission and stake info"
    @property
    def aliases(self) -> list[str]: return ["jitosol_validators", "jito_validators_list"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "epoch": {"type": "number", "required": False, "description": "Epoch to query (default: current epoch)"},
        }


class JitoGetStakerRewardsAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_get_staker_rewards"
    @property
    def description(self) -> str: return "Get claimable Jito MEV staker rewards for a wallet address or validator"
    @property
    def aliases(self) -> list[str]: return ["jito_rewards", "jito_mev_rewards_wallet"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakeAuthority": {"type": "string", "required": False, "description": "Stake authority wallet address"},
            "validatorVoteAccount": {"type": "string", "required": False, "description": "Validator vote account address"},
            "epoch": {"type": "number", "required": False, "description": "Specific epoch to query"},
            "limit": {"type": "number", "required": False, "default": 10, "description": "Max results to return (default 10)"},
        }


class JitoGetMevRewardsAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_get_mev_rewards"
    @property
    def description(self) -> str: return "Get network-wide Jito MEV reward statistics for a given epoch"
    @property
    def aliases(self) -> list[str]: return ["jito_network_mev", "jito_epoch_rewards"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "epoch": {"type": "number", "required": False, "description": "Epoch to query (default: latest)"},
        }


class JitoDeactivateStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_deactivate_stake"
    @property
    def description(self) -> str: return "Deactivate a Jito stake account to begin the cooldown (~1 epoch) before withdrawing SOL"
    @property
    def aliases(self) -> list[str]: return ["deactivate_jito_stake", "jito_start_cooldown"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakeAccount": {"type": "string", "required": True, "description": "Stake account address received from jito_unstake"},
        }


class JitoWithdrawStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_withdraw_stake"
    @property
    def description(self) -> str: return "Withdraw SOL from a fully deactivated Jito stake account to your wallet"
    @property
    def aliases(self) -> list[str]: return ["withdraw_jito_stake", "jito_claim_sol"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakeAccount": {"type": "string", "required": True, "description": "Deactivated stake account address"},
            "recipient": {"type": "string", "required": False, "description": "Recipient wallet address (defaults to your wallet)"},
        }


class JitoGetValidatorRewardsAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_get_validator_rewards"
    @property
    def description(self) -> str: return "Get aggregated MEV rewards earned by a specific Jito validator over time"
    @property
    def aliases(self) -> list[str]: return ["jito_validator_rewards", "jito_mev_validator_rewards"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "voteAccount": {"type": "string", "required": False, "description": "Validator vote account address (leave empty for all validators)"},
            "epoch":       {"type": "number", "required": False, "description": "Specific epoch to query (defaults to recent epochs)"},
            "limit":       {"type": "number", "required": False, "default": 10,  "description": "Number of results to return (default 10)"},
            "sortOrder":   {"type": "string", "required": False, "default": "desc", "description": "Sort order: 'asc' or 'desc' (default desc)"},
        }


class JitoDepositStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "jito_deposit_stake"
    @property
    def description(self) -> str: return "Convert an existing native Solana stake account into jitoSOL liquid staking tokens"
    @property
    def aliases(self) -> list[str]: return ["deposit_stake_jito", "jito_convert_stake"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakeAccount": {"type": "string", "required": True, "description": "Active stake account address to deposit into the Jito stake pool"},
        }


class JitoPlugin(BasePlugin):
    @property
    def id(self) -> str: return "jito"
    @property
    def name(self) -> str: return "Jito"
    @property
    def description(self) -> str: return "Jito Finance liquid staking with MEV rewards"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            JitoStakeAction(),
            JitoUnstakeAction(),
            JitoTipAction(),
            JitoBundleAction(),
            JitoBundleStatusAction(),
            JitoGetStatsAction(),
            JitoGetExchangeRateAction(),
            JitoGetValidatorsAction(),
            JitoGetStakerRewardsAction(),
            JitoGetMevRewardsAction(),
            JitoGetValidatorRewardsAction(),
            JitoDeactivateStakeAction(),
            JitoWithdrawStakeAction(),
            JitoDepositStakeAction(),
        ]


# ============================================================================
# Raydium Plugin
# ============================================================================

class RaydiumSwapAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_swap"
    @property
    def description(self) -> str: return "Swap tokens via Raydium AMM/CLMM routing"
    @property
    def aliases(self) -> list[str]: return ["swap_raydium", "raydium_trade"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint":  {"type": "string", "required": True,  "description": "Input token symbol (SOL, USDC) or mint address"},
            "outputMint": {"type": "string", "required": True,  "description": "Output token symbol or mint address"},
            "amount":     {"type": "string", "required": True,  "description": "Human-readable amount to swap (e.g. '1.5')"},
            "slippageBps":{"type": "number", "required": False, "default": 50,       "description": "Slippage in basis points (default 50 = 0.5%)"},
            "swapMode":   {"type": "string", "required": False, "default": "ExactIn", "enum": ["ExactIn", "ExactOut"], "description": "ExactIn (sell exact input) or ExactOut (receive exact output)"},
        }


class RaydiumAddLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_add_liquidity"
    @property
    def description(self) -> str: return "Add liquidity to a Raydium AMM pool. Use poolId+inputMint+amount (single-sided) OR tokenA+tokenB+amountA+amountB (token-pair)."
    @property
    def aliases(self) -> list[str]: return ["raydium_lp", "raydium_deposit"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            # Mode A: known pool ID (single-sided deposit)
            "poolId":    {"type": "string", "required": False, "description": "Pool address (use this OR tokenA/tokenB pair)"},
            "inputMint": {"type": "string", "required": False, "description": "Required when using poolId mode — the token you are depositing (e.g. SOL, USDC)"},
            "amount":    {"type": "string",  "required": False, "description": "Human-readable amount to deposit when using poolId mode (e.g. '1.5')"},
            # Mode B: token pair (backend resolves pool automatically)
            "tokenA":    {"type": "string", "required": False, "description": "First token symbol or mint (use with tokenB+amountA+amountB)"},
            "tokenB":    {"type": "string", "required": False, "description": "Second token symbol or mint"},
            "amountA":   {"type": "number", "required": False, "description": "Amount of tokenA to deposit"},
            "amountB":   {"type": "number", "required": False, "description": "Amount of tokenB to deposit"},
            "slippageBps":{"type": "number", "required": False, "default": 100, "description": "Slippage in bps (default 100 = 1%)"},
        }


class RaydiumRemoveLiquidityAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_remove_liquidity"
    @property
    def description(self) -> str: return "Remove liquidity (burn LP tokens) from a Raydium AMM pool"
    @property
    def aliases(self) -> list[str]: return ["raydium_withdraw_lp"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "poolId":    {"type": "string", "required": True, "description": "Raydium pool address"},
            "liquidity": {"type": "number", "required": True, "description": "LP token amount to burn"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class RaydiumCreatePoolAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_create_pool"
    @property
    def description(self) -> str: return "Create a new Raydium AMM pool with seed liquidity"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenA":    {"type": "string", "required": True, "description": "First token symbol or mint (e.g. SOL)"},
            "tokenB":    {"type": "string", "required": True, "description": "Second token symbol or mint (e.g. USDC)"},
            "amountA":   {"type": "number", "required": True, "description": "Seed amount for token A"},
            "amountB":   {"type": "number", "required": True, "description": "Seed amount for token B"},
            "startTime": {"type": "number", "required": False, "default": 0, "description": "Unix timestamp when trading opens (0 = immediately)"},
        }


class RaydiumOpenPositionAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_open_position"
    @property
    def description(self) -> str: return "Open a concentrated liquidity (CLMM) position on Raydium with a price range"
    @property
    def aliases(self) -> list[str]: return ["raydium_clmm_open", "raydium_concentrated"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            # Mode A: known pool
            "poolId":      {"type": "string", "required": False, "description": "CLMM pool address (use this OR tokenA+tokenB)"},
            # Mode B: auto-discover pool from token pair
            "tokenA":      {"type": "string", "required": False, "description": "First token (e.g. SOL). Required if poolId not provided."},
            "tokenB":      {"type": "string", "required": False, "description": "Second token (e.g. USDC). Required if poolId not provided."},
            # Input amount (always single-sided on Raydium CLMM)
            "inputMint":   {"type": "string", "required": False, "description": "Which token to deposit (alias for tokenA if poolId mode)"},
            "inputAmount": {"type": "number", "required": True,  "description": "Amount of input token to deposit"},
            # Price range — provide EITHER price bounds OR tick bounds
            "minPrice":    {"type": "number", "required": False, "description": "Lower price bound (tokenB per tokenA). Provide this OR tickLower."},
            "maxPrice":    {"type": "number", "required": False, "description": "Upper price bound (tokenB per tokenA). Provide this OR tickUpper."},
            "tickLower":   {"type": "number", "required": False, "description": "Lower tick (advanced — use minPrice instead)"},
            "tickUpper":   {"type": "number", "required": False, "description": "Upper tick (advanced — use maxPrice instead)"},
            "slippageBps": {"type": "number", "required": False, "default": 100, "description": "Slippage in bps (default 100 = 1%)"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        # Normalize: if user provides tokenA but not inputMint, set inputMint = tokenA
        if "tokenA" in params and "inputMint" not in params:
            params["inputMint"] = params["tokenA"]
        if "amountA" in params and "inputAmount" not in params:
            params["inputAmount"] = params.pop("amountA")
        # Remove amountB if erroneously provided — CLMM deposits are single-sided
        params.pop("amountB", None)
        return await _build_action("raydium_open_position", params)


class RaydiumClosePositionAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_close_position"
    @property
    def description(self) -> str: return "Close a Raydium CLMM position and withdraw all liquidity and fees"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "positionId": {"type": "string", "required": True, "description": "NFT mint of the position"},
        }


class RaydiumIncreasePositionAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_increase_position"
    @property
    def description(self) -> str: return "Add more liquidity to an existing Raydium CLMM position"
    @property
    def aliases(self) -> list[str]: return ["raydium_add_to_position"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "positionId":  {"type": "string", "required": True},
            "inputMint":   {"type": "string", "required": True},
            "inputAmount": {"type": "number", "required": True},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }


class RaydiumDecreasePositionAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_decrease_position"
    @property
    def description(self) -> str: return "Partially or fully withdraw from a Raydium CLMM position"
    @property
    def aliases(self) -> list[str]: return ["raydium_remove_from_position"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "positionId": {"type": "string", "required": True},
            "liquidity":  {"type": "string", "required": True, "description": "Raw liquidity units to remove"},
            "slippageBps": {"type": "number", "required": False, "default": 100},
        }



class RaydiumPlugin(BasePlugin):
    @property
    def id(self) -> str: return "raydium"
    @property
    def name(self) -> str: return "Raydium"
    @property
    def description(self) -> str: return "Raydium AMM and CLMM concentrated liquidity"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            RaydiumSwapAction(),
            RaydiumAddLiquidityAction(),
            RaydiumRemoveLiquidityAction(),
            RaydiumCreatePoolAction(),
            RaydiumOpenPositionAction(),
            RaydiumClosePositionAction(),
            RaydiumIncreasePositionAction(),
            RaydiumDecreasePositionAction(),
            # Raydium — data queries
            RaydiumGetPoolsAction(),
            RaydiumSearchPoolsAction(),
            RaydiumSwapQuoteAction(),
            RaydiumGetPoolInfoAction(),
            RaydiumGetUserPositionsAction(),
            RaydiumGetClmmPositionsAction(),
            RaydiumGetTokenInfoAction(),
            RaydiumGetPlatformStatsAction(),
            RaydiumGetClmmConfigsAction(),
            RaydiumGetPoolsByLpAction(),
            RaydiumGetPoolsV2Action(),
            RaydiumGetPoolKeysAction(),
            RaydiumGetPoolLiquidityHistoryAction(),
            RaydiumGetPoolPositionHistoryAction(),
            RaydiumGetTokenListAction(),
            RaydiumGetTokenPricesAction(),
            RaydiumGetFarmInfoAction(),
            RaydiumGetFarmByLpAction(),
            RaydiumGetFarmKeysAction(),
            RaydiumGetIdoKeysAction(),
            RaydiumGetMainVersionAction(),
            RaydiumGetRpcsAction(),
            RaydiumGetChainTimeAction(),
            RaydiumGetStakePoolsAction(),
            RaydiumGetMigrateLpAction(),
            RaydiumGetAutoFeeAction(),
            RaydiumGetCpmmConfigsAction(),
        ]


# ── Raydium data query actions ───────────────────────────────────────────────

class RaydiumGetPoolsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_pools"
    @property
    def description(self) -> str: return "List Raydium pools with sorting and filtering"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "poolType":  {"type": "string", "required": False, "description": "Pool type: all, amm, clmm, cpmm"},
            "sortField": {"type": "string", "required": False, "description": "Sort by: tvl, volume24h, apr"},
            "page":      {"type": "number", "required": False},
            "pageSize":  {"type": "number", "required": False},
        }


class RaydiumSearchPoolsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_search_pools"
    @property
    def description(self) -> str: return "Search Raydium pools by token address or symbol"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tokenA": {"type": "string", "required": True, "description": "Token A mint address or symbol"},
            "tokenB": {"type": "string", "required": False, "description": "Token B mint address or symbol"},
        }


class RaydiumSwapQuoteAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_swap_quote"
    @property
    def description(self) -> str: return "Get a Raydium swap quote without executing"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint":  {"type": "string", "required": True, "description": "Input token mint"},
            "outputMint": {"type": "string", "required": True, "description": "Output token mint"},
            "amount":     {"type": "number", "required": True, "description": "Input amount"},
            "slippageBps": {"type": "number", "required": False},
        }


class RaydiumGetPoolInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_pool_info"
    @property
    def description(self) -> str: return "Get detailed info for specific Raydium pools by ID"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"ids": {"type": "string", "required": True, "description": "Comma-separated pool IDs"}}


class RaydiumGetUserPositionsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_user_positions"
    @property
    def description(self) -> str: return "Get a wallet's Raydium AMM liquidity positions"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class RaydiumGetClmmPositionsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_clmm_positions"
    @property
    def description(self) -> str: return "Get a wallet's Raydium CLMM concentrated liquidity positions"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class RaydiumGetTokenInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_token_info"
    @property
    def description(self) -> str: return "Get token info from Raydium by mint addresses"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"mints": {"type": "string", "required": True, "description": "Comma-separated token mint addresses"}}


class RaydiumGetPlatformStatsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_platform_stats"
    @property
    def description(self) -> str: return "Get global Raydium platform statistics: TVL, volume, fees"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetClmmConfigsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_clmm_configs"
    @property
    def description(self) -> str: return "Get available Raydium CLMM pool configuration presets"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetPoolsByLpAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_pools_by_lp"
    @property
    def description(self) -> str: return "Find Raydium pools by LP token mint addresses"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"lps": {"type": "string", "required": True, "description": "Comma-separated LP mint addresses"}}


class RaydiumGetPoolsV2Action(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_pools_v2"
    @property
    def description(self) -> str: return "List Raydium pools (v2 API) with token filtering"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "poolType":   {"type": "string", "required": False},
            "mintFilter": {"type": "string", "required": False, "description": "Filter by token mint address"},
            "page":       {"type": "number", "required": False},
            "pageSize":   {"type": "number", "required": False},
        }


class RaydiumGetPoolKeysAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_pool_keys"
    @property
    def description(self) -> str: return "Get Raydium pool account keys for SDK use"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"ids": {"type": "string", "required": True, "description": "Comma-separated pool IDs"}}


class RaydiumGetPoolLiquidityHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_pool_liquidity_history"
    @property
    def description(self) -> str: return "Get historical liquidity data for a Raydium pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"id": {"type": "string", "required": True, "description": "Pool ID"}}


class RaydiumGetPoolPositionHistoryAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_pool_position_history"
    @property
    def description(self) -> str: return "Get position history for a Raydium pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"id": {"type": "string", "required": True, "description": "Pool ID"}}


class RaydiumGetTokenListAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_token_list"
    @property
    def description(self) -> str: return "Get the full Raydium supported token list"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetTokenPricesAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_token_prices"
    @property
    def description(self) -> str: return "Get token prices from Raydium"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"mints": {"type": "string", "required": False, "description": "Comma-separated token mints (omit = all)"}}


class RaydiumGetFarmInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_farm_info"
    @property
    def description(self) -> str: return "Get Raydium farm info by IDs"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"ids": {"type": "string", "required": True, "description": "Comma-separated farm IDs"}}


class RaydiumGetFarmByLpAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_farm_by_lp"
    @property
    def description(self) -> str: return "Find Raydium farms by LP token mint"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "lp":   {"type": "string", "required": True, "description": "LP token mint address"},
            "page": {"type": "number", "required": False},
        }


class RaydiumGetFarmKeysAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_farm_keys"
    @property
    def description(self) -> str: return "Get Raydium farm account keys for SDK use"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"ids": {"type": "string", "required": True, "description": "Comma-separated farm IDs"}}


class RaydiumGetIdoKeysAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_ido_keys"
    @property
    def description(self) -> str: return "Get Raydium IDO pool keys"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"ids": {"type": "string", "required": True, "description": "Comma-separated IDO IDs"}}


class RaydiumGetMainVersionAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_main_version"
    @property
    def description(self) -> str: return "Get current Raydium protocol version info"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetRpcsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_rpcs"
    @property
    def description(self) -> str: return "Get recommended RPC endpoints from Raydium"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetChainTimeAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_chain_time"
    @property
    def description(self) -> str: return "Get current Solana chain time from Raydium"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetStakePoolsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_stake_pools"
    @property
    def description(self) -> str: return "Get Raydium RAY staking pool info"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetMigrateLpAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_migrate_lp"
    @property
    def description(self) -> str: return "Get info on Raydium LP positions eligible for migration"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetAutoFeeAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_auto_fee"
    @property
    def description(self) -> str: return "Get Raydium recommended priority fee for current network conditions"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class RaydiumGetCpmmConfigsAction(BuildableAction):
    @property
    def name(self) -> str: return "raydium_get_cpmm_configs"
    @property
    def description(self) -> str: return "Get available Raydium CPMM pool configuration presets"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


# ============================================================================
# ============================================================================
# Marinade Plugin
# ============================================================================

class MarinadeStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "marinade_stake"
    @property
    def description(self) -> str: return "Stake SOL for mSOL via Marinade"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"amount": {"type": "number", "required": True}}


class MarinadeUnstakeAction(BuildableAction):
    @property
    def name(self) -> str: return "marinade_unstake"
    @property
    def description(self) -> str: return "Instantly unstake mSOL back to SOL via Marinade"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"amount": {"type": "number", "required": True, "description": "mSOL amount to unstake"}}


class MarinadeDelayedUnstakeAction(BuildableAction):
    @property
    def name(self) -> str: return "marinade_delayed_unstake"
    @property
    def description(self) -> str: return "Start a delayed (epoch) unstake of mSOL — cheaper but takes ~2 days"
    @property
    def aliases(self) -> list[str]: return ["marinade_delayed_withdrawal", "marinade_order_unstake"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"amount": {"type": "number", "required": True, "description": "mSOL amount"}}


class MarinadePlugin(BasePlugin):
    @property
    def id(self) -> str: return "marinade"
    @property
    def name(self) -> str: return "Marinade"
    @property
    def description(self) -> str: return "Marinade Finance liquid staking"
    @property
    def actions(self) -> list[PluginAction]:
        return [MarinadeStakeAction(), MarinadeUnstakeAction(), MarinadeDelayedUnstakeAction()]


# ============================================================================
# Magic Eden Plugin — COMING SOON
# ============================================================================

def _nft_coming_soon(platform: str) -> PluginResult:
    return PluginResult(
        success=False,
        error=(
            f"{platform} entegrasyonu yakında geliyor! "
            "Bu özellik henüz aktif değil. "
            "Gelişmelerden haberdar olmak için bizi X üzerinden takip edin."
        ),
    )


class MagicEdenListAction(BuildableAction):
    @property
    def name(self) -> str: return "me_list"
    @property
    def description(self) -> str: return "List an NFT for sale on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mintAddress": {"type": "string", "required": True},
            "price": {"type": "number", "required": True},
            "expiry": {"type": "number", "required": False},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenBuyAction(BuildableAction):
    @property
    def name(self) -> str: return "me_buy"
    @property
    def description(self) -> str: return "Buy an NFT on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mintAddress": {"type": "string", "required": True},
            "price": {"type": "number", "required": True},
            "seller": {"type": "string", "required": False},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenCancelListingAction(BuildableAction):
    @property
    def name(self) -> str: return "me_cancel_listing"
    @property
    def description(self) -> str: return "Cancel an NFT listing on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"mintAddress": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenMakeOfferAction(BuildableAction):
    @property
    def name(self) -> str: return "me_make_offer"
    @property
    def description(self) -> str: return "Place an offer on an NFT on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mintAddress": {"type": "string", "required": True},
            "price": {"type": "number", "required": True},
            "expiry": {"type": "number", "required": False},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenAcceptOfferAction(BuildableAction):
    @property
    def name(self) -> str: return "me_accept_offer"
    @property
    def description(self) -> str: return "Accept an offer on your NFT on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mintAddress": {"type": "string", "required": True},
            "buyer": {"type": "string", "required": True},
            "price": {"type": "number", "required": False},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenCancelOfferAction(BuildableAction):
    @property
    def name(self) -> str: return "me_cancel_offer"
    @property
    def description(self) -> str: return "Cancel an offer on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"mintAddress": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenCollectionInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "me_collection_info"
    @property
    def description(self) -> str: return "Get info about an NFT collection on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"collectionSymbol": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenNftInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "me_nft_info"
    @property
    def description(self) -> str: return "Get info about a specific NFT on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"mintAddress": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenWalletNftsAction(BuildableAction):
    @property
    def name(self) -> str: return "me_wallet_nfts"
    @property
    def description(self) -> str: return "Get all NFTs in a wallet on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenCollectionActivityAction(BuildableAction):
    @property
    def name(self) -> str: return "me_collection_activity"
    @property
    def description(self) -> str: return "Get recent activity for a Magic Eden collection"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"collectionSymbol": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenListingsAction(BuildableAction):
    @property
    def name(self) -> str: return "me_listings"
    @property
    def description(self) -> str: return "View current listings for a Magic Eden collection"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"collectionSymbol": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenOffersAction(BuildableAction):
    @property
    def name(self) -> str: return "me_offers"
    @property
    def description(self) -> str: return "View offers on a specific NFT or collection on Magic Eden"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mintAddress": {"type": "string", "required": False},
            "collectionSymbol": {"type": "string", "required": False},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")

class MagicEdenCollectionNftsAction(BuildableAction):
    @property
    def name(self) -> str: return "me_collection_nfts"
    @property
    def description(self) -> str: return "Get NFTs listed in a Magic Eden collection"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"collectionSymbol": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Magic Eden")


class MagicEdenPlugin(BasePlugin):
    @property
    def id(self) -> str: return "magic_eden"
    @property
    def name(self) -> str: return "Magic Eden"
    @property
    def description(self) -> str: return "Magic Eden NFT marketplace — COMING SOON"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            MagicEdenListAction(),
            MagicEdenBuyAction(),
            MagicEdenCancelListingAction(),
            MagicEdenMakeOfferAction(),
            MagicEdenAcceptOfferAction(),
            MagicEdenCancelOfferAction(),
            MagicEdenCollectionInfoAction(),
            MagicEdenNftInfoAction(),
            MagicEdenWalletNftsAction(),
            MagicEdenCollectionActivityAction(),
            MagicEdenListingsAction(),
            MagicEdenOffersAction(),
            MagicEdenCollectionNftsAction(),
        ]


# ============================================================================
# Tensor Plugin — NFT Marketplace — COMING SOON
# ============================================================================

class TensorBuyAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_buy"
    @property
    def description(self) -> str: return "Buy an NFT on Tensor marketplace"
    @property
    def aliases(self) -> list[str]: return ["tensor_purchase"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mintAddress": {"type": "string", "required": True},
            "maxPrice":    {"type": "number", "required": True},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorListAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_list"
    @property
    def description(self) -> str: return "List an NFT for sale on Tensor"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mintAddress": {"type": "string", "required": True},
            "price":       {"type": "number", "required": True},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorCancelListingAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_cancel_listing"
    @property
    def description(self) -> str: return "Cancel an active NFT listing on Tensor"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"mintAddress": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorMakeOfferAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_make_offer"
    @property
    def description(self) -> str: return "Place a collection-level bid/offer on Tensor"
    @property
    def aliases(self) -> list[str]: return ["tensor_bid", "tensor_offer"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "collectionSlug": {"type": "string", "required": True},
            "price":          {"type": "number", "required": True},
            "quantity":       {"type": "number", "required": False},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorCancelOfferAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_cancel_offer"
    @property
    def description(self) -> str: return "Cancel a Tensor collection bid by bid ID"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"bidId": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorCollectionInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_collection_info"
    @property
    def description(self) -> str: return "Get info about an NFT collection on Tensor"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"collectionSlug": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorNftInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_nft_info"
    @property
    def description(self) -> str: return "Get info about a specific NFT on Tensor"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"mintAddress": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorWalletNftsAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_wallet_nfts"
    @property
    def description(self) -> str: return "Get all NFTs in a wallet on Tensor"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorListingsAction(BuildableAction):
    @property
    def name(self) -> str: return "tensor_listings"
    @property
    def description(self) -> str: return "View current listings for a Tensor collection"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"collectionSlug": {"type": "string", "required": True}}
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return _nft_coming_soon("Tensor")


class TensorPlugin(BasePlugin):
    @property
    def id(self) -> str: return "tensor"
    @property
    def name(self) -> str: return "Tensor"
    @property
    def description(self) -> str: return "Tensor NFT marketplace — COMING SOON"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            TensorBuyAction(),
            TensorListAction(),
            TensorCancelListingAction(),
            TensorMakeOfferAction(),
            TensorCancelOfferAction(),
            TensorCollectionInfoAction(),
            TensorNftInfoAction(),
            TensorWalletNftsAction(),
            TensorListingsAction(),
        ]


# ============================================================================
# Jupiter Plugin — DCA and Limit Orders
# ============================================================================

_FREQUENCY_TO_SECONDS: dict[str, int] = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "monthly": 2592000,
    "biweekly": 1209600,
}


class JupiterDCAAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_dca"
    @property
    def description(self) -> str: return "Create a Jupiter DCA (dollar-cost averaging) recurring buy/sell order"
    @property
    def aliases(self) -> list[str]: return ["jup_dca", "dca_order", "dca"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint":       {"type": "string", "required": True,  "description": "Input token symbol or mint"},
            "outputMint":      {"type": "string", "required": True,  "description": "Output token symbol or mint"},
            "totalAmount":     {"type": "number", "required": True,  "description": "Total input amount to spend across all cycles"},
            "numberOfOrders":  {"type": "number", "required": True,  "description": "Number of DCA cycles (e.g. 10)"},
            "intervalSeconds": {"type": "number", "required": True,  "description": "Seconds between each cycle: 3600=hourly, 86400=daily, 604800=weekly, 2592000=monthly"},
            "startAt":         {"type": "number", "required": False, "description": "Unix timestamp to start. Omit to start immediately."},
            "minOutPerCycle":  {"type": "number", "required": False, "description": "Min output per cycle (price floor — skip cycle if output would be below this)"},
            "maxOutPerCycle":  {"type": "number", "required": False, "description": "Max output per cycle (price ceiling — skip cycle if output would exceed this)"},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        # Safety fallback: if LLM sends `frequency` instead of `intervalSeconds`, convert it.
        if "frequency" in params and "intervalSeconds" not in params:
            freq = str(params.pop("frequency")).lower()
            params["intervalSeconds"] = _FREQUENCY_TO_SECONDS.get(freq, 86400)
            logger.warning("DCA: converted frequency='%s' → intervalSeconds=%d", freq, params["intervalSeconds"])
        return await _build_action("dca", params)


class JupiterCancelDCAAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_cancel_dca"
    @property
    def description(self) -> str: return "Cancel an active Jupiter DCA order"
    @property
    def aliases(self) -> list[str]: return ["jup_cancel_dca", "cancel_dca"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"order": {"type": "string", "required": True, "description": "DCA order account address"}}


class JupiterLimitOrderAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_limit_order"
    @property
    def description(self) -> str: return "Place a Jupiter limit order — buy/sell at a specific target price"
    @property
    def aliases(self) -> list[str]: return ["jup_limit_order", "limit_order"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint":   {"type": "string", "required": True, "description": "Input token symbol or mint"},
            "outputMint":  {"type": "string", "required": True, "description": "Output token symbol or mint"},
            "amount":      {"type": "number", "required": True, "description": "Input token amount to spend"},
            "targetPrice": {"type": "number", "required": True, "description": "Output tokens per input token (e.g. sell 1 SOL → 200 USDC: targetPrice=200)"},
            "slippageBps": {"type": "number", "required": False, "default": 50},
            "expirySeconds": {"type": "number", "required": False, "description": "Order lifetime in seconds (omit = never expires)"},
        }


class JupiterCancelLimitOrderAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_cancel_limit_order"
    @property
    def description(self) -> str: return "Cancel a specific Jupiter limit order by order address"
    @property
    def aliases(self) -> list[str]: return ["jup_cancel_limit", "cancel_limit_order"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "order": {"type": "string", "required": True, "description": "Order account address to cancel"},
        }


class JupiterCancelAllLimitOrdersAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_cancel_all_limit_orders"
    @property
    def description(self) -> str: return "Cancel all open Jupiter limit orders for the user"
    @property
    def aliases(self) -> list[str]: return ["cancel_all_limit_orders", "cancel_all_orders"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class JupiterPlugin(BasePlugin):
    @property
    def id(self) -> str: return "jupiter"
    @property
    def name(self) -> str: return "Jupiter"
    @property
    def description(self) -> str: return "Jupiter DCA, limit orders, and market data"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            JupiterDCAAction(),
            JupiterCancelDCAAction(),
            JupiterLimitOrderAction(),
            JupiterCancelLimitOrderAction(),
            JupiterCancelAllLimitOrdersAction(),
            # Jupiter data queries
            JupDcaOrdersAction(),
            JupLimitOrdersQueryAction(),
            JupPriceAction(),
            JupTokenSearchAction(),
            JupTokensTagAction(),
            JupTokensRecentAction(),
            JupTokensTrendingAction(),
            JupPortfolioPositionsAction(),
            JupStakedJupAction(),
            JupLendPositionsAction(),
            JupLendEarningsAction(),
            JupPendingInvitesAction(),
            JupLendMarketsAction(),
        ]


# ── Jupiter data query actions ──────────────────────────────────────────────

class JupDcaOrdersAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_dca_orders"
    @property
    def description(self) -> str: return "List all active Jupiter DCA orders for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class JupLimitOrdersQueryAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_limit_orders"
    @property
    def description(self) -> str: return "List all active Jupiter limit orders for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class JupPriceAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_price"
    @property
    def description(self) -> str: return "Get current USD price of one or more tokens via Jupiter Price API"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"tokens": {"type": "string", "required": True, "description": "Comma-separated token mint addresses or symbols (e.g. SOL,USDC,BONK)"}}


class JupTokenSearchAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_token_search"
    @property
    def description(self) -> str: return "Search Jupiter token list by name or symbol"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "query": {"type": "string", "required": True, "description": "Token name or symbol to search"},
            "limit": {"type": "number", "required": False, "description": "Max results (default 10)"},
        }


class JupTokensTagAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_tokens_tag"
    @property
    def description(self) -> str: return "Get Jupiter tokens filtered by tag (e.g. community, strict, lst)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "tag":   {"type": "string", "required": True, "description": "Tag filter: strict, community, verified, lst, token-2022"},
            "limit": {"type": "number", "required": False},
        }


class JupTokensRecentAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_tokens_recent"
    @property
    def description(self) -> str: return "Get recently added tokens on Jupiter"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"limit": {"type": "number", "required": False, "description": "Max results"}}


class JupTokensTrendingAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_tokens_trending"
    @property
    def description(self) -> str: return "Get trending tokens on Jupiter by category and interval"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "category": {"type": "string", "required": True, "description": "Category: all, meme, defi, gaming, nft"},
            "interval": {"type": "string", "required": True, "description": "Time interval: 1h, 6h, 24h"},
            "limit":    {"type": "number", "required": False},
        }


class JupPortfolioPositionsAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_portfolio_positions"
    @property
    def description(self) -> str: return "Get Jupiter portfolio positions (DCA, limit orders, lend) for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet":    {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"},
            "platforms": {"type": "string", "required": False, "description": "Comma-separated platforms: dca,limit,lend"},
        }


class JupStakedJupAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_staked_jup"
    @property
    def description(self) -> str: return "Get staked JUP and active vote escrow info for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class JupLendPositionsAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_lend_positions"
    @property
    def description(self) -> str: return "Get all active Jupiter Lend (Earn) positions for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"wallet": {"type": "string", "required": False, "description": "Wallet address (default: connected wallet)"}}


class JupLendEarningsAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_lend_earnings"
    @property
    def description(self) -> str: return "Get Jupiter Lend earnings history for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet":    {"type": "string", "required": False, "description": "Wallet address"},
            "positions": {"type": "string", "required": False, "description": "Comma-separated position addresses to filter"},
        }


class JupPendingInvitesAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_pending_invites"
    @property
    def description(self) -> str: return "Get pending Jupiter referral/invite codes for a wallet"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Wallet address"},
            "page":   {"type": "number", "required": False},
        }


class JupLendMarketsAction(BuildableAction):
    @property
    def name(self) -> str: return "jup_lend_markets"
    @property
    def description(self) -> str: return "Get all available Jupiter Lend markets with APY and liquidity"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


# ============================================================================
# Pump.fun Plugin — Token Launch + Bonding Curve Trading
# ============================================================================

class PumpFunLaunchAction(BuildableAction):
    @property
    def name(self) -> str: return "pumpfun_launch"
    @property
    def description(self) -> str: return "Launch a new token on Pump.fun with optional initial buy"
    @property
    def aliases(self) -> list[str]: return ["launch_token", "pump_launch", "create_token"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "name":            {"type": "string", "required": True, "description": "Token name (max 32 chars)"},
            "symbol":          {"type": "string", "required": True, "description": "Token symbol (max 10 chars, alphanumeric)"},
            "description":     {"type": "string", "required": True, "description": "Token description (max 500 chars)"},
            "imageUrl":        {"type": "string", "required": True, "description": "URL of the token image"},
            "initialBuyAmount": {"type": "number", "required": False, "description": "SOL to spend on initial buy (0–100 SOL)"},
            "twitter":         {"type": "string", "required": False},
            "telegram":        {"type": "string", "required": False},
            "website":         {"type": "string", "required": False},
        }


class PumpFunBuyAction(BuildableAction):
    @property
    def name(self) -> str: return "pumpfun_buy"
    @property
    def description(self) -> str: return "Buy tokens on a Pump.fun bonding curve with SOL"
    @property
    def aliases(self) -> list[str]: return ["pump_buy"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mint":       {"type": "string", "required": True, "description": "Token mint address"},
            "solAmount":  {"type": "number", "required": True, "description": "SOL amount to spend"},
            "slippageBps": {"type": "number", "required": False, "default": 1000, "description": "Slippage in bps (default 10%)"},
        }


class PumpFunSellAction(BuildableAction):
    @property
    def name(self) -> str: return "pumpfun_sell"
    @property
    def description(self) -> str: return "Sell tokens on a Pump.fun bonding curve for SOL"
    @property
    def aliases(self) -> list[str]: return ["pump_sell"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mint":        {"type": "string", "required": True, "description": "Token mint address"},
            "tokenAmount": {"type": "number", "required": True, "description": "Token amount to sell"},
            "slippageBps": {"type": "number", "required": False, "default": 1000},
        }


class PumpFunTokenInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "pumpfun_token_info"
    @property
    def description(self) -> str: return "Get PumpFun token info: bonding curve progress, holders, price, market cap"
    @property
    def aliases(self) -> list[str]: return ["pump_info", "pumpfun_info"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mint": {"type": "string", "required": True, "description": "Token mint address"},
        }


class PumpFunTrendingAction(BuildableAction):
    @property
    def name(self) -> str: return "pumpfun_trending"
    @property
    def description(self) -> str: return "Get trending PumpFun tokens sorted by market cap"
    @property
    def aliases(self) -> list[str]: return ["pump_trending", "pumpfun_top"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class PumpFunPlugin(BasePlugin):
    @property
    def id(self) -> str: return "pumpfun"
    @property
    def name(self) -> str: return "Pump.fun"
    @property
    def description(self) -> str: return "Pump.fun token launchpad — launch tokens and trade on bonding curves"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            PumpFunLaunchAction(),
            PumpFunBuyAction(),
            PumpFunSellAction(),
            PumpFunTokenInfoAction(),
            PumpFunTrendingAction(),
        ]


# ============================================================================
# JupSOL Plugin — Jupiter Liquid Staking
# ============================================================================

class JupSolStakeAction(BuildableAction):
    @property
    def name(self) -> str: return "jupsol_stake"
    @property
    def description(self) -> str: return "Stake SOL for JupSOL (Jupiter's liquid staking token, 0% fee)"
    @property
    def aliases(self) -> list[str]: return ["stake_jupsol", "jup_stake"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount":      {"type": "number", "required": True, "description": "SOL amount to stake"},
            "slippageBps": {"type": "number", "required": False, "default": 50},
        }


class JupSolUnstakeAction(BuildableAction):
    @property
    def name(self) -> str: return "jupsol_unstake"
    @property
    def description(self) -> str: return "Unstake JupSOL back to SOL via Jupiter"
    @property
    def aliases(self) -> list[str]: return ["unstake_jupsol", "jup_unstake"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount":      {"type": "number", "required": True, "description": "JupSOL amount to unstake"},
            "slippageBps": {"type": "number", "required": False, "default": 50},
        }


class JupSolPlugin(BasePlugin):
    @property
    def id(self) -> str: return "jupsol"
    @property
    def name(self) -> str: return "JupSOL"
    @property
    def description(self) -> str: return "Jupiter liquid staking — stake SOL for JupSOL with 0% fee"
    @property
    def actions(self) -> list[PluginAction]:
        return [JupSolStakeAction(), JupSolUnstakeAction()]


# ============================================================================
# Token Utility Plugin — Burn + Transfer
# ============================================================================

class TokenBurnAction(BuildableAction):
    @property
    def name(self) -> str: return "burn"
    @property
    def description(self) -> str: return "Burn SPL tokens permanently. Use 'all' to burn full balance and close the token account."
    @property
    def aliases(self) -> list[str]: return ["burn_token", "token_burn"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mint":      {"type": "string", "required": True, "description": "Token symbol (e.g. BONK) or mint address"},
            "amount":    {"type": "string", "required": True, "description": "Amount to burn, or 'all' to burn entire balance"},
            "closeMint": {"type": "boolean", "required": False, "default": False, "description": "Close token account after burn to reclaim rent"},
        }


class CloseAccountsAction(BuildableAction):
    @property
    def name(self) -> str: return "close_accounts"
    @property
    def description(self) -> str: return "Batch-close empty SPL token accounts to reclaim SOL rent (~0.002 SOL per account). Only accounts with zero balance are closed."
    @property
    def aliases(self) -> list[str]: return ["close_empty_accounts", "reclaim_rent", "close_token_accounts"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "mints": {
                "type": "array",
                "required": True,
                "description": "List of token mint addresses or symbols (e.g. ['BONK', 'WIF']) whose empty ATAs should be closed. Max 15 per transaction.",
            },
        }


class ScanEmptyAccountsAction(BuildableAction):
    @property
    def name(self) -> str: return "scan_empty_accounts"
    @property
    def description(self) -> str: return "Scan wallet for empty token accounts (zero balance) and show how much SOL can be reclaimed by closing them."
    @property
    def aliases(self) -> list[str]: return ["find_empty_accounts", "check_dust", "wallet_cleanup", "scan_dust"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class TokenTransferAction(BuildableAction):
    @property
    def name(self) -> str: return "transfer"
    @property
    def description(self) -> str: return "Transfer SOL or any SPL token to another wallet"
    @property
    def aliases(self) -> list[str]: return ["send", "send_token", "send_sol"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "to":     {"type": "string", "required": True, "description": "Recipient wallet address"},
            "amount": {"type": "number", "required": True, "description": "Amount to send"},
            "token":  {"type": "string", "required": True, "description": "Token symbol (SOL, USDC, etc.) or mint address"},
        }


class ClaimAction(BuildableAction):
    @property
    def name(self) -> str: return "claim"
    @property
    def description(self) -> str: return "Get protocol-specific guidance on claiming rewards, airdrops, or yields. Returns instructions and links — not all protocols support on-chain claiming via OPRAI."
    @property
    def aliases(self) -> list[str]: return ["claim_rewards", "claim_airdrop", "claim_yield"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "protocol": {
                "type": "string",
                "required": True,
                "description": "Protocol name: marinade, jito, jupiter, kamino, orca, meteora, marginfi, etc.",
            },
            "type": {
                "type": "string",
                "required": False,
                "default": "rewards",
                "description": "Claim type: airdrop, staking_rewards, lending_yield, fees",
            },
        }


class VoteAction(BuildableAction):
    @property
    def name(self) -> str: return "vote"
    @property
    def description(self) -> str: return "Get voting guidance and governance portal links for a protocol. On-chain voting requires the protocol's own governance app."
    @property
    def aliases(self) -> list[str]: return ["governance_vote", "dao_vote", "vote_proposal"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "protocol": {
                "type": "string",
                "required": True,
                "description": "Protocol: jupiter, marinade, kamino, orca, marginfi, etc.",
            },
            "proposal": {
                "type": "string",
                "required": False,
                "description": "Proposal ID or title",
            },
            "choice": {
                "type": "string",
                "required": False,
                "default": "yes",
                "description": "Vote choice: yes, no, abstain",
            },
        }


class TokenUtilityPlugin(BasePlugin):
    @property
    def id(self) -> str: return "token_utility"
    @property
    def name(self) -> str: return "Token Utilities"
    @property
    def description(self) -> str: return "Transfer, burn, manage SPL token accounts, claim rewards, and governance voting"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            TokenBurnAction(),
            CloseAccountsAction(),
            ScanEmptyAccountsAction(),
            TokenTransferAction(),
            ClaimAction(),
            VoteAction(),
        ]


# ============================================================================
# Jupiter Lend Plugin — Earn (Deposit/Withdraw) + Borrow/Repay
# ============================================================================

class JupiterLendDepositAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_lend_deposit"
    @property
    def description(self) -> str: return "Deposit tokens into Jupiter Lend to earn yield (USDC, USDT, jupSOL, jitoSOL, EURC)"
    @property
    def aliases(self) -> list[str]: return ["jup_lend", "jup_earn", "jupiter_earn"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":     {"type": "string", "required": True, "description": "Token to deposit: USDC, USDT, jupSOL, jitoSOL, EURC"},
            "amount":    {"type": "number", "required": True},
            "operation": {"type": "string", "required": False, "default": "deposit"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        params.setdefault("operation", "deposit")
        return await _build_action("lend", params)


class JupiterLendWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_lend_withdraw"
    @property
    def description(self) -> str: return "Withdraw deposited tokens from Jupiter Lend"
    @property
    def aliases(self) -> list[str]: return ["jup_withdraw_lend"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":     {"type": "string", "required": True, "description": "Token to withdraw (USDC, USDT, jupSOL, jitoSOL, EURC)"},
            "amount":    {"type": "number", "required": True},
            "operation": {"type": "string", "required": False, "default": "withdraw"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        params["operation"] = "withdraw"
        return await _build_action("withdraw_lend", params)


class JupiterBorrowAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_borrow"
    @property
    def description(self) -> str: return "Borrow tokens from Jupiter Lend against collateral"
    @property
    def aliases(self) -> list[str]: return ["jup_borrow"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":      {"type": "string", "required": True, "description": "Token to borrow (USDC, USDT)"},
            "amount":     {"type": "number", "required": True},
            "collateral": {"type": "string", "required": False, "description": "Collateral token (e.g. SOL, JLP, jupSOL, jitoSOL). UI auto-selects if omitted."},
            "operation":  {"type": "string", "required": False, "default": "borrow"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        params["operation"] = "borrow"
        return await _build_action("borrow", params)


class JupiterRepayAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_repay"
    @property
    def description(self) -> str: return "Repay a Jupiter Lend borrow position"
    @property
    def aliases(self) -> list[str]: return ["jup_repay"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":      {"type": "string", "required": True, "description": "Token to repay (USDC, USDT)"},
            "amount":     {"type": "number", "required": True},
            "collateral": {"type": "string", "required": False, "description": "Collateral token of the position (e.g. SOL, jupSOL)"},
            "operation":  {"type": "string", "required": False, "default": "repay"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        params["operation"] = "repay"
        return await _build_action("repay", params)


class JupiterLendPlugin(BasePlugin):
    @property
    def id(self) -> str: return "jupiter_lend"
    @property
    def name(self) -> str: return "Jupiter Lend"
    @property
    def description(self) -> str: return "Jupiter Lend — earn yield on USDC/USDT/jupSOL and borrow against collateral"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            JupiterLendDepositAction(),
            JupiterLendWithdrawAction(),
            JupiterBorrowAction(),
            JupiterRepayAction(),
        ]


# ============================================================================
# Jupiter Perp Plugin — Perpetuals + JLP Liquidity
# ============================================================================

class JupiterPerpOpenAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_perp_open"
    @property
    def description(self) -> str: return "Open a Jupiter Perpetuals position (SOL, wETH, or wBTC)"
    @property
    def aliases(self) -> list[str]: return ["jup_perp_open", "jup_perp_long", "jup_perp_short"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market":           {"type": "string", "required": True, "description": "SOL, wETH, or wBTC"},
            "side":             {"type": "string", "required": True, "description": "long or short"},
            "collateralAmount": {"type": "number", "required": True, "description": "Collateral amount (e.g. 2 for 2 SOL). Long=SOL collateral, short=USDC."},
            "leverage":         {"type": "number", "required": False, "default": 2, "description": "Leverage multiplier (1-100, default: 2)"},
            "sizeUsd":          {"type": "number", "required": False, "description": "Position size in USD. Optional — defaults to collateralAmount × leverage."},
            "collateralToken":  {"type": "string", "required": False, "description": "Override collateral token (SOL, USDC, USDT). Auto-selected if omitted."},
            "operation":        {"type": "string", "required": False, "default": "open"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        params["operation"] = "open"
        return await _build_action("perp_open", params)


class JupiterPerpCloseAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_perp_close"
    @property
    def description(self) -> str: return "Close a Jupiter Perpetuals position"
    @property
    def aliases(self) -> list[str]: return ["jup_perp_close"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "market":           {"type": "string", "required": True, "description": "SOL, wETH, or wBTC"},
            "side":             {"type": "string", "required": True, "description": "long or short"},
            "collateralAmount": {"type": "number", "required": False, "default": 0, "description": "Amount to close in collateral units. 0 = close entire position."},
            "operation":        {"type": "string", "required": False, "default": "close"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        params["operation"] = "close"
        if "collateralAmount" not in params:
            params["collateralAmount"] = 0
        return await _build_action("perp_close", params)


class JupiterJlpAddAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_jlp_add"
    @property
    def description(self) -> str: return "Add liquidity to Jupiter Liquidity Pool (JLP) and earn trading fees"
    @property
    def aliases(self) -> list[str]: return ["jlp_add", "jup_lp_add"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":  {"type": "string", "required": False, "default": "SOL", "description": "Token to deposit: SOL, USDC, USDT, wETH, wBTC"},
            "amount": {"type": "number", "required": True, "description": "Amount of the token to add"},
            "operation": {"type": "string", "required": False, "default": "add"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        params["operation"] = "add"
        return await _build_action("jlp_add", params)


class JupiterJlpRemoveAction(BuildableAction):
    @property
    def name(self) -> str: return "jupiter_jlp_remove"
    @property
    def description(self) -> str: return "Remove liquidity from Jupiter Liquidity Pool (JLP)"
    @property
    def aliases(self) -> list[str]: return ["jlp_remove", "jup_lp_remove"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {"type": "number", "required": True, "description": "JLP token amount to burn"},
            "token":  {"type": "string", "required": False, "default": "SOL", "description": "Token to receive back: SOL, USDC, USDT"},
            "operation": {"type": "string", "required": False, "default": "remove"},
        }
    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        params["operation"] = "remove"
        return await _build_action("jlp_remove", params)


class JupiterPerpPlugin(BasePlugin):
    @property
    def id(self) -> str: return "jupiter_perp"
    @property
    def name(self) -> str: return "Jupiter Perpetuals"
    @property
    def description(self) -> str: return "Jupiter Perps — trade SOL/ETH/BTC perpetuals and provide JLP liquidity"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            JupiterPerpOpenAction(),
            JupiterPerpCloseAction(),
            JupiterJlpAddAction(),
            JupiterJlpRemoveAction(),
        ]


# ============================================================================
# Streamflow Plugin — Token Streaming / Vesting
# ============================================================================

class StreamflowCreateStreamAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_create"
    @property
    def description(self) -> str: return "Create a token payment stream or vesting schedule on Streamflow"
    @property
    def aliases(self) -> list[str]: return ["stream_create", "create_vesting", "vesting_create"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            # Required
            "recipient":             {"type": "string", "required": True,  "description": "Recipient wallet address"},
            "mint":                  {"type": "string", "required": True,  "description": "Token mint address or symbol (SOL, USDC, USDT, BONK, JUP, JTO, PYTH, or any mint)"},
            "amount":                {"type": "number", "required": True,  "description": "Total tokens to stream (human-readable, e.g. 1000)"},
            "period":                {"type": "number", "required": True,  "description": "Unlock interval in seconds (e.g. 3600=hourly, 86400=daily, 604800=weekly, 2592000=monthly)"},
            "amountPerPeriod":       {"type": "number", "required": True,  "description": "Tokens unlocked each period (e.g. total/periods)"},
            # Timing
            "start":                 {"type": "number", "required": False, "description": "Start Unix timestamp. Omit = now"},
            "cliff":                 {"type": "number", "required": False, "description": "Cliff Unix timestamp — tokens locked until this point"},
            "cliffAmount":           {"type": "number", "required": False, "description": "Tokens unlocked immediately at cliff (default 0)"},
            # Stream config
            "name":                  {"type": "string", "required": False, "description": "Human-readable stream label (default: 'OPRAI Stream')"},
            "canTopup":              {"type": "boolean","required": False, "description": "Allow sender to add more tokens (extends duration). Default false"},
            "cancelableBySender":    {"type": "boolean","required": False, "description": "Allow sender to cancel stream. Default true"},
            "cancelableByRecipient": {"type": "boolean","required": False, "description": "Allow recipient to cancel stream. Default false"},
            "transferableBySender":  {"type": "boolean","required": False, "description": "Allow sender to transfer stream to new recipient. Default true"},
            "transferableByRecipient":{"type":"boolean","required": False, "description": "Allow recipient to transfer stream. Default false"},
            "automaticWithdrawal":   {"type": "boolean","required": False, "description": "Auto-send tokens to recipient each period (no claim needed). Default false"},
            "withdrawalFrequency":   {"type": "number", "required": False, "description": "Auto-withdrawal interval in seconds (requires automaticWithdrawal=true). Default = period"},
            "partner":               {"type": "string", "required": False, "description": "Partner wallet address for fee sharing (optional)"},
            "isNative":              {"type": "boolean","required": False, "description": "Use native SOL instead of wSOL for SOL streams. Default false"},
        }


class StreamflowCreateMultipleAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_create_multiple"
    @property
    def description(self) -> str: return "Batch-create multiple Streamflow token streams to different recipients at once"
    @property
    def aliases(self) -> list[str]: return ["stream_create_multiple", "batch_stream", "bulk_vesting"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            # Required
            "recipients":            {"type": "array",  "required": True,  "description": "List of recipient objects: [{recipient, amount, amountPerPeriod, name?, cliffAmount?}, ...]"},
            "mint":                  {"type": "string", "required": True,  "description": "Token mint address or symbol (shared across all streams)"},
            "period":                {"type": "number", "required": True,  "description": "Unlock interval in seconds (shared, e.g. 86400=daily)"},
            # Timing
            "start":                 {"type": "number", "required": False, "description": "Start Unix timestamp (shared). Omit = now"},
            "cliff":                 {"type": "number", "required": False, "description": "Cliff Unix timestamp (shared)"},
            # Stream config
            "canTopup":              {"type": "boolean","required": False, "description": "Allow sender to top up. Default false"},
            "cancelableBySender":    {"type": "boolean","required": False, "description": "Allow sender to cancel. Default true"},
            "cancelableByRecipient": {"type": "boolean","required": False, "description": "Allow recipient to cancel. Default false"},
            "transferableBySender":  {"type": "boolean","required": False, "description": "Allow sender to transfer. Default true"},
            "transferableByRecipient":{"type":"boolean","required": False, "description": "Allow recipient to transfer. Default false"},
            "automaticWithdrawal":   {"type": "boolean","required": False, "description": "Auto-withdraw each period. Default false"},
            "withdrawalFrequency":   {"type": "number", "required": False, "description": "Auto-withdrawal frequency in seconds"},
            "partner":               {"type": "string", "required": False, "description": "Partner wallet for fee sharing"},
            "isNative":              {"type": "boolean","required": False, "description": "Use native SOL. Default false"},
        }


class StreamflowCancelStreamAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_cancel"
    @property
    def description(self) -> str: return "Cancel an active Streamflow payment stream"
    @property
    def aliases(self) -> list[str]: return ["stream_cancel", "cancel_stream"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {"streamId": {"type": "string", "required": True, "description": "Stream account address (from streamflow_list or app.streamflow.finance)"}}


class StreamflowWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_withdraw"
    @property
    def description(self) -> str: return "Withdraw unlocked (vested) tokens from a Streamflow stream"
    @property
    def aliases(self) -> list[str]: return ["stream_withdraw", "withdraw_stream", "claim_stream"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "streamId": {"type": "string", "required": True,  "description": "Stream account address"},
            "amount":   {"type": "number", "required": False, "description": "Amount to withdraw (omit = all unlocked/vested tokens)"},
        }


class StreamflowTransferStreamAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_transfer"
    @property
    def description(self) -> str: return "Transfer a Streamflow stream ownership to a new recipient"
    @property
    def aliases(self) -> list[str]: return ["stream_transfer", "transfer_stream"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "streamId":     {"type": "string", "required": True, "description": "Stream account address"},
            "newRecipient": {"type": "string", "required": True, "description": "New recipient wallet address"},
        }


class StreamflowTopupAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_topup"
    @property
    def description(self) -> str: return "Add tokens to an existing Streamflow stream to extend its duration"
    @property
    def aliases(self) -> list[str]: return ["stream_topup", "topup_stream", "extend_stream"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "streamId": {"type": "string", "required": True, "description": "Stream account address"},
            "amount":   {"type": "number", "required": True, "description": "Additional tokens to add (extends stream duration at same rate)"},
        }


class StreamflowUpdateAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_update"
    @property
    def description(self) -> str: return "Update mutable parameters of an existing Streamflow stream"
    @property
    def aliases(self) -> list[str]: return ["stream_update", "update_stream"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "streamId":              {"type": "string", "required": True,  "description": "Stream account address"},
            "automaticWithdrawal":   {"type": "boolean","required": False, "description": "Enable/disable automatic withdrawal"},
            "withdrawalFrequency":   {"type": "number", "required": False, "description": "New auto-withdrawal frequency in seconds"},
            "transferableBySender":  {"type": "boolean","required": False, "description": "Enable/disable transfer by sender"},
            "transferableByRecipient":{"type":"boolean","required": False, "description": "Enable/disable transfer by recipient"},
        }


class StreamflowGetOneAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_get_one"
    @property
    def description(self) -> str: return "Get details of a specific Streamflow stream by its account address"
    @property
    def aliases(self) -> list[str]: return ["stream_info", "stream_details", "streamflow_info"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "streamId": {"type": "string", "required": True, "description": "Stream account address to query"},
        }


class StreamflowListAction(BuildableAction):
    @property
    def name(self) -> str: return "streamflow_list"
    @property
    def description(self) -> str: return "List active Streamflow streams for the connected wallet, optionally filtered by direction"
    @property
    def aliases(self) -> list[str]: return ["streamflow_streams", "list_streams", "my_streams"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "direction": {
                "type": "string",
                "required": False,
                "default": "all",
                "description": "Filter by role: 'incoming' (wallet is recipient), 'outgoing' (wallet is sender), 'all' (default)",
            },
        }


class CrossChainSwapAction(BuildableAction):
    @property
    def name(self) -> str: return "cross_chain_swap"
    @property
    def description(self) -> str: return "Bridge tokens cross-chain via Relay, Wormhole, DeBridge, or Squid"
    @property
    def aliases(self) -> list[str]: return ["bridge", "cross_chain_bridge", "relay_bridge"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "fromToken":  {"type": "string", "required": True, "description": "Source token symbol or address"},
            "toToken":    {"type": "string", "required": True, "description": "Destination token symbol or address"},
            "amount":     {"type": "number", "required": True},
            "fromChain":  {"type": "number", "required": True, "description": "Source chain ID (900=Solana, 1=ETH, 42161=Arbitrum, 8453=Base, 10=Optimism, 137=Polygon, 56=BSC)"},
            "toChain":    {"type": "number", "required": True, "description": "Destination chain ID"},
            "provider":   {"type": "string", "required": False, "default": "relay", "description": "Bridge provider: relay (default), wormhole, debridge, squid"},
            "recipient":  {"type": "string", "required": False, "description": "Destination address (defaults to sender)"},
        }


class RelayPlugin(BasePlugin):
    @property
    def id(self) -> str: return "relay"
    @property
    def name(self) -> str: return "Relay"
    @property
    def description(self) -> str: return "Relay cross-chain bridge — Solana ↔ EVM chains via Relay, Wormhole, or DeBridge"
    @property
    def actions(self) -> list[PluginAction]:
        return [CrossChainSwapAction()]


class SquidBridgeAction(BuildableAction):
    @property
    def name(self) -> str: return "squid_bridge"
    @property
    def description(self) -> str: return "Cross-chain swap via Squid Protocol v2 (Axelar GMP) — Solana ↔ Ethereum, Arbitrum, Base, Optimism, Polygon, BSC, Avalanche, Linea, Scroll"
    @property
    def aliases(self) -> list[str]: return ["squid_swap", "squid_cross_chain"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            # ── Required ──────────────────────────────────────────────────────
            "originChainId": {
                "type": "number", "required": True,
                "description": "Source chain numeric ID: 7565164=Solana, 1=Ethereum, 56=BSC, 137=Polygon, 43114=Avalanche, 42161=Arbitrum, 10=Optimism, 8453=Base, 59144=Linea, 534352=Scroll",
            },
            "destinationChainId": {
                "type": "number", "required": True,
                "description": "Destination chain numeric ID (same values as originChainId)",
            },
            "originToken": {
                "type": "string", "required": True,
                "description": "Source token symbol (SOL, USDC, USDT, JUP, ETH, BNB, MATIC, AVAX, WBTC…) or full contract address",
            },
            "destinationToken": {
                "type": "string", "required": True,
                "description": "Destination token symbol or full contract address on destination chain",
            },
            "amount": {
                "type": "number", "required": True,
                "description": "Human-readable amount to bridge (e.g. 1.5 — not in lamports/wei)",
            },
            # ── Optional ──────────────────────────────────────────────────────
            "recipient": {
                "type": "string", "required": False,
                "description": "Destination wallet address. Defaults to sender's address if omitted.",
            },
            "slippage": {
                "type": "number", "required": False,
                "description": "Max slippage in percent (e.g. 0.5 = 0.5%). If omitted, Squid auto-calculates optimal slippage.",
            },
            "enableExpress": {
                "type": "boolean", "required": False,
                "description": "true = use express route (Chainflip/CCTP) for ~2-10 min bridging vs ~20 min standard. User must explicitly request this.",
            },
            "receiveGasOnDestination": {
                "type": "boolean", "required": False,
                "description": "true = airdrop a small amount of native gas token at destination so user can transact there immediately.",
            },
            "prefer": {
                "type": "string", "required": False,
                "description": "Preferred bridge type(s), comma-separated: chainflip, cctp, axelar, hyperlane. Leave empty for auto-selection.",
            },
            "bypass": {
                "type": "string", "required": False,
                "description": "Bridge type(s) to exclude, comma-separated: chainflip, cctp, axelar, hyperlane.",
            },
            "enableBoost": {
                "type": "boolean", "required": False,
                "description": "true = Squid Boost for faster Axelar confirmation. Recommended. Default true.",
            },
            "collectFees": {
                "type": "object", "required": False,
                "description": "Integrator fee collection: {integratorAddress: '0x...', fee: 30} where fee is basis points (30=0.3%). Only for integrators.",
            },
            "postHook": {
                "type": "object", "required": False,
                "description": (
                    "Execute on-chain calls on destination chain after tokens arrive. "
                    "Use for: auto-stake, auto-lend (Aave), NFT purchase, any contract interaction. "
                    "Format: {chainType: 'evm', calls: [{callType: 0|1, target: '0x...', value: '0', callData: '0x...', estimatedGas: '200000', payload?: {tokenAddress, inputPos}}]}. "
                    "callType 1 (FULL_TOKEN_BALANCE) = use entire received token balance."
                ),
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        # Route directly to the Rust `squid` handler (not relay/cross_chain_swap)
        return await _build_action("squid", params)


class SquidStatusAction(BuildableAction):
    @property
    def name(self) -> str: return "squid_status"
    @property
    def description(self) -> str: return "Check Squid cross-chain transaction status via Coral V2 tracking"
    @property
    def aliases(self) -> list[str]: return ["squid_check_status", "squid_tx_status"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "transactionId": {
                "type": "string", "required": True,
                "description": "Source chain transaction hash from the executed Squid bridge",
            },
            "quoteId": {
                "type": "string", "required": True,
                "description": "quoteId from the original route response. Required for Coral V2 status tracking.",
            },
            "requestId": {
                "type": "string", "required": False,
                "description": "Legacy requestId from route response (fallback if quoteId unavailable).",
            },
            "integratorId": {
                "type": "string", "required": False,
                "description": "Your integrator ID for volume attribution.",
            },
            "bridgeType": {
                "type": "string", "required": False,
                "description": "Bridge type used: chainflip, chainflipmultihop, axelar, cctp, hyperlane. Leave empty for auto-detect.",
            },
        }



class SquidPlugin(BasePlugin):
    @property
    def id(self) -> str: return "squid"
    @property
    def name(self) -> str: return "Squid Protocol"
    @property
    def description(self) -> str: return "Squid Router v2 — cross-chain swaps via Axelar GMP with post-hook support, express routes, and Coral V2 status tracking"
    @property
    def actions(self) -> list[PluginAction]:
        return [SquidBridgeAction(), SquidStatusAction()]


class StreamflowPlugin(BasePlugin):
    @property
    def id(self) -> str: return "streamflow"
    @property
    def name(self) -> str: return "Streamflow"
    @property
    def description(self) -> str: return "Streamflow — token streaming, vesting schedules, and payroll on Solana. Full SDK: create, cancel, withdraw, transfer, topup, update, getOne, createMultiple."
    @property
    def actions(self) -> list[PluginAction]:
        return [
            StreamflowCreateStreamAction(),
            StreamflowCreateMultipleAction(),
            StreamflowCancelStreamAction(),
            StreamflowWithdrawAction(),
            StreamflowTransferStreamAction(),
            StreamflowTopupAction(),
            StreamflowUpdateAction(),
            StreamflowGetOneAction(),
            StreamflowListAction(),
        ]


# ============================================================================
# Solend Plugin
# ============================================================================

class SolendDepositAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_deposit"
    @property
    def description(self) -> str: return "Deposit tokens into Solend lending pool to earn interest"
    @property
    def aliases(self) -> list[str]: return ["solend_supply", "solend_lend"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":  {"type": "string", "required": True, "description": "Token symbol (SOL, USDC, USDT, …) or mint address"},
            "amount": {"type": "number", "required": True},
        }


class SolendWithdrawAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_withdraw"
    @property
    def description(self) -> str: return "Withdraw deposited tokens from Solend lending pool"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":  {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
        }


class SolendBorrowAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_borrow"
    @property
    def description(self) -> str: return "Borrow tokens from Solend against deposited collateral"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":  {"type": "string", "required": True, "description": "Token to borrow"},
            "amount": {"type": "number", "required": True},
        }


class SolendRepayAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_repay"
    @property
    def description(self) -> str: return "Repay borrowed tokens on Solend"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":  {"type": "string", "required": True, "description": "Token to repay"},
            "amount": {"type": "number", "required": True},
        }


class SolendAddCollateralAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_add_collateral"
    @property
    def description(self) -> str: return "Add collateral to a Solend lending position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":  {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
        }


class SolendWithdrawCollateralAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_withdraw_collateral"
    @property
    def description(self) -> str: return "Withdraw collateral from a Solend lending position"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token":  {"type": "string", "required": True},
            "amount": {"type": "number", "required": True},
        }


class SolendLiquidateAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_liquidate"
    @property
    def description(self) -> str: return "Liquidate an underwater Solend position (advanced)"
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "borrowerWallet":    {"type": "string", "required": True, "description": "Wallet address of the borrower to liquidate"},
            "tokenToRepay":      {"type": "string", "required": True, "description": "Token to repay on behalf of borrower"},
            "collateralToClaim": {"type": "string", "required": True, "description": "Collateral token to claim"},
            "amount":            {"type": "number", "required": True},
        }


class SolendUserInfoAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_user_info"
    @property
    def description(self) -> str: return "Get Solend positions for a wallet: deposits, borrows, health factor"
    @property
    def aliases(self) -> list[str]: return ["solend_positions", "solend_account"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "wallet": {"type": "string", "required": False, "description": "Wallet address (defaults to connected wallet)"},
        }


class SolendMarketAction(BuildableAction):
    @property
    def name(self) -> str: return "solend_market"
    @property
    def description(self) -> str: return "Get Solend market data: reserve rates, TVL, and available assets"
    @property
    def aliases(self) -> list[str]: return ["solend_reserves", "solend_rates"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}


class SolendPlugin(BasePlugin):
    @property
    def id(self) -> str: return "solend"
    @property
    def name(self) -> str: return "Solend"
    @property
    def description(self) -> str: return "Solend — decentralized lending and borrowing protocol on Solana"
    @property
    def actions(self) -> list[PluginAction]:
        return [
            SolendDepositAction(),
            SolendWithdrawAction(),
            SolendBorrowAction(),
            SolendRepayAction(),
            SolendAddCollateralAction(),
            SolendWithdrawCollateralAction(),
            SolendLiquidateAction(),
            SolendUserInfoAction(),
            SolendMarketAction(),
        ]


# ============================================================================
# DeBridge Plugin — cross-chain bridge
# ============================================================================

class DebridgeAction(BuildableAction):
    @property
    def name(self) -> str: return "debridge"
    @property
    def description(self) -> str: return "Bridge tokens cross-chain via DeBridge Protocol (Solana ↔ EVM chains)"
    @property
    def aliases(self) -> list[str]: return ["debridge_bridge", "debridge_swap"]
    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "originChainId":      {"type": "number", "required": True, "description": "Source chain ID (7565164=Solana, 1=ETH, 42161=Arbitrum, 8453=Base, 10=Optimism, 137=Polygon, 56=BSC)"},
            "destinationChainId": {"type": "number", "required": True, "description": "Destination chain ID"},
            "originCurrency":     {"type": "string", "required": True, "description": "Source token symbol or address"},
            "destinationCurrency":{"type": "string", "required": True, "description": "Destination token symbol or address"},
            "amount":             {"type": "number", "required": True, "description": "Amount to bridge"},
            "recipient":          {"type": "string", "required": False, "description": "Recipient address (defaults to sender)"},
            "slippageBps":        {"type": "number", "required": False, "description": "Slippage in bps (default 100)"},
        }


class DebridgePlugin(BasePlugin):
    @property
    def id(self) -> str: return "debridge"
    @property
    def name(self) -> str: return "DeBridge"
    @property
    def description(self) -> str: return "DeBridge cross-chain messaging and token bridge — Solana ↔ EVM"
    @property
    def actions(self) -> list[PluginAction]:
        return [DebridgeAction()]


# Export all plugins
ALL_PLUGINS = [
    MeteoraPlugin,
    MarginfiPlugin,
    OrcaPlugin,
    KaminoPlugin,
    JitoPlugin,

    RaydiumPlugin,
    MarinadePlugin,
    MagicEdenPlugin,
    TensorPlugin,
    JupiterPlugin,
    PumpFunPlugin,
    JupSolPlugin,
    TokenUtilityPlugin,
    JupiterLendPlugin,
    JupiterPerpPlugin,
    RelayPlugin,
    SquidPlugin,
    StreamflowPlugin,
    SolendPlugin,
    DebridgePlugin,
]

# Backward-compatible aliases for test imports
MarginfiDepositCollateralAction = MarginfiDepositAction
MarginfiWithdrawCollateralAction = MarginfiWithdrawAction

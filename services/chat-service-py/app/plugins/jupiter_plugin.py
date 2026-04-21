"""
Jupiter Swap Plugin

Provides Jupiter DEX aggregator swap functionality.
All calls proxy through the gateway (no direct API key exposure).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.plugins.base import (
    BasePlugin,
    PluginAction,
    PluginProvider,
    PluginContext,
    PluginResult,
    PluginPriority,
)

logger = logging.getLogger(__name__)


class SwapAction(PluginAction):
    """Build a token swap transaction via Jupiter (gateway → solana-service)"""

    @property
    def name(self) -> str:
        return "swap"

    @property
    def description(self) -> str:
        return "Swap tokens using Jupiter aggregator"

    @property
    def aliases(self) -> list[str]:
        return ["exchange", "trade"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint": {
                "type": "string",
                "required": True,
                "description": "Input token symbol (SOL, USDC, USDT) or mint address",
            },
            "outputMint": {
                "type": "string",
                "required": True,
                "description": "Output token symbol (SOL, USDC, USDT) or mint address",
            },
            "amount": {
                "type": "string",
                "required": True,
                "description": "Amount to swap (human-readable, e.g. '1.5'). Use 'all' for full balance.",
            },
            "slippageBps": {
                "type": "number",
                "required": False,
                "description": "Slippage tolerance in basis points (default: 50 = 0.5%). E.g. 100 = 1%.",
                "default": 50,
            },
            "swapMode": {
                "type": "string",
                "required": False,
                "description": "ExactIn (sell exact input, default) or ExactOut (receive exact output amount).",
                "enum": ["ExactIn", "ExactOut"],
                "default": "ExactIn",
            },
            "onlyDirectRoutes": {
                "type": "boolean",
                "required": False,
                "description": "If true, skip multi-hop routing and use a single pool only.",
                "default": False,
            },
            "priorityFee": {
                "type": "string",
                "required": False,
                "description": "Transaction priority fee: 'auto' (default), 'low', 'medium', 'high', or exact lamports as string.",
                "default": "auto",
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {
                "description": "Swap 1 SOL for USDC",
                "params": {
                    "inputMint": "So11111111111111111111111111111111111111112",
                    "outputMint": "EPjFWdd5AufqSSqeMzoqYLswbeSuY5JPtejQ89E",
                    "amount": "1",
                    "slippageBps": 50,
                },
            },
            {
                "description": "Buy exactly 100 USDC with SOL (ExactOut)",
                "params": {
                    "inputMint": "So11111111111111111111111111111111111111112",
                    "outputMint": "EPjFWdd5AufqSSqeMzoqYLswbeSuY5JPtejQ89E",
                    "amount": "100",
                    "swapMode": "ExactOut",
                },
            },
        ]

    @property
    def priority(self) -> PluginPriority:
        return PluginPriority.HIGH

    async def execute(
        self,
        params: dict[str, Any],
        context: PluginContext,
    ) -> PluginResult:
        input_mint = params["inputMint"]
        output_mint = params["outputMint"]
        amount = params["amount"]
        slippage = params.get("slippageBps", 50)

        logger.info("Swap: %s of %s -> %s", amount, input_mint, output_mint)

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{settings.GATEWAY_URL}/actions/build",
                    json={
                        "type": "swap",
                        "params": {
                            "inputMint": input_mint,
                            "outputMint": output_mint,
                            "amount": str(amount),
                            "slippageBps": slippage,
                        },
                    },
                    headers={"X-Internal-Api-Key": settings.OPRAI_INTERNAL_API_KEY},
                )
                resp.raise_for_status()
                data = resp.json()
                return PluginResult(success=True, data=data)
        except Exception as exc:
            logger.error("Swap build failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


class GetQuoteAction(PluginAction):
    """Get a swap quote from Jupiter via gateway"""

    @property
    def name(self) -> str:
        return "get_quote"

    @property
    def description(self) -> str:
        return "Get a swap quote from Jupiter"

    @property
    def aliases(self) -> list[str]:
        return ["quote", "price"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "inputMint": {
                "type": "string",
                "required": True,
                "description": "Input token mint address",
            },
            "outputMint": {
                "type": "string",
                "required": True,
                "description": "Output token mint address",
            },
            "amount": {
                "type": "number",
                "required": True,
                "description": "Amount to swap (in smallest units)",
            },
            "slippageBps": {
                "type": "number",
                "required": False,
                "description": "Slippage tolerance in basis points",
                "default": 50,
            },
        }

    async def execute(
        self,
        params: dict[str, Any],
        context: PluginContext,
    ) -> PluginResult:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{settings.GATEWAY_URL}/actions/quote",
                    json={
                        "type": "swap",
                        "params": {
                            "inputMint": params["inputMint"],
                            "outputMint": params["outputMint"],
                            "amount": str(params["amount"]),
                            "slippageBps": params.get("slippageBps", 50),
                        },
                    },
                    headers={"X-Internal-Api-Key": settings.OPRAI_INTERNAL_API_KEY},
                )
                resp.raise_for_status()
                return PluginResult(success=True, data=resp.json())
        except Exception as exc:
            logger.error("Quote fetch failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


class TokenPriceProvider(PluginProvider):
    """Fetch token prices via gateway /market/prices"""

    @property
    def name(self) -> str:
        return "token_price"

    @property
    def description(self) -> str:
        return "Fetch current token price"

    @property
    def cache_ttl(self) -> int:
        return 30  # seconds

    async def fetch(
        self,
        params: dict[str, Any],
        context: PluginContext,
    ) -> PluginResult:
        mint = params.get("mint")
        if not mint:
            return PluginResult(success=False, error="Missing mint parameter")

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{settings.GATEWAY_URL}/market/prices",
                    params={"ids": mint},
                )
                resp.raise_for_status()
                data = resp.json()
                # Gateway returns { "data": { "<mint>": { "price": ... } } }
                price_info = (data.get("data") or {}).get(mint, {})
                return PluginResult(
                    success=True,
                    data={
                        "mint": mint,
                        "priceUsd": price_info.get("price", 0),
                    },
                )
        except Exception as exc:
            logger.error("Price fetch failed for %s: %s", mint, exc)
            return PluginResult(success=False, error=str(exc))


class JupiterPlugin(BasePlugin):
    """Jupiter DEX aggregator, Lend, and Perps plugin"""

    @property
    def id(self) -> str:
        return "jupiter"

    @property
    def name(self) -> str:
        return "Jupiter"

    @property
    def version(self) -> str:
        return "2.0.0"

    @property
    def description(self) -> str:
        return "Jupiter DEX aggregator, Lend (earn/borrow), and Perpetuals integration"

    @property
    def actions(self) -> list[PluginAction]:
        return [
            SwapAction(),
            GetQuoteAction(),
            # Jupiter Lend - Earn
            LendDepositAction(),
            LendWithdrawAction(),
            # Jupiter Lend - Borrow
            LendBorrowAction(),
            LendRepayAction(),
            # Jupiter Perp
            PerpOpenAction(),
            PerpCloseAction(),
            JlpAddAction(),
            JlpRemoveAction(),
            # JupSOL
            JupSolStakeAction(),
            JupSolUnstakeAction(),
        ]

    @property
    def providers(self) -> list[PluginProvider]:
        return [TokenPriceProvider()]

    @property
    def clients(self) -> list[str]:
        return ["direct", "telegram", "discord"]

    async def on_load(self, context: PluginContext) -> None:
        logger.info("Jupiter plugin loaded (v2 with Lend & Perps)")

    async def on_unload(self, context: PluginContext) -> None:
        logger.info("Jupiter plugin unloaded")


# ──────────────────────────────────────────────────────────────────────────────
# Jupiter Lend Actions (Earn - Deposit/Withdraw)
# ──────────────────────────────────────────────────────────────────────────────

class LendDepositAction(PluginAction):
    """Deposit tokens into Jupiter Lend to earn yield"""

    @property
    def name(self) -> str:
        return "lend"

    @property
    def description(self) -> str:
        return "Deposit tokens into Jupiter Lend to earn yield"

    @property
    def aliases(self) -> list[str]:
        return ["lend_deposit", "jupiter_lend_deposit", "jupiter_earn_deposit"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("lend", params)
            return result
        except Exception as exc:
            logger.error("Lend deposit failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


class LendWithdrawAction(PluginAction):
    """Withdraw tokens from Jupiter Lend"""

    @property
    def name(self) -> str:
        return "withdraw_lend"

    @property
    def description(self) -> str:
        return "Withdraw tokens from Jupiter Lend"

    @property
    def aliases(self) -> list[str]:
        return ["lend_withdraw", "jupiter_lend_withdraw", "jupiter_earn_withdraw"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("withdraw_lend", params)
            return result
        except Exception as exc:
            logger.error("Lend withdraw failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# Jupiter Lend Actions (Borrow/Repay)
# ──────────────────────────────────────────────────────────────────────────────

class LendBorrowAction(PluginAction):
    """Borrow tokens from Jupiter Lend against collateral"""

    @property
    def name(self) -> str:
        return "borrow"

    @property
    def description(self) -> str:
        return "Borrow tokens from Jupiter Lend against collateral"

    @property
    def aliases(self) -> list[str]:
        return ["lend_borrow", "jupiter_lend_borrow", "jupiter_borrow"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("borrow", params)
            return result
        except Exception as exc:
            logger.error("Lend borrow failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


class LendRepayAction(PluginAction):
    """Repay borrowed tokens to Jupiter Lend"""

    @property
    def name(self) -> str:
        return "repay"

    @property
    def description(self) -> str:
        return "Repay borrowed tokens to Jupiter Lend"

    @property
    def aliases(self) -> list[str]:
        return ["lend_repay", "jupiter_lend_repay", "jupiter_repay"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("repay", params)
            return result
        except Exception as exc:
            logger.error("Lend repay failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# Jupiter Perpetuals Actions
# ──────────────────────────────────────────────────────────────────────────────

class PerpOpenAction(PluginAction):
    """Open a perpetual position on Jupiter Perps"""

    @property
    def name(self) -> str:
        return "perp_open"

    @property
    def description(self) -> str:
        return "Open a perpetual futures position on Jupiter Perps"

    @property
    def aliases(self) -> list[str]:
        return ["jupiter_perp_open", "jupiter_perps_open"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("perp_open", params)
            return result
        except Exception as exc:
            logger.error("Perp open failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


class PerpCloseAction(PluginAction):
    """Close a perpetual position on Jupiter Perps"""

    @property
    def name(self) -> str:
        return "perp_close"

    @property
    def description(self) -> str:
        return "Close a perpetual futures position on Jupiter Perps"

    @property
    def aliases(self) -> list[str]:
        return ["jupiter_perp_close", "jupiter_perps_close"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("perp_close", params)
            return result
        except Exception as exc:
            logger.error("Perp close failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


class JlpAddAction(PluginAction):
    """Add liquidity to JLP (Jupiter Liquidity Pool)"""

    @property
    def name(self) -> str:
        return "jlp_add"

    @property
    def description(self) -> str:
        return "Add liquidity to JLP (Jupiter Liquidity Pool)"

    @property
    def aliases(self) -> list[str]:
        return ["jlp_deposit", "jupiter_lp_add"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("jlp_add", params)
            return result
        except Exception as exc:
            logger.error("JLP add failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


class JlpRemoveAction(PluginAction):
    """Remove liquidity from JLP"""

    @property
    def name(self) -> str:
        return "jlp_remove"

    @property
    def description(self) -> str:
        return "Remove liquidity from JLP (Jupiter Liquidity Pool)"

    @property
    def aliases(self) -> list[str]:
        return ["jlp_withdraw", "jupiter_lp_remove"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("jlp_remove", params)
            return result
        except Exception as exc:
            logger.error("JLP remove failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# JupSOL Staking Actions
# ──────────────────────────────────────────────────────────────────────────────

class JupSolStakeAction(PluginAction):
    """Stake SOL to get JupSOL (Jupiter Liquid Staking)"""

    @property
    def name(self) -> str:
        return "jupsol_stake"

    @property
    def description(self) -> str:
        return "Stake SOL to receive JupSOL (Jupiter Liquid Staking Token)"

    @property
    def aliases(self) -> list[str]:
        return ["jup_stake", "jupiter_stake_sol"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("jupsol_stake", params)
            return result
        except Exception as exc:
            logger.error("JupSOL stake failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


class JupSolUnstakeAction(PluginAction):
    """Unstake JupSOL back to SOL"""

    @property
    def name(self) -> str:
        return "jupsol_unstake"

    @property
    def description(self) -> str:
        return "Unstake JupSOL to receive SOL"

    @property
    def aliases(self) -> list[str]:
        return ["jup_unstake", "jupiter_unstake_sol"]

    async def execute(self, params: dict, context: PluginContext) -> PluginResult:
        try:
            result = await _build_action("jupsol_unstake", params)
            return result
        except Exception as exc:
            logger.error("JupSOL unstake failed: %s", exc)
            return PluginResult(success=False, error=str(exc))

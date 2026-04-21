"""
Marinade Finance Plugin

Full integration with @marinade.finance/marinade-ts-sdk via the Solana service REST API.

Liquid staking:
  marinade_stake                    — Deposit SOL → mSOL
  marinade_unstake                  — Instant mSOL → SOL (~0.3% fee)
  marinade_delayed_unstake          — mSOL → ticket, no fee, ~5-7 days
  marinade_claim                    — Claim SOL from ticket after delay

Native stake account deposits:
  marinade_deposit_stake            — Fully-activated stake account → mSOL
  marinade_deposit_deactivating_stake  — Deactivating stake account → mSOL
  marinade_deposit_activating_stake    — Warming-up stake account → mSOL (beta)
  marinade_partial_deposit_stake    — Partial stake account → mSOL (keep some SOL)

Liquidity pool:
  marinade_add_liquidity            — SOL → LP tokens (earn fees)
  marinade_remove_liquidity         — LP tokens → SOL + mSOL

Ticket management:
  marinade_ticket_info              — Single ticket status + claimability
  marinade_list_tickets             — User's pending tickets
  marinade_all_tickets              — All protocol tickets
  marinade_order_unstake_with_key   — Delayed unstake with deterministic ticket pubkey
  marinade_ticket_due_date          — ETA for a new delayed unstake ticket

Protocol queries:
  marinade_state                    — Protocol TVL, validators, APY
  marinade_exchange_rate            — mSOL/SOL rate + APY

Stake pool tokens (beta):
  marinade_deposit_stake_pool_token    — LST token (jitoSOL, bSOL…) → mSOL
  marinade_liquidate_stake_pool_token  — LST token → SOL (atomic: withdraw+deposit+unstake)

Referral program:
  marinade_referral_partner_state   — Partner referral state
  marinade_referral_global_state    — Global referral program config
  marinade_referral_partners        — All registered referral partners

Historical / Market Data (api.marinade.finance):
  marinade_msol_apy                 — mSOL APY for a given period (7d, 2w, 1y…)
  marinade_lp_apy                   — mSOL-SOL LP APY for a given period
  marinade_lp_price                 — LP token price in SOL
  marinade_msol_supply              — Total mSOL supply
  marinade_msol_price_sol           — mSOL price in SOL (historical)
  marinade_msol_price_usd           — mSOL price in USD
  marinade_farm_stats               — Farm stats for mSOL or LP token
  marinade_tlv_history              — Daily TVL snapshots over a date range
  marinade_tlv                      — Current / point-in-time TVL

Snapshots API (snapshots-api.marinade.finance):
  marinade_snapshot_msol            — mSOL balance for a wallet (latest snapshot)
  marinade_snapshot_vemnde          — VeMNDE balance for a wallet (latest snapshot)
  marinade_stakers_all              — All stake balances for a wallet (historical)
  marinade_stakers_ns               — Native stake balance for a wallet
  marinade_stakers_ns_all           — All native stakers (protocol-wide)
  marinade_votes_msol_latest        — Latest mSOL governance votes
  marinade_votes_msol_all           — mSOL votes over a date range
  marinade_votes_vemnde_latest      — Latest veMNDE governance votes
  marinade_votes_vemnde_all         — veMNDE votes over a date range

Validators API (validators-api.marinade.finance):
  marinade_cluster_stats            — Cluster-wide staking statistics
  marinade_validator_scores         — Full validator score list
  marinade_score_breakdown          — Per-validator score breakdown
  marinade_score_breakdowns         — Multiple validator score breakdowns
  marinade_validator_commissions    — Validator commission history
  marinade_validator_uptimes        — Validator uptime history
  marinade_validator_versions       — Validator version history
  marinade_block_rewards            — Block rewards data
  marinade_rewards                  — Staking rewards (block, inflation, Jito, MEV)
  marinade_unstake_hints            — Epoch unstake hints
  marinade_global_unstake_hints     — Protocol-wide global unstake hints
  marinade_jito                     — Jito stake data per validator
  marinade_mev                      — MEV data per validator
  marinade_staking_report           — Planned staking report
  marinade_scoring_report           — Validator scoring report
  marinade_commission_changes       — Commission change history
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

MSOL_MINT = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"


# ── Internal helper ─────────────────────────────────────────────────────────────

async def _build_action(action_type: str, params: dict[str, Any]) -> PluginResult:
    """POST /actions/build through the internal gateway."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{settings.GATEWAY_URL}/actions/build",
                json={"type": action_type, "params": params},
                headers={"X-Internal-Api-Key": settings.OPRAI_INTERNAL_API_KEY},
            )
            resp.raise_for_status()
            return PluginResult(success=True, data=resp.json())
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:300]
        logger.error("Marinade %s HTTP %s: %s", action_type, exc.response.status_code, body)
        return PluginResult(success=False, error=f"Solana service error ({exc.response.status_code}): {body}")
    except Exception as exc:
        logger.error("Marinade %s failed: %s", action_type, exc)
        return PluginResult(success=False, error=str(exc))


# ── 1. Stake: SOL → mSOL ───────────────────────────────────────────────────────

class MarinadeStakeAction(PluginAction):
    """Stake SOL to receive mSOL (Marinade liquid staking)"""

    @property
    def name(self) -> str:
        return "marinade_stake"

    @property
    def description(self) -> str:
        return (
            "Stake SOL with Marinade Finance to receive mSOL (liquid staking token). "
            "mSOL automatically accrues staking rewards (~7-8% APY). "
            "There is no lock-up; mSOL can be used in DeFi or unstaked at any time."
        )

    @property
    def aliases(self) -> list[str]:
        return ["stake", "marinade_deposit", "liquid_stake", "msol_stake"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {
                "type": "string",
                "required": True,
                "description": "SOL amount to stake (e.g. '1.5'). Minimum 0.001 SOL.",
            },
            "referralCode": {
                "type": "string",
                "required": False,
                "description": "Optional Marinade referral code (base58 public key) to earn referral rewards.",
            },
            "mintToOwnerAddress": {
                "type": "string",
                "required": False,
                "description": (
                    "Optional base58 public key of the mSOL token account owner. "
                    "Defaults to the connected wallet. Use when staking on behalf of another address."
                ),
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "Stake 5 SOL with Marinade", "params": {"amount": "5"}},
            {"description": "Stake 0.5 SOL", "params": {"amount": "0.5"}},
        ]

    @property
    def priority(self) -> PluginPriority:
        return PluginPriority.HIGH

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_stake", params)


# ── 2. Liquid Unstake: mSOL → SOL (Instant) ────────────────────────────────────

class MarinadeUnstakeAction(PluginAction):
    """Instantly unstake mSOL back to SOL via Marinade liquid pool (~0.3% fee)"""

    @property
    def name(self) -> str:
        return "marinade_unstake"

    @property
    def description(self) -> str:
        return (
            "Instantly convert mSOL back to SOL via Marinade's liquid unstaking pool. "
            "A small fee (~0.3%) is charged. "
            "If fee is too high, use marinade_delayed_unstake instead (no fee, ~5-7 days)."
        )

    @property
    def aliases(self) -> list[str]:
        return ["unstake", "marinade_liquid_unstake", "msol_unstake", "marinade_redeem"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {
                "type": "string",
                "required": True,
                "description": "mSOL amount to unstake (e.g. '1.5').",
            },
            "msolTokenAccount": {
                "type": "string",
                "required": False,
                "description": (
                    "Optional base58 public key of the mSOL token account to burn from. "
                    "Defaults to the connected wallet's associated mSOL token account. "
                    "Only needed when mSOL is held in a non-standard token account."
                ),
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "Instantly unstake 2 mSOL", "params": {"amount": "2"}},
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_unstake", params)


# ── 3. Delayed Unstake: mSOL → Ticket (No fee) ─────────────────────────────────

class MarinadeDelayedUnstakeAction(PluginAction):
    """Order a delayed unstake of mSOL → SOL with no fee (takes ~5-7 days)"""

    @property
    def name(self) -> str:
        return "marinade_delayed_unstake"

    @property
    def description(self) -> str:
        return (
            "Order a delayed unstake of mSOL to SOL. No fee is charged. "
            "A ticket account is created; after 2-3 Solana epochs (~5-7 days) you can "
            "call marinade_claim with the ticket account address to receive your SOL. "
            "Use this when the liquid unstake fee is too high."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_order_unstake", "delayed_unstake", "marinade_schedule_unstake"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {
                "type": "string",
                "required": True,
                "description": "mSOL amount to schedule for unstaking (e.g. '2.5').",
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "Schedule delayed unstake of 3 mSOL (no fee)", "params": {"amount": "3"}},
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_delayed_unstake", params)


# ── 4. Claim: Ticket → SOL ─────────────────────────────────────────────────────

class MarinadeClaimAction(PluginAction):
    """Claim SOL from a completed delayed unstake ticket"""

    @property
    def name(self) -> str:
        return "marinade_claim"

    @property
    def description(self) -> str:
        return (
            "Claim SOL from a delayed unstake ticket after the 2-3 epoch waiting period. "
            "You must provide the ticket account address that was returned when you called "
            "marinade_delayed_unstake. Use marinade_ticket_info to check if a ticket is claimable."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_claim_unstake", "claim_unstake_ticket", "marinade_withdraw"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "ticketAccount": {
                "type": "string",
                "required": True,
                "description": (
                    "Base58 public key of the ticket account created by marinade_delayed_unstake. "
                    "Example: 'Abc123...xyz'"
                ),
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {
                "description": "Claim SOL from a delayed unstake ticket",
                "params": {"ticketAccount": "TicketAccountPublicKeyHere"},
            },
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_claim", params)


# ── 5. Deposit Stake Account: Native Stake → mSOL ──────────────────────────────

class MarinadeDepositStakeAction(PluginAction):
    """Deposit an existing native Solana stake account into Marinade in exchange for mSOL"""

    @property
    def name(self) -> str:
        return "marinade_deposit_stake"

    @property
    def description(self) -> str:
        return (
            "Convert an existing native Solana stake account into mSOL via Marinade. "
            "The stake account must be fully activated (not warming up or cooling down). "
            "You receive mSOL proportional to the SOL in the stake account. "
            "This is useful for users who already have native stakes and want to make them liquid."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_stake_account", "deposit_stake_account", "native_stake_to_msol"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakeAccount": {
                "type": "string",
                "required": True,
                "description": (
                    "Base58 public key of the fully-activated native Solana stake account to deposit. "
                    "If the account is deactivating, use marinade_deposit_deactivating_stake instead. "
                    "If activating (warm-up), use marinade_deposit_activating_stake instead."
                ),
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {
                "description": "Convert a native fully-activated stake account to mSOL",
                "params": {"stakeAccount": "StakeAccountPublicKeyHere"},
            },
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_deposit_stake", params)


# ── 6. Add Liquidity: SOL → LP Tokens ──────────────────────────────────────────

class MarinadeAddLiquidityAction(PluginAction):
    """Add SOL to the Marinade liquidity pool to earn trading fees"""

    @property
    def name(self) -> str:
        return "marinade_add_liquidity"

    @property
    def description(self) -> str:
        return (
            "Add SOL to the Marinade Finance liquidity pool. "
            "In return you receive LP tokens that earn fees from liquid unstaking operations. "
            "LP tokens can be redeemed at any time for SOL + mSOL."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_provide_liquidity", "marinade_lp_add", "add_marinade_liquidity"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {
                "type": "string",
                "required": True,
                "description": "SOL amount to add to the liquidity pool (e.g. '1.0'). Minimum 0.001 SOL.",
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "Add 2 SOL to Marinade liquidity pool", "params": {"amount": "2"}},
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_add_liquidity", params)


# ── 7. Remove Liquidity: LP Tokens → SOL + mSOL ────────────────────────────────

class MarinadeRemoveLiquidityAction(PluginAction):
    """Remove liquidity from Marinade pool by burning LP tokens"""

    @property
    def name(self) -> str:
        return "marinade_remove_liquidity"

    @property
    def description(self) -> str:
        return (
            "Burn Marinade LP tokens to withdraw from the liquidity pool. "
            "You receive a proportional mix of SOL and mSOL based on the current pool composition."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_withdraw_liquidity", "marinade_lp_remove", "remove_marinade_liquidity"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {
                "type": "string",
                "required": True,
                "description": "LP token amount to burn/redeem (e.g. '0.5').",
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "Remove 1 LP token worth of liquidity", "params": {"amount": "1"}},
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_remove_liquidity", params)


# ── 8. Protocol State (read-only) ──────────────────────────────────────────────

class MarinadeStateAction(PluginAction):
    """Fetch Marinade Finance protocol statistics (TVL, APY, validators, etc.)"""

    @property
    def name(self) -> str:
        return "marinade_state"

    @property
    def description(self) -> str:
        return (
            "Fetch Marinade Finance protocol state: total staked SOL, mSOL supply, "
            "number of validators, staking APY, liquidity pool composition, and more. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_stats", "marinade_info", "marinade_protocol_state"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "Get Marinade Finance stats", "params": {}},
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_state", params)


# ── 9. Exchange Rate (read-only) ────────────────────────────────────────────────

class MarinadeExchangeRateAction(PluginAction):
    """Get the current mSOL/SOL exchange rate and APY"""

    @property
    def name(self) -> str:
        return "marinade_exchange_rate"

    @property
    def description(self) -> str:
        return (
            "Get the current Marinade mSOL to SOL exchange rate and staking APY. "
            "mSOL's price relative to SOL increases over time as staking rewards accumulate. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["msol_price", "marinade_price", "msol_rate", "marinade_apy"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "What is the current mSOL price in SOL?", "params": {}},
            {"description": "What is Marinade's staking APY?", "params": {}},
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_exchange_rate", params)


# ── 10. Ticket Info (read-only) ─────────────────────────────────────────────────

class MarinadeTicketInfoAction(PluginAction):
    """Check the status and claimability of a delayed unstake ticket"""

    @property
    def name(self) -> str:
        return "marinade_ticket_info"

    @property
    def description(self) -> str:
        return (
            "Check the status of a delayed unstake ticket created by marinade_delayed_unstake. "
            "Shows whether the ticket is claimable (delay period passed), how many epochs remain, "
            "and the SOL amount locked in the ticket. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_check_ticket", "check_unstake_ticket", "marinade_ticket_status"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "ticketAccount": {
                "type": "string",
                "required": True,
                "description": "Base58 public key of the delayed unstake ticket account.",
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {
                "description": "Check if a delayed unstake ticket is ready to claim",
                "params": {"ticketAccount": "TicketAccountPublicKeyHere"},
            },
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_ticket_info", params)


# ── 11. Deposit Activating Stake Account ───────────────────────────────────────

class MarinadeDepositActivatingStakeAction(PluginAction):
    """Deposit an activating (warming-up) native stake account into Marinade → mSOL"""

    @property
    def name(self) -> str:
        return "marinade_deposit_activating_stake"

    @property
    def description(self) -> str:
        return (
            "Deposit a native Solana stake account that is in activating (warm-up) state "
            "into Marinade Finance to receive mSOL. "
            "You can specify how much SOL to keep in the original stake account; "
            "the rest is converted to mSOL. "
            "If fully activated, use marinade_deposit_stake instead."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_activating_stake", "deposit_activating_stake"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakeAccount": {
                "type": "string",
                "required": True,
                "description": "Base58 public key of the activating (warming-up) stake account.",
            },
            "solToKeep": {
                "type": "string",
                "required": True,
                "description": "SOL amount to keep in the original stake account (e.g. '0'). Use '0' to convert everything.",
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_deposit_activating_stake", params)


# ── 12. List User's Delayed Unstake Tickets ────────────────────────────────────

class MarinadeListTicketsAction(PluginAction):
    """List all pending delayed unstake tickets for the connected wallet"""

    @property
    def name(self) -> str:
        return "marinade_list_tickets"

    @property
    def description(self) -> str:
        return (
            "List all delayed unstake tickets belonging to the connected wallet. "
            "Shows each ticket's SOL amount, status (PENDING / CLAIMABLE), and time remaining. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_my_tickets", "list_unstake_tickets", "marinade_pending_unstake"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "Show my pending Marinade delayed unstake tickets", "params": {}},
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_list_tickets", params)


# ── 13. Order Unstake with Explicit Ticket Public Key ───────────────────────────

class MarinadeOrderUnstakeWithKeyAction(PluginAction):
    """Order delayed unstake with a pre-determined ticket account public key"""

    @property
    def name(self) -> str:
        return "marinade_order_unstake_with_key"

    @property
    def description(self) -> str:
        return (
            "Order a delayed unstake of mSOL → SOL using a specific ticket account public key. "
            "Use this for deterministic ticket addresses (e.g. in automated systems). "
            "No fee. Takes ~5-7 days. The ticket keypair must co-sign the transaction."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_delayed_unstake_with_key"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "amount": {
                "type": "string",
                "required": True,
                "description": "mSOL amount to schedule for unstaking.",
            },
            "ticketAccountPublicKey": {
                "type": "string",
                "required": True,
                "description": "Base58 public key to use for the ticket account (must be a fresh keypair you control).",
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_order_unstake_with_key", params)


# ── 14. Deposit Deactivating Stake Account ──────────────────────────────────────

class MarinadeDepositDeactivatingStakeAction(PluginAction):
    """Deposit a deactivating (cooling-down) native stake account into Marinade → mSOL"""

    @property
    def name(self) -> str:
        return "marinade_deposit_deactivating_stake"

    @property
    def description(self) -> str:
        return (
            "Deposit a native Solana stake account that is in deactivating (cooling down) state "
            "into Marinade Finance to receive mSOL. "
            "Use this for stake accounts that were deactivated in a previous epoch."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_deactivating_stake", "deposit_deactivating_stake"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakeAccount": {
                "type": "string",
                "required": True,
                "description": "Base58 public key of the deactivating stake account.",
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_deposit_deactivating_stake", params)


# ── 15. Partially Deposit Stake Account ─────────────────────────────────────────

class MarinadePartialDepositStakeAction(PluginAction):
    """Partially convert a native stake account to mSOL, keeping some SOL staked"""

    @property
    def name(self) -> str:
        return "marinade_partial_deposit_stake"

    @property
    def description(self) -> str:
        return (
            "Partially deposit a native Solana stake account into Marinade Finance. "
            "You specify how much SOL to keep in the stake account; "
            "the remainder is converted to mSOL. "
            "Useful when you want to partially liquidate a large stake account."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_partial_stake", "partial_deposit_stake"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakeAccount": {
                "type": "string",
                "required": True,
                "description": "Base58 public key of the stake account to partially deposit.",
            },
            "solToKeep": {
                "type": "string",
                "required": True,
                "description": (
                    "SOL amount to keep in the stake account (e.g. '2.0'). "
                    "The rest will be converted to mSOL."
                ),
            },
        }

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {
                "description": "Convert a stake account to mSOL but keep 5 SOL staked",
                "params": {"stakeAccount": "StakeAccountPublicKeyHere", "solToKeep": "5"},
            },
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_partial_deposit_stake", params)


# ── 16. All Protocol Delayed Unstake Tickets (read-only) ───────────────────────

class MarinadeAllTicketsAction(PluginAction):
    """List ALL delayed unstake tickets across the entire Marinade protocol"""

    @property
    def name(self) -> str:
        return "marinade_all_tickets"

    @property
    def description(self) -> str:
        return (
            "List ALL delayed unstake tickets across the entire Marinade Finance protocol "
            "(not just the connected wallet's tickets). "
            "Returns total count, claimable vs pending breakdown, and total SOL locked. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_protocol_tickets", "marinade_global_tickets"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_all_tickets", params)


# ── 17. Estimated Ticket Due Date (read-only) ───────────────────────────────────

class MarinadeTicketDueDateAction(PluginAction):
    """Get the estimated due date for new delayed unstake tickets if ordered now"""

    @property
    def name(self) -> str:
        return "marinade_ticket_due_date"

    @property
    def description(self) -> str:
        return (
            "Get the estimated due date for a delayed unstake ticket if you were to order one right now. "
            "Returns estimated completion date/epoch based on current Solana epoch timing. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_unstake_eta", "marinade_delayed_unstake_eta"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    @property
    def examples(self) -> list[dict[str, Any]]:
        return [
            {"description": "When would a delayed unstake complete if I ordered one now?", "params": {}},
        ]

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_ticket_due_date", params)


# ── 18. Referral Partner State (read-only) ──────────────────────────────────────

class MarinadeReferralPartnerStateAction(PluginAction):
    """Get the Marinade referral program state for a referral code"""

    @property
    def name(self) -> str:
        return "marinade_referral_partner_state"

    @property
    def description(self) -> str:
        return (
            "Get the Marinade Finance referral partner state for a given referral code. "
            "Shows partner information, earned fees, and referral program details. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_referral_state", "marinade_check_referral"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "referralCode": {
                "type": "string",
                "required": False,
                "description": "Optional base58 public key of the referral code. Omit to query the connected wallet's own referral state.",
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_referral_partner_state", params)


# ── 19. Referral Global State (read-only) ───────────────────────────────────────

class MarinadeReferralGlobalStateAction(PluginAction):
    """Get the Marinade Finance referral program global configuration"""

    @property
    def name(self) -> str:
        return "marinade_referral_global_state"

    @property
    def description(self) -> str:
        return (
            "Get the global state of the Marinade Finance referral program. "
            "Shows program-wide configuration such as referral fee rates and admin settings. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_referral_config", "marinade_referral_program_state"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_referral_global_state", params)


# ── 20. List Referral Partners (read-only) ──────────────────────────────────────

class MarinadeReferralPartnersAction(PluginAction):
    """List all registered Marinade Finance referral partners"""

    @property
    def name(self) -> str:
        return "marinade_referral_partners"

    @property
    def description(self) -> str:
        return (
            "List all registered referral partners in the Marinade Finance referral program. "
            "Shows partner addresses and their program states. "
            "This is a read-only query — no transaction is built."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_list_referral_partners", "marinade_referral_list"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_referral_partners", params)


# ── 21. Deposit Stake Pool Token → mSOL (@beta) ──────────────────────────────────

class MarinadeDepositStakePoolTokenAction(PluginAction):
    """Convert a stake pool LST token (jitoSOL, bSOL, etc.) into mSOL via Marinade.
    Validator stats are fetched internally — only the token mint and amount are needed.
    Uses a versioned transaction with address lookup tables."""

    @property
    def name(self) -> str:
        return "marinade_deposit_stake_pool_token"

    @property
    def description(self) -> str:
        return (
            "Deposit a stake pool LST token (e.g. jitoSOL, bSOL) into Marinade Finance to receive mSOL. "
            "Provide the token's mint address and the amount to deposit. "
            "Minimum deposit equivalent of 1 SOL required. "
            "Beta feature — uses a versioned transaction with address lookup tables."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_deposit_lst", "marinade_lst_to_msol"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakePoolTokenAddress": {
                "type": "string",
                "description": "Mint address (base58) of the stake pool LST token, e.g. jitoSOL or bSOL mint",
                "required": True,
            },
            "amount": {
                "type": "string",
                "description": "Amount of stake pool tokens to deposit (human-readable, e.g. '1.5')",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("stakePoolTokenAddress"):
            return PluginResult(success=False, error="stakePoolTokenAddress is required (mint address of the LST token)")
        if not params.get("amount"):
            return PluginResult(success=False, error="amount is required")
        return await _build_action("marinade_deposit_stake_pool_token", params)


# ── 22. Liquidate Stake Pool Token → SOL (@beta) ──────────────────────────────────

class MarinadeLiquidateStakePoolTokenAction(PluginAction):
    """Convert a stake pool LST token directly to SOL in one atomic versioned transaction:
    withdraw stake → deposit into Marinade → liquid unstake. Validator stats are fetched internally."""

    @property
    def name(self) -> str:
        return "marinade_liquidate_stake_pool_token"

    @property
    def description(self) -> str:
        return (
            "Liquidate a stake pool LST token (e.g. jitoSOL, bSOL) directly into SOL via Marinade Finance. "
            "Combines three steps atomically: withdraw stake → deposit into Marinade → liquid unstake. "
            "Provide the token's mint address and the amount to liquidate. "
            "Minimum equivalent of 1 SOL required. Liquid unstake fee (~0.3%) applies. "
            "Beta feature — uses a versioned transaction."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_liquidate_lst", "marinade_lst_to_sol"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "stakePoolTokenAddress": {
                "type": "string",
                "description": "Mint address (base58) of the stake pool LST token to liquidate",
                "required": True,
            },
            "amount": {
                "type": "string",
                "description": "Amount of stake pool tokens to liquidate (human-readable, e.g. '1.5')",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("stakePoolTokenAddress"):
            return PluginResult(success=False, error="stakePoolTokenAddress is required (mint address of the LST token)")
        if not params.get("amount"):
            return PluginResult(success=False, error="amount is required")
        return await _build_action("marinade_liquidate_stake_pool_token", params)


# ── 23. Cluster Stats ────────────────────────────────────────────────────────────

class MarinadeClusterStatsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_cluster_stats"

    @property
    def description(self) -> str:
        return (
            "Fetch cluster-wide staking statistics from Marinade Finance. "
            "Shows total staked SOL, validator counts, and epoch data. "
            "Optionally filter by number of epochs."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "epochs": {"type": "integer", "description": "Number of epochs to include", "required": False},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_cluster_stats", params)


# ── 24. Validator Scores ──────────────────────────────────────────────────────────

class MarinadeValidatorScoresAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_validator_scores"

    @property
    def description(self) -> str:
        return "Fetch the full list of Marinade validator scores. No parameters required."

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_validator_scores", {})


# ── 25. Validator Score Breakdown ─────────────────────────────────────────────────

class MarinadeScoreBreakdownAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_score_breakdown"

    @property
    def description(self) -> str:
        return (
            "Fetch the detailed score breakdown for a specific Marinade validator. "
            "Shows all scoring components (uptime, commission, decentralization, etc.)."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "voteAccount": {"type": "string", "description": "Vote account address of the validator", "required": True},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("voteAccount"):
            return PluginResult(success=False, error="voteAccount is required")
        return await _build_action("marinade_score_breakdown", params)


# ── 26. Validator Score Breakdowns ────────────────────────────────────────────────

class MarinadeScoreBreakdownsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_score_breakdowns"

    @property
    def description(self) -> str:
        return (
            "Fetch score breakdowns for multiple Marinade validators. "
            "Optionally filter by vote account and/or date."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "voteAccount": {"type": "string", "description": "Filter by vote account address", "required": False},
            "fromDate": {"type": "string", "description": "Filter from date (ISO 8601, e.g. 2024-01-01)", "required": False},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_score_breakdowns", params)


# ── 27. Validator Commission History ─────────────────────────────────────────────

class MarinadeValidatorCommissionsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_validator_commissions"

    @property
    def description(self) -> str:
        return (
            "Fetch the commission history for a specific Marinade validator. "
            "Useful for identifying commission changes ('rugged commission') over time."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_commission_history"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "voteAccount": {"type": "string", "description": "Vote account address of the validator", "required": True},
            "fromDate": {"type": "string", "description": "Filter from date (ISO 8601)", "required": False},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("voteAccount"):
            return PluginResult(success=False, error="voteAccount is required")
        return await _build_action("marinade_validator_commissions", params)


# ── 28. Validator Uptime History ──────────────────────────────────────────────────

class MarinadeValidatorUptimesAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_validator_uptimes"

    @property
    def description(self) -> str:
        return (
            "Fetch the uptime history for a specific Marinade validator. "
            "Shows per-epoch uptime percentage records."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_uptime_history"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "voteAccount": {"type": "string", "description": "Vote account address of the validator", "required": True},
            "fromDate": {"type": "string", "description": "Filter from date (ISO 8601)", "required": False},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("voteAccount"):
            return PluginResult(success=False, error="voteAccount is required")
        return await _build_action("marinade_validator_uptimes", params)


# ── 29. Validator Version History ─────────────────────────────────────────────────

class MarinadeValidatorVersionsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_validator_versions"

    @property
    def description(self) -> str:
        return "Fetch the software version history for a specific Marinade validator."

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "voteAccount": {"type": "string", "description": "Vote account address of the validator", "required": True},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("voteAccount"):
            return PluginResult(success=False, error="voteAccount is required")
        return await _build_action("marinade_validator_versions", params)


# ── 30. Block Rewards ─────────────────────────────────────────────────────────────

class MarinadeBlockRewardsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_block_rewards"

    @property
    def description(self) -> str:
        return "Fetch block rewards data for Marinade validators. No parameters required."

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_block_rewards", {})


# ── 31. Rewards ───────────────────────────────────────────────────────────────────

class MarinadeRewardsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_rewards"

    @property
    def description(self) -> str:
        return (
            "Fetch Marinade staking rewards data including block rewards, inflation estimates, "
            "Jito priority fees, and MEV rewards. Optionally filter by number of epochs."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "epochs": {"type": "integer", "description": "Number of epochs to include", "required": False},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_rewards", params)


# ── 32. Unstake Hints ─────────────────────────────────────────────────────────────

class MarinadeUnstakeHintsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_unstake_hints"

    @property
    def description(self) -> str:
        return (
            "Fetch Marinade's unstake hints for the current or a specific epoch. "
            "Shows which validators Marinade plans to unstake from and why."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "epoch": {"type": "integer", "description": "Epoch number (defaults to current epoch)", "required": False},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_unstake_hints", params)


# ── 33. Global Unstake Hints ──────────────────────────────────────────────────────

class MarinadeGlobalUnstakeHintsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_global_unstake_hints"

    @property
    def description(self) -> str:
        return (
            "Fetch Marinade's protocol-wide global unstake hints for the current or a specific epoch. "
            "Shows the overall unstake strategy across the entire Marinade protocol."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "epoch": {"type": "integer", "description": "Epoch number (defaults to current epoch)", "required": False},
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_global_unstake_hints", params)


# ── 34. Jito Data ─────────────────────────────────────────────────────────────────

class MarinadeJitoAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_jito"

    @property
    def description(self) -> str:
        return "Fetch Jito stake data per validator from Marinade Finance. No parameters required."

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_jito", {})


# ── 35. MEV Data ──────────────────────────────────────────────────────────────────

class MarinadeMevAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_mev"

    @property
    def description(self) -> str:
        return "Fetch MEV (maximal extractable value) data per validator from Marinade Finance. No parameters required."

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_mev", {})


# ── 36. Staking Report ────────────────────────────────────────────────────────────

class MarinadeStakingReportAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_staking_report"

    @property
    def description(self) -> str:
        return "Fetch Marinade's planned staking report showing upcoming stake allocation decisions."

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_staking_report", {})


# ── 37. Scoring Report ────────────────────────────────────────────────────────────

class MarinadeScoringReportAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_scoring_report"

    @property
    def description(self) -> str:
        return "Fetch Marinade's validator scoring report showing how validators are evaluated."

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_scoring_report", {})


# ── 38. Commission Changes ────────────────────────────────────────────────────────

class MarinadeCommissionChangesAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_commission_changes"

    @property
    def description(self) -> str:
        return (
            "Fetch the history of validator commission changes across the Marinade protocol. "
            "Useful for identifying validators that have changed their commission rates."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_commission_changes", {})


# ── api.marinade.finance — Historical / Market Data ──────────────────────────────

# ── 39. mSOL APY ─────────────────────────────────────────────────────────────────

class MarinadeMsolApyAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_msol_apy"

    @property
    def description(self) -> str:
        return (
            "Fetch the actual mSOL APY for a given time period from Marinade Finance. "
            "Period format: number + unit, e.g. '7d' (7 days), '2w' (2 weeks), '1y' (1 year). "
            "Optionally query as-of a specific point in time."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_apy", "msol_apy"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "period": {
                "type": "string",
                "description": "Period string, e.g. '7d', '2w', '1y'",
                "required": True,
            },
            "time": {
                "type": "string",
                "description": "Point-in-time as ISO 8601 datetime (optional)",
                "required": False,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("period"):
            return PluginResult(success=False, error="period is required (e.g. '7d', '2w', '1y')")
        return await _build_action("marinade_msol_apy", params)


# ── 40. LP APY ────────────────────────────────────────────────────────────────────

class MarinadeLpApyAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_lp_apy"

    @property
    def description(self) -> str:
        return (
            "Fetch the actual mSOL-SOL liquidity pool APY for a given time period. "
            "Period format: '7d', '2w', '1y', etc. "
            "Optionally query as-of a specific point in time."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_lp_apy_period"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "period": {
                "type": "string",
                "description": "Period string, e.g. '7d', '2w', '1y'",
                "required": True,
            },
            "time": {
                "type": "string",
                "description": "Point-in-time as ISO 8601 datetime (optional)",
                "required": False,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("period"):
            return PluginResult(success=False, error="period is required (e.g. '7d', '2w', '1y')")
        return await _build_action("marinade_lp_apy", params)


# ── 41. LP Price ──────────────────────────────────────────────────────────────────

class MarinadeLpPriceAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_lp_price"

    @property
    def description(self) -> str:
        return (
            "Fetch the mSOL-SOL LP token price in SOL from Marinade Finance. "
            "Optionally query as-of a specific point in time."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "time": {
                "type": "string",
                "description": "Point-in-time as ISO 8601 datetime (optional)",
                "required": False,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_lp_price", params)


# ── 42. mSOL Supply ───────────────────────────────────────────────────────────────

class MarinadeMsolSupplyAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_msol_supply"

    @property
    def description(self) -> str:
        return (
            "Fetch the total mSOL supply in lamports from Marinade Finance. "
            "Optionally query as-of a specific point in time."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_total_supply"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "time": {
                "type": "string",
                "description": "Point-in-time as ISO 8601 datetime (optional)",
                "required": False,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_msol_supply", params)


# ── 43. mSOL Price in SOL ─────────────────────────────────────────────────────────

class MarinadeMsolPriceSolAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_msol_price_sol"

    @property
    def description(self) -> str:
        return (
            "Fetch the mSOL price in SOL from Marinade Finance historical API. "
            "Optionally query as-of a specific point in time."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_msol_sol_price"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "time": {
                "type": "string",
                "description": "Point-in-time as ISO 8601 datetime (optional)",
                "required": False,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_msol_price_sol", params)


# ── 44. mSOL Price in USD ─────────────────────────────────────────────────────────

class MarinadeMsolPriceUsdAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_msol_price_usd"

    @property
    def description(self) -> str:
        return "Fetch the current mSOL price in USD from Marinade Finance. No parameters required."

    @property
    def aliases(self) -> list[str]:
        return ["marinade_msol_usd_price"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_msol_price_usd", {})


# ── 45. Farm Stats ────────────────────────────────────────────────────────────────

class MarinadeFarmStatsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_farm_stats"

    @property
    def description(self) -> str:
        return (
            "Fetch Marinade farm stats for the mSOL or LP token. "
            "Shows annual rewards rate, total tokens deposited, number of miners, and rewards per token. "
            "token: 'msol' for the mSOL farm, 'lp' for the mSOL-SOL LP farm."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "token": {
                "type": "string",
                "description": "'msol' or 'lp'",
                "required": True,
            },
            "time": {
                "type": "string",
                "description": "Point-in-time as ISO 8601 datetime (optional)",
                "required": False,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if params.get("token") not in ("msol", "lp"):
            return PluginResult(success=False, error="token must be 'msol' or 'lp'")
        return await _build_action("marinade_farm_stats", params)


# ── 46. TVL History ───────────────────────────────────────────────────────────────

class MarinadeTlvHistoryAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_tlv_history"

    @property
    def description(self) -> str:
        return (
            "Fetch daily TVL (Total Value Locked) snapshots for Marinade Finance over a date range. "
            "Returns staked SOL, liquidity SOL, total SOL, Marinade Native stake, and more per day. "
            "Both 'from' and 'to' are required as ISO 8601 datetime strings."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_tvl_history"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "from": {
                "type": "string",
                "description": "Start date (ISO 8601), e.g. '2025-01-01T00:00:00Z'",
                "required": True,
            },
            "to": {
                "type": "string",
                "description": "End date (ISO 8601), e.g. '2025-04-01T00:00:00Z'",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("from"):
            return PluginResult(success=False, error="'from' date is required (ISO 8601)")
        if not params.get("to"):
            return PluginResult(success=False, error="'to' date is required (ISO 8601)")
        return await _build_action("marinade_tlv_history", params)


# ── 47. TVL (current or point-in-time) ───────────────────────────────────────────

class MarinadeTlvAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_tlv"

    @property
    def description(self) -> str:
        return (
            "Fetch Marinade Finance Total Value Locked (TVL) in SOL and USD. "
            "Shows staked SOL, liquidity SOL, mSOL directed stake, and total TVL. "
            "Optionally query as-of a specific point in time."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_tvl", "marinade_total_value_locked"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "time": {
                "type": "string",
                "description": "Point-in-time as ISO 8601 datetime (optional)",
                "required": False,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_tlv", params)


# ── snapshots-api.marinade.finance ───────────────────────────────────────────────

# ── 48. mSOL Balance (latest snapshot) ───────────────────────────────────────────

class MarinadeSnapshotMsolAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_snapshot_msol"

    @property
    def description(self) -> str:
        return (
            "Fetch the mSOL balance for a wallet address from the latest Marinade snapshot. "
            "Use this to check how much mSOL a specific wallet holds."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_msol_balance", "marinade_check_msol"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pubkey": {
                "type": "string",
                "description": "Base58 public key of the wallet to query",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("pubkey"):
            return PluginResult(success=False, error="pubkey is required")
        return await _build_action("marinade_snapshot_msol", params)


# ── 49. VeMNDE Balance (latest snapshot) ─────────────────────────────────────────

class MarinadeSnapshotVemndeAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_snapshot_vemnde"

    @property
    def description(self) -> str:
        return (
            "Fetch the VeMNDE (Marinade governance token) balance for a wallet address "
            "from the latest Marinade snapshot."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_vemnde_balance", "marinade_check_vemnde"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pubkey": {
                "type": "string",
                "description": "Base58 public key of the wallet to query",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("pubkey"):
            return PluginResult(success=False, error="pubkey is required")
        return await _build_action("marinade_snapshot_vemnde", params)


# ── 50. All Staker Balances for a Wallet ─────────────────────────────────────────

class MarinadeStakersAllAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_stakers_all"

    @property
    def description(self) -> str:
        return (
            "Fetch all Marinade stake balances (mSOL + native stake) for a specific wallet "
            "across date intervals."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_all_balances", "marinade_staking_history"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pubkey": {
                "type": "string",
                "description": "Base58 public key of the wallet",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("pubkey"):
            return PluginResult(success=False, error="pubkey is required")
        return await _build_action("marinade_stakers_all", params)


# ── 51. Native Stake Balance for a Wallet ────────────────────────────────────────

class MarinadeStakersNsAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_stakers_ns"

    @property
    def description(self) -> str:
        return (
            "Fetch the Marinade Native Stake balance for a specific wallet "
            "across date intervals."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_native_stake_balance"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "pubkey": {
                "type": "string",
                "description": "Base58 public key of the wallet",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("pubkey"):
            return PluginResult(success=False, error="pubkey is required")
        return await _build_action("marinade_stakers_ns", params)


# ── 52. All Native Stake Balances (protocol-wide) ────────────────────────────────

class MarinadeStakersNsAllAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_stakers_ns_all"

    @property
    def description(self) -> str:
        return (
            "Fetch Marinade Native Stake balances for all stakers across the protocol. "
            "Returns protocol-wide native stake data. No parameters required."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_stakers_ns_all", {})


# ── 53. Latest mSOL Governance Votes ─────────────────────────────────────────────

class MarinadeVotesMsolLatestAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_votes_msol_latest"

    @property
    def description(self) -> str:
        return (
            "Fetch the latest Marinade mSOL governance votes. "
            "Shows the most recent voting results from mSOL holders. No parameters required."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_governance_votes", "marinade_msol_votes"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_votes_msol_latest", {})


# ── 54. All mSOL Governance Votes (date range) ───────────────────────────────────

class MarinadeVotesMsolAllAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_votes_msol_all"

    @property
    def description(self) -> str:
        return (
            "Fetch all Marinade mSOL governance votes within a date range. "
            "Both startDate and endDate are required as ISO 8601 datetime strings."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "startDate": {
                "type": "string",
                "description": "Start date (ISO 8601), e.g. '2025-01-01T00:00:00Z'",
                "required": True,
            },
            "endDate": {
                "type": "string",
                "description": "End date (ISO 8601), e.g. '2025-04-01T00:00:00Z'",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("startDate"):
            return PluginResult(success=False, error="startDate is required (ISO 8601)")
        if not params.get("endDate"):
            return PluginResult(success=False, error="endDate is required (ISO 8601)")
        return await _build_action("marinade_votes_msol_all", params)


# ── 55. Latest VeMNDE Governance Votes ───────────────────────────────────────────

class MarinadeVotesVemndeLatestAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_votes_vemnde_latest"

    @property
    def description(self) -> str:
        return (
            "Fetch the latest Marinade veMNDE governance votes. "
            "Shows the most recent voting results from veMNDE holders. No parameters required."
        )

    @property
    def aliases(self) -> list[str]:
        return ["marinade_vemnde_votes"]

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {}

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        return await _build_action("marinade_votes_vemnde_latest", {})


# ── 56. All VeMNDE Governance Votes (date range) ─────────────────────────────────

class MarinadeVotesVemndeAllAction(PluginAction):
    @property
    def name(self) -> str:
        return "marinade_votes_vemnde_all"

    @property
    def description(self) -> str:
        return (
            "Fetch all Marinade veMNDE governance votes within a date range. "
            "Both startDate and endDate are required as ISO 8601 datetime strings."
        )

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        return {
            "startDate": {
                "type": "string",
                "description": "Start date (ISO 8601), e.g. '2025-01-01T00:00:00Z'",
                "required": True,
            },
            "endDate": {
                "type": "string",
                "description": "End date (ISO 8601), e.g. '2025-04-01T00:00:00Z'",
                "required": True,
            },
        }

    async def execute(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        if not params.get("startDate"):
            return PluginResult(success=False, error="startDate is required (ISO 8601)")
        if not params.get("endDate"):
            return PluginResult(success=False, error="endDate is required (ISO 8601)")
        return await _build_action("marinade_votes_vemnde_all", params)


# ── Provider: mSOL Stats ─────────────────────────────────────────────────────────

class MarinadeStatsProvider(PluginProvider):
    """Provides live mSOL exchange rate and APY for enriching LLM context"""

    @property
    def name(self) -> str:
        return "marinade_stats"

    @property
    def description(self) -> str:
        return "Live Marinade Finance mSOL price and APY"

    @property
    def cache_ttl(self) -> int:
        return 120  # 2 minutes

    async def fetch(self, params: dict[str, Any], context: PluginContext) -> PluginResult:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                apy_resp = await client.get("https://api.marinade.finance/v1/apy")
                apy_data = apy_resp.json() if apy_resp.is_success else {}
                return PluginResult(
                    success=True,
                    data={
                        "msolMint": MSOL_MINT,
                        "apyPercent": apy_data.get("apy"),
                        "source": "marinade-api",
                    },
                )
        except Exception as exc:
            logger.warning("MarinadeStatsProvider fetch failed: %s", exc)
            return PluginResult(success=False, error=str(exc))


# ── Plugin ──────────────────────────────────────────────────────────────────────

class MarinadePlugin(BasePlugin):
    """
    Marinade Finance plugin — complete liquid staking integration.

    Covers all @marinade.finance/marinade-ts-sdk operations:
    liquid staking, instant & delayed unstaking, native stake account deposits,
    liquidity pool management, and read-only protocol queries.
    """

    @property
    def id(self) -> str:
        return "marinade"

    @property
    def name(self) -> str:
        return "Marinade Finance"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return (
            "Complete Marinade Finance integration on Solana. "
            "Liquid staking (SOL → mSOL), instant & delayed unstaking, native stake account deposits "
            "(activated / deactivating / activating / partial), LST token conversion, "
            "liquidity pool management, delayed unstake ticket queries, referral program, "
            "and full Validators API (scores, commissions, uptimes, rewards, MEV, Jito, reports)."
        )

    @property
    def actions(self) -> list[PluginAction]:
        return [
            # ── Core liquid staking ─────────────────────────────────
            MarinadeStakeAction(),
            MarinadeUnstakeAction(),
            MarinadeDelayedUnstakeAction(),
            MarinadeClaimAction(),
            # ── Native stake account operations ─────────────────────
            MarinadeDepositStakeAction(),           # fully activated
            MarinadeDepositDeactivatingStakeAction(),  # deactivating
            MarinadeDepositActivatingStakeAction(),    # warming-up (beta)
            MarinadePartialDepositStakeAction(),    # partial conversion
            # ── Liquidity pool ──────────────────────────────────────
            MarinadeAddLiquidityAction(),
            MarinadeRemoveLiquidityAction(),
            # ── Ticket management ───────────────────────────────────
            MarinadeListTicketsAction(),            # user's tickets
            MarinadeAllTicketsAction(),             # all protocol tickets
            MarinadeOrderUnstakeWithKeyAction(),    # with explicit pubkey
            MarinadeTicketInfoAction(),             # single ticket
            MarinadeTicketDueDateAction(),          # ETA estimate
            # ── Protocol queries ────────────────────────────────────
            MarinadeStateAction(),
            MarinadeExchangeRateAction(),
            # ── Referral program ────────────────────────────────────
            MarinadeReferralPartnerStateAction(),
            MarinadeReferralGlobalStateAction(),
            MarinadeReferralPartnersAction(),
            # ── Stake pool token operations (beta) ──────────────────
            MarinadeDepositStakePoolTokenAction(),   # LST → mSOL
            MarinadeLiquidateStakePoolTokenAction(), # LST → SOL (atomic)
            # ── Validators API ──────────────────────────────────────
            MarinadeClusterStatsAction(),
            MarinadeValidatorScoresAction(),
            MarinadeScoreBreakdownAction(),
            MarinadeScoreBreakdownsAction(),
            MarinadeValidatorCommissionsAction(),
            MarinadeValidatorUptimesAction(),
            MarinadeValidatorVersionsAction(),
            MarinadeBlockRewardsAction(),
            MarinadeRewardsAction(),
            MarinadeUnstakeHintsAction(),
            MarinadeGlobalUnstakeHintsAction(),
            MarinadeJitoAction(),
            MarinadeMevAction(),
            MarinadeStakingReportAction(),
            MarinadeScoringReportAction(),
            MarinadeCommissionChangesAction(),
            # ── Historical / Market Data (api.marinade.finance) ─────
            MarinadeMsolApyAction(),
            MarinadeLpApyAction(),
            MarinadeLpPriceAction(),
            MarinadeMsolSupplyAction(),
            MarinadeMsolPriceSolAction(),
            MarinadeMsolPriceUsdAction(),
            MarinadeFarmStatsAction(),
            MarinadeTlvHistoryAction(),
            MarinadeTlvAction(),
            # ── Snapshots API (snapshots-api.marinade.finance) ──────
            MarinadeSnapshotMsolAction(),
            MarinadeSnapshotVemndeAction(),
            MarinadeStakersAllAction(),
            MarinadeStakersNsAction(),
            MarinadeStakersNsAllAction(),
            MarinadeVotesMsolLatestAction(),
            MarinadeVotesMsolAllAction(),
            MarinadeVotesVemndeLatestAction(),
            MarinadeVotesVemndeAllAction(),
        ]

    @property
    def providers(self) -> list[PluginProvider]:
        return [MarinadeStatsProvider()]

    @property
    def clients(self) -> list[str]:
        return ["direct", "telegram", "discord"]

    async def on_load(self, context: PluginContext) -> None:
        logger.info("Marinade Finance plugin loaded — 56 actions, 1 provider")

    async def on_unload(self, context: PluginContext) -> None:
        logger.info("Marinade Finance plugin unloaded")

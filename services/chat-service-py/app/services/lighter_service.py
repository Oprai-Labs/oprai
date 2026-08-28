"""Lighter perps — orchestration + encrypted agent-key store.

Sits between the FastAPI routes and the low-level SDK wrapper
(``app.clients.lighter_client``). Owns: the Fernet encryption of agent private
keys, their persistence in ``chat_schema.lighter_agent_keys``, and the two-step
onboarding + trade flows that stitch the store and the SDK together.

Security model (see also lighter_account.py / lighter_client.py):
  • The agent private key is generated server-side, stored ONLY encrypted, and
    never returned to any client after onboarding submit.
  • Withdrawals are gated by Lighter to the L1 owner, so the agent key can trade
    but cannot move funds to a thief — the encrypted row is not a bearer secret
    for the user's balance.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import lighter_client
from app.config import settings
from app.models.lighter_account import LighterAgentKey

log = logging.getLogger(__name__)


# ── encryption ───────────────────────────────────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet  # lazy: keeps import cost off cold paths

    key = (settings.OPRAI_LIGHTER_ENC_KEY or "").strip()
    if not key:
        # Deterministic fallback so the feature works without extra config.
        secret = (settings.OPRAI_JWT_SECRET or "oprai-lighter").encode()
        key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest()).decode()
        log.warning("OPRAI_LIGHTER_ENC_KEY unset — deriving agent-key cipher from JWT secret")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def _decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


# ── store ────────────────────────────────────────────────────────────────────
def _norm(addr: str) -> str:
    return (addr or "").strip().lower()


async def get_agent(session: AsyncSession, l1_address: str) -> LighterAgentKey | None:
    res = await session.execute(
        select(LighterAgentKey).where(LighterAgentKey.l1_address == _norm(l1_address))
    )
    return res.scalar_one_or_none()


async def _upsert_agent(
    session: AsyncSession, *, l1_address: str, wallet_address: str,
    account_id, lighter_account_index: int, api_key_index: int,
    agent_public_key: str, agent_private_key: str, status: str,
) -> LighterAgentKey:
    row = await get_agent(session, l1_address)
    enc = _encrypt(agent_private_key)
    if row is None:
        row = LighterAgentKey(
            l1_address=_norm(l1_address), wallet_address=wallet_address,
            account_id=account_id, lighter_account_index=lighter_account_index,
            api_key_index=api_key_index, agent_public_key=agent_public_key,
            agent_private_key_enc=enc, status=status,
        )
        session.add(row)
    else:
        row.wallet_address = wallet_address
        row.account_id = account_id
        row.lighter_account_index = lighter_account_index
        row.api_key_index = api_key_index
        row.agent_public_key = agent_public_key
        row.agent_private_key_enc = enc
        row.status = status
    await session.flush()
    return row


# ── onboarding (two-step, non-custodial) ─────────────────────────────────────
async def onboard_build(
    session: AsyncSession, *, wallet_address: str, account_id, l1_address: str,
) -> dict:
    """Step 1: mint an agent keypair, persist it (encrypted, status=pending), and
    return the exact message the user's EVM wallet must personal_sign."""
    if not lighter_client.sdk_available():
        return {"error": "Lighter SDK unavailable on this server"}
    account_index = await lighter_client.account_index_for(l1_address)
    if account_index is None:
        # No Lighter account yet → must deposit collateral first (that creates it).
        return {"needs_deposit": True,
                "message": "Deposit USDC to Lighter first — that creates your account."}
    built = lighter_client.onboard_build(account_index)
    row = await _upsert_agent(
        session, l1_address=l1_address, wallet_address=wallet_address,
        account_id=account_id, lighter_account_index=account_index,
        api_key_index=built["api_key_index"], agent_public_key=built["agent_public_key"],
        agent_private_key=built["agent_private_key"], status="pending",
    )
    # Hold the change-pubkey envelope server-side; the client gets only an opaque
    # session id + the message to sign, never tx_info.
    row.pending_tx_type = built["tx_type"]
    row.pending_tx_info = built["tx_info"]
    await session.flush()
    return {
        "account_index": account_index,
        "message_to_sign": built["message_to_sign"],
        "session_id": _norm(l1_address),
        "l1_address": _norm(l1_address),
    }


async def onboard_submit(
    session: AsyncSession, *, l1_address: str, l1_signature: str,
) -> dict:
    """Step 2: inject the wallet's signature into the stored change-pubkey
    envelope, broadcast it, and flip the agent to active."""
    row = await get_agent(session, l1_address)
    if row is None or row.pending_tx_info is None or row.pending_tx_type is None:
        return {"ok": False, "error": "No pending Lighter onboarding for this wallet"}
    agent_priv = _decrypt(row.agent_private_key_enc)
    res = await lighter_client.onboard_submit(
        row.lighter_account_index, row.pending_tx_type, row.pending_tx_info, l1_signature,
        api_key_index=row.api_key_index, agent_private_key=agent_priv,
    )
    if res.get("ok"):
        row.status = "active"
        row.pending_tx_type = None
        row.pending_tx_info = None
        await session.flush()
    return res


# ── trading (agent key) ──────────────────────────────────────────────────────
async def _active_agent(session: AsyncSession, l1_address: str) -> tuple[LighterAgentKey | None, str]:
    row = await get_agent(session, l1_address)
    if row is None:
        return None, "Connect Lighter first (onboarding required)"
    if row.status != "active":
        return None, "Finish Lighter onboarding first (sign the authorisation)"
    return row, ""


async def open_position(
    session: AsyncSession, *, l1_address: str, symbol: str, side: str,
    collateral_usd: float | None = None, base_amount: float | None = None,
    leverage: int | None = None,
) -> dict:
    row, err = await _active_agent(session, l1_address)
    if err:
        return {"error": err}
    agent_priv = _decrypt(row.agent_private_key_enc)
    return await lighter_client.open_position(
        account_index=row.lighter_account_index, agent_private_key=agent_priv,
        symbol=symbol, side=side, collateral_usd=collateral_usd,
        base_amount=base_amount, leverage=leverage, api_key_index=row.api_key_index,
    )


async def close_position(
    session: AsyncSession, *, l1_address: str, symbol: str, position_side: str,
    base_amount: float,
) -> dict:
    row, err = await _active_agent(session, l1_address)
    if err:
        return {"error": err}
    agent_priv = _decrypt(row.agent_private_key_enc)
    return await lighter_client.close_position(
        account_index=row.lighter_account_index, agent_private_key=agent_priv,
        symbol=symbol, position_side=position_side, base_amount=base_amount,
        api_key_index=row.api_key_index,
    )


async def set_leverage(
    session: AsyncSession, *, l1_address: str, symbol: str, leverage: int,
) -> dict:
    row, err = await _active_agent(session, l1_address)
    if err:
        return {"error": err}
    agent_priv = _decrypt(row.agent_private_key_enc)
    return await lighter_client.set_leverage(
        account_index=row.lighter_account_index, agent_private_key=agent_priv,
        symbol=symbol, leverage=leverage, api_key_index=row.api_key_index,
    )


# ── deposit (collateral) ─────────────────────────────────────────────────────
async def deposit_build(
    session: AsyncSession, *, l1_address: str, amount: float | None,
    token: str = "USDC", chain_id: int | None = None,
) -> dict:
    """Build the collateral-deposit step.

    Depositing is the one on-chain action (trades are gas-free). The exact
    Lighter/Robinhood-domain deposit contract + Circle CCTP route are NOT yet
    verified end-to-end, and this moves real funds — so rather than emit an
    unverified transaction, we return an honest, actionable guidance response.
    The card renders `error` when ok is false; nothing is signed.
    """
    return {
        "ok": False,
        "manual": True,
        "l1_address": _norm(l1_address),
        "error": (
            "Automated Lighter deposits aren't live yet. Deposit USDC collateral "
            "to Lighter from the official Lighter/Robinhood app, then come back — "
            "your positions and trading will work here immediately."
        ),
    }


# ── read (market list / account state, no key needed) ────────────────────────
async def markets() -> list[dict]:
    """All active Lighter markets for the trade card (live prices, size decimals,
    min order size, per-market max leverage, maintenance margin, fees)."""
    return await lighter_client.markets()


# ── account state ────────────────────────────────────────────────────────────
async def account_state(session: AsyncSession, l1_address: str) -> dict:
    """Onboarding status + live account/positions for the account card."""
    row = await get_agent(session, l1_address)
    status = row.status if row else "none"
    account_index = await lighter_client.account_index_for(l1_address)
    out: dict = {
        "l1_address": _norm(l1_address),
        "onboarded": status == "active",
        "status": status,
        "has_account": account_index is not None,
        "account_index": account_index,
    }
    if account_index is not None:
        try:
            out["positions"] = await lighter_client.positions(account_index)
        except Exception as e:  # pragma: no cover - read best-effort
            log.warning("lighter positions read failed: %s", e)
            out["positions"] = []
    return out

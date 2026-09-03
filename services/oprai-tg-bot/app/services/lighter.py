"""Lighter perpetuals — stocks and memecoins, leveraged, on Robinhood Chain.

The custody model is different from everything else the bot does, and it works
in our favour: orders are signed server-side with a delegated agent key, off
chain and gas-free. Our custodial signer is needed for exactly two things —
a one-time onboarding signature, and the USDG deposit transaction.

    deposit (on-chain, our signer)  ->  creates the Lighter account
    onboard (personal_sign, ours)   ->  delegates an agent key to the server
    open/close/leverage/withdraw    ->  server-signed, nothing for us to sign

Two chain ids are in play and must not be confused: 4663 is Robinhood Mainnet,
where USDG lives and the deposit goes; 466324 is Lighter's own domain id, used
only inside the server's order signer.
"""

from __future__ import annotations

import asyncio
import re

from app.gateway_client import GatewayError, gateway
from app.services import evm
from app.signer_client import SignerError, signer

USDG_DECIMALS = 6
# The server says one of these when the account hasn't delegated a key yet.
NEEDS_ONBOARD = re.compile(r"onboard|connect lighter|authoris", re.I)


class LighterError(RuntimeError):
    pass


def _error_text(r) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body.get("message") or r.text)[:300]
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code}"


async def _post(jwt: str, path: str, body: dict) -> dict:
    try:
        r = await gateway.post(path, body, jwt=jwt)
    except GatewayError as e:
        raise LighterError(str(e)) from e
    if r.status_code != 200:
        raise LighterError(_error_text(r))
    res = r.json()
    # Lighter answers 200 with an error string rather than a status code.
    if isinstance(res, dict) and (res.get("error") or res.get("ok") is False):
        raise LighterError(str(res.get("error") or "Lighter rejected that"))
    return res


async def _get(jwt: str, path: str, params: dict | None = None) -> dict:
    try:
        r = await gateway.get(path, jwt=jwt, params=params)
    except GatewayError as e:
        raise LighterError(str(e)) from e
    if r.status_code != 200:
        raise LighterError(_error_text(r))
    return r.json()


# ── read ────────────────────────────────────────────────────────────────────
async def account(jwt: str, wallet: str) -> dict:
    """-> {onboarded, status, has_account, collateral, positions: [...]}."""
    return await _get(jwt, "/market/lighter/account", {"wallet": wallet})


async def markets(jwt: str) -> list[dict]:
    res = await _get(jwt, "/market/lighter/markets")
    return res.get("markets") or []


async def market_for(jwt: str, symbol: str) -> dict | None:
    want = symbol.upper().lstrip("$")
    for m in await markets(jwt):
        if (m.get("symbol") or "").upper() == want:
            return m
    return None


def min_collateral_usd(market: dict, leverage: int) -> float:
    """The real floor is the larger of Lighter's two minimums, divided by
    leverage — quoting only one of them sends people into a rejection."""
    price = float(market.get("mark_price") or market.get("last_price") or 0) or 0.0
    min_quote = float(market.get("min_quote_amount") or 0)
    min_base = float(market.get("min_base_amount") or 0)
    notional = max(min_quote, min_base * price)
    return notional / max(leverage, 1)


def position_for(state: dict, symbol: str) -> dict | None:
    want = symbol.upper().lstrip("$")
    for p in state.get("positions") or []:
        if (p.get("symbol") or "").upper() == want and float(p.get("size") or 0) > 0:
            return p
    return None


# ── funding ─────────────────────────────────────────────────────────────────
async def deposit(jwt: str, enc_key_ref: str, wallet: str, amount_usdg: float) -> str:
    """Send USDG to the account's Lighter intent address. Returns the tx hash.

    This is what creates the Lighter account in the first place, so it has to
    happen before onboarding can succeed.
    """
    res = await _post(
        jwt, "/actions/lighter/deposit/build", {"wallet": wallet, "amount": amount_usdg}
    )
    txs = res.get("transactions") or []
    if not txs:
        raise LighterError("couldn't build the deposit just now — try again shortly")
    tx = await evm.build_tx_from_provider(wallet, txs[0])
    try:
        return await evm.send_and_confirm(enc_key_ref, tx, "USDG deposit")
    except evm.EvmError as e:
        raise LighterError(str(e)) from e


async def onboard(jwt: str, enc_key_ref: str, wallet: str) -> dict:
    """Delegate an agent key so the server can sign orders on the user's behalf.

    One EIP-191 signature from the custodial key; after this, trading needs no
    wallet interaction at all.
    """
    built = await _post(jwt, "/actions/lighter/onboard/build", {"wallet": wallet})
    if built.get("needs_deposit"):
        raise LighterError(
            "Deposit USDG to Lighter first — that is what creates your account."
        )
    message = built.get("message_to_sign")
    if not message:
        raise LighterError("onboarding didn't return a message to sign")

    try:
        signed = await signer.sign("evm", enc_key_ref, message)
    except SignerError as e:
        raise LighterError(f"couldn't sign the authorisation: {e}") from e

    return await _post(
        jwt,
        "/actions/lighter/onboard/submit",
        {
            "wallet": wallet,
            "session_id": built.get("session_id"),
            "signature": signed["signature"],
        },
    )


async def wait_for_account(jwt: str, wallet: str, timeout_s: float = 90.0) -> dict:
    """A deposit is swept by Lighter's bridge, not by us, so the account appears
    a little after the transfer confirms."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    state = await account(jwt, wallet)
    while not state.get("has_account") and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(4)
        state = await account(jwt, wallet)
    return state


# ── trading ─────────────────────────────────────────────────────────────────
async def open_position(
    jwt: str,
    enc_key_ref: str,
    wallet: str,
    *,
    symbol: str,
    side: str,
    collateral_usd: float,
    leverage: int = 1,
    order_type: str = "market",
    limit_price: float | None = None,
) -> dict:
    """Open a position, onboarding on the fly if the account hasn't yet.

    Onboarding is a one-time signature the user shouldn't have to know about —
    asking them to run a separate command before their first trade is a good
    way to lose the trade.
    """
    body: dict = {
        "wallet": wallet,
        "symbol": symbol.upper().lstrip("$"),
        "side": side,
        "collateralUsd": collateral_usd,
        "leverage": leverage,
        "orderType": order_type,
    }
    if limit_price is not None:
        body["limitPrice"] = limit_price

    try:
        return await _post(jwt, "/actions/lighter/open", body)
    except LighterError as e:
        if not NEEDS_ONBOARD.search(str(e)):
            raise
        await onboard(jwt, enc_key_ref, wallet)
        return await _post(jwt, "/actions/lighter/open", body)


async def close_position(
    jwt: str, wallet: str, *, symbol: str, side: str, base_amount: float
) -> dict:
    """Close (or reduce) a position. Lighter has no 'close all' — the size has
    to be named, so callers read it from the live position first."""
    return await _post(
        jwt,
        "/actions/lighter/close",
        {
            "wallet": wallet,
            "symbol": symbol.upper().lstrip("$"),
            "side": side,
            "baseAmount": base_amount,
        },
    )


async def set_leverage(jwt: str, wallet: str, *, symbol: str, leverage: int) -> dict:
    return await _post(
        jwt,
        "/actions/lighter/leverage",
        {"wallet": wallet, "symbol": symbol.upper().lstrip("$"), "leverage": leverage},
    )


async def withdraw(jwt: str, wallet: str, amount_usdg: float) -> dict:
    """Agent-signed and gas-free; Lighter sends it to the L1 owner."""
    return await _post(
        jwt, "/actions/lighter/withdraw", {"wallet": wallet, "amount": amount_usdg}
    )

"""Relay: swaps on Robinhood Chain and bridges into it.

Relay is how OPRAI trades on EVM (there is no Jupiter here), and it is also the
on-ramp: the custodial address is the same on every EVM chain, so funds sitting
on Base or Ethereum can be bridged to Robinhood without the user leaving chat.

We reuse the existing gateway pipeline — quote and build come from
`POST /actions/build` (which also applies OPRAI's commission server-side) — and
the bot signs and broadcasts each returned transaction itself.

Relay returns a LIST of steps (an ERC-20 input adds an approval before the
swap). Every step, and every item within it, must be signed, broadcast and
confirmed IN ORDER — signing only the first one approves the token and never
trades it.
"""

from __future__ import annotations

from typing import Any

from app.gateway_client import GatewayError, gateway
from app.services import evm

ROBINHOOD_CHAIN_ID = 4663
# Relay's sentinel for a chain's native asset. NOT 0xEeee… — that is not
# special-cased and would fail the currency lookup.
NATIVE = "0x0000000000000000000000000000000000000000"


class RelayError(RuntimeError):
    pass


def build_params(
    *,
    origin_currency: str,
    destination_currency: str,
    amount: str,
    origin_chain_id: int = ROBINHOOD_CHAIN_ID,
    destination_chain_id: int = ROBINHOOD_CHAIN_ID,
    sender: str | None = None,
    recipient: str | None = None,
    trade_type: str = "EXACT_INPUT",
) -> dict[str, Any]:
    """`amount` is HUMAN units ("0.01") — the service scales it; pre-scaling to
    wei is rejected."""
    p: dict[str, Any] = {
        "originChainId": origin_chain_id,
        "destinationChainId": destination_chain_id,
        "originCurrency": origin_currency,
        "destinationCurrency": destination_currency,
        "amount": amount,
        "tradeType": trade_type,
    }
    if sender:
        p["sender"] = sender
    if recipient:
        p["recipient"] = recipient
    return p


async def _build_action(jwt: str, action_type: str, params: dict) -> dict:
    try:
        r = await gateway.post(
            "/actions/build", {"type": action_type, "params": params}, jwt=jwt
        )
    except GatewayError as e:
        raise RelayError(str(e)) from e
    if r.status_code != 200:
        raise RelayError(_error_text(r))
    return r.json()


def _error_text(r) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body.get("message") or r.text)[:300]
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code}"


async def quote(jwt: str, params: dict) -> dict:
    """Price-only. Returns the raw Relay quote (details/fees/steps)."""
    res = await _build_action(jwt, "relay_get_quote", params)
    q = res.get("quote")
    if not q:
        raise RelayError("no route for that pair right now")
    return q


async def build(jwt: str, params: dict) -> tuple[list[dict], str | None, dict]:
    """-> (execution_steps, request_id, quote). Nothing is signed yet."""
    res = await _build_action(jwt, "relay_bridge", params)
    steps = res.get("executionSteps") or []
    q = res.get("quote") or {}
    if not steps:
        raise RelayError("Relay returned no transactions to execute")
    return steps, q.get("requestId"), q


def summarize(q: dict) -> dict:
    """Pull the few numbers a person actually needs out of a Relay quote."""
    details = q.get("details") or {}
    cin = details.get("currencyIn") or {}
    cout = details.get("currencyOut") or {}

    def side(c: dict) -> dict:
        cur = c.get("currency") or {}
        return {
            "symbol": cur.get("symbol") or "?",
            "amount": c.get("amountFormatted") or c.get("amount") or "?",
            "usd": c.get("amountUsd"),
        }

    return {
        "in": side(cin),
        "out": side(cout),
        "min_out": (cout.get("minimumAmount")),
        "impact": details.get("priceImpact"),
        "eta_s": details.get("estimatedTime"),
    }


def count_transactions(steps: list[dict]) -> int:
    return sum(
        1
        for s in steps
        for it in (s.get("items") or [])
        if (it.get("data") or {}).get("to")
    )


async def execute_steps(
    enc_key_ref: str,
    from_addr: str,
    steps: list[dict],
    on_step=None,
) -> list[str]:
    """Sign, broadcast and confirm EVERY step in order.

    Relay's array is the whole plan: with an ERC-20 input the first step is an
    approval and the trade is a later one, so stopping after the first would
    approve the token and never trade it. Each transaction must confirm before
    the next is built, because the next one depends on its state.
    """
    hashes: list[str] = []
    total = count_transactions(steps)
    for step in steps:
        for item in step.get("items") or []:
            data = item.get("data") or {}
            if not data.get("to"):
                continue  # nothing to broadcast (e.g. a signature-only step)

            tx = await evm.build_tx_from_provider(from_addr, data)
            n = len(hashes) + 1
            if on_step:
                await on_step(n, total, step.get("type") or "step")
            try:
                hashes.append(
                    await evm.send_and_confirm(enc_key_ref, tx, f"step {n}/{total}")
                )
            except evm.EvmError as e:
                raise RelayError(str(e)) from e
    if not hashes:
        raise RelayError("Relay returned no broadcastable transaction")
    return hashes


async def record(jwt: str, request_id: str) -> None:
    """Book the fill for volume/tier/cashback. Best-effort: a failure here must
    never make a settled trade look failed."""
    try:
        await gateway.post("/actions/relay/record", {"requestId": request_id}, jwt=jwt)
    except GatewayError:
        pass


async def intent_status(jwt: str, request_id: str) -> dict:
    r = await gateway.get(
        "/actions/relay/intent-status", jwt=jwt, params={"requestId": request_id}
    )
    if r.status_code != 200:
        raise RelayError(_error_text(r))
    return r.json()

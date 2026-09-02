"""Uniswap on Robinhood Chain — where the tokenized stocks actually trade.

NVDA, TSLA and the rest sit in Uniswap V3 pools (USDG/… and WETH/…), not in
Relay's routes, so every stock trade goes through here. Quote and swap-building
come from the existing gateway pipeline; the bot signs and broadcasts.

An ERC-20 input needs up to three transactions/signatures in order:
  1. approve Permit2 to move the token (only when `approval` is returned),
  2. an EIP-712 Permit2 signature (only when `needsPermit`),
  3. the swap itself.
A native-ETH input needs none of the first two.
"""

from __future__ import annotations

from app.gateway_client import GatewayError, gateway
from app.services import evm
from app.signer_client import SignerError, signer

NATIVE = "0x0000000000000000000000000000000000000000"


class UniswapError(RuntimeError):
    pass


def _error_text(r) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body.get("message") or r.text)[:300]
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code}"


def build_params(
    *,
    origin_currency: str,
    destination_currency: str,
    amount: str,
    sender: str,
    recipient: str | None = None,
    chain_id: int = evm.CHAIN_ID,
    trade_type: str = "EXACT_INPUT",
) -> dict:
    """`amount` is HUMAN units — the service scales it using the token's
    decimals (read from the chain for tokens Relay's list doesn't carry)."""
    return {
        "originChainId": chain_id,
        "destinationChainId": chain_id,
        "originCurrency": origin_currency,
        "destinationCurrency": destination_currency,
        "amount": amount,
        "tradeType": trade_type,
        "sender": sender,
        "recipient": recipient or sender,
    }


async def quote(jwt: str, params: dict) -> dict:
    """Price a same-chain swap. Returns the full quote result, which carries the
    display fields, the opaque `quote`, and any `approval`/`permitData`."""
    try:
        r = await gateway.post("/actions/uniswap/quote", params, jwt=jwt)
    except GatewayError as e:
        raise UniswapError(str(e)) from e
    if r.status_code != 200:
        raise UniswapError(_error_text(r))
    return r.json()


def summarize(q: dict) -> dict:
    return {
        "in_amount": q.get("inputAmountDisplay"),
        "in_symbol": q.get("inputSymbol"),
        "out_amount": q.get("outputAmountDisplay"),
        "out_symbol": q.get("outputSymbol"),
        "impact": q.get("priceImpact"),
        "slippage": q.get("slippage"),
        "gas_usd": q.get("estimatedGasUsd"),
        "needs_permit": bool(q.get("needsPermit")),
        "has_approval": bool(q.get("approval")),
    }


async def build_swap(
    jwt: str, quote_obj: dict, permit_data: dict | None, signature: str | None
) -> dict:
    """-> the unsigned swap transaction."""
    body: dict = {"quote": quote_obj}
    if permit_data is not None:
        body["permitData"] = permit_data
    if signature is not None:
        body["signature"] = signature
    try:
        r = await gateway.post("/actions/uniswap/swap", body, jwt=jwt)
    except GatewayError as e:
        raise UniswapError(str(e)) from e
    if r.status_code != 200:
        raise UniswapError(_error_text(r))
    return r.json()


def transaction_count(quote_result: dict) -> int:
    """How many on-chain transactions this trade will take (the permit is a
    signature, not a transaction)."""
    return 1 + (1 if quote_result.get("approval") else 0)


async def execute(
    jwt: str,
    enc_key_ref: str,
    from_addr: str,
    quote_result: dict,
    on_step=None,
) -> list[str]:
    """Approve (if needed) -> permit (if needed) -> swap. Returns tx hashes.

    Each transaction is confirmed before the next is built: the swap depends on
    the approval being mined, and a permit signed against an unapproved token
    would revert.
    """
    hashes: list[str] = []
    total = transaction_count(quote_result)

    approval = quote_result.get("approval")
    if approval:
        if on_step:
            await on_step(1, total, "approve")
        tx = await evm.build_tx_from_provider(from_addr, approval)
        try:
            hashes.append(await evm.send_and_confirm(enc_key_ref, tx, "approval"))
        except evm.EvmError as e:
            raise UniswapError(str(e)) from e

    signature = None
    permit_data = quote_result.get("permitData")
    if quote_result.get("needsPermit"):
        if not permit_data:
            raise UniswapError("a permit is required but the quote carried none")
        try:
            signed = await signer.sign_typed_data(enc_key_ref, permit_data)
        except SignerError as e:
            raise UniswapError(f"permit signing failed: {e}") from e
        signature = signed["signature"]

    swap_tx = await build_swap(
        jwt, quote_result.get("quote"), permit_data if signature else None, signature
    )
    if on_step:
        await on_step(len(hashes) + 1, total, "swap")
    tx = await evm.build_tx_from_provider(from_addr, swap_tx)
    try:
        hashes.append(await evm.send_and_confirm(enc_key_ref, tx, "swap"))
    except evm.EvmError as e:
        raise UniswapError(str(e)) from e
    return hashes

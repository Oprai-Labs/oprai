"""Robinhood Chain transaction pipeline: build -> sign -> submit -> confirm.

The bot builds the transaction from live chain state (nonce, EIP-1559 fees, gas
estimate), hands the UNSIGNED fields to the isolated signer, and submits the
returned raw tx to the node. The bot never sees a private key; the signer never
decides policy.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import settings
from app.logging_config import log
from app.services import chains
from app.signer_client import signer

CHAIN_ID = 4663  # Robinhood Chain
NATIVE_TRANSFER_GAS = 21_000
# ERC-20 transfer(address,uint256)
ERC20_TRANSFER_SELECTOR = "a9059cbb"


class EvmError(RuntimeError):
    pass


def rpc_urls(chain_id: int = CHAIN_ID) -> list[str]:
    """Every endpoint worth trying for this chain, best first.

    Our own node is fastest and freshest, and it is also the one that goes
    quiet for an hour while it prunes. The fallback exists so that hour costs
    latency rather than every chain read the bot makes.
    """
    urls = [rpc_url(chain_id)]
    fallback = settings.OPRAI_TG_RPC_FALLBACK
    if int(chain_id) == CHAIN_ID and fallback and fallback not in urls:
        urls.append(fallback)
    return urls


def rpc_url(chain_id: int = CHAIN_ID) -> str:
    """Home chain honours the bot's override (our own node in prod); every other
    chain comes from the shared registry."""
    if int(chain_id) == CHAIN_ID:
        return settings.robinhood_rpc()
    return chains.rpc_for(chain_id)


def _why(e: Exception) -> str:
    """Never report an empty reason.

    Several httpx errors stringify to nothing, so "rpc unreachable: {e}" came
    out as "rpc unreachable:" — a sentence that stops at the colon and tells a
    reader less than saying nothing would. It reached the logs during an
    outage and told us precisely nothing about it.
    """
    detail = str(e).strip()
    return detail or e.__class__.__name__


async def rpc(method: str, params: list | None = None, chain_id: int = CHAIN_ID) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    # One retry on a transport failure: a dropped keep-alive or a node
    # restarting is a blip, and treating it as an outage stops a whole polling
    # cycle for something that costs half a second to ride out.
    urls = rpc_urls(chain_id)

    r = None
    for index, url in enumerate(urls):
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(url, json=payload)
            break
        except httpx.HTTPError as e:
            if index == 0 and len(urls) == 1:
                # Nothing to fall back to: ride out a blip, then give up.
                await asyncio.sleep(0.4)
                try:
                    async with httpx.AsyncClient(timeout=15.0) as c:
                        r = await c.post(url, json=payload)
                    break
                except httpx.HTTPError as again:
                    raise EvmError(f"rpc unreachable: {_why(again)}") from again
            if index + 1 < len(urls):
                log.info("rpc_fallback", why=_why(e))
                continue
            raise EvmError(f"rpc unreachable: {_why(e)}") from e
    if r.status_code != 200:
        raise EvmError(f"rpc HTTP {r.status_code}")
    body = r.json()
    if "error" in body:
        raise EvmError(str(body["error"].get("message", body["error"])))
    return body.get("result")


async def rpc_batch(reqs: list[tuple[str, list]], chain_id: int = CHAIN_ID) -> list[Any]:
    """One HTTP round-trip for many calls. Returns results in request order;
    an entry is None where that individual call errored."""
    if not reqs:
        return []
    payload = [
        {"jsonrpc": "2.0", "id": i, "method": m, "params": p}
        for i, (m, p) in enumerate(reqs)
    ]
    # Public RPCs rate-limit batches; back off and retry rather than losing the
    # whole sync. Our own node (prod ROBINHOOD_RPC) doesn't hit this.
    urls = rpc_urls(chain_id)
    r = None
    unreachable: Exception | None = None
    for index, url in enumerate(urls):
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=30.0) as c:
                    r = await c.post(url, json=payload)
            except httpx.HTTPError as e:
                unreachable, r = e, None
                break
            if r.status_code != 429:
                break
            await asyncio.sleep(1.5 * (attempt + 1))
        if r is not None:
            break
        if index + 1 < len(urls):
            log.info("rpc_batch_fallback", why=_why(unreachable))
    if r is None and unreachable is not None:
        raise EvmError(f"rpc unreachable: {_why(unreachable)}") from unreachable
    if r is None or r.status_code != 200:
        raise EvmError(f"rpc HTTP {r.status_code if r else 'no response'}")
    body = r.json()
    if isinstance(body, dict):  # node rejected the batch as a whole
        raise EvmError(str(body.get("error", "batch rejected")))
    out: list[Any] = [None] * len(reqs)
    for item in body:
        idx = item.get("id")
        if isinstance(idx, int) and 0 <= idx < len(out) and "result" in item:
            out[idx] = item["result"]
    return out


def _hex_to_int(v: str | int | None, default: int = 0) -> int:
    if v is None:
        return default
    if isinstance(v, int):
        return v
    return int(v, 16) if v.startswith("0x") else int(v)


async def get_nonce(address: str, chain_id: int = CHAIN_ID) -> int:
    """Pending nonce, so back-to-back sends don't collide."""
    return _hex_to_int(
        await rpc("eth_getTransactionCount", [address, "pending"], chain_id)
    )


async def get_fees(chain_id: int = CHAIN_ID) -> tuple[int, int]:
    """(max_fee_per_gas, max_priority_fee_per_gas) in wei, EIP-1559."""
    block = await rpc("eth_getBlockByNumber", ["latest", False], chain_id)
    base = _hex_to_int((block or {}).get("baseFeePerGas"), 0)
    try:
        priority = _hex_to_int(await rpc("eth_maxPriorityFeePerGas", None, chain_id))
    except EvmError:
        priority = 0
    # Headroom for a couple of base-fee bumps while the tx is pending.
    max_fee = base * 2 + priority
    if max_fee == 0:  # node reported no base fee (unlikely) — fall back
        max_fee = max(priority, 10**8)
    return max_fee, priority


async def estimate_gas(
    from_: str, to: str, value_wei: int, data: str = "0x", chain_id: int = CHAIN_ID
) -> int:
    call = {"from": from_, "to": to, "value": hex(value_wei), "data": data or "0x"}
    try:
        est = _hex_to_int(await rpc("eth_estimateGas", [call], chain_id))
    except EvmError as e:
        # A bare native send is always 21k, and the caller's own balance check
        # gives a better message than the node's refusal — so answer the gas
        # question here rather than failing the whole flow.
        if (data or "0x") == "0x":
            return NATIVE_TRANSFER_GAS
        # For a call, a node won't estimate what the sender can't afford. That
        # is an affordability answer, not a gas one, and the raw RPC sentence is
        # not something to show a person.
        if "insufficient funds" in str(e).lower():
            raise EvmError(
                "this wallet doesn't hold enough ETH to cover the amount plus gas"
            ) from e
        raise
    return int(est * 1.2)  # buffer for state drift between estimate and inclusion


def encode_erc20_transfer(to: str, amount: int) -> str:
    """calldata for transfer(address,uint256)."""
    addr = to.lower().removeprefix("0x").rjust(64, "0")
    amt = f"{amount:x}".rjust(64, "0")
    return f"0x{ERC20_TRANSFER_SELECTOR}{addr}{amt}"


async def build_transfer(
    from_: str, to: str, value_wei: int, data: str = "0x", chain_id: int = CHAIN_ID
) -> dict[str, str]:
    """Build an EIP-1559 transfer from live chain state. Amounts as strings."""
    nonce = await get_nonce(from_, chain_id)
    max_fee, priority = await get_fees(chain_id)
    gas = await estimate_gas(from_, to, value_wei, data, chain_id)
    return {
        "chain_id": str(chain_id),
        "nonce": str(nonce),
        "to": to,
        "value": str(value_wei),
        "data": data or "0x",
        "gas": str(gas),
        "max_fee_per_gas": str(max_fee),
        "max_priority_fee_per_gas": str(priority),
    }


def to_int(v: str | int | None, default: int = 0) -> int:
    """Providers mix decimal strings, 0x-hex and ints in the same fields."""
    if v is None or v == "":
        return default
    if isinstance(v, int):
        return v
    s = str(v).strip()
    return int(s, 16) if s.startswith("0x") else int(s)


async def build_tx_from_provider(
    from_addr: str, data: dict, chain_id: int | None = None
) -> dict[str, str]:
    """Complete a provider's unsigned transaction (a Relay step or a Uniswap
    swap) into a signable EIP-1559 tx.

    Providers hand back to/data/value and sometimes gas and fees, as decimal
    strings, and never a nonce — that is ours to supply, freshly, per
    transaction, because each one is broadcast only after the previous confirms.
    """
    # A bridge step executes on its ORIGIN chain, so the provider's own chainId
    # wins over our home chain.
    chain = int(chain_id or to_int(data.get("chainId"), CHAIN_ID) or CHAIN_ID)
    to = data.get("to") or ""
    if not to:
        raise EvmError("provider returned a transaction with no recipient")
    calldata = data.get("data") or "0x"
    value = to_int(data.get("value"))

    gas = to_int(data.get("gas") or data.get("gasLimit"))
    if gas <= 0:
        gas = await estimate_gas(from_addr, to, value, calldata, chain)

    max_fee = to_int(data.get("maxFeePerGas"))
    priority = to_int(data.get("maxPriorityFeePerGas"))
    if max_fee <= 0:
        max_fee, priority = await get_fees(chain)

    return {
        "chain_id": str(chain),
        "nonce": str(await get_nonce(from_addr, chain)),
        "to": to,
        "value": str(value),
        "data": calldata,
        "gas": str(gas),
        "max_fee_per_gas": str(max_fee),
        "max_priority_fee_per_gas": str(priority),
    }


async def send_and_confirm(enc_key_ref: str, tx: dict[str, str], label: str = "transaction") -> str:
    """Sign, broadcast and wait for the receipt. Raises unless it succeeded."""
    chain = to_int(tx.get("chain_id"), CHAIN_ID)
    tx_hash = await sign_and_send(enc_key_ref, tx)
    receipt = await wait_receipt(tx_hash, chain_id=chain)
    if receipt is None:
        raise EvmError(
            f"{label} is taking longer than expected ({tx_hash[:10]}…) — "
            "check the explorer before retrying"
        )
    if not receipt_succeeded(receipt):
        raise EvmError(f"{label} reverted ({tx_hash[:10]}…)")
    return tx_hash


def tx_cost_wei(tx: dict[str, str]) -> int:
    """Worst-case debit: value + gas * max_fee — what the sender must hold."""
    return int(tx["value"]) + int(tx["gas"]) * int(tx["max_fee_per_gas"])


async def sign_and_send(enc_key_ref: str, tx: dict[str, str]) -> str:
    """Sign via the signer and broadcast — on the tx's OWN chain."""
    signed = await signer.sign_tx(enc_key_ref, tx)
    chain = to_int(tx.get("chain_id"), CHAIN_ID)
    return await rpc("eth_sendRawTransaction", [signed["raw"]], chain)


async def wait_receipt(
    tx_hash: str, timeout_s: float = 60.0, chain_id: int = CHAIN_ID
) -> dict | None:
    """Poll for the receipt. None on timeout — a pending tx is not a failure."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        receipt = await rpc("eth_getTransactionReceipt", [tx_hash], chain_id)
        if receipt:
            return receipt
        await asyncio.sleep(1.5)
    return None


def receipt_succeeded(receipt: dict) -> bool:
    return _hex_to_int(receipt.get("status"), 0) == 1

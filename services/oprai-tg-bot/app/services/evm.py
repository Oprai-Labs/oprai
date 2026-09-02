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
from app.signer_client import signer

CHAIN_ID = 4663  # Robinhood Chain
NATIVE_TRANSFER_GAS = 21_000
# ERC-20 transfer(address,uint256)
ERC20_TRANSFER_SELECTOR = "a9059cbb"


class EvmError(RuntimeError):
    pass


async def rpc(method: str, params: list | None = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(settings.robinhood_rpc(), json=payload)
    except httpx.HTTPError as e:
        raise EvmError(f"rpc unreachable: {e}") from e
    if r.status_code != 200:
        raise EvmError(f"rpc HTTP {r.status_code}")
    body = r.json()
    if "error" in body:
        raise EvmError(str(body["error"].get("message", body["error"])))
    return body.get("result")


def _hex_to_int(v: str | int | None, default: int = 0) -> int:
    if v is None:
        return default
    if isinstance(v, int):
        return v
    return int(v, 16) if v.startswith("0x") else int(v)


async def get_nonce(address: str) -> int:
    """Pending nonce, so back-to-back sends don't collide."""
    return _hex_to_int(await rpc("eth_getTransactionCount", [address, "pending"]))


async def get_fees() -> tuple[int, int]:
    """(max_fee_per_gas, max_priority_fee_per_gas) in wei, EIP-1559."""
    block = await rpc("eth_getBlockByNumber", ["latest", False])
    base = _hex_to_int((block or {}).get("baseFeePerGas"), 0)
    try:
        priority = _hex_to_int(await rpc("eth_maxPriorityFeePerGas"))
    except EvmError:
        priority = 0
    # Headroom for a couple of base-fee bumps while the tx is pending.
    max_fee = base * 2 + priority
    if max_fee == 0:  # node reported no base fee (unlikely) — fall back
        max_fee = max(priority, 10**8)
    return max_fee, priority


async def estimate_gas(from_: str, to: str, value_wei: int, data: str = "0x") -> int:
    call = {"from": from_, "to": to, "value": hex(value_wei), "data": data or "0x"}
    try:
        est = _hex_to_int(await rpc("eth_estimateGas", [call]))
    except EvmError:
        # A bare native send is always 21k; anything else must surface the error.
        if (data or "0x") == "0x":
            return NATIVE_TRANSFER_GAS
        raise
    return int(est * 1.2)  # buffer for state drift between estimate and inclusion


def encode_erc20_transfer(to: str, amount: int) -> str:
    """calldata for transfer(address,uint256)."""
    addr = to.lower().removeprefix("0x").rjust(64, "0")
    amt = f"{amount:x}".rjust(64, "0")
    return f"0x{ERC20_TRANSFER_SELECTOR}{addr}{amt}"


async def build_transfer(
    from_: str, to: str, value_wei: int, data: str = "0x"
) -> dict[str, str]:
    """Build an EIP-1559 transfer from live chain state. Amounts as strings."""
    nonce = await get_nonce(from_)
    max_fee, priority = await get_fees()
    gas = await estimate_gas(from_, to, value_wei, data)
    return {
        "chain_id": str(CHAIN_ID),
        "nonce": str(nonce),
        "to": to,
        "value": str(value_wei),
        "data": data or "0x",
        "gas": str(gas),
        "max_fee_per_gas": str(max_fee),
        "max_priority_fee_per_gas": str(priority),
    }


def tx_cost_wei(tx: dict[str, str]) -> int:
    """Worst-case debit: value + gas * max_fee — what the sender must hold."""
    return int(tx["value"]) + int(tx["gas"]) * int(tx["max_fee_per_gas"])


async def sign_and_send(enc_key_ref: str, tx: dict[str, str]) -> str:
    """Sign via the signer and broadcast. Returns the tx hash."""
    signed = await signer.sign_tx(enc_key_ref, tx)
    tx_hash = await rpc("eth_sendRawTransaction", [signed["raw"]])
    return tx_hash


async def wait_receipt(tx_hash: str, timeout_s: float = 60.0) -> dict | None:
    """Poll for the receipt. None on timeout — a pending tx is not a failure."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        receipt = await rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt:
            return receipt
        await asyncio.sleep(1.5)
    return None


def receipt_succeeded(receipt: dict) -> bool:
    return _hex_to_int(receipt.get("status"), 0) == 1

"""SushiSwap — same-chain swaps on Robinhood Chain.

Sushi is the deepest venue for the chain's own pairs (USDG, USDe, WETH), which
is exactly where Relay is being asked to do a job it wasn't built for: Relay
routes value between chains, and using it for a swap that never leaves 4663
means paying a bridge to do a DEX's work.

There is no Permit2 here — a plain ERC-20 approve, and only when the allowance
is short. The approval comes first and must confirm before the swap, or the
swap's transferFrom races an allowance that hasn't been mined.
"""

from __future__ import annotations

from app.gateway_client import GatewayError, gateway
from app.services import evm

CHAIN_ID = 4663
# What the builder treats as "the chain's own coin" rather than a token.
NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"


class SushiError(RuntimeError):
    pass


def _error_text(r) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body.get("message") or r.text)[:300]
    except Exception:  # noqa: BLE001
        return f"HTTP {r.status_code}"


async def swap(jwt: str, *, wallet: str, token_in: str, token_out: str,
               amount: float, slippage_pct: float = 0.5) -> dict:
    """Quote and build in one call — Sushi returns the transactions with the
    numbers, so there is no separate quote step to go stale.

    `amount` is in human units; the builder scales it by the input token's own
    on-chain decimals, which is the only source that is right for both USDG (6)
    and everything else (18).
    """
    body = {
        "tokenIn": token_in,
        "tokenOut": token_out,
        "walletAddress": wallet,
        "amount": str(amount),
        "slippagePct": slippage_pct,
    }
    try:
        r = await gateway.post("/actions/sushi/swap", body, jwt=jwt)
    except GatewayError as e:
        raise SushiError(str(e)) from e
    if r.status_code != 200:
        raise SushiError(_error_text(r))

    res = r.json() or {}
    if not res.get("transactions"):
        raise SushiError("no route for that pair right now")
    return res


def summarize(res: dict) -> dict:
    """The numbers a person needs, named the way the other venues name them so
    a caller can compare quotes without knowing which venue produced them."""
    return {
        "venue": "sushi",
        "out_amount": res.get("expectedAmountOut"),
        "impact": res.get("priceImpact"),
        "price": res.get("swapPrice"),
        "steps": len(res.get("transactions") or []),
    }


def transaction_count(res: dict) -> int:
    return len(res.get("transactions") or [])


async def execute(enc_key_ref: str, wallet: str, res: dict, on_step=None) -> list[str]:
    """Approval first, swap last, each confirmed before the next."""
    txs = res.get("transactions") or []
    hashes: list[str] = []
    for i, data in enumerate(txs, start=1):
        if on_step:
            await on_step(i, len(txs))
        tx = await evm.build_tx_from_provider(wallet, data, chain_id=CHAIN_ID)
        try:
            hashes.append(
                await evm.send_and_confirm(enc_key_ref, tx, f"step {i}/{len(txs)}")
            )
        except evm.EvmError as e:
            raise SushiError(str(e)) from e
    return hashes

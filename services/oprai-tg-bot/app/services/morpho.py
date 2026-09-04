"""Morpho Blue — lending and borrowing on Robinhood Chain.

Every market here lends USDG against a different collateral, so "which market"
is really "which collateral", and that is how the commands ask it. Supplying
is the exception: all four accept USDG, so the choice is which rate to take.

Two things about the transactions matter. The service builds any ERC-20
approval for us and puts it FIRST, and the Morpho call is always LAST — so the
steps must be sent in order, each confirmed before the next, or the transfer
races an allowance that has not been mined. And amounts go over the wire in
base units read from the market's own decimals: USDG is 6 where the collateral
is often 18, and one assumption there is a millionfold error.
"""

from __future__ import annotations

from decimal import Decimal

from app.gateway_client import GatewayError, gateway
from app.services import evm

CHAIN_ID = 4663


class MorphoError(RuntimeError):
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
        raise MorphoError(str(e)) from e
    if r.status_code != 200:
        raise MorphoError(_error_text(r))
    return r.json() or {}


# ── reads ───────────────────────────────────────────────────────────────────
async def markets(jwt: str, limit: int = 20) -> list[dict]:
    res = await _post(
        jwt, "/actions/build",
        {"type": "morpho_markets", "params": {"chain": CHAIN_ID, "limit": limit}},
    )
    return ((res.get("data") or {}).get("markets")) or []


async def positions(jwt: str, wallet: str) -> list[dict]:
    """Only this chain's positions.

    The read answers for every chain Morpho is deployed on, and showing a
    Robinhood user their Base position would be answering a question they
    didn't ask.
    """
    res = await _post(
        jwt, "/actions/build",
        {"type": "morpho_positions", "params": {"wallet": wallet}},
    )
    rows = ((res.get("data") or {}).get("positions")) or []
    return [
        _to_human(p) for p in rows if int(p.get("chainId") or 0) == CHAIN_ID
    ]


def _to_human(position: dict) -> dict:
    """Scale the amounts once, here, rather than in every place that shows them.

    Morpho reports supply, borrow and collateral in BASE units and hands us the
    decimals to divide by. Showing them raw made 1,480,746 out of 1.48 USDG —
    a million times the truth, on a wallet holding under a dollar. Every
    consumer got it wrong because each was trusting a number that looked
    already scaled.
    """
    out = dict(position)
    loan_decimals = int(position.get("loanDecimals") or 18)
    collateral_decimals = int(position.get("collateralDecimals") or 18)
    for field, decimals in (
        ("supplyAssets", loan_decimals),
        ("borrowAssets", loan_decimals),
        ("collateral", collateral_decimals),
    ):
        raw = position.get(field)
        if raw is None:
            continue
        try:
            out[field] = float(Decimal(str(raw)) / (10 ** decimals))
        except (ArithmeticError, ValueError):
            out[field] = 0.0
    return out


def market_for_collateral(rows: list[dict], symbol: str) -> dict | None:
    want = symbol.upper().lstrip("$")
    for m in rows:
        if (m.get("collateralSymbol") or "").upper() == want:
            return m
    return None


def best_supply_market(rows: list[dict]) -> dict | None:
    """The market paying most to supply. Every market here lends USDG, so a
    supplier is choosing a rate, and defaulting to anything but the best one
    costs them money for no reason."""
    return max(rows, key=lambda m: float(m.get("supplyApy") or 0), default=None)


def base_units(amount: float | str, decimals: int) -> str:
    """Base units as a string. The builder takes `amountBaseUnits` verbatim,
    which keeps the scaling on our side of the wire where the decimals are
    known, instead of re-parsing a human number downstream."""
    return str(int(Decimal(str(amount)) * (10**int(decimals))))


def apy_pct(value) -> float:
    return float(value or 0) * 100


# ── writes ──────────────────────────────────────────────────────────────────
async def build_supply(jwt: str, wallet: str, market: dict, amount: float) -> dict:
    return await _post(jwt, "/actions/morpho/supply", {
        "marketId": market["marketId"],
        "walletAddress": wallet,
        "chain": CHAIN_ID,
        "amountBaseUnits": base_units(amount, market["loanDecimals"]),
    })


async def build_borrow(jwt: str, wallet: str, market: dict, *, borrow: float,
                       collateral: float = 0) -> dict:
    """Post collateral and borrow in one go.

    Collateral is optional because someone topping up an existing loan already
    has it posted; passing zero simply skips that leg.
    """
    body = {
        "marketId": market["marketId"],
        "walletAddress": wallet,
        "chain": CHAIN_ID,
        "borrowBaseUnits": base_units(borrow, market["loanDecimals"]),
    }
    if collateral > 0:
        body["collateralBaseUnits"] = base_units(
            collateral, market["collateralDecimals"]
        )
    return await _post(jwt, "/actions/morpho/borrow", body)


async def build_repay(jwt: str, wallet: str, market: dict,
                      amount: float | None) -> dict:
    """`amount=None` repays everything.

    Repaying "all" by a number cannot work: interest accrues between the quote
    and the block, so a repayment sized now is always slightly short. The
    builder has a sentinel that repays by shares instead, which is the only
    way to actually close a loan.
    """
    body = {
        "marketId": market["marketId"],
        "walletAddress": wallet,
        "chain": CHAIN_ID,
    }
    if amount is None:
        body["max"] = True
    else:
        body["amountBaseUnits"] = base_units(amount, market["loanDecimals"])
    return await _post(jwt, "/actions/morpho/repay", body)


async def build_withdraw(jwt: str, wallet: str, market: dict,
                         amount: float | None, *, target: str = "supply") -> dict:
    """Take back what was supplied (`target='supply'`) or collateral that is no
    longer backing a loan (`target='collateral'`)."""
    body = {
        "marketId": market["marketId"],
        "walletAddress": wallet,
        "chain": CHAIN_ID,
        "target": target,
    }
    if amount is None:
        body["max"] = True
    else:
        decimals = (
            market["loanDecimals"] if target == "supply"
            else market["collateralDecimals"]
        )
        body["amountBaseUnits"] = base_units(amount, decimals)
    return await _post(jwt, "/actions/morpho/withdraw", body)


async def execute(enc_key_ref: str, wallet: str, built: dict, on_step=None) -> list[str]:
    """Send every transaction in order, waiting for each.

    The approval comes first and the Morpho call last; sending them together
    means the Morpho call can be mined before the allowance exists.
    """
    txs = built.get("transactions") or []
    if not txs:
        raise MorphoError("nothing to sign — try again in a moment")

    hashes: list[str] = []
    for i, data in enumerate(txs, start=1):
        if on_step:
            await on_step(i, len(txs))
        tx = await evm.build_tx_from_provider(wallet, data)
        try:
            hashes.append(
                await evm.send_and_confirm(enc_key_ref, tx, f"step {i}/{len(txs)}")
            )
        except evm.EvmError as e:
            raise MorphoError(str(e)) from e
    return hashes

"""Executing a copied buy.

The engine decides *whether* and *how much*; this spends it. It buys the token
with ETH from the user's own custodial wallet, through the same venue
comparison a manual /swap uses — a copied trade should not get a worse fill
than one the user typed themselves.

Copying spends money without anyone watching, so every refusal here happens
before a transaction is signed, and a copy that cannot be afforded is skipped
rather than half-attempted. The engine's own limits (per-trade ETH, daily USD)
have already been applied by the time we are called.
"""

from __future__ import annotations

from decimal import Decimal

from app.logging_config import log
from app.services import auth as auth_svc
from app.services import evm, relay, sushi
from app.services import portfolio as pf
from app.services import wallet as wallet_svc


class CopyExecutionError(RuntimeError):
    pass


# USDG is the chain's dollar, and Sushi is where we would actually trade — so
# the price enforcing someone's daily dollar cap is the price they would get,
# not a number written down months ago.
USDG_DECIMALS = 6
_PRICE_TTL_SECONDS = 120
_price_cache: dict[str, float] = {}
_price_at: dict[str, float] = {}


async def eth_price_usd(telegram_id: int) -> float | None:
    """What one ETH is worth in USDG, read from the chain.

    Returns None when it can't be read: a daily spend cap enforced against a
    guessed price is not a cap, so the caller must skip rather than trade.
    """
    import time

    now = time.monotonic()
    if _price_cache.get("eth") and now - _price_at.get("eth", 0) < _PRICE_TTL_SECONDS:
        return _price_cache["eth"]

    try:
        jwt = await auth_svc.get_jwt(telegram_id)
        address = await wallet_svc.wallet_address(telegram_id)
        quote = await sushi.swap(
            jwt, wallet=address, token_in="eth", token_out="usdg", amount=1.0
        )
    except Exception as e:  # noqa: BLE001 — any failure means "no price"
        log.info("copy_price_unavailable", error=str(e)[:120])
        return None

    raw = sushi.summarize(quote).get("out_amount")
    if raw is None:
        return None
    price = float(raw) / (10**USDG_DECIMALS)
    if price <= 0:
        return None
    _price_cache["eth"], _price_at["eth"] = price, now
    return price


async def buy(telegram_id: int, token: str, amount_eth: float) -> str:
    """Buy `token` with `amount_eth` of ETH. Returns the last transaction hash.

    Raises CopyExecutionError with something a person can read — the message
    reaches them as a Telegram notification, not a log line.
    """
    try:
        address = await wallet_svc.wallet_address(telegram_id)
        jwt = await auth_svc.get_jwt(telegram_id)
        balance = (await pf.native_balance(telegram_id))["wei"]
    except (auth_svc.AuthError, pf.PortfolioError, evm.EvmError) as e:
        raise CopyExecutionError(f"couldn't reach your wallet: {e}") from e

    want = int(Decimal(str(amount_eth)) * (10**18))
    if balance <= want:
        # Not "< want": the trade also has to leave something for gas.
        raise CopyExecutionError(
            f"not enough ETH — {Decimal(balance) / 10**18:.4f} left, "
            f"{amount_eth:.4f} needed plus gas"
        )

    # Same venue comparison as a manual swap: Relay and Sushi both route this,
    # and which fills better changes with the pair and the size.
    sushi_res = None
    try:
        sushi_res = await sushi.swap(
            jwt, wallet=address, token_in="eth", token_out=token,
            amount=amount_eth,
        )
    except sushi.SushiError as e:
        log.info("copy_sushi_unavailable", token=token, error=str(e)[:120])

    w = await wallet_svc.get_or_create_wallet(telegram_id)
    if sushi_res is not None:
        try:
            hashes = await sushi.execute(w["enc_key_ref"], address, sushi_res)
            return hashes[-1]
        except sushi.SushiError as e:
            log.warning("copy_sushi_failed", token=token, error=str(e)[:160])

    try:
        params = relay.build_params(
            origin_currency=relay.NATIVE, destination_currency=token,
            amount=str(amount_eth), sender=address, recipient=address,
        )
        steps, request_id, _quote = await relay.build(jwt, params)
        hashes = await relay.execute_steps(w["enc_key_ref"], address, steps)
    except (relay.RelayError, evm.EvmError) as e:
        raise CopyExecutionError(str(e)) from e

    if request_id:
        await relay.record(jwt, request_id)  # book volume/tier; never fatal
    return hashes[-1]

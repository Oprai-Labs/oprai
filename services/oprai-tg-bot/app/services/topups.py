"""Buying conversation credits with $OPRAI.

The wallet is already ours to sign with, so a top-up is a transfer we can make
on the user's behalf: they name an amount, confirm, and the credits land when
the transfer confirms. Nobody leaves the chat, and no human has to notice a
transaction hash and credit it by hand.

Where the money goes is a product decision that has changed once already and
will change again: for now 100% reaches the dev wallet and any burn is done
off-bot, with `OPRAI_TG_BURN_BPS` left as the seam for automating it later.

The credit is written against the transaction hash, so a retry after a timeout
credits nothing twice.
"""

from __future__ import annotations

from decimal import Decimal

from app.config import settings
from app.logging_config import log
from app.services import credits as credits_mod
from app.services import evm, portfolio, tokens
from app.services import wallet as wallet_svc
from app.signer_client import SignerError

# transfer(address,uint256)
SEL_TRANSFER = "0xa9059cbb"


class TopupError(RuntimeError):
    pass


def _encode_transfer(to: str, amount: int) -> str:
    return (
        SEL_TRANSFER
        + to.lower().removeprefix("0x").rjust(64, "0")
        + f"{amount:x}".rjust(64, "0")
    )


async def _token_decimals() -> int:
    """Read them; never assume. USDG is 6 where everything else is 18, and one
    wrong assumption there sends a million times the intended amount."""
    decimals = await tokens.read_decimals(settings.OPRAI_TG_TOKEN_ADDRESS)
    if decimals is None:
        raise TopupError("couldn't read the $OPRAI token just now — try again shortly")
    return decimals


async def pay(
    telegram_id: int,
    amount_oprai: float,
    *,
    scope_id: int,
    is_group: bool,
    credits: int,
    on_sent=None,
) -> "credits_mod.Balance":
    """Pay $OPRAI for credits and grant them once the transfer confirms.

    Every refusal here happens before a transaction is signed, so a top-up
    either costs nothing or buys credits — never takes the money and leaves
    the balance untouched.
    """
    decimals = await _token_decimals()
    want = int(Decimal(str(amount_oprai)) * (10**decimals))
    address = await wallet_svc.wallet_address(telegram_id)

    held = await tokens.token_balance(settings.OPRAI_TG_TOKEN_ADDRESS, address)
    if held < want:
        have = Decimal(held) / (10**decimals)
        raise TopupError(
            f"You hold {have:.4f} $OPRAI and this top-up costs "
            f"{amount_oprai:g}.\n\nBuy some with "
            f"<code>/swap 0.01 ETH OPRAI</code> first."
        )

    tx_data = {
        "to": settings.OPRAI_TG_TOKEN_ADDRESS,
        "data": _encode_transfer(settings.OPRAI_TG_DEV_WALLET, want),
        "value": "0",
        "chainId": evm.CHAIN_ID,
    }
    try:
        tx = await evm.build_tx_from_provider(address, tx_data)
    except evm.EvmError as e:
        raise TopupError(str(e)) from e

    native = (await portfolio.native_balance(telegram_id))["wei"]
    gas_cost = evm.tx_cost_wei(tx)
    if native < gas_cost:
        raise TopupError(
            "This wallet doesn't have enough ETH for gas on Robinhood Chain. "
            f"Send a little to <code>{address}</code> and try again."
        )

    w = await wallet_svc.get_or_create_wallet(telegram_id)
    try:
        tx_hash = await evm.sign_and_send(w["enc_key_ref"], tx)
    except (evm.EvmError, SignerError) as e:
        raise TopupError(str(e)) from e

    # Claim the payment before waiting on it. From here the money is spent, so
    # the debt has to survive a crash, a timeout or a restart — it is settled
    # below if the receipt arrives, and by the reconciler if it doesn't.
    await credits_mod.record_payment(
        scope_id, is_group, telegram_id, tx_hash, want, credits
    )

    if on_sent:
        result = on_sent()
        if hasattr(result, "__await__"):
            await result

    receipt = await evm.wait_receipt(tx_hash, chain_id=evm.CHAIN_ID)
    if receipt is None:
        log.info("topup_unconfirmed", telegram_id=telegram_id, tx=tx_hash)
        raise TopupError(
            "Your payment is sent but hasn't confirmed yet. The credits are "
            "added as soon as it does — check /credits in a moment."
        )
    if not evm.receipt_succeeded(receipt):
        # A signature is not a success: crediting here would hand out credits
        # for a transfer that moved nothing.
        await credits_mod.settle_payment(tx_hash, succeeded=False)
        raise TopupError(
            "The payment didn't go through, so nothing was charged. "
            "Try again in a moment."
        )

    balance = await credits_mod.settle_payment(tx_hash, succeeded=True)
    if balance is None:
        # Settled by the reconciler between our wait and this call.
        return await credits_mod.balance(scope_id, is_group)
    return balance


async def settle_pending() -> list[dict]:
    """Finish top-ups whose receipt we never saw.

    Someone paid and is owed credits. Runs beside the deposit watcher, so a
    payment that outlived its confirmation wait is credited a few seconds
    later rather than needing anyone to notice.
    """
    settled: list[dict] = []
    for row in await credits_mod.pending_payments():
        receipt = await evm.rpc("eth_getTransactionReceipt", [row["tx_hash"]])
        if not receipt:
            continue  # still pending — a slow block is not a failure
        ok = evm.receipt_succeeded(receipt)
        balance = await credits_mod.settle_payment(row["tx_hash"], succeeded=ok)
        if ok and balance is not None:
            settled.append({**row, "balance": balance})
        elif not ok:
            log.warning("topup_reverted", tx=row["tx_hash"],
                        telegram_id=row["telegram_id"])
    return settled

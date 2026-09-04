"""A paid month.

Subscription rather than pay-per-question, because per-question pricing bills
the wrong thing. Our cost grows with questions asked; a person's willingness to
pay does not. The median wallet asks two questions a month and would never buy
a credit at any price, while the heaviest asks two hundred and would be the
only customer — so the meter monetised precisely the users it cost the most to
serve, at a price near cost. A month decouples the two: everyone pays the same,
and the light majority is what makes it work.

Payment is in ETH. It is the gas token, so a wallet that can transact already
holds it, nobody has to swap first, and the treasury accumulates the asset that
funds buying $OPRAI back.

`expires_at` is the whole truth. Nothing has to run on time for a subscription
to lapse — it lapses because the clock passes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import settings
from app.db import pool
from app.logging_config import log
from app.services import evm, portfolio, pricing
from app.services import wallet as wallet_svc
from app.signer_client import SignerError


class SubscriptionError(RuntimeError):
    pass


@dataclass
class Subscription:
    scope_id: int
    expires_at: datetime
    months: int
    paid_usd: Decimal

    @property
    def live(self) -> bool:
        return self.expires_at > datetime.now(timezone.utc)

    @property
    def days_left(self) -> int:
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(delta.days, 0)


async def get(scope_id: int) -> Subscription | None:
    row = await pool().fetchrow(
        "SELECT scope_id, expires_at, months, paid_usd FROM tg_subscriptions "
        "WHERE scope_id = $1",
        scope_id,
    )
    if row is None:
        return None
    return Subscription(row["scope_id"], row["expires_at"], row["months"],
                        Decimal(row["paid_usd"]))


async def is_live(scope_id: int) -> bool:
    sub = await get(scope_id)
    return bool(sub and sub.live)


async def extend(scope_id: int, telegram_id: int, is_group: bool, *,
                 wei: int, usd: float, days: int | None = None) -> Subscription:
    """Add a paid month, from now or from the end of what is already paid for.

    Renewing early must not cost somebody the days they still have, so the new
    month starts at whichever is later: now, or the existing expiry.
    """
    days = days or settings.OPRAI_TG_SUB_DAYS
    row = await pool().fetchrow(
        """
        INSERT INTO tg_subscriptions
            (scope_id, telegram_id, is_group, expires_at, paid_wei, paid_usd, months)
        VALUES ($1, $2, $3, now() + $4::interval, $5::numeric, $6::numeric, 1)
        ON CONFLICT (scope_id) DO UPDATE SET
            expires_at = GREATEST(tg_subscriptions.expires_at, now()) + $4::interval,
            paid_wei   = tg_subscriptions.paid_wei + $5::numeric,
            paid_usd   = tg_subscriptions.paid_usd + $6::numeric,
            months     = tg_subscriptions.months + 1,
            telegram_id = EXCLUDED.telegram_id,
            updated_at = now()
        RETURNING scope_id, expires_at, months, paid_usd
        """,
        scope_id, telegram_id, is_group, timedelta(days=days), str(wei), str(usd),
    )
    return Subscription(row["scope_id"], row["expires_at"], row["months"],
                        Decimal(row["paid_usd"]))


async def revenue() -> dict:
    """What has come in, for deciding how much to buy back with."""
    row = await pool().fetchrow(
        "SELECT count(*) AS subscribers, "
        "       coalesce(sum(paid_wei),0) AS wei, "
        "       coalesce(sum(paid_usd),0) AS usd, "
        "       coalesce(sum(months),0)   AS months, "
        "       count(*) FILTER (WHERE expires_at > now()) AS live "
        "  FROM tg_subscriptions"
    )
    return {
        "subscribers": row["subscribers"], "live": row["live"],
        "eth": Decimal(row["wei"]) / Decimal(10**18),
        "usd": Decimal(row["usd"]), "months": row["months"],
    }


async def pay(telegram_id: int, *, scope_id: int, is_group: bool,
              eth: float, usd: float, on_sent=None) -> Subscription:
    """Pay for a month in ETH and start it once the transfer confirms.

    Every refusal happens before anything is signed, so a sign-up either costs
    nothing or buys a month — it never takes the money and leaves the person
    without one. The month is only granted on a SUCCEEDED receipt: a signature
    is not a payment, and crediting on submit would hand out a month for a
    transfer that moved nothing.
    """
    address = await wallet_svc.wallet_address(telegram_id)
    want = int(Decimal(str(eth)) * (10**18))

    tx_data = {
        "to": settings.treasury_wallet(),
        "data": "0x",
        "value": hex(want),
        "chainId": evm.CHAIN_ID,
    }
    try:
        tx = await evm.build_tx_from_provider(address, tx_data)
    except evm.EvmError as e:
        raise SubscriptionError(str(e)) from e

    held = (await portfolio.native_balance(telegram_id))["wei"]
    needed = want + evm.tx_cost_wei(tx)
    if held < needed:
        short = Decimal(needed - held) / Decimal(10**18)
        raise SubscriptionError(
            f"This wallet is {short:.6f} ETH short of the subscription plus "
            f"gas.\n\nSend a little to <code>{address}</code> and try again."
        )

    w = await wallet_svc.get_or_create_wallet(telegram_id)
    try:
        tx_hash = await evm.sign_and_send(w["enc_key_ref"], tx)
    except (evm.EvmError, SignerError) as e:
        raise SubscriptionError(str(e)) from e

    if on_sent:
        result = on_sent()
        if hasattr(result, "__await__"):
            await result

    receipt = await evm.wait_receipt(tx_hash, chain_id=evm.CHAIN_ID)
    if receipt is None:
        # The money is gone but the month is not granted. Record nothing and
        # say so plainly — the reconciler below finishes it when the receipt
        # lands, which is the only honest option once a transfer is out.
        await _remember_unconfirmed(scope_id, telegram_id, is_group, tx_hash,
                                    want, usd)
        raise SubscriptionError(
            "Your payment is sent but hasn't confirmed yet. The month starts "
            "as soon as it does — check /subscription in a moment."
        )
    if not evm.receipt_succeeded(receipt):
        raise SubscriptionError(
            "The payment didn't go through, so nothing was charged. "
            "Try again in a moment."
        )

    log.info("subscription_paid", telegram_id=telegram_id, scope=scope_id,
             usd=usd, tx=tx_hash)
    return await extend(scope_id, telegram_id, is_group, wei=want, usd=usd)


async def _remember_unconfirmed(scope_id: int, telegram_id: int, is_group: bool,
                                tx_hash: str, wei: int, usd: float) -> None:
    """Someone paid and is owed a month. The debt has to outlive this process."""
    await pool().execute(
        """
        INSERT INTO tg_topups
            (tx_hash, scope_id, telegram_id, oprai_wei, credits, status,
             currency, usd)
        VALUES ($1, $2, $3, $4::numeric, 0, 'pending', 'ETH', $5::numeric)
        ON CONFLICT (tx_hash) DO NOTHING
        """,
        tx_hash.lower(), scope_id, telegram_id, str(wei), str(usd),
    )


async def settle_pending() -> list[dict]:
    """Start months whose receipt we never saw.

    Runs beside the deposit watcher. A payment that outlived its confirmation
    wait becomes a month a few seconds later, rather than needing a person to
    notice that somebody paid and got nothing.
    """
    rows = await pool().fetch(
        "SELECT tx_hash, scope_id, telegram_id, oprai_wei, usd FROM tg_topups "
        " WHERE status = 'pending' AND credits = 0 "
        "   AND created_at < now() - interval '20 seconds' "
        " ORDER BY created_at LIMIT 25"
    )
    started: list[dict] = []
    for row in rows:
        receipt = await evm.rpc("eth_getTransactionReceipt", [row["tx_hash"]])
        if not receipt:
            continue  # a slow block is not a failure
        ok = evm.receipt_succeeded(receipt)
        claimed = await pool().fetchrow(
            "UPDATE tg_topups SET status = $2, updated_at = now() "
            " WHERE tx_hash = $1 AND status = 'pending' RETURNING scope_id",
            row["tx_hash"], "credited" if ok else "failed",
        )
        if claimed is None:
            continue  # somebody else settled it
        if not ok:
            log.warning("subscription_reverted", tx=row["tx_hash"])
            continue
        sub = await extend(
            row["scope_id"], row["telegram_id"], row["scope_id"] < 0,
            wei=int(Decimal(row["oprai_wei"])), usd=float(row["usd"] or 0),
        )
        started.append({"scope_id": row["scope_id"],
                        "telegram_id": row["telegram_id"], "subscription": sub})
    return started


async def cost() -> tuple[float, float, float]:
    """-> (ETH to pay, dollar price, rate). Raises PriceUnavailable."""
    return await pricing.subscription_cost_eth()

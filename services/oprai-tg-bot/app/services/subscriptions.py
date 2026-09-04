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
from uuid import uuid4
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

    if await in_flight(scope_id):
        raise SubscriptionError(
            "A payment for this is already going through. Give it a moment — "
            "check /subscription rather than paying twice."
        )

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

    # Written BEFORE anything is signed. From the broadcast onward the money is
    # gone, so the record of it has to already exist: a crash in the moment
    # between sending and recording used to leave a payment nobody could find.
    intent = await _open_intent(scope_id, telegram_id, is_group, address,
                                want, usd)
    try:
        tx_hash = await evm.sign_and_send(w["enc_key_ref"], tx)
    except (evm.EvmError, SignerError) as e:
        # Nothing was broadcast, so there is nothing to recover.
        await _close_intent(intent, "failed")
        raise SubscriptionError(str(e)) from e
    intent = await _attach_hash(intent, tx_hash)

    if on_sent:
        result = on_sent()
        if hasattr(result, "__await__"):
            await result

    receipt = await evm.wait_receipt(tx_hash, chain_id=evm.CHAIN_ID)
    if receipt is None:
        # The money is out but not yet mined. The intent row stays open and the
        # reconciler finishes it — the only honest option once a transfer is
        # broadcast is to remember the debt.
        raise SubscriptionError(
            "Your payment is sent but hasn't confirmed yet. The month starts "
            "as soon as it does — check /subscription in a moment."
        )
    if not evm.receipt_succeeded(receipt):
        await _close_intent(intent, "failed")
        raise SubscriptionError(
            "The payment didn't go through, so nothing was charged. "
            "Try again in a moment."
        )

    # The status change is the lock: only the attempt that moves the row out of
    # flight grants a month, so this call, a retry and the reconciler can race
    # without ever granting two.
    if not await _close_intent(intent, "credited"):
        return await get(scope_id)
    log.info("subscription_paid", telegram_id=telegram_id, scope=scope_id,
             usd=usd, tx=tx_hash)
    return await extend(scope_id, telegram_id, is_group, wei=want, usd=usd)


async def in_flight(scope_id: int) -> bool:
    """Is a payment for this scope already on its way?

    Two taps on Subscribe a second apart used to send two transfers and charge
    twice for one month. The open intent row is the lock.
    """
    return bool(await pool().fetchval(
        "SELECT 1 FROM tg_topups WHERE scope_id = $1 "
        " AND status IN ('sending','pending') "
        " AND created_at > now() - interval '10 minutes' LIMIT 1",
        scope_id,
    ))


async def _open_intent(scope_id: int, telegram_id: int, is_group: bool,
                       wallet: str, wei: int, usd: float) -> str:
    """Record what we are about to spend, and where to start looking for it."""
    key = f"sending:{uuid4().hex}"
    try:
        block = evm.to_int(await evm.rpc("eth_blockNumber", []))
    except evm.EvmError:
        block = 0  # a missing block only widens the later search
    await pool().execute(
        """
        INSERT INTO tg_topups
            (tx_hash, scope_id, telegram_id, oprai_wei, credits, status,
             currency, usd, wallet, from_block)
        VALUES ($1, $2, $3, $4::numeric, 0, 'sending', 'ETH', $5::numeric, $6, $7)
        """,
        key, scope_id, telegram_id, str(wei), str(usd), wallet.lower(), block,
    )
    return key


async def _attach_hash(key: str, tx_hash: str) -> str:
    """The row is keyed by its hash from here on. Returns the new key."""
    await pool().execute(
        "UPDATE tg_topups SET tx_hash = $2, status = 'pending', "
        "updated_at = now() WHERE tx_hash = $1",
        key, tx_hash.lower(),
    )
    return tx_hash.lower()


async def _close_intent(key: str, status: str) -> bool:
    """-> True if THIS call closed it. False means somebody else already did."""
    row = await pool().fetchrow(
        "UPDATE tg_topups SET status = $2, updated_at = now() "
        " WHERE tx_hash = $1 AND status IN ('sending','pending') "
        "RETURNING tx_hash",
        key, status,
    )
    return row is not None


# How long a payment may sit unresolved before we stop looking for it. Long
# enough for a congested chain, short enough that a genuinely-never-sent
# payment does not block the scope from paying for ever.
_GIVE_UP_MINUTES = 30

# The search window. A payment we are looking for was broadcast seconds ago, so
# this only has to cover a reconciler that was itself down for a while.
_MAX_SCAN_BLOCKS = 5_000


async def settle_pending() -> list[dict]:
    """Finish payments this process did not get to finish.

    Two kinds. One has a hash and just needs its receipt. The other never got
    that far — the process died between the broadcast and recording the hash —
    and has to be FOUND: we know the wallet, the amount and the block we were
    at, so the transfer is looked for by scanning forward from there.
    """
    rows = await pool().fetch(
        "SELECT tx_hash, scope_id, telegram_id, oprai_wei, usd, wallet, "
        "       from_block, created_at, status "
        "  FROM tg_topups "
        " WHERE status IN ('sending','pending') AND credits = 0 "
        "   AND created_at < now() - interval '20 seconds' "
        " ORDER BY created_at LIMIT 25"
    )
    started: list[dict] = []
    for row in rows:
        tx_hash = row["tx_hash"]
        if row["status"] == "sending":
            tx_hash = await _find_lost_payment(row)
            if tx_hash is None:
                await _give_up_if_stale(row)
                continue
            log.warning("subscription_payment_recovered",
                        scope=row["scope_id"], tx=tx_hash)
            await _attach_hash(row["tx_hash"], tx_hash)

        receipt = await evm.rpc("eth_getTransactionReceipt", [tx_hash])
        if not receipt:
            continue  # a slow block is not a failure
        ok = evm.receipt_succeeded(receipt)
        if not await _close_intent(tx_hash, "credited" if ok else "failed"):
            continue  # somebody else settled it
        if not ok:
            log.warning("subscription_reverted", tx=tx_hash)
            continue
        sub = await extend(
            row["scope_id"], row["telegram_id"], row["scope_id"] < 0,
            wei=int(Decimal(row["oprai_wei"])), usd=float(row["usd"] or 0),
        )
        started.append({"scope_id": row["scope_id"],
                        "telegram_id": row["telegram_id"], "subscription": sub})
    return started


async def _find_lost_payment(row) -> str | None:
    """Look for a transfer we know we may have sent but never recorded.

    Bounded on both sides: from the block we were at when we started, to the
    head. That range is seconds of chain in the case this exists for, and the
    match has to be exact — this wallet, our treasury, that amount — so a
    coincidental transfer cannot be mistaken for the payment.
    """
    wallet = (row["wallet"] or "").lower()
    if not wallet or not row["from_block"]:
        return None
    treasury = settings.treasury_wallet().lower()
    want = int(Decimal(row["oprai_wei"]))

    try:
        head = evm.to_int(await evm.rpc("eth_blockNumber", []))
    except evm.EvmError:
        return None
    # Clamped on both ends. A start block ahead of the head — a lagging node,
    # a reorg — would otherwise make the range empty and the search silently
    # find nothing, which looks exactly like "no payment exists".
    first = max(min(int(row["from_block"]), head), head - _MAX_SCAN_BLOCKS)

    for number in range(first, head + 1):
        try:
            block = await evm.rpc("eth_getBlockByNumber", [hex(number), True])
        except evm.EvmError:
            return None
        for tx in (block or {}).get("transactions") or []:
            if (tx.get("from") or "").lower() != wallet:
                continue
            if (tx.get("to") or "").lower() != treasury:
                continue
            if evm.to_int(tx.get("value")) != want:
                continue
            return tx.get("hash")
    return None


async def _give_up_if_stale(row) -> None:
    """A payment we never found and can no longer look for.

    Almost always it was never broadcast — the failure happened before the
    signer answered. Closing it releases the scope to try again, which matters
    more than keeping a row open for ever on the chance it turns up.
    """
    age = datetime.now(timezone.utc) - row["created_at"]
    if age < timedelta(minutes=_GIVE_UP_MINUTES):
        return
    if await _close_intent(row["tx_hash"], "failed"):
        log.warning("subscription_payment_abandoned", scope=row["scope_id"],
                    wallet=row["wallet"], wei=str(row["oprai_wei"]))


async def cost() -> tuple[float, float, float]:
    """-> (ETH to pay, dollar price, rate). Raises PriceUnavailable."""
    return await pricing.subscription_cost_eth()

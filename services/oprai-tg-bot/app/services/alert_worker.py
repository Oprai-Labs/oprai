"""Alpha alert worker — the background loop that turns the chain-intel signal feed
into Telegram pings. It's the runtime heart of the tracking feature.

Kept behind two seams so it's unit-testable against the LIVE feed with fakes:
  • `store`  — subscription state (tracked wallets, discovery prefs, cursors, dedup),
               backed by Postgres in prod, an in-memory fake in tests.
  • `send`   — async (chat_id, html_text, buttons) → delivers the alert; the aiogram
               bot in prod, a capture list in tests.

Each subscription carries its own `cursor_block`: on first add it's seeded to the
current index tip (alerts start from when you subscribe, never a historical
backfill — which also avoids scanning a hyper-active wallet's whole history), then
advances to the feed tip each poll. A per-(user, token, kind) cooldown stops a token
that keeps getting bought from spamming the same user."""
from __future__ import annotations

import asyncio

from app.config import settings
from app.logging_config import log
from app.services import alerts as fmt
from app.services.signals_client import SignalsClient, SignalsError


async def run_once(client: SignalsClient, store, send, cooldown_min: int) -> int:
    """One poll pass over every subscription. Returns the number of alerts sent."""
    sent = 0
    tip = await client.tip()

    # ── smart-money DISCOVERY subscribers ──────────────────────────────────
    for sub in await store.discovery_subs():
        uid = sub["telegram_id"]
        since = sub.get("cursor_block") or tip
        try:
            alerts, new_cur = await fmt.poll_discovery(
                client, since, min_smart=sub.get("min_smart", 3))
        except SignalsError as e:
            log.warning("discovery_poll_failed", telegram_id=uid, error=str(e)[:120])
            continue
        for a in alerts:
            if sub.get("new_only") and not a.get("is_new_launch"):
                continue
            if await store.was_sent_recently(uid, a["token"], "discovery", cooldown_min):
                continue
            await send(uid, a["text"], a["buttons"])
            await store.mark_sent(uid, a["token"], "discovery")
            sent += 1
        await store.set_discovery_cursor(uid, new_cur)

    # ── per-wallet TRACKING subscribers ────────────────────────────────────
    for tw in await store.tracked_wallets():
        uid, addr = tw["telegram_id"], tw["address"]
        since = tw.get("cursor_block") or tip
        kind = f"wallet:{addr}"
        try:
            alerts, new_cur = await fmt.poll_tracked_wallet(
                client, addr, since, label=tw.get("label"))
        except SignalsError as e:
            log.warning("wallet_poll_failed", telegram_id=uid, wallet=addr,
                        error=str(e)[:120])
            continue
        for a in alerts:
            if await store.was_sent_recently(uid, a["token"], kind, cooldown_min):
                continue
            await send(uid, a["text"], a["buttons"])
            await store.mark_sent(uid, a["token"], kind)
            sent += 1
        await store.set_wallet_cursor(tw["id"], new_cur)

    return sent


async def run_forever(client: SignalsClient, store, send) -> None:
    """The long-running loop. Self-heals: a bad poll never kills the worker."""
    poll_s = settings.ALERT_POLL_SECONDS
    cooldown = settings.ALERT_COOLDOWN_MINUTES
    log.info("alert_worker_start", poll_seconds=poll_s, cooldown_min=cooldown)
    while True:
        try:
            n = await run_once(client, store, send, cooldown)
            if n:
                log.info("alerts_sent", count=n)
        except Exception as e:  # never die
            log.warning("alert_worker_cycle_error", error=str(e)[:160])
        await asyncio.sleep(poll_s)

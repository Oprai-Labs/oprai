"""Copy-trade decision + risk engine.

Auto-execution moves real money without a per-trade tap, so the risk layer is the
point of the whole thing. `decide()` is a PURE function (buy + user config + today's
spend → a CopyDecision) so every guard is unit-testable; the CopyEngine wires the
watcher's detections through decide() to the injected `execute` seam (the signer+
gateway buy) and records/notifies. Nothing here touches keys."""
from __future__ import annotations

from dataclasses import dataclass

# Base/quote assets — never COPY a buy of the money side (that's just funding).
_BASE_ASSETS = {
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",  # USDG
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",  # WETH
}


@dataclass
class CopyConfig:
    enabled: bool = False
    mode: str = "fixed"          # 'fixed' = always amount_eth; 'proportional' = ratio × leader
    amount_eth: float = 0.01     # fixed spend, or (mode=proportional) the ratio
    max_per_trade_eth: float = 0.05
    min_per_trade_eth: float = 0.001
    daily_cap_usd: float = 100.0


@dataclass
class CopyDecision:
    execute: bool
    amount_eth: float
    reason: str


def decide(token: str, leader_eth: float, cfg: CopyConfig,
           spent_today_usd: float, eth_price_usd: float) -> CopyDecision:
    """Should we copy this buy, and for how much ETH? Pure + fully guarded."""
    if not cfg.enabled:
        return CopyDecision(False, 0.0, "copy disabled")
    if token.lower() in _BASE_ASSETS:
        return CopyDecision(False, 0.0, "base/quote asset — not a real buy")

    # size the copy
    if cfg.mode == "proportional":
        amount = leader_eth * cfg.amount_eth
    else:
        amount = cfg.amount_eth
    # clamp to per-trade bounds
    amount = min(amount, cfg.max_per_trade_eth)
    if amount < cfg.min_per_trade_eth:
        return CopyDecision(False, 0.0,
                            f"size {amount:.4f} ETH below min {cfg.min_per_trade_eth}")

    # daily USD cap
    add_usd = amount * eth_price_usd
    if spent_today_usd + add_usd > cfg.daily_cap_usd:
        room = cfg.daily_cap_usd - spent_today_usd
        if room <= 0:
            return CopyDecision(False, 0.0, "daily cap reached")
        # shrink to the remaining room if still above the min
        shrunk = room / eth_price_usd if eth_price_usd else 0.0
        if shrunk < cfg.min_per_trade_eth:
            return CopyDecision(False, 0.0, "daily cap: remaining room below min")
        return CopyDecision(True, round(shrunk, 6), "sized down to daily-cap room")

    return CopyDecision(True, round(amount, 6), "ok")


class CopyEngine:
    """Wires a detected buy → decide() → execute seam. `store` yields per-(user,
    wallet) CopyConfig + today's spend and records fills; `execute(user, token,
    amount_eth)` builds+signs+submits via the signer; `notify(user, text)` pings."""

    def __init__(self, store, execute, notify, eth_price_usd: float = 2500.0):
        self._store = store
        self._execute = execute
        self._notify = notify
        self._eth = eth_price_usd

    async def on_buy(self, leader: str, token: str, eth_spent: float, tx_hash: str) -> None:
        # every user copying THIS leader wallet
        for sub in await self._store.copiers_of(leader):
            uid = sub["telegram_id"]
            cfg = sub["config"]
            spent = await self._store.spent_today_usd(uid)
            d = decide(token, eth_spent, cfg, spent, self._eth)
            if not d.execute:
                continue
            try:
                res = await self._execute(uid, token, d.amount_eth)
            except Exception as e:
                await self._notify(uid, f"⚠️ Copy-buy failed: {str(e)[:100]}")
                continue
            await self._store.record_fill(uid, leader, token, d.amount_eth,
                                          d.amount_eth * self._eth, tx_hash)
            await self._notify(
                uid,
                f"🤖 <b>Copied</b> {leader[:8]}… → bought <code>{token}</code> "
                f"for {d.amount_eth:.4f} ETH ({d.reason}).")

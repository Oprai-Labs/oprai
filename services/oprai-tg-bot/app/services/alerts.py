"""Alert engine — turns chain-intel signal results into Telegram messages.

Pure/testable: the format_* functions take signal dicts and return (html_text,
buttons) where `buttons` is a list of (label, kind, payload) the send layer maps to
an aiogram inline keyboard. The poll_* functions take a SignalsClient + a cursor
(the last block already alerted for this subscription) and return the fresh alerts +
the new cursor, so the caller (the bot's poll loop) stays a thin DB+send shell and
this stays unit-testable against the live feed.

Buttons follow the Based-bot pattern: every alert offers Buy / Chart / Track so a
signal is one tap from action (Buy routes into the bot's existing gateway+signer
trade flow; Track adds the token/wallet to the user's watchlist)."""
from __future__ import annotations

from app.services.signals_client import SignalsClient

_EXPLORER = "https://robinhood-explorer.example"  # tx/token explorer base (set in prod)


def _short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if addr and len(addr) > 12 else (addr or "?")


def _sym(s: dict) -> str:
    return f"${s['symbol']}" if s.get("symbol") else _short(s.get("token", ""))


def _buttons(token: str) -> list[tuple[str, str, str]]:
    """(label, kind, payload) — the send layer builds callback_data / urls from kind."""
    return [
        ("🟢 Buy", "buy", token),
        ("📈 Chart", "chart", token),
        ("➕ Track", "track_token", token),
    ]


def format_smart_buy(sig: dict) -> tuple[str, list]:
    """Discovery alert: smart wallets are buying this token."""
    sym = _sym(sig)
    n = sig.get("smart_buyers", 0)
    buys = sig.get("buys", 0)
    fresh = " 🆕 <b>fresh launch</b>" if sig.get("is_new_launch") else ""
    text = (
        f"🟢 <b>Smart money buying {sym}</b>{fresh}\n\n"
        f"• <b>{n}</b> smart wallet{'s' if n != 1 else ''} bought in\n"
        f"• <b>{buys}</b> total buys just now\n"
        f"<code>{sig.get('token')}</code>"
    )
    return text, _buttons(sig.get("token", ""))


def format_new_launch(launch: dict) -> tuple[str, list]:
    """Fresh-launch alert (optionally already smart-bought)."""
    sym = _sym(launch)
    n = launch.get("smart_buyers", 0)
    smart = f"\n• already <b>{n}</b> smart buyer{'s' if n != 1 else ''}" if n else ""
    text = (
        f"🚀 <b>New launch: {sym}</b>{smart}\n"
        f"<code>{launch.get('token')}</code>"
    )
    return text, _buttons(launch.get("token", ""))


def format_wallet_buy(wallet: str, buy: dict, wallet_is_smart: bool,
                      label: str | None = None) -> tuple[str, list]:
    """Tracked-wallet alert: a wallet you follow just bought a token."""
    who = label or _short(wallet)
    tag = " 🧠<i>smart</i>" if wallet_is_smart else ""
    sym = _sym(buy)
    usd = f" (~${buy['usd']:,.0f})" if buy.get("usd") else ""
    text = (
        f"👀 <b>{who}</b>{tag} bought <b>{sym}</b>{usd}\n"
        f"<code>{buy.get('token')}</code>"
    )
    return text, _buttons(buy.get("token", ""))


async def poll_discovery(client: SignalsClient, since_block: int,
                         min_smart: int = 2, limit: int = 30) -> tuple[list, int]:
    """Fresh discovery alerts since `since_block`. Returns (alerts, new_cursor) where
    each alert is {token, text, buttons, smart_buyers}. The caller dedupes/throttles
    per (subscriber, token) — this reports the current window truthfully."""
    res = await client.smart_buys(since_block, min_smart=min_smart, limit=limit)
    alerts = []
    for sig in res.get("signals", []):
        text, buttons = format_smart_buy(sig)
        alerts.append({"token": sig.get("token"), "text": text, "buttons": buttons,
                       "smart_buyers": sig.get("smart_buyers", 0),
                       "is_new_launch": bool(sig.get("is_new_launch")),
                       "last_block": sig.get("last_block", 0)})
    return alerts, int(res.get("tip") or since_block)


async def poll_tracked_wallet(client: SignalsClient, wallet: str, since_block: int,
                              label: str | None = None) -> tuple[list, int]:
    """Fresh buys by one tracked wallet since `since_block`. (alerts, new_cursor)."""
    res = await client.wallet_recent_buys(wallet, since_block)
    is_smart = bool(res.get("wallet_is_smart"))
    alerts = []
    for buy in res.get("buys", []):
        text, buttons = format_wallet_buy(wallet, buy, is_smart, label)
        alerts.append({"token": buy.get("token"), "text": text, "buttons": buttons,
                       "block": buy.get("block", 0)})
    return alerts, int(res.get("tip") or since_block)

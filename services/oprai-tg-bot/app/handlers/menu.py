"""Menus that open other menus.

A button whose whole job is to print "now type /swap 0.05 ETH NVDA" has moved
the work back to the person. So a tap opens the next set of choices in place —
the message is edited rather than a new one sent, which is what makes it feel
like a screen instead of a chat log — and every screen has a way back.

Some things genuinely cannot be a button: a recipient address, an arbitrary
amount, a token nobody holds yet. Those screens narrow it down as far as
buttons can (your tokens, a percentage of your balance) and ask for the last
piece only when it can't be offered.
"""

from __future__ import annotations

from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Icons that read at a glance on a phone and don't fight each other: one
# visual weight, no mixed styles, and none that mean something else.
ICONS = {
    "portfolio": "📊", "wallet": "🔑", "swap": "⇄", "send": "→",
    "lend": "🏦", "perps": "📈", "nft": "🖼", "launch": "✦",
    "bridge": "⇥", "alpha": "◎", "ask": "💬", "credits": "◆",
    "refresh": "↻", "help": "?", "back": "‹ Back",
}

# What a token is likely to be swapped into, when we have nothing better to go
# on than "they hold ETH".
COMMON_TARGETS = ("USDG", "NVDA", "TSLA", "USDe", "WETH")


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def grid(pairs: list[tuple[str, str]], per_row: int = 2,
         back: str | None = None) -> InlineKeyboardMarkup:
    """Lay buttons out and add the way back.

    Every screen below the top needs one: a menu you can only leave by
    scrolling up and re-running a command is a dead end.
    """
    rows = [
        [button(t, d) for t, d in pairs[i:i + per_row]]
        for i in range(0, len(pairs), per_row)
    ]
    if back:
        rows.append([button(ICONS["back"], back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def home_keyboard() -> InlineKeyboardMarkup:
    return grid([
        (f"{ICONS['portfolio']} Portfolio", "home:portfolio"),
        (f"{ICONS['wallet']} Wallet", "home:wallet"),
        (f"{ICONS['swap']} Swap", "menu:swap"),
        (f"{ICONS['send']} Send", "menu:send"),
        (f"{ICONS['lend']} Lend & borrow", "home:lend"),
        (f"{ICONS['perps']} Perps", "home:perps"),
        (f"{ICONS['nft']} NFTs", "home:nft"),
        (f"{ICONS['launch']} Launch", "menu:launch"),
        (f"{ICONS['bridge']} Bridge in", "menu:bridge"),
        (f"{ICONS['alpha']} Alpha", "home:alpha"),
        (f"{ICONS['ask']} Ask OPRAI", "menu:ask"),
        (f"{ICONS['credits']} Credits", "home:credits"),
        (f"{ICONS['refresh']} Refresh", "home:refresh"),
        (f"{ICONS['help']} Help", "home:help"),
    ])


# ── swap ────────────────────────────────────────────────────────────────────
def sell_menu(holdings: list[dict], native_eth: Decimal) -> InlineKeyboardMarkup:
    """What they can sell — offered from what they actually hold.

    Listing tokens someone doesn't have is offering a choice that fails two
    taps later.
    """
    pairs: list[tuple[str, str]] = []
    if native_eth > 0:
        pairs.append((f"ETH · {native_eth:.4f}", "swap:from:ETH"))
    for h in holdings[:9]:
        pairs.append((f"{h['symbol']} · {h['display']}", f"swap:from:{h['symbol']}"))
    if not pairs:
        return grid([("Fund the wallet first", "home:wallet")], 1, back="home:refresh")
    pairs.append(("Something else…", "swap:from:?"))
    return grid(pairs, 2, back="home:refresh")


def buy_menu(selling: str, holdings: list[dict]) -> InlineKeyboardMarkup:
    """What to buy. Their own tokens first — selling back is as common as
    buying — then the ones most people want."""
    seen = {selling.upper()}
    pairs: list[tuple[str, str]] = []
    for h in holdings:
        symbol = h["symbol"].upper()
        if symbol not in seen:
            seen.add(symbol)
            pairs.append((symbol, f"swap:to:{symbol}"))
        if len(pairs) >= 4:
            break
    for symbol in COMMON_TARGETS:
        if symbol.upper() not in seen:
            seen.add(symbol.upper())
            pairs.append((symbol, f"swap:to:{symbol}"))
    if "ETH" not in seen:
        pairs.append(("ETH", "swap:to:ETH"))
    pairs.append(("Something else…", "swap:to:?"))
    return grid(pairs, 3, back="menu:swap")


def amount_menu(selling: str) -> InlineKeyboardMarkup:
    """A percentage is the honest unit here: it is always affordable, which a
    typed number is not."""
    return grid([
        ("25%", "swap:amt:25"), ("50%", "swap:amt:50"),
        ("75%", "swap:amt:75"), ("Max", "swap:amt:100"),
        ("Type an amount…", "swap:amt:?"),
    ], 2, back="menu:swap")


def percent_of(amount: Decimal, percent: int, *, leave_gas: bool) -> Decimal:
    """A share of a balance, keeping enough back to pay for the transaction.

    "Max" that spends the last wei leaves nothing for gas and fails on submit —
    the one thing a Max button must never do.
    """
    share = amount * Decimal(percent) / Decimal(100)
    if leave_gas and percent >= 100:
        share = amount - GAS_RESERVE_ETH
    return share if share > 0 else Decimal(0)


# Enough for a swap or two at Robinhood Chain fees; the point is that Max never
# empties the wallet below what the transaction itself costs.
GAS_RESERVE_ETH = Decimal("0.0005")

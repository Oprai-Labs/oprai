"""/balance and /portfolio — what you hold on Robinhood Chain.

/balance is the quick one: ETH, nothing else. /portfolio is the whole
position — tokens and tokenized stocks, what is lent and borrowed on Morpho,
perps collateral on Lighter, and NFTs — because the first thing anyone asks
after their first trade is "what do I actually have now", and answering that
with only a gas balance is not an answer.

Every section is read independently and a section that fails says so instead
of taking the rest of the page down with it: a portfolio that renders nothing
because one provider hiccuped is worse than one with a gap in it.
"""

from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import audit, upsert_tg_user
from app.handlers.privacy import private_answer
from app.services import auth as auth_svc
from app.services import lighter, morpho, opensea
from app.services import portfolio as pf
from app.services import wallet as wallet_svc

router = Router(name="portfolio")

# Telegram rejects a message over 4096 characters outright, so a wallet
# holding a lot of stocks must not be able to produce one.
MAX_ROWS = 25


@router.message(Command("balance"))
async def balance_cmd(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    await audit(user.id, "balance", {})
    try:
        bal = await pf.native_balance(user.id)
        await private_answer(
            message, f"<b>Balance</b> · Robinhood Chain\n\n⬡ {bal['eth']:.4f} ETH"
        )
    except pf.PortfolioError as e:
        await private_answer(message, f"⚠️ Couldn't read your balance right now: {e}")


@router.message(Command("portfolio"))
async def portfolio_cmd(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    await audit(user.id, "portfolio", {})

    address = await wallet_svc.wallet_address(user.id)
    note = await private_answer(message, "Reading your positions…")
    if note is None:
        return

    try:
        jwt = await auth_svc.get_jwt(user.id)
    except auth_svc.AuthError:
        jwt = ""

    # Each section is independent, so one failing provider costs its own
    # section and nothing else.
    native, tokens, lending, perps, nfts = await asyncio.gather(
        pf.native_balance(user.id),
        pf.token_holdings(address, user.id),
        morpho.positions(jwt, address) if jwt else _none(),
        lighter.account(jwt, address) if jwt else _none(),
        opensea.wallet_nfts(jwt, address, limit=50) if jwt else _none(),
        return_exceptions=True,
    )

    lines = ["<b>Portfolio</b> · Robinhood Chain", ""]

    if isinstance(native, Exception):
        lines.append("⬡ ETH — couldn't read it just now")
    else:
        lines.append(f"⬡ <b>{native['eth']:.4f} ETH</b>")

    lines += _tokens_section(tokens)
    lines += _lending_section(lending)
    lines += _perps_section(perps)
    lines += _nft_section(nfts)

    lines += ["", f"<code>{address}</code>"]
    await note.edit_text("\n".join(lines), disable_web_page_preview=True)


async def _none():
    return None


def _tokens_section(tokens) -> list[str]:
    if isinstance(tokens, Exception) or tokens is None:
        return ["", "<i>Token balances unavailable right now.</i>"]
    if not tokens:
        return []

    stocks = [t for t in tokens if t["is_stock"]]
    others = [t for t in tokens if not t["is_stock"]]
    out: list[str] = []
    for title, group in (("Stocks", stocks), ("Tokens", others)):
        if not group:
            continue
        out += ["", f"<b>{title}</b>"]
        for t in group[:MAX_ROWS]:
            out.append(f"• {t['display']} {t['symbol']}")
        if len(group) > MAX_ROWS:
            out.append(f"<i>…and {len(group) - MAX_ROWS} more</i>")
    return out


def _lending_section(positions) -> list[str]:
    if isinstance(positions, Exception) or not positions:
        return []
    out = ["", "<b>Lending</b> · Morpho"]
    for p in positions[:MAX_ROWS]:
        bits = []
        if float(p.get("supplyAssets") or 0):
            bits.append(f"supplied {float(p['supplyAssets']):,.2f} {p.get('loanSymbol')}")
        if float(p.get("collateral") or 0):
            bits.append(f"collateral {float(p['collateral']):,.4f} {p.get('collateralSymbol')}")
        if float(p.get("borrowAssets") or 0):
            bits.append(f"borrowed {float(p['borrowAssets']):,.2f} {p.get('loanSymbol')}")
        health = p.get("healthFactor")
        if bits and health and float(p.get("borrowAssets") or 0):
            bits.append(f"health {float(health):.2f}")
        if bits:
            out.append("• " + " · ".join(bits))
    return out if len(out) > 2 else []


def _perps_section(state) -> list[str]:
    if isinstance(state, Exception) or not state or not state.get("has_account"):
        return []
    out = ["", "<b>Perps</b> · Lighter",
           f"• Collateral ${float(state.get('collateral') or 0):,.2f}"]
    for p in (state.get("positions") or [])[:MAX_ROWS]:
        if float(p.get("size") or 0) <= 0:
            continue
        pnl = float(p.get("unrealized_pnl") or 0)
        out.append(
            f"• {'🟢' if p.get('side') == 'long' else '🔴'} {p.get('symbol')} "
            f"{p.get('side')} {float(p.get('size') or 0):,.4f} · "
            f"PnL {'+' if pnl >= 0 else ''}${pnl:,.2f}"
        )
    return out


def _nft_section(nfts) -> list[str]:
    if isinstance(nfts, Exception) or not nfts:
        return []
    names = ", ".join(
        sorted({str(n.get("collection") or "?") for n in nfts})
    )[:200]
    return ["", f"<b>NFTs</b> — {len(nfts)}", f"<i>{names}</i>"]

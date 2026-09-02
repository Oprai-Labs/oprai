"""/balance and /portfolio — read-only holdings on Robinhood Chain.

Native ETH balance comes straight from our Robinhood node (see
services/portfolio.py). Stock/token holdings land in the next phase.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import audit, upsert_tg_user
from app.services import portfolio as pf
from app.services import wallet as wallet_svc

router = Router(name="portfolio")


@router.message(Command("balance"))
async def balance_cmd(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    await audit(user.id, "balance", {})
    try:
        bal = await pf.native_balance(user.id)
        await message.answer(
            f"<b>Balance</b> · Robinhood Chain\n\n⬡ {bal['eth']:.4f} ETH"
        )
    except pf.PortfolioError as e:
        await message.answer(f"⚠️ Couldn't read your balance right now: {e}")


@router.message(Command("portfolio"))
async def portfolio_cmd(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    await audit(user.id, "portfolio", {})
    address = await wallet_svc.wallet_address(user.id)
    lines = ["<b>Portfolio</b> · Robinhood Chain", ""]
    try:
        bal = await pf.native_balance(user.id)
        lines.append(f"⬡ {bal['eth']:.4f} ETH")
    except pf.PortfolioError as e:
        lines.append(f"⬡ ETH: unavailable ({e})")
    lines += [
        "",
        f"<code>{address}</code>",
        "",
        "<i>Native balance. Stock &amp; token holdings coming soon.</i>",
    ]
    await message.answer("\n".join(lines))

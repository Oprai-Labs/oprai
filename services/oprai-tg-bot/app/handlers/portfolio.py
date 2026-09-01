"""/balance and /portfolio — read-only native balances across the user's wallets.

Balances come from direct chain RPC (see services/portfolio.py). EVM native
balance needs OPRAI_TG_EVM_RPC configured; without it we render "unavailable"
rather than failing the whole command. Rich token portfolios land in Faz 1.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import audit, upsert_tg_user
from app.services import portfolio as pf
from app.services import wallet as wallet_svc

router = Router(name="portfolio")


def _fmt_sol(bal: dict) -> str:
    return f"◎ <b>Solana</b>: {bal['sol']:.4f} SOL"


def _fmt_evm(bal: dict) -> str:
    return f"⬡ <b>EVM</b>: {bal['eth']:.4f} ETH"


@router.message(Command("balance"))
async def balance_cmd(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    await audit(user.id, "balance", {})
    lines = ["<b>Balance</b>", ""]
    try:
        lines.append(_fmt_sol(await pf.solana_balance(user.id)))
    except pf.PortfolioError as e:
        lines.append(f"◎ <b>Solana</b>: unavailable ({e})")
    try:
        lines.append(_fmt_evm(await pf.evm_native_balance(user.id)))
    except pf.PortfolioError:
        pass  # EVM RPC optional — omit rather than clutter
    await message.answer("\n".join(lines))


@router.message(Command("portfolio"))
async def portfolio_cmd(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    await audit(user.id, "portfolio", {})
    wallets = await wallet_svc.ensure_all_wallets(user.id)
    lines = ["<b>Portfolio</b>", ""]

    try:
        lines.append(_fmt_sol(await pf.solana_balance(user.id)))
    except pf.PortfolioError as e:
        lines.append(f"◎ <b>Solana</b>: unavailable ({e})")

    try:
        lines.append(_fmt_evm(await pf.evm_native_balance(user.id)))
    except pf.PortfolioError as e:
        lines.append(f"⬡ <b>EVM</b>: unavailable ({e})")

    lines += [
        "",
        f"◎ <code>{wallets['solana']}</code>",
        f"⬡ <code>{wallets['evm']}</code>",
        "",
        "<i>Native balances. Full token portfolios coming soon.</i>",
    ]
    await message.answer("\n".join(lines))

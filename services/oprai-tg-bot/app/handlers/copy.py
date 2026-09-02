"""/copy — copy-trade a wallet: when it buys, we buy the same token from your custodial
wallet within your limits (sized by CopyEngine.decide, executed by copy_executor).

  /copy 0x…                 start copying (default 0.01 ETH per buy)
  /copy 0x… 0.02            start/update with a fixed ETH size
  /copy limits 0x… 0.05 100 max ETH per trade, daily USD cap
  /copy off 0x…             stop
  /copy list                your copies
"""
from __future__ import annotations

import re

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.db import audit, upsert_tg_user
from app.services import wallet as wallet_svc
from app.services.copy_store import CopyStore

router = Router(name="copy")
_store = CopyStore()
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

USAGE = (
    "<b>Copy-trade</b>\n"
    "<code>/copy 0x… [eth]</code> — copy a wallet (default 0.01 ETH per buy)\n"
    "<code>/copy limits 0x… &lt;maxEth&gt; &lt;dailyUsd&gt;</code> — risk limits\n"
    "<code>/copy off 0x…</code> — stop · <code>/copy list</code> — your copies"
)


@router.message(Command("copy"))
async def copy_cmd(message: Message, command: CommandObject) -> None:
    u = message.from_user
    await upsert_tg_user(u.id, u.username)
    args = (command.args or "").split()
    if not args:
        await message.answer(USAGE)
        return

    if args[0] == "list":
        rows = await _store.list_copies(u.id)
        if not rows:
            await message.answer("No copies yet.\n\n" + USAGE)
            return
        lines = ["<b>Your copies</b>"]
        for r in rows:
            st = "🟢" if r["enabled"] else "⏸"
            lines.append(f"{st} <code>{r['leader']}</code> — {float(r['amount_eth']):.4f} ETH/buy, "
                         f"max {float(r['max_per_trade_eth']):.3f} ETH, cap ${float(r['daily_cap_usd']):.0f}/day")
        await message.answer("\n".join(lines))
        return

    if args[0] == "off" and len(args) > 1 and _EVM_RE.match(args[1]):
        await _store.set_copy(u.id, args[1], enabled=False)
        await audit(u.id, "copy_off", {"leader": args[1]})
        await message.answer(f"⏸ Stopped copying <code>{args[1]}</code>.")
        return

    if args[0] == "limits" and len(args) >= 4 and _EVM_RE.match(args[1]):
        try:
            mx, cap = float(args[2]), float(args[3])
        except ValueError:
            await message.answer(USAGE)
            return
        await _store.set_copy(u.id, args[1], enabled=True, max_per_trade_eth=mx, daily_cap_usd=cap)
        await message.answer(f"✅ Limits for <code>{args[1]}</code>: max {mx} ETH/trade, ${cap:.0f}/day.")
        return

    if _EVM_RE.match(args[0]):
        if not await wallet_svc.get_wallet(u.id):
            await message.answer("You need a custodial wallet first — run /wallet, then fund it with ETH.")
            return
        amt = None
        if len(args) > 1:
            try:
                amt = float(args[1])
            except ValueError:
                await message.answer(USAGE)
                return
        await _store.set_copy(u.id, args[0], enabled=True, amount_eth=amt)
        await audit(u.id, "copy_on", {"leader": args[0], "amount_eth": amt})
        await message.answer(
            f"🤖 Copying <code>{args[0]}</code> — {amt or 0.01} ETH per buy, ~1-2s behind them. "
            f"Limits default to 0.05 ETH/trade and $100/day; change with /copy limits.")
        return

    await message.answer(USAGE)

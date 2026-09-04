"""Ask OPRAI anything — the assistant behind the commands.

In a private chat, any message that isn't a command is a question. In a group,
the bot stays quiet unless it is spoken to (mentioned, replied to, or asked
with /ask) — a bot that answers every message in a room gets removed from it.

This is the one path that costs credits, because it is the one that costs us:
the model. Commands that touch the chain are never metered here; they already
pay OPRAI's trading commission, and charging twice for one intent teaches
people to avoid the assistant.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from app.config import settings
from app.db import audit, upsert_tg_user
from app.logging_config import log
from app.services import auth as auth_svc
from app.services import chat as chat_svc
from app.services import credits, topups

router = Router(name="chat")

_topups: dict[str, dict] = {}

# How often the growing answer is edited into the message. Telegram throttles
# edits, and a message that updates every token reads as a stutter.
EDIT_INTERVAL_SECONDS = 1.6

OUT_OF_CREDITS_PRIVATE = (
    "You're out of conversation credits for now.\n\n"
    "They refill every {hours}h. Commands like /swap, /send and /long keep "
    "working — only asking OPRAI questions uses credits."
)
OUT_OF_CREDITS_GROUP = (
    "This group is out of conversation credits.\n\n"
    "They refill every {hours}h, or an admin can top the group up with $OPRAI "
    "— see /topup. Trading commands keep working either way."
)


def _scope(message: Message) -> tuple[int, bool]:
    """Where the credits come from: a group shares one balance, a DM is its own."""
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    return (message.chat.id if is_group else message.from_user.id), is_group


async def _addressed_to_us(message: Message) -> bool:
    """In a group, only answer when spoken to."""
    me = await message.bot.me()
    if message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.id == me.id:
            return True
    text = message.text or message.caption or ""
    return f"@{me.username}".lower() in text.lower()


def _strip_mention(text: str, username: str) -> str:
    return text.replace(f"@{username}", " ").strip()


# ── credits ─────────────────────────────────────────────────────────────────
@router.message(Command("credits"))
async def credits_cmd(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    scope_id, is_group = _scope(message)
    bal = await credits.balance(scope_id, is_group)

    where = "This group" if is_group else "You"
    lines = [
        f"<b>Conversation credits</b>\n",
        f"{where} can ask <b>{bal.remaining}</b> more question"
        f"{'' if bal.remaining == 1 else 's'}.",
        f"  · {bal.free_left} free (refills every {settings.OPRAI_TG_FREE_WINDOW_HOURS}h)",
    ]
    if bal.paid:
        lines.append(f"  · {bal.paid} topped up (never expire)")
    lines += [
        "",
        "<i>Only questions to OPRAI use credits. Trading commands don't — "
        "they already pay the normal trading fee.</i>",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("topup"))
async def topup_cmd(message: Message, command: CommandObject) -> None:
    """Buy credits with $OPRAI, paid from the user's own bot wallet.

    The wallet is already ours to sign with, so there is no reason to make
    someone leave, send a transfer by hand and wait for a human to notice the
    hash. They name an amount, confirm, and the credits land when the transfer
    confirms.
    """
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    scope_id, is_group = _scope(message)
    rate = settings.OPRAI_TG_CREDITS_PER_OPRAI
    minimum = settings.OPRAI_TG_MIN_TOPUP_OPRAI

    if is_group and not await _is_group_admin(message):
        await message.answer(
            "Only a group admin can top up this group's credits."
        )
        return

    amount = _parse_amount((command.args or "").strip())
    if amount is None:
        who = "this group" if is_group else "you"
        await message.answer(
            f"<b>Top up credits</b>\n\n"
            f"<code>/topup &lt;amount&gt;</code> pays $OPRAI from your wallet and "
            f"credits {who} at <b>{rate} questions per $OPRAI</b>.\n\n"
            f"Example: <code>/topup 10</code> → {10 * rate} questions\n"
            f"Minimum: {minimum} $OPRAI\n\n"
            "<i>Credits are spent only on questions to OPRAI — trading "
            "commands are never charged for.</i>"
        )
        return
    if amount < minimum:
        await message.answer(f"The minimum top-up is {minimum} $OPRAI.")
        return

    granted = int(amount * rate)
    if granted <= 0:
        await message.answer("That amount is too small to buy a credit.")
        return

    pid = secrets.token_urlsafe(8)
    _topups[pid] = {"telegram_id": user.id, "scope_id": scope_id,
                    "is_group": is_group, "amount": amount, "credits": granted}
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Pay {_fmt_amount(amount)} $OPRAI",
                             callback_data=f"top:ok:{pid}"),
        InlineKeyboardButton(text="Cancel", callback_data=f"top:no:{pid}"),
    ]])
    await message.answer(
        f"<b>Top up {'this group' if is_group else 'your credits'}</b>\n\n"
        f"Pay: <b>{_fmt_amount(amount)} $OPRAI</b> (plus gas)\n"
        f"Get: <b>{granted}</b> questions\n\n"
        "<i>Topped-up credits never expire.</i>",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("top:"))
async def topup_confirm(cb: CallbackQuery) -> None:
    _, action, pid = cb.data.split(":", 2)
    p = _topups.get(pid)
    if not p:
        await cb.answer("This top-up expired. Run /topup again.", show_alert=True)
        return
    if cb.from_user.id != p["telegram_id"]:
        await cb.answer("This isn't your top-up.", show_alert=True)
        return
    if action == "no":
        _topups.pop(pid, None)
        await cb.answer("Cancelled")
        await cb.message.edit_text("Cancelled — nothing was paid.")
        return

    _topups.pop(pid, None)
    await cb.answer()
    await cb.message.edit_text("Paying…")

    try:
        balance = await topups.pay(
            p["telegram_id"], p["amount"],
            on_sent=lambda: cb.message.edit_text("⏳ Paid — waiting for the transfer to confirm…"),
            scope_id=p["scope_id"], is_group=p["is_group"], credits=p["credits"],
        )
    except topups.TopupError as e:
        log.warning("topup_failed", telegram_id=p["telegram_id"], error=str(e)[:200])
        await audit(p["telegram_id"], "topup_failed", {"error": str(e)[:200]})
        await cb.message.edit_text(f"❌ {e}")
        return

    await audit(p["telegram_id"], "topup",
                {"oprai": p["amount"], "credits": p["credits"], "group": p["is_group"]})
    await cb.message.edit_text(
        f"✅ <b>{p['credits']} credits added.</b>\n\n"
        f"{'This group' if p['is_group'] else 'You'} can now ask "
        f"<b>{balance.remaining}</b> questions."
    )


def _parse_amount(text: str) -> float | None:
    try:
        value = float(Decimal(text.lstrip("$").replace(",", "")))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    return value if value > 0 else None


def _fmt_amount(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


async def _is_group_admin(message: Message) -> bool:
    """Telegram is the authority on who runs a room; asking it beats keeping
    our own list that drifts the moment someone is promoted."""
    try:
        member = await message.bot.get_chat_member(
            message.chat.id, message.from_user.id
        )
    except Exception:  # noqa: BLE001 — an unreadable membership is not an admin
        return False
    return member.status in ("creator", "administrator")


# ── the assistant ───────────────────────────────────────────────────────────
@router.message(Command("ask"))
async def ask_cmd(message: Message, command: CommandObject) -> None:
    question = (command.args or "").strip()
    if not question:
        await message.answer(
            "Ask me anything about Robinhood Chain — a token, your portfolio, "
            "a strategy.\n\nExample: <code>/ask is NVDA worth holding here?</code>"
        )
        return
    await _answer(message, question)


@router.message(F.text & ~F.text.startswith("/"))
async def freeform(message: Message) -> None:
    """Plain text: a question in a DM, and in a group only when addressed."""
    text = message.text or ""
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if not await _addressed_to_us(message):
            return
        me = await message.bot.me()
        text = _strip_mention(text, me.username or "")
    if not text.strip():
        return
    await _answer(message, text)


async def _answer(message: Message, question: str) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    scope_id, is_group = _scope(message)

    # Charge before asking. A model call we can't pay for shouldn't be made,
    # and the refund path below covers the case where it fails to answer.
    spent = await credits.spend(
        scope_id, is_group, user.id, 1, {"chars": len(question)}
    )
    if spent is None:
        template = OUT_OF_CREDITS_GROUP if is_group else OUT_OF_CREDITS_PRIVATE
        await message.answer(
            template.format(hours=settings.OPRAI_TG_FREE_WINDOW_HOURS)
        )
        return

    placeholder = await message.answer("💭 <i>Thinking…</i>")
    await message.bot.send_chat_action(message.chat.id, "typing")

    # A question that needs live chain data takes twenty-odd seconds, and the
    # answer arrives in one burst at the end — so nothing we stream can fill
    # that gap. Saying what is happening, as it happens, is the difference
    # between a wait and a hang. These are elapsed-time stages, not invented
    # progress: each one only claims that the previous is still running.
    ticker = asyncio.create_task(_keep_company(placeholder, message.bot, message.chat.id))
    # Remember that someone is waiting on this exact message. If the process
    # stops before the answer lands, nothing in here survives to tell them —
    # so the fact has to outlive the process.
    await _mark_waiting(message.chat.id, placeholder.message_id, user.id)

    session_id = await chat_svc.session_for(scope_id, user.id)
    last_edit = time.monotonic()

    async def on_progress(partial: str) -> None:
        nonlocal last_edit
        now = time.monotonic()
        if now - last_edit < EDIT_INTERVAL_SECONDS or len(partial) < 40:
            return
        last_edit = now
        shown = chat_svc.to_telegram_html(partial[: chat_svc.TELEGRAM_LIMIT - 32])
        try:
            await placeholder.edit_text(shown + " ▌")
        except Exception:  # noqa: BLE001 — an edit that fails is cosmetic
            pass

    try:
        jwt = await auth_svc.get_jwt(user.id)
        answer = await chat_svc.stream(
            jwt, session_id, question, on_progress=on_progress
        )
    except (chat_svc.ChatError, auth_svc.AuthError) as e:
        ticker.cancel()
        # Nothing was delivered, so nothing should be charged.
        await credits.refund(scope_id, user.id, 1, reason=type(e).__name__)
        log.warning("chat_failed", telegram_id=user.id, error=str(e)[:200])
        await audit(user.id, "chat_failed", {"error": str(e)[:200]})
        try:
            await placeholder.edit_text(f"⚠️ {e}")
        except Exception:  # noqa: BLE001
            await message.answer(f"⚠️ {e}")
        return

    ticker.cancel()
    await _done_waiting(message.chat.id, placeholder.message_id)
    await chat_svc.remember_session(scope_id, user.id, answer.session_id or "")
    await audit(user.id, "chat_answered",
                {"chars": len(answer.text), "group": is_group})

    # An instruction gets carried out, not described back. If we can run what
    # the model decided, the person sees the confirmation card they were
    # asking for; only when we can't do we fall back to naming the command.
    if answer.actions:
        try:
            if await _run_action(message, answer.actions[0]):
                if answer.text.strip():
                    await placeholder.edit_text(chat_svc.to_telegram_html(answer.text))
                else:
                    await placeholder.delete()
                return
        except Exception as e:  # noqa: BLE001 — fall back rather than lose the turn
            log.warning("chat_action_failed", telegram_id=user.id,
                        kind=answer.actions[0].get("type"), error=str(e)[:200])

    html = chat_svc.to_telegram_html(answer.text)
    if answer.actions:
        html += "\n\n" + _action_hint(answer.actions[0])

    parts = chat_svc.split_for_telegram(html)
    try:
        await placeholder.edit_text(parts[0])
    except Exception:  # noqa: BLE001 — e.g. identical text, or a parse refusal
        await message.answer(parts[0])
    for extra in parts[1:]:
        await asyncio.sleep(0.3)  # keep Telegram's flood limiter happy
        await message.answer(extra)


def _args_for(action: dict) -> tuple[str, str] | None:
    """Turn what the model decided into a command this bot can run.

    Saying "buy 0.01 NVDA" is an instruction, not a question — the model
    answers it by emitting an action and no prose. Rendering that as a
    suggestion to type a command would be handing the work back for no reason;
    we have the parameters, so we run the same flow the command would, which is
    where the confirmation card and the balance checks already live.

    -> (handler key, argument string), or None when we can't run it faithfully.
    """
    kind = str(action.get("type") or "").lower()
    p = action.get("params") or {}

    def first(*names, default=""):
        for n in names:
            v = p.get(n)
            if v not in (None, ""):
                return str(v)
        return default

    if kind in ("swap", "sushi_swap", "uniswap_swap", "relay_bridge",
                "cross_chain_swap"):
        amount = first("amount", "amountIn", "inputAmount")
        # Each venue names these differently — Sushi says tokenIn/tokenOut,
        # Relay says originCurrency/destinationCurrency. Missing one meant the
        # action was understood and then quietly dropped.
        sell = first("tokenIn", "fromToken", "inputToken", "originCurrency", "from")
        buy = first("tokenOut", "toToken", "outputToken", "destinationCurrency", "to")
        if amount and sell and buy:
            # The action type names the venue the model chose — usually because
            # the person said so. Carrying it through is the difference between
            # honouring "swap on Sushi" and quietly re-deciding it.
            venue = {"sushi_swap": "sushi", "uniswap_swap": "uniswap",
                     "relay_bridge": "relay", "cross_chain_swap": "relay"}.get(kind)
            args = f"{amount} {sell} {buy}"
            return "swap", (f"{args} on {venue}" if venue else args)
        return None

    if kind in ("transfer", "send", "evm_transfer"):
        amount = first("amount")
        token = first("token", "symbol", "mint", default="ETH")
        to = first("to", "recipient", "toAddress", "destination")
        if amount and to:
            return "send", f"{amount} {token} {to}"
        return None

    if kind == "lighter_open":
        symbol = first("symbol", "market")
        collateral = first("collateralUsd", "collateral", "amount")
        leverage = first("leverage", default="")
        side = first("side", default="long").lower()
        if symbol and collateral:
            args = f"{symbol} {collateral}" + (f" {leverage}" if leverage else "")
            return ("long" if side != "short" else "short"), args
        return None

    if kind == "lighter_close":
        symbol = first("symbol", "market")
        return ("close", symbol) if symbol else None

    if kind in ("morpho_supply", "lend"):
        amount = first("amount", "amountBaseUnits")
        return ("lend", amount) if amount else None

    if kind in ("morpho_borrow", "borrow"):
        amount = first("borrowAmount", "amount")
        return ("borrow", amount) if amount else None

    if kind in ("pools_launch", "pons_launch", "launch", "token_launch"):
        symbol = first("tokenSymbol", "symbol", "ticker")
        name = first("tokenName", "name")
        return ("launch", f"{symbol} {name}") if symbol and name else None

    return None


# Which handler runs each command, and whether it takes a CommandObject.
_HANDLERS = {
    "swap": ("swap", "swap_cmd"),
    "send": ("send", "send_cmd"),
    "long": ("perps", "open_cmd"),
    "short": ("perps", "open_cmd"),
    "close": ("perps", "close_cmd"),
    "lend": ("lend", "lend_router"),
    "borrow": ("lend", "lend_router"),
    "launch": ("launch", "launch_cmd"),
}


async def _run_action(message: Message, action: dict) -> bool:
    """Run what the model asked for. Returns whether anything ran."""
    mapped = _args_for(action)
    if mapped is None:
        return False
    command, args = mapped
    module_name, func_name = _HANDLERS[command]

    # /long, /short, /lend and friends read the verb out of message.text, so
    # the message has to look like the command that was meant.
    faithful = message.model_copy(update={"text": f"/{command} {args}"}).as_(message.bot)
    module = __import__(f"app.handlers.{module_name}", fromlist=[func_name])
    await audit(message.from_user.id, "chat_action",
                {"type": action.get("type"), "command": command})
    await getattr(module, func_name)(faithful, _Args(args))
    return True


class _Args:
    """A stand-in for aiogram's CommandObject carrying just the arguments."""

    def __init__(self, args: str):
        self.args = args
        self.command = None
        self.prefix = "/"
        self.mention = None
        self.magic_result = None


# What the wait actually consists of, in the order it happens. Each line is
# shown once the one before it has been true for a while — so the message is
# always a fair description of where the turn has got to, never a promise.
STAGES = (
    (4, "🔎 <i>Working out what you need…</i>"),
    (9, "⛓ <i>Reading the chain…</i>"),
    (16, "🧮 <i>Going through the numbers…</i>"),
    (26, "✍️ <i>Writing it up…</i>"),
    (40, "⏳ <i>Still going — this one is taking a while.</i>"),
)


async def _mark_waiting(chat_id: int, message_id: int, telegram_id: int) -> None:
    from app.db import pool

    try:
        await pool().execute(
            "INSERT INTO tg_inflight (chat_id, message_id, telegram_id) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            chat_id, message_id, telegram_id,
        )
    except Exception as e:  # noqa: BLE001 — bookkeeping must not break the turn
        log.warning("inflight_mark_failed", error=str(e)[:160])


async def _done_waiting(chat_id: int, message_id: int) -> None:
    from app.db import pool

    try:
        await pool().execute(
            "DELETE FROM tg_inflight WHERE chat_id = $1 AND message_id = $2",
            chat_id, message_id,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("inflight_clear_failed", error=str(e)[:160])


async def close_orphaned_turns(bot) -> None:
    """Tell anyone left waiting by a restart.

    A deploy or a crash takes the task that would have answered AND the task
    that would have reported the failure, so the placeholder sits on "Thinking…"
    for ever. Nobody should have to guess whether a bot is working or dead.
    """
    from app.db import pool

    try:
        rows = await pool().fetch(
            "DELETE FROM tg_inflight RETURNING chat_id, message_id, telegram_id"
        )
    except Exception as e:  # noqa: BLE001
        log.warning("inflight_sweep_failed", error=str(e)[:160])
        return

    for row in rows:
        try:
            await bot.edit_message_text(
                chat_id=row["chat_id"], message_id=row["message_id"],
                text="⚠️ <i>I was restarted while working on that — ask me again "
                     "and I'll pick it straight up.</i>",
            )
        except Exception as e:  # noqa: BLE001 — an unreachable chat is not fatal
            log.info("inflight_notice_failed", chat_id=row["chat_id"],
                     error=str(e)[:120])
    if rows:
        log.info("inflight_swept", count=len(rows))


async def _keep_company(placeholder, bot, chat_id: int) -> None:
    """Say what is happening while the model works.

    The answer arrives in a single burst at the end, so there is nothing to
    stream into the gap. Twenty seconds of an unchanging "Thinking…" reads as
    a hang; the same twenty seconds narrated reads as work.
    """
    waited = 0.0
    for after, text in STAGES:
        try:
            await asyncio.sleep(after - waited)
        except asyncio.CancelledError:
            return
        waited = after
        try:
            await placeholder.edit_text(text)
            await bot.send_chat_action(chat_id, "typing")
        except Exception:  # noqa: BLE001 — the notice is courtesy, not the answer
            return


# What to say when we cannot run it faithfully — the command that would.
_ACTION_COMMANDS = {
    "swap": "/swap &lt;amount&gt; &lt;from&gt; &lt;to&gt;",
    "transfer": "/send &lt;amount&gt; &lt;token&gt; &lt;address&gt;",
    "send": "/send &lt;amount&gt; &lt;token&gt; &lt;address&gt;",
    "bridge": "/bridge &lt;amount&gt; ETH from &lt;chain&gt;",
    "relay_bridge": "/bridge &lt;amount&gt; ETH from &lt;chain&gt;",
    "cross_chain_swap": "/bridge &lt;amount&gt; ETH from &lt;chain&gt;",
    "perp_open": "/long &lt;SYMBOL&gt; &lt;$&gt; [leverage]",
    "perp_close": "/close &lt;SYMBOL&gt;",
    "launch": "/launch &lt;TICKER&gt; &lt;name&gt;",
    "token_launch": "/launch &lt;TICKER&gt; &lt;name&gt;",
}


def _action_hint(action: dict) -> str:
    """Point at the command that does it, rather than executing from here.

    Two ways to execute the same intent is two ways for them to disagree; the
    commands own execution because that is where confirmation and the balance
    checks already live.
    """
    kind = str(action.get("type") or "").lower()
    command = _ACTION_COMMANDS.get(kind)
    if not command:
        return "<i>Ready when you are — /help lists what I can run.</i>"
    return f"<i>To do it: <code>{command}</code></i>"

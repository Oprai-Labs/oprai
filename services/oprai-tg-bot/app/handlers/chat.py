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
from app.services import credits, pricing, subscriptions

router = Router(name="chat")

# How often the growing answer is edited into the message. Telegram throttles
# edits, and a message that updates every token reads as a stutter.
EDIT_INTERVAL_SECONDS = 1.6

OUT_OF_CREDITS_PRIVATE = (
    "That's your questions for today.\n\n"
    "They refill every {hours}h, or /subscribe raises the daily limit. "
    "Commands like /swap, /send and /long keep working either way — only "
    "asking OPRAI questions is metered."
)
OUT_OF_CREDITS_GROUP = (
    "That's this group's questions for today.\n\n"
    "They refill every {hours}h, or an admin can /subscribe to raise the "
    "daily limit. Trading commands keep working either way."
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


# ── the subscription ────────────────────────────────────────────────────────
_NO_PRICE = (
    "I can't read a reliable ETH price right now, so I won't quote a "
    "subscription — converting at a guessed rate would charge you the wrong "
    "amount. Try again in a minute."
)

_PITCH = (
    "<b>OPRAI Pro</b>\n\n"
    "Questions to OPRAI use one credit each. You get <b>{free}</b> a day for "
    "free; Pro raises that to <b>{pro}</b> a day for <b>${usd:,.2f}</b> a "
    "month.\n\n"
    "Token scans, /swap, /send, /long and every other command are free either "
    "way — only asking OPRAI questions is metered.\n\n"
    "Paid in ETH from your wallet at the live rate "
    "(<code>${rate:,.2f}</code> per ETH)."
)


def _sub_keyboard(eth: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Subscribe · {_fmt_amount(eth)} ETH",
                             callback_data="sub:go"),
    ]])


@router.message(Command("credits", "subscription", "sub"))
async def credits_cmd(message: Message) -> None:
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    scope_id, is_group = _scope(message)
    bal = await credits.balance(scope_id, is_group)
    sub = await subscriptions.get(scope_id)

    where = "This group" if is_group else "You"
    lines = [f"<b>Credits</b>\n"]
    if sub and sub.live:
        lines += [
            f"<b>Pro</b> — {sub.days_left} day"
            f"{'' if sub.days_left == 1 else 's'} left.",
            f"{where} can ask <b>{bal.free_left}</b> more today "
            f"(of {bal.allowance}).",
        ]
    else:
        lines += [
            f"{where} can ask <b>{bal.free_left}</b> more today "
            f"(of {bal.allowance} free).",
            "",
            f"<i>Pro raises that to {settings.OPRAI_TG_SUB_DAILY_CREDITS} a "
            f"day — /subscribe</i>",
        ]
    if bal.paid:
        lines.append(f"  · {bal.paid} extra credits (never expire)")
    lines += [
        "",
        "<i>Only questions to OPRAI use credits. Trading commands don't.</i>",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("subscribe", "topup", "pro"))
async def subscribe_cmd(message: Message) -> None:
    """Buy a month, paid in ETH from the user's own bot wallet.

    The wallet is already ours to sign with, so nobody has to leave the chat,
    send a transfer by hand and wait for someone to notice the hash.
    """
    user = message.from_user
    await upsert_tg_user(user.id, user.username)
    scope_id, is_group = _scope(message)

    if is_group and not await _is_group_admin(message):
        await message.answer("Only a group admin can subscribe this group.")
        return

    sub = await subscriptions.get(scope_id)
    try:
        eth, usd, rate = await subscriptions.cost()
    except pricing.PriceUnavailable:
        await message.answer(_NO_PRICE)
        return

    text = _PITCH.format(free=credits.free_allowance(is_group),
                         pro=settings.OPRAI_TG_SUB_DAILY_CREDITS,
                         usd=usd, rate=rate)
    if sub and sub.live:
        text += (f"\n\n<i>Already Pro — {sub.days_left} days left. Paying "
                 f"again adds a month to the end, it doesn't replace it.</i>")
    await message.answer(text, reply_markup=_sub_keyboard(eth))


@router.callback_query(F.data == "sub:go")
async def subscribe_confirm(cb: CallbackQuery) -> None:
    is_group = cb.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    if is_group and not await _is_group_admin(cb.message, cb.from_user.id):
        await cb.answer("Only a group admin can subscribe this group.",
                        show_alert=True)
        return
    scope_id = cb.message.chat.id if is_group else cb.from_user.id

    # Quote at the moment of paying, not at the moment the message was posted:
    # a button tapped an hour later must not charge an hour-old ETH price.
    try:
        eth, usd, _ = await subscriptions.cost()
    except pricing.PriceUnavailable:
        await cb.answer()
        await cb.message.edit_text(_NO_PRICE)
        return

    await cb.answer()
    await cb.message.edit_text("Paying…")
    try:
        sub = await subscriptions.pay(
            cb.from_user.id, scope_id=scope_id, is_group=is_group,
            eth=eth, usd=usd,
            on_sent=lambda: cb.message.edit_text(
                "⏳ Paid — waiting for the transfer to confirm…"),
        )
    except subscriptions.SubscriptionError as e:
        log.warning("subscribe_failed", telegram_id=cb.from_user.id,
                    error=str(e)[:200])
        await audit(cb.from_user.id, "subscribe_failed", {"error": str(e)[:200]})
        await cb.message.edit_text(f"❌ {e}")
        return

    await audit(cb.from_user.id, "subscribe",
                {"eth": eth, "usd": usd, "group": is_group,
                 "months": sub.months})
    await cb.message.edit_text(
        f"✅ <b>OPRAI Pro is live.</b>\n\n"
        f"{settings.OPRAI_TG_SUB_DAILY_CREDITS} questions a day, "
        f"{sub.days_left} days left.\n\n"
        f"<i>Thanks — this is what pays for the model.</i>"
    )


def _fmt_amount(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


async def _is_group_admin(message: Message, user_id: int | None = None) -> bool:
    """Telegram is the authority on who runs a room; asking it beats keeping
    our own list that drifts the moment someone is promoted.

    `user_id` must be given whenever the message is not the person's own — a
    button tap arrives on a message the BOT sent, so reading the sender off it
    checks the bot's membership instead of the tapper's.
    """
    try:
        member = await message.bot.get_chat_member(
            message.chat.id, user_id if user_id is not None else message.from_user.id
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
    (38, "⏳ <i>Still going — if this doesn't land shortly I'll say so.</i>"),
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

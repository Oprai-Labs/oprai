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
import time

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.config import settings
from app.db import audit, upsert_tg_user
from app.logging_config import log
from app.services import auth as auth_svc
from app.services import chat as chat_svc
from app.services import credits

router = Router(name="chat")

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
async def topup_cmd(message: Message) -> None:
    _, is_group = _scope(message)
    who = "this group" if is_group else "your account"
    await message.answer(
        f"<b>Top up {who}</b>\n\n"
        f"Send $OPRAI to:\n<code>{settings.OPRAI_TG_DEV_WALLET}</code>\n"
        "on Robinhood Chain, then send the transaction hash here and an admin "
        "will credit it.\n\n"
        "<i>Credits are only spent on questions to OPRAI — trading commands "
        "are never charged for.</i>"
    )


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
        # Nothing was delivered, so nothing should be charged.
        await credits.refund(scope_id, user.id, 1, reason=type(e).__name__)
        log.warning("chat_failed", telegram_id=user.id, error=str(e)[:200])
        await audit(user.id, "chat_failed", {"error": str(e)[:200]})
        try:
            await placeholder.edit_text(f"⚠️ {e}")
        except Exception:  # noqa: BLE001
            await message.answer(f"⚠️ {e}")
        return

    await chat_svc.remember_session(scope_id, user.id, answer.session_id or "")
    await audit(user.id, "chat_answered",
                {"chars": len(answer.text), "group": is_group})

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


# Commands the bot really has, mapped from what the model wanted to do. The
# assistant explains; execution stays in the commands, where the confirmation
# card and the balance checks live.
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

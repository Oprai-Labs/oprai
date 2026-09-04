"""Answering privately when the question was asked in a room.

Everything the bot does with money is personal: how much someone holds, what
they are about to spend, which wallet it leaves from. In a private chat that is
between us. In a group it is a broadcast — and a group is exactly where people
use a bot like this, so the default has to be right rather than left to each
handler to remember.

So a card that shows a balance or asks for a confirmation goes to the person's
DM, and the room gets a line saying so. Nothing about the amount, the token or
the wallet appears in the group.

The one case that needs care: a bot cannot open a conversation with someone who
has never started it. Telegram refuses, and the answer would vanish silently —
so the refusal is caught and the room is told how to fix it.
"""

from __future__ import annotations

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message

from app.logging_config import log


def in_group(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


async def private_answer(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    public_note: str | None = None,
    to_user_id: int | None = None,
    **kwargs,
) -> Message | None:
    """Reply where the reply belongs.

    In a private chat this is just `message.answer`. In a group the content
    goes to the person's DM and the room gets a short, contentless note.

    `to_user_id` must be given when replying to a button press: the message a
    callback carries was sent by the BOT, so its `from_user` is the bot, and
    the DM would go to ourselves. Pass `callback.from_user.id` — the person who
    actually pressed it.

    Returns the message that carries the content (so a caller can edit it), or
    None when the person could not be reached — in which case the room has
    already been told why.
    """
    if not in_group(message):
        return await message.answer(text, reply_markup=reply_markup, **kwargs)

    target = to_user_id if to_user_id is not None else message.from_user.id
    try:
        sent = await message.bot.send_message(
            target, text, reply_markup=reply_markup, **kwargs
        )
    except TelegramForbiddenError:
        # Telegram will not let a bot speak first. Say so in the room, without
        # repeating anything the person typed.
        me = await message.bot.me()
        await message.reply(
            f"I'll answer privately — but you need to start me first: "
            f"open @{me.username}, press Start, then run that again."
        )
        return None
    except Exception as e:  # noqa: BLE001 — never lose the room's turn to a send error
        log.warning("private_answer_failed", telegram_id=target, error=str(e))
        await message.reply("I couldn't message you privately just now.")
        return None

    try:
        await message.reply(public_note or "📬 Sent you the details privately.")
    except Exception:  # noqa: BLE001 — the note is courtesy, the DM was the answer
        pass
    return sent

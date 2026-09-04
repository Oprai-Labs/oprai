"""Talking to OPRAI — the assistant, not the command set.

This is the part people actually came for: "is NVDA worth holding here", "what
is my portfolio exposed to", "explain this wallet's last week". The commands
execute; this explains, analyses and advises.

It streams from chat-service over SSE. Streaming is not for show — a real
analysis can take twenty seconds, and a Telegram message that grows is the
difference between "thinking" and "broken". We accumulate deltas and edit the
message occasionally rather than on every token, because Telegram rate-limits
edits and a stuttering message reads worse than a calm one.

Scope: Robinhood Chain. The bot is Robinhood-only, so every question is asked
with that context attached — otherwise the classifier's Solana bias answers a
Robinhood question with a Solana answer.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from dataclasses import dataclass, field

import httpx

from app.config import settings

# The protocols that live on Robinhood Chain. Passing them steers tool
# selection to this chain instead of letting the classifier default elsewhere.
ROBINHOOD_PROTOCOLS = ["relay", "morpho", "sushi", "uniswap", "lighter", "pons"]

# Telegram rejects messages over 4096 characters.
TELEGRAM_LIMIT = 4096


class ChatError(RuntimeError):
    pass


@dataclass
class Answer:
    # Everything the model streamed, reasoning included. The service marks its
    # reasoning inline with <think>…</think> rather than as a separate event,
    # so the raw stream is kept and the visible text derived from it — a tag
    # can arrive split across two deltas, and deciding chunk by chunk would
    # leak the opening fragment before its match shows up.
    raw: str = ""
    session_id: str | None = None
    title: str | None = None
    # What the model wanted to *do*, if anything. We don't execute from here —
    # the bot has real commands for that — but naming the action lets us point
    # the user at the one that does it.
    actions: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def text(self) -> str:
        """The answer, with the reasoning removed.

        Showing a model's reasoning to someone who asked a question is showing
        them the working instead of the answer.
        """
        return strip_reasoning(self.raw)


_THINK = re.compile(r"<think>.*?</think>", re.S)
# A block still being streamed has no closing tag yet; drop it from the tail so
# a progress edit never flashes the reasoning.
_THINK_OPEN = re.compile(r"<think>.*\Z", re.S)


def strip_reasoning(text: str) -> str:
    return _THINK_OPEN.sub("", _THINK.sub("", text)).strip()


def new_local_session() -> str:
    """A placeholder the backend swaps for a real session on first use, so a
    first question costs one round trip instead of two."""
    return f"local:{uuid.uuid4()}"


async def stream(
    jwt: str,
    session_id: str,
    content: str,
    *,
    on_progress=None,
    timeout: float = 180.0,
) -> Answer:
    """Ask a question and collect the answer.

    `on_progress(text)` is called as the answer grows so a caller can show it
    arriving; it is advisory and its failures never break the stream.
    """
    url = f"{settings.GATEWAY_URL.rstrip('/')}/chat/messages/stream"
    headers = {
        "Authorization": f"Bearer {jwt}",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/event-stream",
    }
    body = {
        "sessionId": session_id,
        "content": content[:2000],
        "protocols": ROBINHOOD_PROTOCOLS,
    }

    answer = Answer(session_id=None if session_id.startswith("local:") else session_id)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=body, headers=headers) as r:
                if r.status_code != 200:
                    raw = (await r.aread()).decode(errors="replace")
                    raise ChatError(_readable_error(r.status_code, raw))
                async for line in r.aiter_lines():
                    # Heartbeats arrive as SSE comments (": hb") to keep the
                    # connection alive through a long analysis — not data.
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if _consume(event, answer) and on_progress:
                        try:
                            await on_progress(answer.text)
                        except Exception:  # noqa: BLE001 — display is not the answer
                            pass
    except httpx.HTTPError as e:
        raise ChatError(f"couldn't reach OPRAI: {e}") from e

    if answer.error:
        raise ChatError(answer.error)
    # An answer made only of an action is a complete answer — "buy 0.01 NVDA"
    # is meant to DO something, and the model says so by emitting the action
    # and no prose. Treating that as failure threw away the very thing the
    # person asked for and told them to rephrase.
    if not answer.text.strip() and not answer.actions:
        raise ChatError("OPRAI didn't have an answer for that — try rephrasing it.")
    return answer


def _consume(event: dict, answer: Answer) -> bool:
    """Fold one SSE event into the answer. Returns True if the *visible* text
    grew — a delta that is only reasoning must not trigger a redraw showing
    nothing new."""
    if "delta" in event:
        before = len(answer.text)
        answer.raw += event["delta"] or ""
        return len(answer.text) > before
    if "sessionId" in event:
        answer.session_id = event["sessionId"]
    elif "title" in event:
        answer.title = event["title"]
    elif "action" in event:
        answer.actions.append(event["action"] or {})
    elif "error" in event:
        answer.error = str(event["error"])
    # 'thinking', 'query', 'clarify', 'messageId' are deliberately not shown:
    # reasoning traces and internal ids are not answers.
    return False


def _readable_error(status: int, raw: str) -> str:
    """Turn a transport failure into something worth showing a person.

    Log text in a chat message tells the user nothing they can act on, so we
    map the cases that have an actual next step and stay vague about the rest.
    """
    try:
        detail = str(json.loads(raw).get("error") or json.loads(raw).get("detail") or "")
    except (json.JSONDecodeError, AttributeError, TypeError):
        detail = ""
    if status == 429 or "limit" in detail.lower():
        return "OPRAI is at its rate limit right now — try again in a moment."
    if status in (401, 403):
        return "your session expired — send /start and try again."
    return "OPRAI is having trouble answering right now. Try again shortly."


# ── formatting ──────────────────────────────────────────────────────────────
# The model writes Markdown; Telegram's HTML mode is what the rest of the bot
# uses, and mixing the two produces visible asterisks or a parse error that
# silently drops the whole message.
def to_telegram_html(text: str) -> str:
    # Fence and inline code first, so their contents are never re-parsed.
    holds: list[str] = []

    def hold(rendered: str) -> str:
        holds.append(rendered)
        return f"\x00{len(holds) - 1}\x00"

    def fence(m: re.Match) -> str:
        return hold(f"<pre>{html.escape(m.group(2))}</pre>")

    text = re.sub(r"```(\w+)?\n(.*?)```", fence, text, flags=re.S)
    text = re.sub(r"`([^`\n]+)`",
                  lambda m: hold(f"<code>{html.escape(m.group(1))}</code>"), text)

    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    # Markdown headings have no Telegram equivalent; bold is the closest thing.
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+(.+)$", r"<b>\1</b>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', text)

    for i, rendered in enumerate(holds):
        text = text.replace(f"\x00{i}\x00", rendered)
    return text.strip()


def split_for_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split a long answer without cutting a tag in half.

    Splitting inside an HTML tag makes Telegram reject the whole message, so we
    break on blank lines, then lines, and only then on length.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"))
        if cut < limit // 2:  # no sensible break — fall back to a space
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining.strip():
        parts.append(remaining.strip())
    return parts


# ── sessions ────────────────────────────────────────────────────────────────
async def session_for(scope_id: int, telegram_id: int) -> str:
    """The conversation thread for this chat, so follow-ups keep their context.

    One thread per Telegram scope: in a group the conversation is the room's,
    which is what makes "and what about TSLA?" work after someone else asked
    about NVDA.
    """
    from app.db import pool

    row = await pool().fetchrow(
        "SELECT session_id FROM tg_chat_sessions WHERE scope_id = $1", scope_id
    )
    if row:
        return row["session_id"]
    return new_local_session()


async def remember_session(scope_id: int, telegram_id: int, session_id: str) -> None:
    """Persist the real session id the backend handed back. A `local:` id is a
    placeholder that was never a session, so it is not worth storing."""
    from app.db import pool

    if not session_id or session_id.startswith("local:"):
        return
    await pool().execute(
        """
        INSERT INTO tg_chat_sessions (scope_id, telegram_id, session_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (scope_id) DO UPDATE
            SET session_id = EXCLUDED.session_id,
                telegram_id = EXCLUDED.telegram_id,
                updated_at = now()
        """,
        scope_id, telegram_id, session_id,
    )


async def reset_session(scope_id: int) -> None:
    from app.db import pool

    await pool().execute("DELETE FROM tg_chat_sessions WHERE scope_id = $1", scope_id)

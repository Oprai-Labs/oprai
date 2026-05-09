"""Message CRUD and streaming operations against chat_messages table."""

import asyncio
import base64
import json
import logging
import re
import unicodedata
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime

from prometheus_client import Counter
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.clients import market_data
from app.config import settings
from app.models.message import ChatMessage
from app.models.session import ChatSession
from app.services.llm import LLMService
from app.services.summary import build_llm_context, maybe_create_summary

_log = logging.getLogger(__name__)

# Prometheus counter — incremented on every suspected prompt injection attempt.
# Enables alerting when the rate spikes (e.g. alert if > 10/min per wallet).
PROMPT_INJECTION_ATTEMPTS = Counter(
    "oprai_prompt_injection_attempts_total",
    "Total suspected prompt injection attempts detected in user messages",
    ["wallet"],
)


async def get_messages(
    db: AsyncSession,
    wallet: str,
    session_id: str,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    """Return messages for a session, ordered chronologically.

    Superseded messages (those replaced by an inline edit) are filtered out —
    the user soft-deleted them by editing an earlier turn, so neither the
    chat UI nor the LLM should see them again.
    """
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == uuid.UUID(session_id),
            ChatMessage.wallet_address == wallet,
        )
        .order_by(ChatMessage.created_at.asc())
    )
    if offset and offset > 0:
        stmt = stmt.offset(offset)
    if limit and limit > 0:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    messages = result.scalars().all()
    visible = [m for m in messages if not (m.metadata_ or {}).get("superseded_at")]
    return [
        {
            "id": str(m.id),
            "speaker": m.role,
            "content": m.content,
            "timestamp": m.created_at.isoformat(),
            "annotations": (m.metadata_ or {}).get("annotations"),
            "metadata": {
                k: v for k, v in (m.metadata_ or {}).items()
                if k in (
                    "action_results", "query_snapshots",
                    # Structured intent data from function calling
                    "actions", "queries", "clarifications",
                    # User-side: explicit protocol tags entered via @-mention
                    # in the composer; the bubble renders them as chips.
                    "protocols",
                    # Edit history — frontend may show "(edited)" indicator.
                    "edited_at",
                    # QueryCard data shown to the user (slim summary). Used by
                    # build_llm_context to give the model cross-turn awareness
                    # without re-fetching.
                    "query_card_results",
                )
            },
        }
        for m in visible
    ]


async def create_message(
    db: AsyncSession,
    session_id: str,
    wallet: str,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> dict:
    """Persist a single message and update session timestamp."""
    msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=uuid.UUID(session_id),
        wallet_address=wallet,
        role=role,
        content=content,
        metadata_=metadata or {},
    )
    db.add(msg)

    # Bump the session's updated_at timestamp
    await db.execute(
        update(ChatSession)
        .where(ChatSession.id == uuid.UUID(session_id))
        .values(updated_at=func.now())
    )

    await db.flush()
    await db.refresh(msg)
    return {
        "id": str(msg.id),
        "speaker": msg.role,
        "content": msg.content,
        "timestamp": msg.created_at.isoformat(),
    }


async def update_message_metadata(
    db: AsyncSession,
    session_id: str,
    message_id: str,
    wallet: str,
    patch: dict,
) -> bool:
    """Merge `patch` into message metadata_. Returns False if not found or IDs invalid."""
    try:
        msg_uuid = uuid.UUID(message_id)
        sess_uuid = uuid.UUID(session_id)
    except ValueError:
        return False
    stmt = select(ChatMessage).where(
        ChatMessage.id == msg_uuid,
        ChatMessage.session_id == sess_uuid,
        ChatMessage.wallet_address == wallet,
    )
    result = await db.execute(stmt)
    msg = result.scalar_one_or_none()
    if msg is None:
        return False

    existing = dict(msg.metadata_ or {})
    # Deep-merge top-level keys (action_results / query_snapshots dicts)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(existing.get(key), dict):
            existing[key] = {**existing[key], **value}
        else:
            existing[key] = value
    msg.metadata_ = existing
    flag_modified(msg, "metadata_")
    await db.flush()
    return True


async def _increment_message_count(
    db: AsyncSession,
    session_id: str,
) -> int:
    """Atomically increment message_count and return the new value."""
    result = await db.execute(
        update(ChatSession)
        .where(ChatSession.id == uuid.UUID(session_id))
        .values(message_count=ChatSession.message_count + 1)
        .returning(ChatSession.message_count)
    )
    row = result.fetchone()
    await db.flush()
    return row[0] if row else 0


# ── Prompt injection defence ──────────────────────────────────────────────────
#
# These patterns are used to escape user-supplied content before it is sent
# to the LLM.  With function calling the LLM should never emit [ACTION:...]
# text blocks, but sanitising the input provides defence-in-depth against
# adversarial messages that try to inject action syntax or system-prompt
# overrides into the conversation context.

_INJECTION_BLOCK_RE = re.compile(
    r"\[(ACTION|QUERY|CLARIFY):",
    re.IGNORECASE,
)

# Hard-block: unambiguous injection attempts with no legitimate use case.
# Applied AFTER Unicode NFKC normalisation (see _sanitize_user_input) to catch
# homoglyph variants (e.g. Cyrillic І instead of Latin I).
_INJECTION_HARD_BLOCK_RE = re.compile(
    # English
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?|"
    r"disregard\s+(the\s+)?system\s+prompt|"
    r"forget\s+(all\s+)?(previous|your)\s+instructions?|"
    r"new\s+system\s+prompt\s*:|"
    r"override\s+(the\s+)?(system\s+)?prompt|"
    r"do\s+not\s+follow\s+(the\s+)?(previous|above|system)\s+instructions?|"
    # Turkish
    r"önceki\s+(tüm\s+)?talimatlar[ıi]\s+(görmezden\s+gel|yoksay|unut)|"
    r"sistem\s+prompt[u']?\s*(görmezden\s+gel|yoksay|unut|değiştir)|"
    # Spanish
    r"ignora\s+(todas\s+las\s+)?instrucciones\s+anteriores|"
    r"olvida\s+(todas\s+)?tus\s+instrucciones|"
    # French
    r"ignore\s+(toutes\s+les\s+)?instructions\s+précédentes|"
    r"oublie\s+(toutes\s+)?tes\s+instructions|"
    # German
    r"ignoriere\s+(alle\s+)?vorherigen\s+anweisungen|"
    # Portuguese
    r"ignore\s+(todas\s+as\s+)?instruções\s+anteriores|"
    # Chinese (Simplified)
    r"忽略(所有|之前|上面)(的)?指令|"
    r"忘记(所有|之前)(的)?指令|"
    # Russian
    r"игнорируй\s+(все\s+)?предыдущие\s+инструкции|"
    r"забудь\s+(все\s+)?предыдущие\s+инструкции|"
    # Arabic
    r"تجاهل\s+(جميع\s+)?التعليمات\s+السابقة",
    re.IGNORECASE,
)

# Patterns for system-prompt extraction attempts — hard-blocked.
_EXTRACTION_BLOCK_RE = re.compile(
    r"repeat\s+(everything|all|your|the)\s+(above|before|instructions?|prompt|system)|"
    r"print\s+(your|the|all|every)?\s*(system\s+)?prompt|"
    r"(what|show|display|output|reveal|tell\s+me)\s+(are|is|were|was)?\s*(your|the)?\s*"
    r"(initial|original|full|complete|current|all)?\s*(instructions?|system\s+prompt|context|guidelines?)|"
    r"verbatim\s+(system|prompt|instruction|above)|"
    r"(copy|dump|leak|exfiltrate)\s+(the\s+)?(system\s+)?prompt|"
    # Turkish
    r"sistem\s+(promptunu?|talimatlar[ıi]n[ıi])\s+(tekrarla|yaz|göster|söyle)|"
    r"(başlangıç|başlangıçtaki)\s+talimatlar[ıi]\s+(göster|yaz|söyle)",
    re.IGNORECASE,
)

# Soft-log only: ambiguous phrases with false-positive risk.
_OVERRIDE_PHRASES_RE = re.compile(
    r"you\s+are\s+now\s+(?!OPRAI)|"
    r"forget\s+(all\s+)?(previous|your)\s+instructions?|"
    r"talimatlar[ıi]\s+(unut|sil|değiştir)|"
    r"artık\s+sen\s+(?!OPRAI)",
    re.IGNORECASE,
)

# Base64 pattern: 40+ consecutive base64 chars (long enough to encode a jailbreak phrase).
# Short base64 strings (file names, tokens, etc.) are common and benign.
_BASE64_LONG_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _try_decode_base64(text: str) -> str | None:
    """Return the decoded string if the text looks like meaningful base64, else None."""
    try:
        decoded = base64.b64decode(text + "==").decode("utf-8", errors="strict")
        # Only count as base64 if it decodes to printable ASCII (not binary noise)
        if all(0x20 <= ord(c) < 0x7F or c in "\n\r\t" for c in decoded):
            return decoded
    except Exception:
        pass
    return None

_MAX_USER_CONTENT_LEN = 2_000   # ~500 tokens; protects against token-flooding
_MAX_SESSION_MESSAGES = 200  # 100 conversation turns (user + assistant)
_MAX_TOOL_CALLS_PER_RESPONSE = 5  # prevent LLM fan-out abuse (e.g. 100 swap actions)

# Query types that render as an interactive QueryCard mini-app on the frontend
# (search + sort + paginated table) instead of being summarized as prose by the
# LLM. These types are STILL fetched server-side so the LLM can write a short
# intro, but the SSE `{query}` event is also emitted so the card mounts and the
# user gets the full interactive list. Add a type here when its frontend render
# branch (apps/oprai/.../query-card.component.ts) is implemented.
QUERY_CARD_RENDER_TYPES: frozenset[str] = frozenset({
    "meteora_dlmm_get_pairs",
    "meteora_dammv2_get_pools",
    "meteora_dammv1_get_pools",
})

# Cap each persisted summary so chat history doesn't balloon: only the first
# `_QUERY_CARD_SUMMARY_ROWS` rows of a list are kept, with key fields only.
_QUERY_CARD_SUMMARY_ROWS = 5

def _summarize_query_card_payload(name: str, params: dict, result: object) -> dict | None:
    """Compact, model-readable summary of one QueryCard fetch.

    The aim is two-fold:
      1. Tell the next-turn model exactly what the user already saw.
      2. Stay tiny — capped row count, key fields only — so chat history
         doesn't grow O(N×rows) over a long session.

    Returns a dict ready to be JSON-serialised onto the assistant message
    metadata, or None if the payload shape isn't summarisable.
    """
    if not isinstance(result, dict):
        return None

    # Meteora DLMM list responses share this envelope shape:
    # { current_page, page_size, pages, total, data: [<pool>, …] }
    pairs = result.get("data") if isinstance(result.get("data"), list) else None

    if name == "meteora_dlmm_get_pairs" and pairs is not None:
        rows = []
        for p in pairs[:_QUERY_CARD_SUMMARY_ROWS]:
            if not isinstance(p, dict):
                continue
            rows.append({
                "address": p.get("address"),
                "name": p.get("name"),
                "tvl": p.get("tvl"),
                "apy": p.get("apy"),
                "volume_24h": (p.get("volume") or {}).get("24h") if isinstance(p.get("volume"), dict) else None,
                "fee_24h": (p.get("fees") or {}).get("24h") if isinstance(p.get("fees"), dict) else None,
                "bin_step": (p.get("pool_config") or {}).get("bin_step") if isinstance(p.get("pool_config"), dict) else None,
                "base_fee_pct": (p.get("pool_config") or {}).get("base_fee_pct") if isinstance(p.get("pool_config"), dict) else None,
            })
        return {
            "type": name,
            "params": params,
            "total": result.get("total"),
            "page": result.get("current_page"),
            "pages": result.get("pages"),
            "page_size": result.get("page_size"),
            "rows_shown_in_summary": len(rows),
            "top_rows": rows,
        }

    # Fallback for other future card types: keep the first N items if the
    # payload is a list; otherwise drop a tiny "key set" hint.
    return None

# Keys that only appear inside tool-call JSON blobs — never in normal prose.
_TOOL_BLOB_KEYS = ('"query_type"', '"action_type"', '"query_onchain"', '"execute_action"')


def _clean_delta(text: str) -> str:
    """Single pipeline for all text-stream post-processing."""
    text = _strip_tool_blob_lines(text)
    text = _strip_kb_citations(text)
    return text


def _strip_tool_blob_lines(text: str) -> str:
    """Remove lines that look like raw tool-call JSON the LLM accidentally emitted as text."""
    lines = text.split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("{") and s.endswith("}") and any(k in s for k in _TOOL_BLOB_KEYS):
            _log.debug("stripped LLM tool-call JSON blob from text stream: %.120s", s)
            continue
        out.append(line)
    return "\n".join(out)


_KB_CITATION_RE = re.compile(r"\s*\[\w[\w.\-]*:\d+\]")
_MULTI_SPACE_RE  = re.compile(r"  +")


def _strip_kb_citations(text: str) -> str:
    """Remove inline KB citation tags the model copies from the Knowledge Context block."""
    if "[" not in text:
        return text
    text = _KB_CITATION_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text


class PromptInjectionError(ValueError):
    """Raised when a message contains an unambiguous prompt-injection attempt."""


def _sanitize_user_input(content: str, wallet: str = "unknown") -> str:
    """Sanitise user-supplied message content before sending it to the LLM.

    1. Truncate to _MAX_USER_CONTENT_LEN characters.
    2. Unicode NFKC normalisation — collapses homoglyphs (Cyrillic І → I, etc.).
    3. Escape [ACTION: / [QUERY: / [CLARIFY: block syntax.
    4. Hard-block unambiguous injection / extraction attempts.
    5. Check decoded Base64 segments for injection patterns.
    6. Soft-log ambiguous phrases for Prometheus monitoring.
    """

    if len(content) > _MAX_USER_CONTENT_LEN:
        content = content[:_MAX_USER_CONTENT_LEN]
        _log.debug("User content truncated to %d chars", _MAX_USER_CONTENT_LEN)

    # NFKC normalisation neutralises homoglyph attacks (e.g. "Іgnore" with Cyrillic І).
    content = unicodedata.normalize("NFKC", content)

    def _escape_block(m: re.Match) -> str:
        return f"⌊{m.group(1)}⌋:"

    content = _INJECTION_BLOCK_RE.sub(_escape_block, content)

    def _block(reason: str) -> None:
        _log.warning(
            "Prompt injection blocked — %s (wallet=%s, preview=%r)",
            reason, wallet, content[:80],
        )
        PROMPT_INJECTION_ATTEMPTS.labels(wallet=wallet[:16] + "…").inc()
        raise PromptInjectionError("Message contains disallowed content.")

    if _INJECTION_HARD_BLOCK_RE.search(content):
        _block("override pattern")

    if _EXTRACTION_BLOCK_RE.search(content):
        _block("extraction pattern")

    # Scan long base64 segments — decode and re-check for injection patterns.
    for b64_match in _BASE64_LONG_RE.finditer(content):
        decoded = _try_decode_base64(b64_match.group())
        if decoded and (
            _INJECTION_HARD_BLOCK_RE.search(decoded)
            or _EXTRACTION_BLOCK_RE.search(decoded)
        ):
            _block("base64-encoded injection")

    if _OVERRIDE_PHRASES_RE.search(content):
        _log.warning(
            "Suspected prompt-injection attempt (wallet=%s, preview=%r)",
            wallet, content[:80],
        )
        PROMPT_INJECTION_ATTEMPTS.labels(wallet=wallet[:16] + "…").inc()

    return content


async def _build_recent_context_from_db(db, session_id: str) -> str:
    """
    Pull the last assistant turn (+ user turn before it) directly from the
    chat-message table, formatted for the intent classifier.

    The classifier runs BEFORE `build_llm_context()`, so we cannot use the
    full LLM-formatted message list (it doesn't exist yet at that point).
    Reading the last 2-3 rows from the DB is cheap and enough — the
    classifier only needs context to resolve referential phrases like
    "tamamını" / "kapat onu" against the prior assistant turn.

    Stays small on purpose: 2 turns × ~400 chars cap = ~800 chars total.
    """
    try:
        result = await db.execute(
            select(ChatMessage.role, ChatMessage.content)
            .where(ChatMessage.session_id == uuid.UUID(session_id))
            .order_by(ChatMessage.created_at.desc())
            .limit(4)
        )
        rows = list(result.all())
    except Exception:
        return ""

    pieces: list[str] = []
    seen_assistant = False
    seen_user = False
    for role, content in rows:
        if role not in ("assistant", "user"):
            continue
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        if role == "assistant" and not seen_assistant:
            pieces.append(f"Assistant: {text[:400]}")
            seen_assistant = True
        elif role == "user" and seen_assistant and not seen_user:
            pieces.append(f"User: {text[:400]}")
            seen_user = True
        if seen_assistant and seen_user:
            break
    # Order chronologically (older first) for the classifier prompt.
    return "\n".join(reversed(pieces))


def _build_recent_context(model_messages: list[dict]) -> str:
    """
    Legacy helper kept for any caller still operating on a built LLM-context
    list. New code should prefer `_build_recent_context_from_db` so the
    classifier can run before `build_llm_context`.
    """
    if not model_messages:
        return ""
    # Skip the current user message (it's already passed separately to the
    # classifier as the primary signal). Walk backwards through prior turns.
    pieces: list[str] = []
    seen_assistant = False
    seen_user = False
    for msg in reversed(model_messages):
        role = msg.get("role")
        if role not in ("assistant", "user"):
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        if role == "assistant" and not seen_assistant:
            pieces.append(f"Assistant: {text[:400]}")
            seen_assistant = True
        elif role == "user" and seen_assistant and not seen_user:
            # Only include the user turn that prompted the last assistant reply.
            pieces.append(f"User: {text[:400]}")
            seen_user = True
            break
    return "\n".join(reversed(pieces))


# ── Main streaming function ───────────────────────────────────────────────────

async def stream_chat_response(
    db: AsyncSession,
    session_id: str,
    wallet: str,
    user_content: str,
    is_first_message: bool = False,
    attachments: list[dict] | None = None,
    protocols: list[str] | None = None,
    extra_user_metadata: dict | None = None,
) -> AsyncGenerator[str, None]:
    """Save user message, stream LLM response via SSE, then persist assistant reply.

    Context is built entirely from the database (summaries + raw messages).
    Yields SSE-formatted strings: ``data: <json>\\n\\n``

    SSE event types:
      {delta: str}                       — text chunk
      {thinking: str}                    — reasoning chunk (reasoning models)
      {action: {type, params, ...}}      — validated action from function calling
      {query: {type, params}}            — validated query from function calling
      {clarify: {category, question, options}} — disambiguation request
      {sessionId: str}                   — server session ID (first message only)
      {messageId: str}                   — DB message ID (after streaming completes)
      {title: str}                       — auto-generated title (first message only)
      {error: str, errorType: str}       — error event
      [DONE]                             — stream end sentinel

    Prompt injection defence:
      - User content is sanitised before being sent to the LLM.
      - The LLM uses OpenAI function calling (execute_action / query_onchain /
        request_clarification) instead of text-based [ACTION:...] blocks.
      - Tool call arguments are validated against strict Pydantic schemas with
        Solana address format checks before being forwarded to the client.
    """
    from app.services.title_generator import generate_title
    from app.services import session as session_svc
    from app.services.action_schemas import (
        ValidatedAction,
        ValidatedQuery,
        ValidatedClarify,
        validate_tool_call,
    )
    from app.services.tool_selector import ToolSelector

    # ── 0. Check session message limit ──────────────────────────────────────
    count_row = await db.execute(
        select(ChatSession.message_count)
        .where(ChatSession.id == uuid.UUID(session_id))
    )
    current_count = count_row.scalar() or 0
    if current_count >= _MAX_SESSION_MESSAGES:
        yield f"data: {json.dumps({'error': 'This conversation has reached its limit. Please start a new chat to continue.', 'errorType': 'chat_limit'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # ── 0b. Per-wallet LLM daily caps ───────────────────────────────────────
    # Backstop against runaway agent loops, malicious clients, or buggy
    # integrations exhausting our OpenAI budget. Two caps cooperate:
    #   • OPRAI_LLM_DAILY_TOKEN_CAP — authoritative, recorded post-call (~).
    #   • OPRAI_LLM_DAILY_MESSAGE_CAP — pre-call gate, recorded immediately.
    # See `cost_cap.py`.
    from app.services.cost_cap import (
        assert_under_cap,
        record_message,
        record_tokens,
        LLMCapExceeded,
    )
    try:
        await assert_under_cap(wallet)
    except LLMCapExceeded as cap_err:
        yield f"data: {json.dumps({'error': str(cap_err), 'errorType': 'llm_daily_cap'})}\n\n"
        yield "data: [DONE]\n\n"
        return
    # Record the message-count immediately so a runaway client can't bypass
    # the gate by never letting the stream complete.
    await record_message(wallet)

    # Build metadata with attachments + explicit protocol tags. The frontend
    # composer lets the user type `@jupiter` etc. to scope a message — we
    # persist those tags so they re-render on reload and so any future read
    # path (audit, debugging) knows what the user explicitly opted into.
    msg_metadata: dict = {}
    if attachments:
        msg_metadata["attachments"] = attachments
    if protocols:
        # Normalise once so DB matches the canonical tag format used elsewhere.
        msg_metadata["protocols"] = sorted({p.lower().replace("-", "_") for p in protocols if p})
    # Caller-supplied flags merged last so they can override (e.g. the edit
    # endpoint adds `edited_at` so the bubble keeps its "(edited)" tag after
    # a page reload). Reserved keys we never let the caller set are stripped:
    # `superseded_at` and `superseded_by_edit_of` are private supersede state
    # owned by the edit pipeline, not arbitrary callers.
    if extra_user_metadata:
        for k, v in extra_user_metadata.items():
            if k in ("superseded_at", "superseded_by_edit_of"):
                continue
            msg_metadata[k] = v

    # ── 1. Sanitise user input (prompt injection defence) ────────────────
    try:
        safe_content = _sanitize_user_input(user_content, wallet=wallet)
    except PromptInjectionError:
        yield f"data: {json.dumps({'error': 'Your message contains disallowed content.', 'errorType': 'prompt_injection'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # ── 2. Persist user message (store original, send sanitised to LLM) ──
    user_msg_obj = ChatMessage(
        id=uuid.uuid4(),
        session_id=uuid.UUID(session_id),
        wallet_address=wallet,
        role="user",
        content=user_content,   # persist original for display
        metadata_=msg_metadata if msg_metadata else None,
    )
    db.add(user_msg_obj)
    await db.flush()

    # Surface the user message's real DB id to the client immediately.
    # Without this, the frontend's optimistic `temp-${Date.now()}` id is
    # the only id it ever has — and any subsequent edit on the same bubble
    # POSTs that fake id, which the edit endpoint can't parse as a UUID.
    # Yielding `userMessageId` lets the frontend swap the temp id for the
    # real one before the user has a chance to click "Edit" again.
    yield f"data: {json.dumps({'userMessageId': str(user_msg_obj.id)})}\n\n"

    # ── 3. Increment message_count ───────────────────────────────────────
    new_count = await _increment_message_count(db, session_id)

    # Commit user message and counter now, before the LLM stream starts.
    # If the client disconnects mid-stream (navigation, tab close), SQLAlchemy
    # receives CancelledError and rolls back the session — without this early
    # commit the user's message would vanish. The assistant message is committed
    # separately after streaming completes (step 8).
    await db.commit()

    # ── 4. Summarise previous block if needed ────────────────────────────
    await maybe_create_summary(db, session_id, wallet, new_count)

    # ── 5. Classify intent + pre-fetch knowledge in parallel (~100–200 ms) ──
    # Intent classification (gpt-4o-mini, ~100 ms) and Qdrant RAG pre-fetch
    # (~50–150 ms) overlap completely — both fire at the same time. The KB
    # block is passed into build_llm_context; injection is conditioned on
    # intent (action turns skip KB to protect prompt-cache hit-rate).
    from app.services.intent_router import IntentRouter, filter_tools_by_intent

    async def _rag_prefetch() -> str | None:
        if not settings.KNOWLEDGE_RAG_ENABLED or not safe_content:
            return None
        try:
            from app.rag import get_rag_service
            return await get_rag_service().get_context_for_query(
                query=safe_content,
                max_tokens=settings.KNOWLEDGE_RAG_TOKEN_BUDGET,
            )
        except Exception:
            logger.warning("RAG pre-fetch failed — will skip KB injection", exc_info=True)
            return None

    recent_context = await _build_recent_context_from_db(db, session_id)
    intent_result, prefetched_knowledge = await asyncio.gather(
        IntentRouter().classify(user_content, recent_context),
        _rag_prefetch(),
    )

    # Protocol scoping rule:
    #   • If the user explicitly @-tagged one or more protocols in the
    #     composer, those tags are AUTHORITATIVE — the classifier's guess
    #     is discarded. Otherwise the model treats "0.1 sol stake et" as
    #     ambiguous-staking and offers all of {marinade, jito, jupsol},
    #     defeating the entire point of the tag.
    #   • If no tag is set, fall back to whatever the classifier inferred.
    _explicit = {p.lower().replace("-", "_") for p in (protocols or [])}
    if _explicit:
        _all_protocols = sorted(_explicit)
    else:
        _all_protocols = sorted(set(intent_result.protocols))

    # ── 6. Build LLM context with the union of protocols ──────────────────
    # The prompt loader scopes which protocol prompt files to load; passing
    # the classifier-detected ids keeps prompt docs aligned with the tool
    # subset (model sees the tool name AND its usage docs).
    model_messages = await build_llm_context(
        db, session_id, wallet,
        current_attachments=attachments,
        sanitised_last_user_content=safe_content,
        protocols=_all_protocols,
        intent=intent_result.intent,
        prefetched_knowledge=prefetched_knowledge,
    )

    # ── Language enforcement (per-turn, high-priority, language-agnostic)
    # The base prompt has a "respond in user's language" rule but it's
    # buried in <output_style>, and the followup system messages plus the
    # fetched tool data are English-heavy — so the model occasionally
    # defaults to English mid-stream. We add a per-turn directive at the
    # END of the message list (highest recency) that anchors the language
    # to the user's actual last message verbatim. This works for ANY
    # language — Turkish, French, Russian, Japanese, Spanish, etc. — with
    # zero hardcoded language detection.
    if user_content and user_content.strip():
        # Excerpt is bounded so a long message can't blow the prompt.
        _user_excerpt = user_content.strip()[:400]
        model_messages.append({
            "role": "system",
            "content": (
                "LANGUAGE LOCK — match the user's language exactly.\n"
                "The user's last message:\n"
                f"<<<{_user_excerpt}>>>\n"
                "Your entire response (every word — including warnings, "
                "intros, comparisons, error messages, takeaways) must be "
                "in the SAME language as that message. Do not switch "
                "languages mid-response. Tool results and earlier system "
                "messages may contain English text — translate any labels "
                "or descriptions you reference; never quote them verbatim "
                "in another language."
            ),
        })

    # If the user explicitly @-tagged protocols in the composer, surface that
    # as a side-channel hint to the LLM. Without it, "0.1 sol stake et" with
    # tag=jupiter looks identical to "0.1 sol stake et" without a tag —
    # the model has no signal that the user already picked a venue, so it
    # asks again via request_clarification. The hint goes in just before the
    # last user turn (closest to the model's attention budget) and uses
    # imperative language so the model treats it as policy, not advice.
    if _explicit:
        _tag_list = ", ".join(sorted(_explicit))
        model_messages.append({
            "role": "system",
            "content": (
                f"User explicitly tagged the following protocol(s) in the composer: {_tag_list}.\n"
                "This is an AUTHORITATIVE protocol selection. Do NOT call "
                "`request_clarification` to ask which protocol/venue to use — "
                "the user already answered that question by tagging. Choose the "
                "matching action_type for the tagged protocol and execute it. "
                "If the message names an action type already (e.g. 'stake', "
                "'swap', 'lend') and amount/recipient are present, call "
                "`execute_action` directly. Only call `request_clarification` "
                "for OTHER kinds of ambiguity (missing amount, missing "
                "recipient address, etc.) — never for protocol choice."
            ),
        })

    # ── 7. Build the tool catalogue ───────────────────────────────────────
    # Tool selector takes the same classifier protocol set. Queries are
    # always full catalogue; actions are protocol-scoped for TX safety.
    # `filter_tools_by_intent` may further drop everything when the
    # classifier is highly confident the message is pure conversation
    # ("advice").
    tools = ToolSelector().build(_all_protocols)
    tools = filter_tools_by_intent(tools, intent_result)

    # Tool-choice gate: action / query intents MUST trigger a tool call. This
    # defeats Haiku 4.5's documented avoidance behaviour and the cheaper-model
    # tendency to answer "yes/no" to existence questions from training data
    # rather than calling the list query. "advice" / "casual" stay on auto so
    # small talk doesn't get force-fed a tool.
    if tools and intent_result.intent in ("action", "query"):
        tool_choice_mode = "required"
    else:
        tool_choice_mode = "auto"

    _log.info(
        "intent=%s confidence=%.2f protocols=%s source=%s tools_count=%d tool_choice=%s",
        intent_result.intent, intent_result.confidence,
        list(intent_result.protocols), intent_result.reason, len(tools),
        tool_choice_mode,
    )
    # TEMP DEBUG
    try:
        with open("/tmp/oprai-debug.log", "a") as _df:
            _df.write(f"\n========== NEW REQ wallet={wallet[:10]} ==========\n")
            _df.write(f"user_content={(user_content or '')[:200]}\n")
            _df.write(f"intent={intent_result.intent} conf={intent_result.confidence} "
                      f"protocols={list(intent_result.protocols)}\n")
            _df.write(f"all_protocols={_all_protocols}\n")
            _df.write(f"tools_count={len(tools)}\n")
            # Dump the message-by-message context the LLM is about to see.
            # Trimmed to first 220 chars per message so we can scan the trace
            # without flooding /tmp. The [Previous-turn data] injection should
            # appear here when an earlier turn rendered a QueryCard.
            for _idx, _m in enumerate(model_messages):
                _role = _m.get("role", "?")
                _c = _m.get("content", "")
                if isinstance(_c, list):
                    _c = " ".join(str(p) for p in _c)
                _txt = str(_c).replace("\n", " ")[:220]
                _df.write(f"  ctx[{_idx}] {_role}: {_txt}\n")
    except Exception:
        pass

    llm = LLMService()
    collected_text_chunks: list[str] = []
    collected_tool_calls: list[tuple[str, str]] = []   # (name, args_json)
    # Tool calls the model emitted but `validate_tool_call` rejected (bad
    # params, unknown action_type, malformed addresses, etc.). When all
    # tool calls in a turn end up here AND the model wrote no prose, the
    # assistant message is empty and the user sees a blank bubble. We
    # surface that as a friendly error event at end-of-stream.
    dropped_tool_calls: list[tuple[str, str]] = []

    _MAX_RETRIES = 2
    _RETRYABLE = ("rate_limit", "rate limit", "429", "quota", "too many requests",
                  "timeout", "timed out", "read timeout", "connect timeout",
                  "connection", "network", "unreachable", "refused")
    _attempt = 0

    # <think>…</think> state machine (unchanged from before)
    _CLOSE_TAG = "</think>"
    _CLOSE_LEN = len(_CLOSE_TAG)
    _TAIL_HOLD = _CLOSE_LEN - 1

    try:
        in_thinking = True
        thinking_buffer = ""
        think_open_stripped = False
        _stream_ok = False

        while _attempt <= _MAX_RETRIES:
            try:
                collected_text_chunks.clear()
                collected_tool_calls.clear()
                in_thinking = True
                thinking_buffer = ""
                think_open_stripped = False

                # Pre-tool text buffer. We hold all visible-text chunks in here
                # until the stream ends (or the response is known tool-free).
                # If any tool call fires, the buffered text is discarded — it's
                # always preamble like "Let me check…" and the followup pass
                # produces the real answer. If no tool calls fire, we flush the
                # buffer as the response.
                pre_tool_buffer: list[str] = []
                pre_tool_thinking_buffer: list[str] = []

                async for event in llm.astream_with_tools(model_messages, tools, tool_choice=tool_choice_mode):
                    kind = event[0]

                    if kind == "tool_call":
                        # Accumulate tool calls — validated and emitted after text stream
                        _, tc_name, tc_args = event
                        _log.debug("raw_tool_call name=%s args=%s", tc_name, tc_args)
                        # TEMP DEBUG: dump every tool-call to /tmp so we can see
                        # what the model actually emitted on user-side errors.
                        try:
                            with open("/tmp/oprai-debug.log", "a") as _df:
                                _df.write(f"[{wallet[:10]}] TOOL_CALL name={tc_name} args={tc_args[:300]}\n")
                        except Exception:
                            pass
                        collected_tool_calls.append((tc_name, tc_args))
                        # Drop any text that has accumulated so far — it's preamble.
                        pre_tool_buffer.clear()
                        pre_tool_thinking_buffer.clear()
                        continue

                    # kind == "text"
                    _, chunk = event

                    # ── <think>…</think> state machine ──────────────────
                    if in_thinking:
                        thinking_buffer += chunk

                        if not think_open_stripped:
                            stripped = thinking_buffer.lstrip()
                            if stripped.startswith("<think>"):
                                thinking_buffer = stripped[len("<think>"):]
                                think_open_stripped = True
                            elif len(stripped) > 10 and not stripped.startswith("<"):
                                in_thinking = False
                                tb_filtered = _strip_tool_blob_lines(thinking_buffer)
                                if tb_filtered:
                                    pre_tool_buffer.append(tb_filtered)
                                thinking_buffer = ""
                            continue

                        if _CLOSE_TAG in thinking_buffer:
                            before, after = thinking_buffer.split(_CLOSE_TAG, 1)
                            if before.strip():
                                pre_tool_thinking_buffer.append(before)
                            in_thinking = False
                            remainder = after.lstrip("\n")
                            if remainder:
                                remainder_filtered = _strip_tool_blob_lines(remainder)
                                if remainder_filtered:
                                    pre_tool_buffer.append(remainder_filtered)
                            thinking_buffer = ""
                        else:
                            safe_len = len(thinking_buffer) - _TAIL_HOLD
                            if safe_len > 0:
                                pre_tool_thinking_buffer.append(thinking_buffer[:safe_len])
                                thinking_buffer = thinking_buffer[safe_len:]
                    else:
                        filtered = _clean_delta(chunk)
                        if filtered:
                            pre_tool_buffer.append(filtered)

                # Stream ended. If the response had no tool calls, the buffered
                # text IS the answer — flush it out + record it. If tool calls
                # fired, the buffer was already discarded above.
                # Join all chunks before stripping so citations that arrived
                # across multiple streaming events are caught as a whole.
                if not collected_tool_calls:
                    for _t in pre_tool_thinking_buffer:
                        yield f"data: {json.dumps({'thinking': _t})}\n\n"
                    _full_text = _strip_kb_citations("".join(pre_tool_buffer))
                    if _full_text:
                        collected_text_chunks.append(_full_text)
                        yield f"data: {json.dumps({'delta': _full_text})}\n\n"

                _stream_ok = True
                break

            except Exception as _retry_exc:
                _exc_str = str(_retry_exc).lower()
                _is_retryable = any(k in _exc_str for k in _RETRYABLE)
                if _is_retryable and _attempt < _MAX_RETRIES:
                    _attempt += 1
                    _backoff = 2 ** _attempt
                    _log.warning(
                        "LLM stream attempt %d failed (retryable), retrying in %ds: %s",
                        _attempt, _backoff, _retry_exc,
                    )
                    await asyncio.sleep(_backoff)
                    continue
                raise

        if not _stream_ok:
            raise RuntimeError("LLM stream failed after retries")

        # Flush leftover thinking buffer
        if in_thinking and thinking_buffer.strip():
            yield f"data: {json.dumps({'thinking': thinking_buffer})}\n\n"

        # ── 7. Validate tool calls and emit structured SSE events ────────
        validated_actions: list[dict] = []
        validated_queries: list[dict] = []
        validated_clarifications: list[dict] = []
        # Collected market data tool results for follow-up LLM interpretation.
        # Each entry: (query_type, params, raw_result_or_error_dict)
        market_data_results: list[tuple[str, dict, object]] = []

        if len(collected_tool_calls) > _MAX_TOOL_CALLS_PER_RESPONSE:
            _log.warning(
                "LLM emitted %d tool calls (max %d) — truncating (wallet=%s)",
                len(collected_tool_calls), _MAX_TOOL_CALLS_PER_RESPONSE, wallet,
            )
            collected_tool_calls = collected_tool_calls[:_MAX_TOOL_CALLS_PER_RESPONSE]

        # Deduplicate execute_action calls with identical (action_type, params).
        # gpt-5.4-nano sometimes calls the same action twice in one response.
        _seen_actions: set[str] = set()
        _deduped: list[tuple[str, str]] = []
        for tc_name, tc_args in collected_tool_calls:
            if tc_name == "execute_action":
                try:
                    _key = json.dumps(json.loads(tc_args), sort_keys=True)
                except Exception:
                    _key = tc_args
                if _key in _seen_actions:
                    _log.warning("duplicate execute_action dropped (wallet=%s)", wallet)
                    continue
                _seen_actions.add(_key)
            _deduped.append((tc_name, tc_args))
        collected_tool_calls = _deduped

        # Pre-scan: if any clarification is requested, block all execute_action calls.
        # Mixing clarification + actions in one response is invalid — LLM must wait for user.
        _has_clarification = any(tc_name == "request_clarification" for tc_name, _ in collected_tool_calls)
        if _has_clarification:
            _original_count = len(collected_tool_calls)
            collected_tool_calls = [
                (tc_name, tc_args) for tc_name, tc_args in collected_tool_calls
                if tc_name != "execute_action"
            ]
            if len(collected_tool_calls) < _original_count:
                _log.warning(
                    "clarification_action_conflict: dropped %d execute_action call(s) (wallet=%s)",
                    _original_count - len(collected_tool_calls), wallet,
                )

        chain_depth = 0
        for tc_name, tc_args in collected_tool_calls:
            # Inject chain depth into args so _validate_execute_action can enforce the cap.
            # Never reset chain_depth to 0 — prevents bypass by interspersing non-chained actions.
            try:
                _tc_args_parsed = json.loads(tc_args)
                if _tc_args_parsed.get("chain_from_previous"):
                    chain_depth += 1
                # Non-chained actions do NOT reset depth — cumulative cap enforced.
                _tc_args_parsed["_chain_depth"] = chain_depth
                tc_args_with_depth = json.dumps(_tc_args_parsed)
            except Exception:
                tc_args_with_depth = tc_args
            validated = validate_tool_call(tc_name, tc_args_with_depth, authenticated_wallet=wallet)
            if validated is None:
                _log.warning(
                    "tool_call_dropped tool=%s wallet=%s args=%.300s",
                    tc_name, wallet[:16] + "…", tc_args,
                )
                dropped_tool_calls.append((tc_name, tc_args))
                continue

            if isinstance(validated, ValidatedAction):
                d = validated.to_frontend_dict()
                # Second-pass sanity check — does this action plausibly answer
                # the user's last message? Catches semantic drift the schema
                # validator can't ("show price" → execute_action transfer).
                # Best-effort: any failure of the validator itself fails open,
                # because a model hiccup here must not block a legitimate user.
                try:
                    from app.services.output_validator import validate_tool_call as _sanity_check

                    _verdict = await _sanity_check(user_content, tc_name, tc_args)
                    if _verdict.should_block:
                        _log.warning(
                            "tool_call_blocked_by_validator tool=%s reason=%s wallet=%s",
                            tc_name, _verdict.reason, wallet[:16] + "…",
                        )
                        dropped_tool_calls.append((tc_name, tc_args))
                        continue
                    if not _verdict.ok and _verdict.severity == "warn":
                        _log.info(
                            "tool_call_validator_warn tool=%s reason=%s wallet=%s",
                            tc_name, _verdict.reason, wallet[:16] + "…",
                        )
                except Exception:
                    _log.debug("sanity-check skipped on error", exc_info=True)
                validated_actions.append(d)
                # Structured security audit log — every proposed on-chain action is recorded.
                # Params are included but amount redacted to avoid leaking trading strategy.
                _log.info(
                    "llm_action_proposed action_type=%s chain=%s "
                    "unverified_dest=%s wallet=%s session=%s",
                    validated.type.value,
                    validated.chain_from_previous,
                    validated.warn_unverified_destination,
                    wallet[:16] + "…",
                    session_id,
                )
                yield f"data: {json.dumps({'action': d})}\n\n"

            elif isinstance(validated, ValidatedQuery):
                # Market data queries are fetched; results fed back to LLM for interpretation.
                if validated.type.value in market_data.MARKET_DATA_TYPES:
                    params_dict = dict(validated.params or {})
                    # Inject connected wallet for queries that scan the user's own wallet.
                    _wallet_auto_inject = {"scan_empty_accounts", "my_stake_accounts"}
                    if validated.type.value in _wallet_auto_inject and "wallet" not in params_dict:
                        params_dict["wallet"] = wallet
                    # Some market-data query types ALSO render as an interactive
                    # QueryCard mini-app on the frontend (paginated pool lists,
                    # etc.). For those, emit the SSE `{query}` event in addition
                    # to the prose follow-up — the card fetches the data itself,
                    # and the LLM still writes a short intro paragraph.
                    if validated.type.value in QUERY_CARD_RENDER_TYPES:
                        d = validated.to_frontend_dict()
                        validated_queries.append(d)
                        yield f"data: {json.dumps({'query': d})}\n\n"
                    _log.info(
                        "market_data_query query_type=%s wallet=%s session=%s",
                        validated.type.value, wallet[:16] + "…", session_id,
                    )
                    try:
                        raw = await market_data.call(validated.type.value, params_dict, wallet=wallet)
                        market_data_results.append((validated.type.value, params_dict, raw))
                        # TEMP DEBUG
                        try:
                            with open("/tmp/oprai-debug.log", "a") as _df:
                                _summary = (
                                    f"keys={list(raw.keys())[:6]}" if isinstance(raw, dict)
                                    else f"len={len(raw)}" if isinstance(raw, list)
                                    else f"type={type(raw).__name__}"
                                )
                                _df.write(f"  MARKET_DATA_OK type={validated.type.value} "
                                          f"params={params_dict} → {_summary}\n")
                        except Exception:
                            pass
                    except Exception as exc:
                        _log.warning("market_data_error type=%s err=%s", validated.type.value, exc)
                        market_data_results.append(
                            (validated.type.value, params_dict, {"error": str(exc)})
                        )
                        # TEMP DEBUG
                        try:
                            with open("/tmp/oprai-debug.log", "a") as _df:
                                _df.write(f"  MARKET_DATA_ERR type={validated.type.value} "
                                          f"params={params_dict} err={type(exc).__name__}: {str(exc)[:300]}\n")
                        except Exception:
                            pass
                    continue

                d = validated.to_frontend_dict()
                validated_queries.append(d)
                _log.info(
                    "llm_query_proposed query_type=%s wallet=%s session=%s",
                    validated.type.value, wallet[:16] + "…", session_id,
                )
                yield f"data: {json.dumps({'query': d})}\n\n"

            elif isinstance(validated, ValidatedClarify):
                d = validated.to_frontend_dict()
                validated_clarifications.append(d)
                yield f"data: {json.dumps({'clarify': d})}\n\n"

        # ── 7b. Follow-up LLM interpretation of market data tool results ──
        # Split results into two categories:
        #   • jup_token_search — token resolution needed before an action; requires a
        #     follow-up call WITH tools so the LLM can call execute_action.
        #   • everything else — pure data queries; text-only follow-up is fine.
        if market_data_results:
            token_resolution = [(n, p, r) for n, p, r in market_data_results if n == "jup_token_search"]
            text_only = [(n, p, r) for n, p, r in market_data_results if n != "jup_token_search"]

            # ── 7b-i. Token resolution: re-run WITH tools ────────────────────
            if token_resolution:
                token_text = "\n\n".join(
                    f"### Token Search: {params.get('query', '')}\n"
                    f"Result:\n```json\n{json.dumps(result, ensure_ascii=False, indent=2, default=str)[:2000]}\n```"
                    for _, params, result in token_resolution
                )
                token_followup_msgs = list(model_messages) + [
                    {
                        "role": "system",
                        "content": (
                            "Token resolution is complete. The search result is below.\n"
                            "Now decide what to do next based on the user's ORIGINAL message:\n\n"
                            "1. **Simple action that just needed a mint** (swap / transfer / send / "
                            "burn / launch_token / pumpfun_buy / lend / borrow with a single token "
                            "symbol the user mentioned):\n"
                            "   → call execute_action with the EXACT action type and parameters the "
                            "user originally requested, substituting the resolved mint address for "
                            "the unknown token symbol. Keep ALL other params (amount, to, etc.) "
                            "unchanged.\n"
                            "   slippageBps is in basis points: multiply the user's % by 100. "
                            "Examples: 0.5% → 50, 1% → 100, 50% → 5000.\n\n"
                            "2. **Multi-step request** (the user asked for a list of pools, "
                            "options, positions, or any data scoped to a specific protocol — "
                            "DLMM pools, Meteora pairs, Kamino markets, Orca whirlpools, "
                            "Raydium pools — and THEN wants to act on a chosen row):\n"
                            "   → do NOT call execute_action yet. Call the protocol's "
                            "query_onchain to fetch the data the user actually wants "
                            "(e.g. meteora_dlmm_get_pairs for DLMM pools, raydium_get_pools "
                            "for Raydium, orca_get_pools for Orca, kamino_market_reserves for "
                            "Kamino). The next system turn will let you present options or pick "
                            "a row to execute.\n\n"
                            "3. **Pure data question** about the resolved token (price, holders, "
                            "security, history): call the relevant query_onchain (birdeye_*, "
                            "helius_*, etc.) — never narrate that you are doing it.\n\n"
                            "Respond with the function call only — NO pre-tool text, NO "
                            "narration like 'let me check' or 'I'll fetch'.\n\n"
                            f"{token_text}"
                        ),
                    }
                ]
                try:
                    _followup_tool_calls: list[tuple[str, str]] = []
                    # Buffer pre-tool text. If the model decides to call another
                    # tool after a token search, any narration ("I see the
                    # previous query returned X, let me fetch Y", or worse, a
                    # quoted "0 token(s) found for ...") is preamble that
                    # should be discarded — the next tool result will be
                    # presented properly by the text-only followup branch
                    # below. If no tool call follows, this IS the final
                    # answer, so flush the buffer at end of stream.
                    _fu_buffer: list[str] = []
                    async for event in llm.astream_with_tools(token_followup_msgs, tools):
                        kind = event[0]
                        if kind == "tool_call":
                            _, tc_name, tc_args = event
                            _followup_tool_calls.append((tc_name, tc_args))
                            _fu_buffer.clear()
                        else:
                            _, chunk = event
                            filtered = _strip_tool_blob_lines(chunk)
                            if filtered:
                                _fu_buffer.append(filtered)
                    if not _followup_tool_calls and _fu_buffer:
                        for _txt in _fu_buffer:
                            collected_text_chunks.append(_txt)
                            yield f"data: {json.dumps({'delta': _txt})}\n\n"
                    # The followup may emit:
                    #   • execute_action — the original action with the resolved mint
                    #   • query_onchain  — when the user's ORIGINAL request was multi-
                    #     step (e.g. "show DLMM pools for jupSOL/SOL"); the resolved
                    #     mint is just a parameter the next read needs, not the answer
                    #   • request_clarification — when picking from multiple options
                    for tc_name, tc_args in _followup_tool_calls:
                        if tc_name == "execute_action":
                            try:
                                _p = json.loads(tc_args)
                                _p["_chain_depth"] = 0
                                tc_args = json.dumps(_p)
                            except Exception:
                                pass
                            _v = validate_tool_call(tc_name, tc_args, authenticated_wallet=wallet)
                            if isinstance(_v, ValidatedAction):
                                _d = _v.to_frontend_dict()
                                validated_actions.append(_d)
                                _log.info(
                                    "token_resolution_action action_type=%s wallet=%s",
                                    _d.get("type"), wallet[:16] + "…",
                                )
                                yield f"data: {json.dumps({'action': _d})}\n\n"
                        elif tc_name == "query_onchain":
                            # Stage-2 read after token resolution — fetch the data
                            # the user actually wanted (pool list, market reserves,
                            # etc.) and feed it through the standard text_only
                            # followup branch below.
                            _v = validate_tool_call(tc_name, tc_args, authenticated_wallet=wallet)
                            if isinstance(_v, ValidatedQuery) and _v.type.value in market_data.MARKET_DATA_TYPES:
                                _params = dict(_v.params or {})
                                _wallet_auto_inject = {"scan_empty_accounts", "my_stake_accounts"}
                                if _v.type.value in _wallet_auto_inject and "wallet" not in _params:
                                    _params["wallet"] = wallet
                                try:
                                    _raw = await market_data.call(_v.type.value, _params, wallet=wallet)
                                    market_data_results.append((_v.type.value, _params, _raw))
                                    _log.info(
                                        "token_resolution_chained_query type=%s wallet=%s",
                                        _v.type.value, wallet[:16] + "…",
                                    )
                                except Exception as exc:
                                    _log.warning(
                                        "token_resolution_chained_query_error type=%s err=%s",
                                        _v.type.value, exc,
                                    )
                                    market_data_results.append(
                                        (_v.type.value, _params, {"error": str(exc)})
                                    )
                        elif tc_name == "request_clarification":
                            _v = validate_tool_call(tc_name, tc_args, authenticated_wallet=wallet)
                            if isinstance(_v, ValidatedClarify):
                                _d = _v.to_frontend_dict()
                                validated_clarifications.append(_d)
                                yield f"data: {json.dumps({'clarify': _d})}\n\n"
                except Exception as exc:
                    _log.warning("token_resolution_followup_error err=%s", exc)
                else:
                    # If the chained query produced results, recompute the split
                    # so the text_only branch below sees the new (non-token) data.
                    text_only = [(n, p, r) for n, p, r in market_data_results if n != "jup_token_search"]

            # ── 7b-ii. Tool-enabled follow-up for general market data ────────
            # The follow-up was once text-only — model could only describe the
            # fetched data in prose. That broke multi-step flows like "list pools
            # and let me pick one to add liquidity": the model summarised pools
            # in text, but the user got no clickable picker (request_clarification)
            # and no auto-execute when the user's wording already disambiguated
            # ("highest TVL" / "cheapest").
            #
            # Now the follow-up gets a SUBSET of tools — request_clarification +
            # execute_action — so the model can:
            #   • emit request_clarification with concrete options when the user
            #     must choose from the data (e.g. multiple matching pools);
            #   • emit execute_action directly when the user's message ALREADY
            #     specifies which row to act on (e.g. "use the highest TVL pool",
            #     "the cheapest one", "the first one");
            #   • otherwise stream a polished markdown summary as plain text.
            #
            # query_onchain is intentionally excluded from this subset: the
            # follow-up has just received query results, calling another query
            # would loop. Any new query the model genuinely needs comes on the
            # NEXT turn after the user replies.
            if text_only:
                text_only_text = "\n\n".join(
                    f"### Tool: {name}\n"
                    f"Parameters: {json.dumps(params, ensure_ascii=False)}\n"
                    f"Result:\n```json\n{json.dumps(result, ensure_ascii=False, indent=2, default=str)[:4000]}\n```"
                    for name, params, result in text_only
                )
                # If ANY result is a QueryCard-rendered type, the frontend is
                # already showing the full interactive table — the model must
                # NOT re-list rows in prose. We append a short directive to the
                # followup system prompt so it overrides the default
                # "bullet-per-item" formatting rule for this turn.
                _query_card_in_results = any(
                    name in QUERY_CARD_RENDER_TYPES for name, _, _ in text_only
                )
                _query_card_directive = (
                    "\n\n## QueryCard rendering active — IMPORTANT\n"
                    "One or more tool results below render as an INTERACTIVE QueryCard "
                    "on the frontend. The user ALREADY sees a paginated table with "
                    "every row, search, sort controls, copy-pool-address buttons, and "
                    "a per-row 'Use' button that opens an action card pre-filled with "
                    "that row's pool/mints. So:\n"
                    "- DO NOT call `request_clarification`. The QueryCard IS the picker. "
                    "Calling clarify on top duplicates the UI and confuses the user.\n"
                    "- DO call `execute_action` when the user's wording already points "
                    "at ONE specific row WITHOUT ambiguity (superlative like 'highest "
                    "TVL', 'best APY', 'cheapest fee', 'most liquid', 'first one'; or "
                    "an exact pool address pasted by the user). Fill ALL required "
                    "params from that row.\n"
                    "- OTHERWISE write ONE short intro sentence (e.g. 'Here are the "
                    "JupSOL DLMM pools — pick one or tell me which to use.'). "
                    "A second sentence is allowed only if it adds a WHOLE-LIST "
                    "observation (e.g. 'TVL is concentrated in the top two pairs'). "
                    "Never enumerate or name specific rows — the table shows them.\n"
                    "- Tools that render as QueryCards in this turn: "
                    f"{sorted(n for n, _, _ in text_only if n in QUERY_CARD_RENDER_TYPES)}.\n"
                ) if _query_card_in_results else ""
                # Followup interpretation: do NOT include `model_messages`
                # (the full tool-dispatch history). That history contains the
                # original tool schema and confuses the model into writing
                # additional fake tool calls when the fetched data is partial.
                # Pass only the minimal context: the user's original question
                # and the data we already have.
                _last_user_msg = next(
                    (m for m in reversed(model_messages) if m.get("role") == "user"),
                    {"role": "user", "content": ""},
                )
                # Filter the original tool list down to the safe followup subset.
                # `tools` was built earlier in step 6 — same provider-shaped schema.
                # When a QueryCard is already showing in this turn, drop
                # `request_clarification` entirely — the table IS the picker, and
                # asking the model to pick a 2-option subset on top of it would
                # duplicate the UI and confuse the user. Structural removal here
                # makes the prompt directive impossible to disobey.
                _followup_tool_names = (
                    {"execute_action"}
                    if _query_card_in_results
                    else {"request_clarification", "execute_action"}
                )
                followup_tools = [
                    t for t in tools
                    if t.get("function", {}).get("name") in _followup_tool_names
                ]
                # Per-turn language anchor carried into the text-only
                # followup. Echoes the user's message verbatim so the model
                # has a direct anchor for the response language — works for
                # any language, no detection needed.
                _user_excerpt_fu = (user_content or "").strip()[:400]
                _lang_followup_prefix = (
                    "LANGUAGE LOCK — match the user's language exactly. "
                    f"The user's message: <<<{_user_excerpt_fu}>>>. "
                    "Your ENTIRE response (every word, headings, bullets, "
                    "intros, takeaways) must be in the SAME language. "
                    "Tool result data may contain English labels — translate "
                    "them; never quote verbatim in another language.\n\n"
                ) if _user_excerpt_fu else ""
                followup_messages = [
                    {
                        "role": "system",
                        "content": (
                            f"{_lang_followup_prefix}"
                            "You are presenting already-fetched on-chain data to the user. "
                            "Respond in the SAME LANGUAGE the user wrote in — every word.\n\n"
                            "## Tool decision (CRITICAL — do this BEFORE writing text)\n"
                            "Examine the fetched data + the user's original message:\n"
                            "1. **execute_action** — Call this immediately when the user's "
                            "wording already picks ONE row from the data without ambiguity. "
                            "Examples (any language): 'highest TVL pool', 'cheapest', "
                            "'biggest', 'the first one', 'best APY', 'most liquid'. Pick the "
                            "matching row, fill ALL required params from it (pool address, "
                            "mints, fees, etc.), and emit the action. Do NOT also write "
                            "text — the action card is the answer.\n"
                            "2. **request_clarification** — Call this when the user said "
                            "'list / show / let me pick' or asked which to use. Pass 2-5 "
                            "concrete options derived from the data, each with the action "
                            "they would trigger. Do NOT also write text.\n"
                            "3. **Plain text only** — When the user just wants the data "
                            "displayed (balance, prices, history, analytics) and there is "
                            "no follow-up action to take, stream a polished markdown "
                            "summary using the format below. No tool call.\n\n"
                            "## Hard rules for the text path\n"
                            "- NO PREAMBLE. Never start with 'I'll check', 'Let me look up', "
                            "'Sorgu çalıştıracağım', 'I'm going to fetch', or any narration of "
                            "what you're about to do. Open with the answer itself.\n"
                            "- NEVER mention tool names, service names, API names, or data "
                            "sources (birdeye, helius, dexscreener, jupiter API, query_onchain, "
                            "raydium_pools, dex_token, etc.). Speak as if the knowledge is yours.\n"
                            "- NO raw JSON, function calls, `{...}` blocks, or tool-call syntax "
                            "in the visible text. If you call a tool, do NOT also describe it.\n"
                            "- If the data is insufficient, say so plainly in one sentence in "
                            "the user's language and stop. Never invent numbers or pool names.\n\n"
                            "## Format (text path)\n"
                            "Use Markdown. Aim for a polished, scannable layout:\n"
                            "- Lead with the headline answer (one sentence or a bold total).\n"
                            "- Use a short bullet list for protocol-by-protocol or item-by-item "
                            "breakdowns. One line per item.\n"
                            "- Bold the key number in each bullet (e.g. `**$1,039,916.22**`).\n"
                            "- Close with a one-sentence takeaway only when it adds insight "
                            "(percentage share, where the bulk sits, anomaly, etc.). Skip it "
                            "for trivial answers.\n"
                            "- Numbers: `$1,234.56`, percentages `2.3%`, changes `+5.2%` / `-1.8%`.\n\n"
                            "## Example shape (text path — do not copy verbatim)\n"
                            "**Total liquidity: $1,870,314.27**\n\n"
                            "- **Orca** — $1,039,916.22 across 7 pairs · 24h vol $675,629.34\n"
                            "- **Meteora** — $821,714.66 across 16 pairs · 24h vol $170,878.40\n"
                            "- **Raydium** — $8,683.39 across 5 pairs · 24h vol $322.91\n\n"
                            "Most of the liquidity sits on **Orca** (≈55%) and **Meteora** (≈44%).\n\n"
                            f"{_query_card_directive}"
                            "## FETCHED DATA\n"
                            f"{text_only_text}"
                        ),
                    },
                    _last_user_msg,
                ]
                try:
                    _followup_tool_calls: list[tuple[str, str]] = []
                    # Same pre-tool buffering as the token-resolution followup:
                    # if the model decides to clarify or execute, drop any
                    # narration that came before the tool call.
                    _fu_buffer: list[str] = []
                    async for event in llm.astream_with_tools(followup_messages, followup_tools):
                        kind = event[0]
                        if kind == "tool_call":
                            _, tc_name, tc_args = event
                            _followup_tool_calls.append((tc_name, tc_args))
                            _fu_buffer.clear()
                        else:
                            _, chunk = event
                            filtered = _clean_delta(chunk)
                            if filtered:
                                _fu_buffer.append(filtered)
                    if not _followup_tool_calls and _fu_buffer:
                        for _txt in _fu_buffer:
                            collected_text_chunks.append(_txt)
                            yield f"data: {json.dumps({'delta': _txt})}\n\n"
                    # ── Validate + stream tool calls emitted in the followup ──
                    # Same pipeline as the primary tool-call processing earlier
                    # in the request: `validate_tool_call` enforces param shape
                    # and Solana address format before we let the frontend act.
                    for tc_name, tc_args in _followup_tool_calls:
                        if tc_name not in _followup_tool_names:
                            continue
                        # Reset chain depth so the action runs fresh on the
                        # frontend; the followup is a new logical step.
                        if tc_name == "execute_action":
                            try:
                                _p = json.loads(tc_args)
                                _p["_chain_depth"] = 0
                                tc_args = json.dumps(_p)
                            except Exception:
                                pass
                        _v = validate_tool_call(tc_name, tc_args, authenticated_wallet=wallet)
                        if isinstance(_v, ValidatedAction):
                            _d = _v.to_frontend_dict()
                            # Second-pass semantic check — same gate as primary path.
                            try:
                                from app.services.output_validator import validate_tool_call as _sanity_check
                                _verdict = await _sanity_check(user_content, tc_name, tc_args)
                                if _verdict.should_block:
                                    _log.warning(
                                        "market_data_followup_blocked tool=%s reason=%s",
                                        tc_name, _verdict.reason,
                                    )
                                    dropped_tool_calls.append((tc_name, tc_args))
                                    continue
                            except Exception:
                                pass
                            validated_actions.append(_d)
                            _log.info(
                                "market_data_followup_action action_type=%s wallet=%s",
                                _d.get("type"), wallet[:16] + "…",
                            )
                            yield f"data: {json.dumps({'action': _d})}\n\n"
                        elif isinstance(_v, ValidatedClarify):
                            _d = _v.to_frontend_dict()
                            validated_clarifications.append(_d)
                            _log.info(
                                "market_data_followup_clarify category=%s options=%d wallet=%s",
                                _d.get("category"), len(_d.get("options") or []), wallet[:16] + "…",
                            )
                            yield f"data: {json.dumps({'clarify': _d})}\n\n"
                except Exception as exc:
                    _log.warning("market_data_followup_error err=%s", exc)
                    err_msg = f"\n[Interpretation error: {exc}]\n"
                    collected_text_chunks.append(err_msg)
                    yield f"data: {json.dumps({'delta': err_msg})}\n\n"

        # ── 8. Build full response text ───────────────────────────────────
        full_response = "".join(collected_text_chunks)
        # Strip any <think>…</think> wrapper from persisted text
        clean_response = re.sub(
            r"<think>.*?</think>\s*", "", full_response, flags=re.DOTALL
        ).strip()
        if clean_response:
            full_response = clean_response

        # Fallback for "everything got dropped" turns: the model produced
        # tool calls that all failed validation (bad addresses, wrong
        # action_type, missing required params, etc.) AND wrote no text.
        # Without this branch the assistant message is empty, the SSE
        # stream finishes silently, and the user just sees a blank reply.
        # Emit a plain delta so the bubble has content + an actionable hint.
        if (
            not full_response
            and not validated_actions
            and not validated_queries
            and not validated_clarifications
            and dropped_tool_calls
        ):
            _bad_names = ", ".join(sorted({n for n, _ in dropped_tool_calls}))
            fallback = (
                "I tried to act on that but couldn't validate the parameters "
                f"(rejected tool call(s): {_bad_names}). Please try rephrasing "
                "with the specific token amount and protocol, or pick an option "
                "from the previous list."
            )
            collected_text_chunks.append(fallback)
            full_response = fallback
            yield f"data: {json.dumps({'delta': fallback})}\n\n"
            _log.warning(
                "assistant_response_empty_after_validation wallet=%s dropped=%s",
                wallet[:16] + "…", _bad_names,
            )

        # ── 8b. Fallback: extract actions the model emitted as JSON text ──
        # Some model versions (gpt-5.4-nano) fail to call function tools and
        # instead write the function arguments as raw JSON in the text output.
        # Detect and validate them here so the action card still appears.
        if not validated_actions and not validated_clarifications:
            _decoder = json.JSONDecoder()
            _pos = 0
            while _pos < len(full_response):
                if full_response[_pos] == '{':
                    try:
                        _obj, _end = _decoder.raw_decode(full_response, _pos)
                        if isinstance(_obj, dict) and "action_type" in _obj:
                            _args = json.dumps({
                                "action_type": _obj.get("action_type", ""),
                                "params": _obj.get("params", {}),
                                "chain_from_previous": _obj.get("chain_from_previous", False),
                                "_chain_depth": 0,
                            })
                            _v = validate_tool_call("execute_action", _args, authenticated_wallet=wallet)
                            if _v and isinstance(_v, ValidatedAction):
                                _d = _v.to_frontend_dict()
                                validated_actions.append(_d)
                                _log.info(
                                    "json_fallback_action action_type=%s wallet=%s",
                                    _obj.get("action_type"), wallet[:16] + "…",
                                )
                                yield f"data: {json.dumps({'action': _d})}\n\n"
                        _pos = _end
                        continue
                    except (json.JSONDecodeError, Exception):
                        pass
                _pos += 1

        # ── 9. Fire-and-forget: store to long-term memory ────────────────
        if validated_actions or validated_queries:
            try:
                from app.services.memory_client import store_memory
                mem_type = "decision" if validated_actions else "meta"
                memory_summary = (
                    f"User: {user_content[:200]}\n"
                    f"Assistant actions: {[a['type'] for a in validated_actions]}"
                )
                await store_memory(
                    wallet=wallet,
                    memory_type=mem_type,
                    summary=memory_summary,
                    extra={"session_id": session_id},
                )
            except Exception:
                _log.debug("Memory store skipped", exc_info=True)

        # ── 10. Persist assistant message with validated intent metadata ──
        assistant_metadata: dict = {}
        if validated_actions:
            assistant_metadata["actions"] = validated_actions
        if validated_queries:
            assistant_metadata["queries"] = validated_queries
        if validated_clarifications:
            assistant_metadata["clarifications"] = validated_clarifications

        # Persist a slim summary of any QueryCard-rendered data the user just
        # saw, so the NEXT turn's `build_llm_context` can surface it to the
        # model. Without this, follow-ups like "the highest TVL one" have no
        # anchor — the model only sees its own prose intro in chat history.
        # Kept tiny (≤5 rows, key fields only) to avoid bloating context.
        _card_results = [
            (name, params, result)
            for name, params, result in market_data_results
            if name in QUERY_CARD_RENDER_TYPES
            and isinstance(result, (dict, list))
        ]
        # TEMP DEBUG: trace every step of the card-state persistence so we
        # can tell which one drops it on the floor.
        try:
            with open("/tmp/oprai-debug.log", "a") as _df:
                _df.write(
                    f"[CARD_WRITE] session={session_id[:8]} "
                    f"market_data_results_count={len(market_data_results)} "
                    f"market_data_types={[n for n,_,_ in market_data_results]} "
                    f"_card_results_count={len(_card_results)}\n"
                )
        except Exception:
            pass
        if _card_results:
            summaries = []
            for name, params, result in _card_results:
                summary = _summarize_query_card_payload(name, params, result)
                if summary:
                    summaries.append(summary)
            try:
                with open("/tmp/oprai-debug.log", "a") as _df:
                    _df.write(
                        f"[CARD_WRITE] summaries_count={len(summaries)} "
                        f"types={[s.get('type') for s in summaries]}\n"
                    )
            except Exception:
                pass
            if summaries:
                assistant_metadata["query_card_results"] = summaries
                # Also mirror to Redis so the data survives the 10-message
                # block summarizer that strips per-message metadata.
                try:
                    from app.services.cache import get_cache_service
                    _cache = await get_cache_service()
                    _ok = await _cache.set("session:card_state", summaries, session_id, ttl=1800)
                    try:
                        with open("/tmp/oprai-debug.log", "a") as _df:
                            _df.write(
                                f"[CARD_WRITE] redis_set ok={_ok} "
                                f"key=oprai:chat:card:{session_id[:8]}…\n"
                            )
                    except Exception:
                        pass
                except Exception as _exc:
                    try:
                        with open("/tmp/oprai-debug.log", "a") as _df:
                            _df.write(f"[CARD_WRITE] redis_set EXCEPTION: {_exc!r}\n")
                    except Exception:
                        pass
                    _log.debug("card_state cache write failed: %s", _exc)

        assistant_msg = ChatMessage(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            wallet_address=wallet,
            role="assistant",
            content=full_response,
            metadata_=assistant_metadata if assistant_metadata else None,
        )
        db.add(assistant_msg)

        await _increment_message_count(db, session_id)
        await db.execute(
            update(ChatSession)
            .where(ChatSession.id == uuid.UUID(session_id))
            .values(updated_at=func.now())
        )
        # Commit assistant message immediately — client may disconnect before
        # get_session's post-yield commit runs (title generation, [DONE] yield, etc.)
        await db.commit()

        # Update the session_state snapshot so the next turn sees the latest
        # intent / candidates / pending decision. Best-effort: a model hiccup
        # here must not break the user-facing reply, so we swallow errors and
        # let the next turn re-derive from raw history.
        try:
            from app.services.session_state import update_session_state

            ss_row = await db.execute(
                select(ChatSession.session_state).where(ChatSession.id == uuid.UUID(session_id))
            )
            prev_state = ss_row.scalar_one_or_none() or {}
            await update_session_state(
                db,
                session_id=session_id,
                previous_state=prev_state,
                user_text=user_content,
                assistant_text=full_response,
            )
            await db.commit()
        except Exception:
            _log.debug("session_state update skipped", exc_info=True)
            await db.rollback()

        # Extract durable preferences (preferred wallet, default slippage,
        # risk tolerance, etc.) so they persist across sessions. Mirrors the
        # ChatGPT "Memory" feature; see user_facts.py. Failures here are
        # silent — memory is a nice-to-have, never a blocker.
        try:
            from app.services.user_facts import extract_and_upsert as _extract_facts

            await _extract_facts(
                db,
                wallet=wallet,
                user_text=user_content,
                assistant_text=full_response,
            )
            await db.commit()
        except Exception:
            _log.debug("user_facts extraction skipped", exc_info=True)
            await db.rollback()

        # ── 11. Emit message ID and optional title ────────────────────────
        yield f"data: {json.dumps({'messageId': str(assistant_msg.id)})}\n\n"

        if is_first_message:
            try:
                title = await generate_title(user_content)
                await session_svc.update_title(db, wallet, session_id, title)
                yield f"data: {json.dumps({'title': title})}\n\n"
            except Exception:
                _log.warning("Title generation failed in stream", exc_info=True)

        yield "data: [DONE]\n\n"

    except Exception as exc:
        exc_str = str(exc).lower()
        if any(k in exc_str for k in ("rate_limit", "rate limit", "429", "quota", "too many requests")):
            err_msg = "Rate limit reached. Please wait a moment before trying again."
            error_type = "rate_limit"
        elif any(k in exc_str for k in ("timeout", "timed out", "read timeout", "connect timeout")):
            err_msg = "The response timed out. Please try again."
            error_type = "timeout"
        elif any(k in exc_str for k in ("authentication", "api key", "invalid_api_key")):
            err_msg = "AI service configuration error. Please contact support."
            error_type = "auth"
        elif any(k in exc_str for k in ("connection", "network", "unreachable", "refused")):
            err_msg = "Network error reaching the AI service. Please try again."
            error_type = "network"
        elif any(k in exc_str for k in ("context_length", "maximum context", "token limit")):
            err_msg = "The conversation is too long. Please start a new chat."
            error_type = "context_limit"
        else:
            err_msg = "I can't respond right now. Please try again later."
            error_type = "unknown"

        _log.error("LLM stream error [%s]: %s", error_type, exc, exc_info=True)

        error_record = ChatMessage(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            wallet_address=wallet,
            role="system",
            content=err_msg,
        )
        db.add(error_record)
        await db.flush()

        yield f"data: {json.dumps({'error': err_msg, 'errorType': error_type})}\n\n"
        yield "data: [DONE]\n\n"

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
    """Return messages for a session, ordered chronologically."""
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
                )
            },
        }
        for m in messages
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
    """Merge `patch` into message metadata_. Returns False if not found."""
    stmt = select(ChatMessage).where(
        ChatMessage.id == uuid.UUID(message_id),
        ChatMessage.session_id == uuid.UUID(session_id),
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


# ── Main streaming function ───────────────────────────────────────────────────

async def stream_chat_response(
    db: AsyncSession,
    session_id: str,
    wallet: str,
    user_content: str,
    is_first_message: bool = False,
    attachments: list[dict] | None = None,
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
        OPRAI_TOOLS,
        ValidatedAction,
        ValidatedQuery,
        ValidatedClarify,
        validate_tool_call,
    )

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

    # Build metadata with attachments
    msg_metadata: dict = {}
    if attachments:
        msg_metadata["attachments"] = attachments

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

    # ── 3. Increment message_count ───────────────────────────────────────
    new_count = await _increment_message_count(db, session_id)

    # ── 4. Summarise previous block if needed ────────────────────────────
    await maybe_create_summary(db, session_id, wallet, new_count)

    # ── 5. Build LLM context ─────────────────────────────────────────────
    # Pass sanitised content as the current user turn so injection patterns
    # never reach the model.
    model_messages = await build_llm_context(
        db, session_id, wallet,
        current_attachments=attachments,
        sanitised_last_user_content=safe_content,
    )

    # ── 6. Stream from LLM with function calling ─────────────────────────
    llm = LLMService()
    collected_text_chunks: list[str] = []
    collected_tool_calls: list[tuple[str, str]] = []   # (name, args_json)

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

                async for event in llm.astream_with_tools(model_messages, OPRAI_TOOLS):
                    kind = event[0]

                    if kind == "tool_call":
                        # Accumulate tool calls — validated and emitted after text stream
                        _, tc_name, tc_args = event
                        _log.debug("raw_tool_call name=%s args=%s", tc_name, tc_args)
                        collected_tool_calls.append((tc_name, tc_args))
                        continue

                    # kind == "text"
                    _, chunk = event
                    collected_text_chunks.append(chunk)

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
                                yield f"data: {json.dumps({'delta': thinking_buffer})}\n\n"
                                thinking_buffer = ""
                            continue

                        if _CLOSE_TAG in thinking_buffer:
                            before, after = thinking_buffer.split(_CLOSE_TAG, 1)
                            if before.strip():
                                yield f"data: {json.dumps({'thinking': before})}\n\n"
                            in_thinking = False
                            remainder = after.lstrip("\n")
                            if remainder:
                                yield f"data: {json.dumps({'delta': remainder})}\n\n"
                            thinking_buffer = ""
                        else:
                            safe_len = len(thinking_buffer) - _TAIL_HOLD
                            if safe_len > 0:
                                yield f"data: {json.dumps({'thinking': thinking_buffer[:safe_len]})}\n\n"
                                thinking_buffer = thinking_buffer[safe_len:]
                    else:
                        yield f"data: {json.dumps({'delta': chunk})}\n\n"

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

        if len(collected_tool_calls) > _MAX_TOOL_CALLS_PER_RESPONSE:
            _log.warning(
                "LLM emitted %d tool calls (max %d) — truncating (wallet=%s)",
                len(collected_tool_calls), _MAX_TOOL_CALLS_PER_RESPONSE, wallet,
            )
            collected_tool_calls = collected_tool_calls[:_MAX_TOOL_CALLS_PER_RESPONSE]

        chain_depth = 0
        for tc_name, tc_args in collected_tool_calls:
            # Inject chain depth into args so _validate_execute_action can enforce the cap.
            # We track depth by counting consecutive chain_from_previous=True actions.
            try:
                _tc_args_parsed = json.loads(tc_args)
                if _tc_args_parsed.get("chain_from_previous"):
                    chain_depth += 1
                else:
                    chain_depth = 0
                _tc_args_parsed["_chain_depth"] = chain_depth
                tc_args_with_depth = json.dumps(_tc_args_parsed)
            except Exception:
                tc_args_with_depth = tc_args
            validated = validate_tool_call(tc_name, tc_args_with_depth, authenticated_wallet=wallet)
            if validated is None:
                _log.warning(
                    "tool_call_dropped tool=%s wallet=%s",
                    tc_name, wallet[:16] + "…",
                )
                continue

            if isinstance(validated, ValidatedAction):
                d = validated.to_frontend_dict()
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

        # ── 8. Build full response text ───────────────────────────────────
        full_response = "".join(collected_text_chunks)
        # Strip any <think>…</think> wrapper from persisted text
        clean_response = re.sub(
            r"<think>.*?</think>\s*", "", full_response, flags=re.DOTALL
        ).strip()
        if clean_response:
            full_response = clean_response

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
        await db.flush()

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

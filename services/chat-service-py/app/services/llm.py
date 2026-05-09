"""LLM orchestration — supports Chat Completions and OpenAI Responses API.

Reasoning models (gpt-5.4-nano, gpt-5-series, o-series) use the Responses API
(/v1/responses) with reasoning summaries, encrypted reasoning context, and
web search source attribution.

Chat Completions models (gpt-4o-mini, gpt-4o, etc.) use LangChain / direct SDK
with streaming function calling support.

Tool calling is supported for both paths:
- Chat Completions: standard function calling via stream deltas
- Responses API: flat tool format, parsed from function_call_arguments.done events
"""

import logging
import re
from collections.abc import AsyncGenerator
from typing import Literal

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# Models that use the Responses API (/v1/responses).
# These support reasoning summaries and encrypted reasoning context.
_RESPONSES_API_MODELS = frozenset({
    "gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4",
    "gpt-5-mini", "gpt-5",
    "o1", "o1-mini", "o1-preview",
    "o3", "o3-mini",
    "o4-mini",
})

# Responses API call parameters aligned with OpenAI spec.
_RESPONSES_TEXT_FORMAT = {"type": "text"}
_RESPONSES_INCLUDE = [
    "reasoning.encrypted_content",       # enables multi-turn reasoning continuity
    "web_search_call.action.sources",    # web search source attribution
]

# Yielded item types from astream_with_tools
type StreamEvent = tuple[Literal["text"], str] | tuple[Literal["tool_call"], str, str]


class LLMService:
    """Thin wrapper supporting OpenAI Chat Completions, OpenAI Responses, and Anthropic Messages.

    Provider is selected by `OPRAI_LLM_PROVIDER`:
      - "openai"    → OpenAI (Chat Completions or Responses API based on model)
      - "anthropic" → Claude Messages API
    """

    def __init__(self) -> None:
        self._provider = settings.OPRAI_LLM_PROVIDER.lower()

        if self._provider == "anthropic":
            if not settings.OPRAI_ANTHROPIC_API_KEY:
                raise RuntimeError("LLM provider=anthropic but OPRAI_ANTHROPIC_API_KEY is empty")
            self._model = settings.OPRAI_RESPONDER_MODEL_ANTHROPIC
            self._anthropic = AsyncAnthropic(api_key=settings.OPRAI_ANTHROPIC_API_KEY)
            # Unused for Anthropic but referenced by sibling methods — keep set
            # to None so accidental access fails loudly instead of silently.
            self._client = None
            self._llm = None
            self._use_responses_api = False
            logger.info("LLMService initialized provider=anthropic model=%s", self._model)
            return

        # Default: OpenAI
        if not settings.OPRAI_OPENAI_API_KEY:
            raise RuntimeError("LLM integration is not configured: OPRAI_OPENAI_API_KEY is empty")

        self._model = settings.OPRAI_RESPONDER_MODEL_OPENAI
        self._use_responses_api = self._model in _RESPONSES_API_MODELS
        self._client = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)
        self._anthropic = None

        if not self._use_responses_api:
            primary = ChatOpenAI(
                model=self._model,
                api_key=settings.OPRAI_OPENAI_API_KEY,  # type: ignore[arg-type]
                temperature=0.3,
                max_tokens=settings.OPRAI_GPT_MAX_TOKENS,
                streaming=True,
            )
            fallback_model = settings.OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI
            if fallback_model and fallback_model != self._model:
                fallback = ChatOpenAI(
                    model=fallback_model,
                    api_key=settings.OPRAI_OPENAI_API_KEY,  # type: ignore[arg-type]
                    temperature=0.3,
                    max_tokens=settings.OPRAI_GPT_MAX_TOKENS,
                    streaming=True,
                )
                self._llm = primary.with_fallbacks([fallback])
            else:
                self._llm = primary
        logger.info("LLMService initialized provider=openai model=%s responses_api=%s",
                    self._model, self._use_responses_api)

    async def astream(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        """Async-stream LLM token chunks (text only, no tool calling)."""
        if self._provider == "anthropic":
            async for chunk in self._astream_anthropic(messages):
                yield chunk
        elif self._use_responses_api:
            async for chunk in self._astream_responses(messages):
                yield chunk
        else:
            async for chunk in self._astream_chat(messages):
                yield chunk

    async def astream_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream with function calling.

        Yields tuples:
          ("text", content)               — a text delta chunk
          ("tool_call", name, args_json)  — a complete tool call

        Args:
            tool_choice: "auto" (default — model decides), "required" (model
                MUST call at least one tool), or "none" (model MUST answer
                with text). Used to override Haiku 4.5's documented tool-
                avoidance on action/query intents — see Anthropic issue
                anthropics/claude-code#10029. When tools is empty, the
                value is ignored and a plain text completion is requested.

        Supports OpenAI (Chat Completions + Responses API) and Anthropic Messages.
        """
        if self._provider == "anthropic":
            async for event in self._astream_anthropic_with_tools(messages, tools, tool_choice):
                yield event
            return
        if self._use_responses_api:
            async for event in self._astream_responses_with_tools(messages, tools, tool_choice):
                yield event
            return

        async for event in self._astream_chat_with_tools(messages, tools, tool_choice):
            yield event

    async def acomplete(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        """Non-streaming completion that returns the full response text."""
        if self._provider == "anthropic":
            return await self._acomplete_anthropic(messages)
        if self._use_responses_api:
            return await self._acomplete_responses(messages)
        lc_messages = _to_langchain(messages)
        response = await self._llm.ainvoke(lc_messages)
        return response.content if isinstance(response.content, str) else ""

    # ── Responses API (gpt-5.4-nano, gpt-5-series, o-series) ─────────────────

    @staticmethod
    def _convert_tools_to_responses_format(tools: list[dict]) -> list[dict]:
        """
        Convert Chat Completions tool format to Responses API flat format.

        Chat Completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        Responses API:   {"type": "function", "name": ..., "description": ..., "parameters": ...}
        """
        result = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                fn = tool["function"]
                result.append({
                    "type": "function",
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                })
            else:
                result.append(tool)
        return result

    def _build_responses_kwargs(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        tools: list[dict] | None = None,
    ) -> dict:
        """Build keyword arguments for openai.responses.create()."""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        instructions = "\n\n".join(m["content"] for m in system_msgs) or None

        responses_tools = self._convert_tools_to_responses_format(tools) if tools else []

        kwargs: dict = {
            "model": self._model,
            "input": [{"role": m["role"], "content": m["content"]} for m in non_system],
            "text": {
                "format": _RESPONSES_TEXT_FORMAT,
                "verbosity": "medium",
            },
            "reasoning": {
                "effort": settings.OPRAI_GPT_REASONING_EFFORT,
                "summary": "concise",
            },
            "tools": responses_tools,
            "store": True,
            "include": _RESPONSES_INCLUDE,
            "stream": stream,
        }
        # Set tool_choice explicitly when tools are provided. Without this, reasoning
        # models can leak Harmony-format tool-call syntax (e.g. `{"tool":"..."}to=...`)
        # into the text channel instead of emitting proper function_call items.
        if responses_tools:
            kwargs["tool_choice"] = "auto"
        if instructions:
            kwargs["instructions"] = instructions
        return kwargs

    async def _astream_responses(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        stream = await self._client.responses.create(
            **self._build_responses_kwargs(messages, stream=True)
        )

        reasoning_started = False
        reasoning_done = False
        # Buffer + sanitize text deltas so the same Harmony / tool-call leakage
        # filter that protects the tool-dispatch path also runs on no-tool calls
        # like the market_data_followup interpretation step. gpt-5.4-nano can
        # emit `{"name":"query_onchain",...}` and `to=query_onchain` markers
        # even on a pure prose call when the prior turn used tools.
        text_buffer = ""

        async for event in stream:
            if event.type == "response.reasoning_summary_text.delta":
                if not reasoning_started:
                    yield "<think>"
                    reasoning_started = True
                yield event.delta
            elif event.type == "response.output_text.delta":
                if reasoning_started and not reasoning_done:
                    yield "</think>"
                    reasoning_done = True
                text_buffer += event.delta
                clean, text_buffer = _strip_tool_call_leakage(text_buffer)
                if clean:
                    yield clean

        # Close thinking tag if model produced only reasoning (edge case)
        if reasoning_started and not reasoning_done:
            yield "</think>"

        # Flush remaining buffered text through one final sanitize pass.
        if text_buffer:
            tail, _ = _strip_tool_call_leakage(text_buffer, flush=True)
            if tail:
                yield tail

    async def _astream_responses_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream Responses API with tool calling support.

        Parses both text deltas and function_call events from the stream.
        Function names are tracked via output_item.added events, arguments
        are assembled from function_call_arguments.delta and yielded on .done.

        Tool-call JSON / Harmony channel markers that leak into the text channel
        are filtered out so they never reach the user.
        """
        kwargs = self._build_responses_kwargs(messages, stream=True, tools=tools)
        # Override the default "auto" baked in by _build_responses_kwargs when
        # the caller wants to force a tool call. "required" makes the model
        # emit at least one function_call item — used for action/query intents
        # so the model can't punt to a hallucinated text answer.
        if tools and tool_choice in ("required", "none"):
            kwargs["tool_choice"] = tool_choice
        stream = await self._client.responses.create(**kwargs)

        reasoning_started = False
        reasoning_done = False
        # Map item_id -> {"name": str, "arguments": str}
        fn_calls: dict[str, dict] = {}
        # Buffer text output so we can sanitize tool-call JSON before yielding.
        text_buffer = ""

        async for event in stream:
            etype = event.type

            # ── Reasoning summary ──
            if etype == "response.reasoning_summary_text.delta":
                if not reasoning_started:
                    yield ("text", "<think>")
                    reasoning_started = True
                yield ("text", event.delta)

            # ── Text output ──
            elif etype == "response.output_text.delta":
                if reasoning_started and not reasoning_done:
                    yield ("text", "</think>")
                    reasoning_done = True
                text_buffer += event.delta
                clean, text_buffer = _strip_tool_call_leakage(text_buffer)
                if clean:
                    yield ("text", clean)

            # ── Function call item started — capture the name ──
            elif etype == "response.output_item.added":
                item = getattr(event, "item", None)
                if item and getattr(item, "type", None) == "function_call":
                    fn_calls[item.id] = {"name": item.name, "arguments": ""}

            # ── Function call argument delta ──
            elif etype == "response.function_call_arguments.delta":
                item_id = getattr(event, "item_id", None)
                if item_id and item_id in fn_calls:
                    fn_calls[item_id]["arguments"] += event.delta

            # ── Function call complete ──
            elif etype == "response.function_call_arguments.done":
                item_id = getattr(event, "item_id", None)
                if item_id and item_id in fn_calls:
                    fn = fn_calls.pop(item_id)
                    # Use final arguments from the .done event (authoritative)
                    args = getattr(event, "arguments", fn["arguments"])
                    if fn["name"] and args:
                        yield ("tool_call", fn["name"], args)

        if reasoning_started and not reasoning_done:
            yield ("text", "</think>")

        # Flush any remaining buffered text (one final sanitize pass, no holdback this time).
        if text_buffer:
            tail, _ = _strip_tool_call_leakage(text_buffer, flush=True)
            if tail:
                yield ("text", tail)

    async def _acomplete_responses(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        response = await self._client.responses.create(
            **self._build_responses_kwargs(messages, stream=False)
        )

        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "output_text":
                        return content.text
        return ""

    # ── Chat Completions (gpt-4o-mini, gpt-4o, etc.) ──────────────────────

    async def _astream_chat(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        lc_messages = _to_langchain(messages)
        # Same leakage filter as _astream_responses — Chat Completions can
        # leak Harmony/tool-call patterns too when the prior turn used tools
        # (most often on the followup-interpretation step).
        text_buffer = ""
        async for chunk in self._llm.astream(lc_messages):
            text = chunk.content if isinstance(chunk.content, str) else ""
            if not text:
                continue
            text_buffer += text
            clean, text_buffer = _strip_tool_call_leakage(text_buffer)
            if clean:
                yield clean
        if text_buffer:
            tail, _ = _strip_tool_call_leakage(text_buffer, flush=True)
            if tail:
                yield tail

    async def _astream_chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream Chat Completions with function calling.

        Tool call argument deltas arrive incrementally and are assembled
        by index. Once the stream ends (finish_reason tool_calls | stop),
        we yield the assembled tool calls as ("tool_call", name, args_json).

        If the primary model fails, retries once with the fallback model.
        """
        fallback_model = settings.OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI
        models_to_try = [self._model]
        if fallback_model and fallback_model != self._model:
            models_to_try.append(fallback_model)

        last_error: Exception | None = None
        for model in models_to_try:
            try:
                async for event in self._astream_with_model(messages, tools, model, tool_choice):
                    yield event
                return  # success — stop trying further models
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Model %s failed in _astream_chat_with_tools, trying next: %s",
                    model, exc,
                )

        if last_error:
            raise last_error

    async def _astream_with_model(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        model: str,
        tool_choice: str = "auto",
    ) -> AsyncGenerator[StreamEvent, None]:
        """Internal: stream a single Chat Completions model with tool calling."""
        # Assembled tool calls indexed by tool_call delta index
        tool_calls_buf: dict[int, dict] = {}

        # OpenAI Chat Completions doesn't allow tool_choice when tools is empty.
        effective_choice = tool_choice if tools else "none"

        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            tool_choice=effective_choice,
            temperature=0.3,
            max_tokens=settings.OPRAI_GPT_MAX_TOKENS,
            stream=True,
        )

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue

            delta = choice.delta

            # ── Text content delta ──
            if delta.content:
                yield ("text", delta.content)

            # ── Tool call deltas ──
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_buf:
                        tool_calls_buf[idx] = {"name": "", "arguments": ""}
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_buf[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_buf[idx]["arguments"] += tc_delta.function.arguments

        # Yield assembled tool calls after stream ends
        for tc in tool_calls_buf.values():
            name = tc["name"]
            args = tc["arguments"]
            if name and args:
                yield ("tool_call", name, args)

    # ── Anthropic Messages API ──────────────────────────────────────────────

    async def _astream_anthropic(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        """No-tool prose stream via Anthropic Messages API.

        Used by `astream()` on text-only follow-up calls (e.g. the
        market_data_followup interpretation pass after a query_onchain
        returned its data). Claude's text deltas are clean — no Harmony
        markers can appear because Claude doesn't have that channel format
        — but we still pipe through `_strip_tool_call_leakage` for symmetry
        with the OpenAI paths.
        """
        system_blocks, non_system = _anthropic_split_system(messages)
        text_buffer = ""
        async with self._anthropic.messages.stream(
            model=self._model,
            max_tokens=settings.OPRAI_GPT_MAX_TOKENS,
            system=system_blocks,
            messages=non_system,
        ) as stream:
            async for chunk in stream.text_stream:
                if not chunk:
                    continue
                text_buffer += chunk
                clean, text_buffer = _strip_tool_call_leakage(text_buffer)
                if clean:
                    yield clean
        if text_buffer:
            tail, _ = _strip_tool_call_leakage(text_buffer, flush=True)
            if tail:
                yield tail

    async def _astream_anthropic_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> AsyncGenerator[StreamEvent, None]:
        """Tool-calling stream via Anthropic Messages API.

        Mirrors the OpenAI path's contract: yields ("text", chunk) for prose
        deltas and ("tool_call", name, args_json) for completed tool_use
        blocks. Claude emits tool args as `input_json_delta` events (partial
        JSON strings); we accumulate them per-block_id and emit on
        content_block_stop.

        tool_choice maps to Anthropic's structured form:
          "auto"     -> {"type": "auto"} (model decides)
          "required" -> {"type": "any"}  (any tool MUST be called — we use
                          this to defeat Haiku 4.5's documented avoidance
                          behaviour on action/query intents)
          "none"     -> drop tools entirely so Claude won't try to call any
        """
        system_blocks, non_system = _anthropic_split_system(messages)
        anthropic_tools = _convert_tools_openai_to_anthropic(tools)

        # Per content-block accumulator: index → {name, input_json}.
        # Claude streams tool_use as a new content block with its own index;
        # text blocks share index 0 typically.
        tool_buf: dict[int, dict[str, str]] = {}
        text_buffer = ""

        # Translate to Anthropic's tool_choice schema. Anthropic does NOT accept
        # the OpenAI string literals — must be a dict {"type": ...}.
        stream_kwargs: dict = {
            "model": self._model,
            "max_tokens": settings.OPRAI_GPT_MAX_TOKENS,
            "system": system_blocks,
            "messages": non_system,
        }
        if tool_choice == "none" or not anthropic_tools:
            # No tools at all — Claude can't call anything regardless.
            pass
        elif tool_choice == "required":
            stream_kwargs["tools"] = anthropic_tools
            # disable_parallel_tool_use=False keeps Claude's parallel-call
            # behaviour intact while still forcing at least one call.
            stream_kwargs["tool_choice"] = {"type": "any", "disable_parallel_tool_use": False}
        else:
            stream_kwargs["tools"] = anthropic_tools
            stream_kwargs["tool_choice"] = {"type": "auto"}

        async with self._anthropic.messages.stream(**stream_kwargs) as stream:
            async for event in stream:
                etype = getattr(event, "type", None)

                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block is not None and getattr(block, "type", None) == "tool_use":
                        tool_buf[event.index] = {
                            "name": getattr(block, "name", ""),
                            "input_json": "",
                        }

                elif etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    dtype = getattr(delta, "type", None) if delta is not None else None
                    if dtype == "text_delta":
                        text_chunk = getattr(delta, "text", "")
                        if text_chunk:
                            text_buffer += text_chunk
                            clean, text_buffer = _strip_tool_call_leakage(text_buffer)
                            if clean:
                                yield ("text", clean)
                    elif dtype == "input_json_delta":
                        idx = event.index
                        if idx in tool_buf:
                            tool_buf[idx]["input_json"] += getattr(delta, "partial_json", "") or ""

                elif etype == "content_block_stop":
                    idx = event.index
                    if idx in tool_buf:
                        entry = tool_buf.pop(idx)
                        if entry["name"]:
                            # Anthropic returns valid JSON (or empty) — pass
                            # through verbatim; downstream validate_tool_call
                            # already json.loads + schema-checks.
                            args = entry["input_json"] or "{}"
                            yield ("tool_call", entry["name"], args)

        if text_buffer:
            tail, _ = _strip_tool_call_leakage(text_buffer, flush=True)
            if tail:
                yield ("text", tail)

    async def _acomplete_anthropic(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        """Non-streaming completion via Anthropic Messages API."""
        system_blocks, non_system = _anthropic_split_system(messages)
        resp = await self._anthropic.messages.create(
            model=self._model,
            max_tokens=settings.OPRAI_GPT_MAX_TOKENS,
            system=system_blocks,
            messages=non_system,
        )
        # Claude returns content as a list of blocks; concat all text blocks.
        out: list[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                out.append(getattr(block, "text", ""))
        return "".join(out)


def _to_langchain(
    messages: list[dict[str, str]],
) -> list[SystemMessage | HumanMessage | AIMessage]:
    """Convert plain dicts to LangChain message objects."""
    result: list[SystemMessage | HumanMessage | AIMessage] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
        else:
            result.append(HumanMessage(content=content))
    return result


# ── Anthropic Messages API ───────────────────────────────────────────────────
# Claude doesn't use Harmony channels — text and tool_use are first-class
# content blocks emitted in the proper structured form, so the Harmony leak
# filter that wraps the OpenAI paths is unnecessary here. We still pipe text
# through `_strip_tool_call_leakage` on the off chance Claude ever writes
# tool-name vocabulary in prose, but in practice it never does.

def _anthropic_split_system(messages: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    """Split into Anthropic system blocks and a non-system message list.

    The system text is wrapped in a single text block with `cache_control`
    set so prompt caching kicks in (10% of input price on cache hits, 1.25x
    on the first write). The system prompt + tool definitions are the largest
    repeated input — caching them turns a 15K-token prefix from a bottleneck
    into a near-zero-cost prefix on every subsequent turn within 5 minutes.
    """
    system_text = "\n\n".join(
        m["content"] for m in messages if m.get("role") == "system" and m.get("content")
    )
    system_blocks: list[dict] = []
    if system_text:
        system_blocks.append({
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        })

    non_system: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            continue
        # Anthropic only accepts user/assistant roles. Tool result messages
        # arriving as role="tool" must be reshaped into a user message with
        # tool_result content blocks — but our current callers don't pass any
        # role="tool" entries (tool results are inlined as system data) so a
        # passthrough is fine.
        role = m["role"]
        if role not in ("user", "assistant"):
            role = "user"
        non_system.append({"role": role, "content": m.get("content", "")})
    # Anthropic requires the first message to be from "user". If callers handed
    # us only system prompts (no real turn yet), inject a noop user prompt to
    # keep the API happy.
    if not non_system:
        non_system.append({"role": "user", "content": " "})
    return system_blocks, non_system


def _convert_tools_openai_to_anthropic(tools: list[dict]) -> list[dict]:
    """OpenAI Chat Completions tool format → Anthropic tool format.

    OpenAI: {"type": "function", "function": {"name", "description", "parameters"}}
    Claude:  {"name", "description", "input_schema"}
    """
    out: list[dict] = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            fn = t["function"]
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {}),
            })
        elif "name" in t and "input_schema" in t:
            # Already Anthropic-shaped
            out.append(t)
    # Cache control on the LAST tool block caches all preceding tool defs +
    # the system block above. One marker covers the whole prefix.
    if out:
        out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


# ── Tool-call leakage filter ────────────────────────────────────────────────
# Reasoning models occasionally emit Harmony-format tool-call syntax into the
# user-facing text channel instead of the function_call channel. Examples seen:
#   {"query_type":"top_validators","params":{}}to=query_onchain
#   {"tool":"query_onchain","query_type":"top_validators","params":{}}
#   to=request_clarification
# These patterns must never reach the chat UI — they're internal model artifacts.

# Channel marker like `to=query_onchain` (with optional trailing words).
_HARMONY_CHANNEL_RE = re.compile(r"\s*to=[a-zA-Z_][a-zA-Z0-9_]*\b[^\n]*")
# JSON keys that signal a tool-call leakage object.
_LEAK_JSON_KEYS = ("tool", "type", "query_type", "action", "category", "name", "function", "arguments")
# Common self-talk / scratchpad phrases the model leaks when confused.
_SELFTALK_RE = re.compile(
    r"(?im)^\s*(Ok\s+I[' ]?ll output|Actually use tool call syntax|hmm|code\?|Let me try)[^\n]*\n?"
)

# Distinctive tool-call vocabulary that should never appear in user-facing
# prose — the system prompt explicitly forbids the model from naming its own
# tools or call mechanism. When the model gets confused (most often under
# tool_choice="auto"), it starts narrating its own tool dispatch ("I'll
# proceed assuming tool call works with syntax query_onchain(...)"), and
# those whole lines need to be dropped.
#
# Each pattern below matches a phrase that is ~impossible in a legitimate
# DeFi-assistant reply. Any line hitting this regex is treated as leakage
# and removed.
_TECHNICAL_LEAK_RE = re.compile(
    r"\b("
    r"query_onchain|execute_action|request_clarification|dex_token|"
    r"info_query|action_type|"
    r"tool call|tool calls|tool invocation|tool result|tool results|"
    r"function call|function calling|function calls|"
    r"commentary channel|meta tool|the wrapper|"
    r"OpenAI function|Harmony format|Harmony tool|"
    r"system prompt|earlier message from system|"
    r"produced garbage|correct format|in this interface|in this environment|"
    r"proceed assuming|as tool invocation|tool call (?:not )?possible|"
    r"untrusted data"
    r")\b",
    re.IGNORECASE,
)


def _drop_leak_lines(text: str) -> str:
    """Drop any line that contains technical tool-call leakage vocabulary."""
    return "\n".join(ln for ln in text.split("\n") if not _TECHNICAL_LEAK_RE.search(ln))


def _find_balanced_brace(s: str, start: int) -> int:
    """Return index just past the matching `}` for the `{` at `start`, or -1 if unmatched.

    Tracks string literals so braces inside JSON strings don't throw off the count.
    """
    depth = 0
    in_str = False
    escape = False
    i = start
    while i < len(s):
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return -1


def _strip_leak_json_blocks(s: str) -> str:
    """Remove balanced `{...}` blocks whose first key matches a known leakage key.

    Also strips empty `{}` blocks — the model emits these as malformed Harmony
    parameter placeholders when it tries (and fails) to call a tool inline,
    e.g. `{}to=query_onchain`. Empty braces never appear in legitimate prose.
    """
    out = []
    i = 0
    while i < len(s):
        if s[i] == "{":
            end = _find_balanced_brace(s, i)
            if end != -1:
                block = s[i:end]
                # Empty `{}` (or whitespace-only) — always a malformed leak.
                if block.strip("{}") == "":
                    i = end
                    continue
                # Look at the first quoted key after the opening brace.
                key_match = re.match(r'\{\s*"([^"]+)"\s*:', block)
                if key_match and key_match.group(1) in _LEAK_JSON_KEYS:
                    i = end
                    continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _strip_tool_call_leakage(buffer: str, *, flush: bool = False) -> tuple[str, str]:
    """
    Strip Harmony tool-call leakage from a streaming text buffer.

    Returns (text_to_yield, holdback). The holdback is the trailing portion that
    might still be the start of a leakage pattern — yield it on the next call when
    more bytes arrive. Pass flush=True at end-of-stream to release everything.

    Three layers of stripping run in order:
      1. `to=...` Harmony channel markers (whole-segment until newline).
      2. Balanced JSON blocks whose first key is in `_LEAK_JSON_KEYS`
         (e.g. `{"tool":"query_onchain",...}`).
      3. Whole lines containing distinctive technical leak vocabulary
         (`tool call`, `query_onchain`, `proceed assuming`, etc.) — the
         model narrating its own dispatch logic.

    During streaming we only line-filter COMPLETE lines (those that end in
    a newline already in the buffer). The trailing partial is held back if
    it already shows leak vocabulary, so growing leakage doesn't briefly
    flash into the UI before being deleted on the next chunk.
    """
    cleaned = buffer
    cleaned = _HARMONY_CHANNEL_RE.sub("", cleaned)
    cleaned = _strip_leak_json_blocks(cleaned)
    cleaned = _SELFTALK_RE.sub("", cleaned)

    if flush:
        cleaned = _drop_leak_lines(cleaned)
        return cleaned.strip(), ""

    # Apply the line-level technical-leak filter to complete lines only;
    # split off any trailing partial line for further holdback evaluation.
    last_nl = cleaned.rfind("\n")
    if last_nl == -1:
        # Whole buffer is one in-progress line. If it's already showing a
        # leak token, hold the entire thing — we'll either drop it after
        # the next newline or flush-strip it at end of stream.
        partial_tail = cleaned
        if _TECHNICAL_LEAK_RE.search(partial_tail):
            return "", buffer
        cleaned_complete = ""
    else:
        complete_part = cleaned[: last_nl + 1]
        partial_tail = cleaned[last_nl + 1 :]
        cleaned_complete = _drop_leak_lines(complete_part)
        # If the partial tail itself already shows a leak token, hold it
        # so the user never sees the half-formed leak line.
        if _TECHNICAL_LEAK_RE.search(partial_tail):
            return cleaned_complete, partial_tail

    visible = cleaned_complete + partial_tail

    # Hold back trailing content that may still be growing into a leakage pattern.
    # If the buffer ends with an unclosed `{` whose first key is suspicious, OR a
    # partial `to=` marker without a newline yet, hold from there.
    last_open = visible.rfind("{")
    last_to = visible.rfind("to=")
    cuts: list[int] = []
    if last_open != -1 and _find_balanced_brace(visible, last_open) == -1:
        cuts.append(last_open)
    if last_to != -1 and "\n" not in visible[last_to:]:
        cuts.append(last_to)

    # Stream-chunk lookahead: even when no in-progress leak is yet detectable,
    # a Harmony marker like `to=query_onchain` can arrive split across chunks
    # (e.g. `{}t` then `o=query_onchain code:`). Neither chunk matches the
    # `to=word` regex on its own, so the leak slips through. Hold the trailing
    # `t` / `to` / `{` / `{"…` if it sits right after a boundary (whitespace,
    # closing brace, newline, or start-of-string) — those are the contexts a
    # real leak marker starts in. Words like "but"/"hot" don't trigger because
    # the `t` follows letters, not a boundary.
    if not cuts and not flush:
        suspicious_tail = re.search(
            r'(?:[}\s\n]|^)(\{["a-zA-Z]*|to?)\Z',
            visible,
        )
        if suspicious_tail:
            cuts.append(suspicious_tail.start(1))

    if not cuts:
        return visible, ""
    cut = min(cuts)
    return visible[:cut], visible[cut:]

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
from collections.abc import AsyncGenerator
from typing import Literal

from openai import AsyncOpenAI
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
    """Thin wrapper supporting both Chat Completions and Responses API."""

    def __init__(self) -> None:
        if not settings.OPRAI_OPENAI_API_KEY:
            raise RuntimeError("LLM integration is not configured: OPRAI_OPENAI_API_KEY is empty")

        self._model = settings.OPRAI_OPENAI_MODEL
        self._use_responses_api = self._model in _RESPONSES_API_MODELS
        self._client = AsyncOpenAI(api_key=settings.OPRAI_OPENAI_API_KEY)

        if not self._use_responses_api:
            primary = ChatOpenAI(
                model=self._model,
                api_key=settings.OPRAI_OPENAI_API_KEY,  # type: ignore[arg-type]
                temperature=0.3,
                max_tokens=settings.OPRAI_GPT_MAX_TOKENS,
                streaming=True,
            )
            fallback_model = settings.OPRAI_OPENAI_FALLBACK_MODEL
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

    async def astream(
        self,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        """Async-stream LLM token chunks (text only, no tool calling)."""
        if self._use_responses_api:
            async for chunk in self._astream_responses(messages):
                yield chunk
        else:
            async for chunk in self._astream_chat(messages):
                yield chunk

    async def astream_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream with function calling.

        Yields tuples:
          ("text", content)               — a text delta chunk
          ("tool_call", name, args_json)  — a complete tool call

        Supports both Chat Completions (gpt-4o-*) and Responses API (gpt-5.4-nano, o-series).
        """
        if self._use_responses_api:
            async for event in self._astream_responses_with_tools(messages, tools):
                yield event
            return

        async for event in self._astream_chat_with_tools(messages, tools):
            yield event

    async def acomplete(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        """Non-streaming completion that returns the full response text."""
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
                "summary": "auto",
            },
            "tools": responses_tools,
            "store": True,
            "include": _RESPONSES_INCLUDE,
            "stream": stream,
        }
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
                yield event.delta

        # Close thinking tag if model produced only reasoning (edge case)
        if reasoning_started and not reasoning_done:
            yield "</think>"

    async def _astream_responses_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream Responses API with tool calling support.

        Parses both text deltas and function_call events from the stream.
        Function names are tracked via output_item.added events, arguments
        are assembled from function_call_arguments.delta and yielded on .done.
        """
        stream = await self._client.responses.create(
            **self._build_responses_kwargs(messages, stream=True, tools=tools)
        )

        reasoning_started = False
        reasoning_done = False
        # Map item_id -> {"name": str, "arguments": str}
        fn_calls: dict[str, dict] = {}

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
                yield ("text", event.delta)

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
        async for chunk in self._llm.astream(lc_messages):
            text = chunk.content if isinstance(chunk.content, str) else ""
            if text:
                yield text

    async def _astream_chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream Chat Completions with function calling.

        Tool call argument deltas arrive incrementally and are assembled
        by index. Once the stream ends (finish_reason tool_calls | stop),
        we yield the assembled tool calls as ("tool_call", name, args_json).

        If the primary model fails, retries once with the fallback model.
        """
        fallback_model = settings.OPRAI_OPENAI_FALLBACK_MODEL
        models_to_try = [self._model]
        if fallback_model and fallback_model != self._model:
            models_to_try.append(fallback_model)

        last_error: Exception | None = None
        for model in models_to_try:
            try:
                async for event in self._astream_with_model(messages, tools, model):
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
    ) -> AsyncGenerator[StreamEvent, None]:
        """Internal: stream a single Chat Completions model with tool calling."""
        # Assembled tool calls indexed by tool_call delta index
        tool_calls_buf: dict[int, dict] = {}

        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
            tool_choice="auto",
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

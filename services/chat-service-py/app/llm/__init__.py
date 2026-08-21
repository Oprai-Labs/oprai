"""
Model-Agnostic LLM Provider System

Based on elizaOS model-agnostic architecture.
Supports OpenAI, Anthropic, Gemini, Ollama, Grok, Deepseek.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ModelProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    GROK = "grok"
    DEEPSEEK = "deepseek"
    LIVEPEER = "livepeer"
    LLAMA_LOCAL = "llama_local"


@dataclass
class ModelConfig:
    """Model configuration"""
    provider: ModelProvider
    model: str
    temperature: float = 0.7
    max_tokens: int = 2000
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    stop_sequences: list[str] = field(default_factory=list)


@dataclass
class LLMResponse:
    """LLM response wrapper"""
    content: str
    model: str
    provider: ModelProvider
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    latency_ms: float = 0.0


class BaseLLMProvider(ABC):
    """Base class for LLM providers"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url

    @property
    @abstractmethod
    def provider(self) -> ModelProvider:
        """Return provider type"""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> LLMResponse:
        """Generate a completion"""

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> AsyncGenerator[str, None]:
        """Stream a completion"""

    async def embed(self, text: str) -> list[float]:
        """Generate embeddings (optional)"""
        raise NotImplementedError("Embeddings not supported by this provider")


class OpenAIProvider(BaseLLMProvider):
    """OpenAI provider"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key, base_url)
        self._client = None

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.OPENAI

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> LLMResponse:
        import time

        client = self._get_client()
        start = time.perf_counter()

        response = await client.chat.completions.create(
            model=config.model,
            messages=messages,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            frequency_penalty=config.frequency_penalty,
            presence_penalty=config.presence_penalty,
            stop=config.stop_sequences or None,
        )

        latency = (time.perf_counter() - start) * 1000

        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=response.model,
            provider=self.provider,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            finish_reason=response.choices[0].finish_reason,
            latency_ms=latency,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()

        stream = await client.chat.completions.create(
            model=config.model,
            messages=messages,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def embed(self, text: str) -> list[float]:
        client = self._get_client()
        response = await client.embeddings.create(
            model="text-embedding-3-large",
            input=text,
        )
        return response.data[0].embedding


class AnthropicProvider(BaseLLMProvider):
    """Anthropic provider"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key, base_url)
        self._client = None

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.ANTHROPIC

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._client

    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> LLMResponse:
        import time

        client = self._get_client()
        start = time.perf_counter()

        # Extract system message
        system = None
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)

        response = await client.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            system=system,
            messages=chat_messages,
        )

        latency = (time.perf_counter() - start) * 1000

        return LLMResponse(
            content=response.content[0].text,
            model=response.model,
            provider=self.provider,
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            },
            finish_reason=response.stop_reason,
            latency_ms=latency,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()

        system = None
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)

        async with client.messages.stream(
            model=config.model,
            max_tokens=config.max_tokens,
            system=system,
            messages=chat_messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text


class GeminiProvider(BaseLLMProvider):
    """Google Gemini provider"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key, base_url)
        self._client = None

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.GEMINI

    def _get_client(self):
        if self._client is None:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            self._client = genai
        return self._client

    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> LLMResponse:
        import time

        genai = self._get_client()

        # Convert messages to Gemini format. Gemini takes the system prompt as a
        # constructor argument, not as a turn in `contents` — so it has to be
        # collected before the model is built. It used to be collected and then
        # dropped, which sent every Gemini call out with no system prompt at all.
        contents = []
        system_instruction = None

        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                contents.append({"role": "user", "parts": [msg["content"]]})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [msg["content"]]})

        model = genai.GenerativeModel(
            config.model,
            system_instruction=system_instruction,
        )

        start = time.perf_counter()

        response = await asyncio.to_thread(
            model.generate_content,
            contents,
            generation_config={
                "temperature": config.temperature,
                "max_output_tokens": config.max_tokens,
            },
        )

        latency = (time.perf_counter() - start) * 1000

        return LLMResponse(
            content=response.text,
            model=config.model,
            provider=self.provider,
            latency_ms=latency,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> AsyncGenerator[str, None]:
        genai = self._get_client()
        model = genai.GenerativeModel(config.model)

        contents = []
        for msg in messages:
            if msg["role"] == "user":
                contents.append({"role": "user", "parts": [msg["content"]]})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [msg["content"]]})

        response = model.generate_content(
            contents,
            generation_config={"temperature": config.temperature},
            stream=True,
        )

        for chunk in response:
            if chunk.text:
                yield chunk.text


class OllamaProvider(BaseLLMProvider):
    """Ollama local provider"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key, base_url or "http://localhost:11434")

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.OLLAMA

    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> LLMResponse:
        import time

        import httpx

        start = time.perf_counter()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": config.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": config.temperature,
                        "num_predict": config.max_tokens,
                    },
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

        latency = (time.perf_counter() - start) * 1000

        return LLMResponse(
            content=data["message"]["content"],
            model=config.model,
            provider=self.provider,
            latency_ms=latency,
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> AsyncGenerator[str, None]:
        import httpx

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json={
                    "model": config.model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "temperature": config.temperature,
                        "num_predict": config.max_tokens,
                    },
                },
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        import json
                        data = json.loads(line)
                        if "message" in data and "content" in data["message"]:
                            yield data["message"]["content"]


class DeepseekProvider(BaseLLMProvider):
    """Deepseek provider (OpenAI-compatible)"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        super().__init__(api_key, base_url or "https://api.deepseek.com/v1")
        self._openai_provider = None

    @property
    def provider(self) -> ModelProvider:
        return ModelProvider.DEEPSEEK

    def _get_provider(self) -> OpenAIProvider:
        if self._openai_provider is None:
            self._openai_provider = OpenAIProvider(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        return self._openai_provider

    async def complete(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> LLMResponse:
        response = await self._get_provider().complete(messages, config)
        response.provider = self.provider
        return response

    async def stream(
        self,
        messages: list[dict[str, str]],
        config: ModelConfig,
    ) -> AsyncGenerator[str, None]:
        async for chunk in self._get_provider().stream(messages, config):
            yield chunk


class LLMService:
    """
    Unified LLM service supporting multiple providers.
    Handles provider selection, fallback, and load balancing.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._providers: dict[ModelProvider, BaseLLMProvider] = {}
        self._default_provider = ModelProvider.OPENAI
        self._fallback_order = [
            ModelProvider.OPENAI,
            ModelProvider.ANTHROPIC,
            ModelProvider.GEMINI,
            ModelProvider.DEEPSEEK,
            ModelProvider.OLLAMA,
        ]

    def register_provider(self, provider: BaseLLMProvider) -> None:
        """Register a provider"""
        self._providers[provider.provider] = provider
        logger.info(f"Registered LLM provider: {provider.provider}")

    def get_provider(self, provider: ModelProvider) -> BaseLLMProvider | None:
        """Get a provider by type"""
        return self._providers.get(provider)

    def set_default_provider(self, provider: ModelProvider) -> None:
        """Set the default provider"""
        self._default_provider = provider

    def _get_model_config(
        self,
        provider: ModelProvider,
        model: str | None = None,
    ) -> ModelConfig:
        """Get model configuration for a provider"""
        default_models = {
            ModelProvider.OPENAI: "gpt-4o-mini",
            ModelProvider.ANTHROPIC: "claude-3-5-sonnet-20241031",
            ModelProvider.GEMINI: "gemini-1.5-flash",
            ModelProvider.OLLAMA: "llama3.1",
            ModelProvider.DEEPSEEK: "deepseek-chat",
        }

        return ModelConfig(
            provider=provider,
            model=model or default_models.get(provider, "gpt-4o-mini"),
        )

    async def complete(
        self,
        messages: list[dict[str, str]],
        provider: ModelProvider | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """
        Generate a completion with fallback support.

        Args:
            messages: Chat messages
            provider: Preferred provider (optional)
            model: Model name (optional)

        Returns:
            LLM response
        """
        providers_to_try = [provider] if provider else [self._default_provider]
        providers_to_try.extend(self._fallback_order)

        last_error = None

        for prov in providers_to_try:
            prov_instance = self._providers.get(prov)
            if not prov_instance:
                continue

            try:
                config = self._get_model_config(prov, model)
                return await prov_instance.complete(messages, config)
            except Exception as e:
                logger.warning("Provider {prov} failed", exc_info=True)
                last_error = e
                continue

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    async def stream(
        self,
        messages: list[dict[str, str]],
        provider: ModelProvider | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a completion"""
        prov = provider or self._default_provider
        prov_instance = self._providers.get(prov)

        if not prov_instance:
            raise ValueError(f"Provider {prov} not available")

        config = self._get_model_config(prov, model)
        async for chunk in prov_instance.stream(messages, config):
            yield chunk

    async def acomplete(
        self,
        messages: list[dict[str, str]],
        provider: ModelProvider | None = None,
        model: str | None = None,
    ) -> str:
        """Simple async complete returning just the content"""
        response = await self.complete(messages, provider, model)
        return response.content

    async def embed(
        self,
        text: str,
        provider: ModelProvider = ModelProvider.OPENAI,
    ) -> list[float]:
        """Generate embeddings"""
        prov_instance = self._providers.get(provider)
        if not prov_instance:
            raise ValueError(f"Provider {provider} not available")

        return await prov_instance.embed(text)


# Global LLM service
_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Get the global LLM service"""
    global _llm_service
    if _llm_service is None:
        from app.config import settings

        _llm_service = LLMService()

        # Register providers based on available API keys
        if settings.OPRAI_OPENAI_API_KEY:
            _llm_service.register_provider(OpenAIProvider(
                api_key=settings.OPRAI_OPENAI_API_KEY
            ))

        if hasattr(settings, 'OPRAI_ANTHROPIC_API_KEY') and settings.OPRAI_ANTHROPIC_API_KEY:
            _llm_service.register_provider(AnthropicProvider(
                api_key=settings.OPRAI_ANTHROPIC_API_KEY
            ))

        if hasattr(settings, 'OPRAI_GEMINI_API_KEY') and settings.OPRAI_GEMINI_API_KEY:
            _llm_service.register_provider(GeminiProvider(
                api_key=settings.OPRAI_GEMINI_API_KEY
            ))

        if hasattr(settings, 'OPRAI_DEEPSEEK_API_KEY') and settings.OPRAI_DEEPSEEK_API_KEY:
            _llm_service.register_provider(DeepseekProvider(
                api_key=settings.OPRAI_DEEPSEEK_API_KEY
            ))

        # Always try to register Ollama (local)
        try:
            _llm_service.register_provider(OllamaProvider())
        except Exception:
            pass

    return _llm_service

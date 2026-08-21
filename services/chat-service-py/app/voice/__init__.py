"""
Voice System

Based on elizaOS voice capabilities.
Provides text-to-speech and speech-to-text integration.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import wave
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class VoiceProvider(str, Enum):
    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"
    DEEPGRAM = "deepgram"
    GOOGLE = "google"
    AZURE = "azure"
    LOCAL = "local"


@dataclass
class VoiceConfig:
    """Voice configuration"""
    provider: VoiceProvider
    voice_id: str
    model: str | None = None
    speed: float = 1.0
    pitch: float = 1.0
    language: str = "en-US"


@dataclass
class TTSResult:
    """Text-to-speech result"""
    audio_data: bytes
    content_type: str = "audio/mpeg"
    duration_seconds: float = 0.0
    provider: VoiceProvider = VoiceProvider.OPENAI


@dataclass
class STTResult:
    """Speech-to-text result"""
    text: str
    language: str = "en-US"
    confidence: float = 1.0
    duration_seconds: float = 0.0
    provider: VoiceProvider = VoiceProvider.OPENAI


class BaseTTSProvider(ABC):
    """Base class for text-to-speech providers"""

    @property
    @abstractmethod
    def provider(self) -> VoiceProvider:
        pass

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        config: VoiceConfig,
    ) -> TTSResult:
        """Synthesize speech from text"""

    async def synthesize_stream(
        self,
        text: str,
        config: VoiceConfig,
    ) -> AsyncGenerator[bytes, None]:
        """Stream synthesized speech"""
        result = await self.synthesize(text, config)
        yield result.audio_data


class BaseSTTProvider(ABC):
    """Base class for speech-to-text providers"""

    @property
    @abstractmethod
    def provider(self) -> VoiceProvider:
        pass

    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "en-US",
    ) -> STTResult:
        """Transcribe audio to text"""

    async def transcribe_stream(
        self,
        audio_stream: AsyncGenerator[bytes, None],
        language: str = "en-US",
    ) -> AsyncGenerator[str, None]:
        """Stream transcription"""
        # Default: collect all audio and transcribe
        chunks = []
        async for chunk in audio_stream:
            chunks.append(chunk)

        audio_data = b"".join(chunks)
        result = await self.transcribe(audio_data, language)
        yield result.text


class OpenAITTSProvider(BaseTTSProvider):
    """OpenAI TTS provider"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self._client = None

    @property
    def provider(self) -> VoiceProvider:
        return VoiceProvider.OPENAI

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def synthesize(
        self,
        text: str,
        config: VoiceConfig,
    ) -> TTSResult:
        import time

        client = self._get_client()
        start = time.perf_counter()

        response = await client.audio.speech.create(
            model=config.model or "tts-1",
            voice=config.voice_id,
            input=text,
            speed=config.speed,
        )

        audio_data = response.content
        latency = time.perf_counter() - start

        # Estimate duration (rough approximation)
        duration = len(text.split()) / 2.5  # ~150 words per minute

        return TTSResult(
            audio_data=audio_data,
            content_type="audio/mpeg",
            duration_seconds=duration,
            provider=self.provider,
        )


class OpenAISTTProvider(BaseSTTProvider):
    """OpenAI STT provider (Whisper)"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self._client = None

    @property
    def provider(self) -> VoiceProvider:
        return VoiceProvider.OPENAI

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "en-US",
    ) -> STTResult:
        client = self._get_client()

        # Create a temporary file for the audio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            with open(temp_path, "rb") as audio_file:
                response = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=language.split("-")[0],  # Whisper uses 2-letter codes
                )

            return STTResult(
                text=response.text,
                language=language,
                provider=self.provider,
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)


class ElevenLabsTTSProvider(BaseTTSProvider):
    """ElevenLabs TTS provider"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self.base_url = "https://api.elevenlabs.io/v1"

    @property
    def provider(self) -> VoiceProvider:
        return VoiceProvider.ELEVENLABS

    async def synthesize(
        self,
        text: str,
        config: VoiceConfig,
    ) -> TTSResult:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/text-to-speech/{config.voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": config.model or "eleven_monolingual_v1",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                },
                timeout=60.0,
            )
            response.raise_for_status()

            audio_data = response.content
            duration = len(text.split()) / 2.5

            return TTSResult(
                audio_data=audio_data,
                content_type="audio/mpeg",
                duration_seconds=duration,
                provider=self.provider,
            )

    async def synthesize_stream(
        self,
        text: str,
        config: VoiceConfig,
    ) -> AsyncGenerator[bytes, None]:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/text-to-speech/{config.voice_id}/stream",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": config.model or "eleven_monolingual_v1",
                },
                timeout=60.0,
            ) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk


class DeepgramSTTProvider(BaseSTTProvider):
    """Deepgram STT provider"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key
        self.base_url = "https://api.deepgram.com/v1"

    @property
    def provider(self) -> VoiceProvider:
        return VoiceProvider.DEEPGRAM

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "en-US",
    ) -> STTResult:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/listen",
                headers={
                    "Authorization": f"Token {self.api_key}",
                    "Content-Type": "audio/wav",
                },
                params={
                    "model": "nova-2",
                    "language": language,
                },
                content=audio_data,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

            channel = data["results"]["channels"][0]
            transcript = channel["alternatives"][0]["transcript"]
            confidence = channel["alternatives"][0].get("confidence", 1.0)
            duration = data["results"]["channels"][0].get("duration", 0.0)

            return STTResult(
                text=transcript,
                language=language,
                confidence=confidence,
                duration_seconds=duration,
                provider=self.provider,
            )


class VoiceService:
    """
    Unified voice service.
    Manages TTS and STT providers.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._tts_providers: dict[VoiceProvider, BaseTTSProvider] = {}
        self._stt_providers: dict[VoiceProvider, BaseSTTProvider] = {}
        self._default_tts = VoiceProvider.OPENAI
        self._default_stt = VoiceProvider.OPENAI

    def register_tts(self, provider: BaseTTSProvider) -> None:
        """Register a TTS provider"""
        self._tts_providers[provider.provider] = provider

    def register_stt(self, provider: BaseSTTProvider) -> None:
        """Register an STT provider"""
        self._stt_providers[provider.provider] = provider

    async def text_to_speech(
        self,
        text: str,
        voice_id: str = "alloy",
        provider: VoiceProvider | None = None,
        speed: float = 1.0,
    ) -> TTSResult:
        """
        Convert text to speech.

        Args:
            text: Text to synthesize
            voice_id: Voice identifier
            provider: TTS provider (optional)
            speed: Speech speed multiplier

        Returns:
            TTS result with audio data
        """
        prov = provider or self._default_tts
        tts_provider = self._tts_providers.get(prov)

        if not tts_provider:
            raise ValueError(f"TTS provider {prov} not available")

        config = VoiceConfig(
            provider=prov,
            voice_id=voice_id,
            speed=speed,
        )

        return await tts_provider.synthesize(text, config)

    async def speech_to_text(
        self,
        audio_data: bytes,
        language: str = "en-US",
        provider: VoiceProvider | None = None,
    ) -> STTResult:
        """
        Convert speech to text.

        Args:
            audio_data: Audio bytes (WAV format preferred)
            language: Language code
            provider: STT provider (optional)

        Returns:
            STT result with transcribed text
        """
        prov = provider or self._default_stt
        stt_provider = self._stt_providers.get(prov)

        if not stt_provider:
            raise ValueError(f"STT provider {prov} not available")

        return await stt_provider.transcribe(audio_data, language)

    async def stream_tts(
        self,
        text: str,
        voice_id: str = "alloy",
        provider: VoiceProvider | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Stream TTS audio"""
        prov = provider or self._default_tts
        tts_provider = self._tts_providers.get(prov)

        if not tts_provider:
            raise ValueError(f"TTS provider {prov} not available")

        config = VoiceConfig(provider=prov, voice_id=voice_id)

        async for chunk in tts_provider.synthesize_stream(text, config):
            yield chunk

    def list_voices(self, provider: VoiceProvider | None = None) -> list[dict]:
        """List available voices"""
        # OpenAI voices
        if provider == VoiceProvider.OPENAI or provider is None:
            return [
                {"id": "alloy", "name": "Alloy", "provider": "openai"},
                {"id": "echo", "name": "Echo", "provider": "openai"},
                {"id": "fable", "name": "Fable", "provider": "openai"},
                {"id": "onyx", "name": "Onyx", "provider": "openai"},
                {"id": "nova", "name": "Nova", "provider": "openai"},
                {"id": "shimmer", "name": "Shimmer", "provider": "openai"},
            ]
        return []


# Global voice service
_voice_service: VoiceService | None = None


def get_voice_service() -> VoiceService:
    """Get the global voice service"""
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()

        # Register OpenAI providers if API key available
        if settings.OPRAI_OPENAI_API_KEY:
            _voice_service.register_tts(OpenAITTSProvider(
                api_key=settings.OPRAI_OPENAI_API_KEY
            ))
            _voice_service.register_stt(OpenAISTTProvider(
                api_key=settings.OPRAI_OPENAI_API_KEY
            ))

        # Register ElevenLabs if API key available
        elevenlabs_key = getattr(settings, 'ELEVENLABS_API_KEY', None)
        if elevenlabs_key:
            _voice_service.register_tts(ElevenLabsTTSProvider(
                api_key=elevenlabs_key
            ))

        # Register Deepgram if API key available
        deepgram_key = getattr(settings, 'DEEPGRAM_API_KEY', None)
        if deepgram_key:
            _voice_service.register_stt(DeepgramSTTProvider(
                api_key=deepgram_key
            ))

    return _voice_service

"""
Tests for Voice Service module.

Tests TTS and STT providers.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestVoiceProvider:
    """Test VoiceProvider enum"""

    def test_provider_values(self):
        """Test all provider values"""
        from app.voice import VoiceProvider

        assert VoiceProvider.OPENAI.value == "openai"
        assert VoiceProvider.ELEVENLABS.value == "elevenlabs"
        assert VoiceProvider.DEEPGRAM.value == "deepgram"
        assert VoiceProvider.GOOGLE.value == "google"
        assert VoiceProvider.AZURE.value == "azure"


class TestVoiceConfig:
    """Test VoiceConfig dataclass"""

    def test_config_defaults(self):
        """Test config default values"""
        from app.voice import VoiceConfig, VoiceProvider

        config = VoiceConfig(
            provider=VoiceProvider.OPENAI,
            voice_id="alloy"
        )

        assert config.speed == 1.0
        assert config.pitch == 1.0
        assert config.language == "en-US"

    def test_config_with_values(self):
        """Test config with custom values"""
        from app.voice import VoiceConfig, VoiceProvider

        config = VoiceConfig(
            provider=VoiceProvider.ELEVENLABS,
            voice_id="rachel",
            speed=1.5,
            pitch=0.8
        )

        assert config.speed == 1.5
        assert config.pitch == 0.8


class TestTTSResult:
    """Test TTSResult dataclass"""

    def test_tts_result_defaults(self):
        """Test TTS result defaults"""
        from app.voice import TTSResult, VoiceProvider

        result = TTSResult(audio_data=b"test")

        assert result.content_type == "audio/mpeg"
        assert result.duration_seconds == 0.0
        assert result.provider == VoiceProvider.OPENAI


class TestSTTResult:
    """Test STTResult dataclass"""

    def test_stt_result_defaults(self):
        """Test STT result defaults"""
        from app.voice import STTResult, VoiceProvider

        result = STTResult(text="Hello world")

        assert result.language == "en-US"
        assert result.confidence == 1.0
        assert result.duration_seconds == 0.0


class TestBaseTTSProvider:
    """Test BaseTTSProvider"""

    def test_is_abstract(self):
        """Test is abstract"""
        from abc import ABC

        from app.voice import BaseTTSProvider

        assert issubclass(BaseTTSProvider, ABC)


class TestBaseSTTProvider:
    """Test BaseSTTProvider"""

    def test_is_abstract(self):
        """Test is abstract"""
        from abc import ABC

        from app.voice import BaseSTTProvider

        assert issubclass(BaseSTTProvider, ABC)


class TestOpenAITTSProvider:
    """Test OpenAI TTS provider"""

    def test_init(self):
        """Test initialization"""
        from app.voice import OpenAITTSProvider, VoiceProvider

        provider = OpenAITTSProvider(api_key="test_key")

        assert provider.api_key == "test_key"
        assert provider.provider == VoiceProvider.OPENAI


class TestOpenAISTTProvider:
    """Test OpenAI STT provider"""

    def test_init(self):
        """Test initialization"""
        from app.voice import OpenAISTTProvider, VoiceProvider

        provider = OpenAISTTProvider(api_key="test_key")

        assert provider.api_key == "test_key"
        assert provider.provider == VoiceProvider.OPENAI


class TestElevenLabsTTSProvider:
    """Test ElevenLabs TTS provider"""

    def test_init(self):
        """Test initialization"""
        from app.voice import ElevenLabsTTSProvider, VoiceProvider

        provider = ElevenLabsTTSProvider(api_key="test_key")

        assert provider.api_key == "test_key"
        assert provider.provider == VoiceProvider.ELEVENLABS
        assert provider.base_url == "https://api.elevenlabs.io/v1"


class TestDeepgramSTTProvider:
    """Test Deepgram STT provider"""

    def test_init(self):
        """Test initialization"""
        from app.voice import DeepgramSTTProvider, VoiceProvider

        provider = DeepgramSTTProvider(api_key="test_key")

        assert provider.api_key == "test_key"
        assert provider.provider == VoiceProvider.DEEPGRAM
        assert provider.base_url == "https://api.deepgram.com/v1"


class TestVoiceService:
    """Test VoiceService"""

    def test_init(self):
        """Test service initialization"""
        from app.voice import VoiceService

        service = VoiceService()

        assert service._default_tts.value == "openai"
        assert service._default_stt.value == "openai"

    def test_register_tts(self):
        """Test registering TTS provider"""
        from app.voice import OpenAITTSProvider, VoiceProvider, VoiceService

        service = VoiceService()
        provider = OpenAITTSProvider(api_key="key")

        service.register_tts(provider)

        assert VoiceProvider.OPENAI in service._tts_providers

    def test_register_stt(self):
        """Test registering STT provider"""
        from app.voice import OpenAISTTProvider, VoiceProvider, VoiceService

        service = VoiceService()
        provider = OpenAISTTProvider(api_key="key")

        service.register_stt(provider)

        assert VoiceProvider.OPENAI in service._stt_providers


class TestVoiceServiceTTS:
    """Test TTS operations"""

    @pytest.mark.asyncio
    async def test_text_to_speech_no_provider(self):
        """Test TTS with no provider registered"""
        from app.voice import VoiceService

        service = VoiceService()

        with pytest.raises(ValueError, match="not available"):
            await service.text_to_speech("Hello")

    @pytest.mark.asyncio
    async def test_text_to_speech_success(self):
        """Test successful TTS"""
        from app.voice import OpenAITTSProvider, TTSResult, VoiceProvider, VoiceService

        service = VoiceService()

        mock_provider = MagicMock()
        mock_provider.provider = VoiceProvider.OPENAI
        mock_provider.synthesize = AsyncMock(return_value=TTSResult(audio_data=b"audio"))
        service.register_tts(mock_provider)

        result = await service.text_to_speech("Hello world", provider=VoiceProvider.OPENAI)

        assert result.audio_data == b"audio"


class TestVoiceServiceSTT:
    """Test STT operations"""

    @pytest.mark.asyncio
    async def test_speech_to_text_no_provider(self):
        """Test STT with no provider registered"""
        from app.voice import VoiceService

        service = VoiceService()

        with pytest.raises(ValueError, match="not available"):
            await service.speech_to_text(b"audio data")

    @pytest.mark.asyncio
    async def test_speech_to_text_success(self):
        """Test successful STT"""
        from app.voice import OpenAISTTProvider, STTResult, VoiceProvider, VoiceService

        service = VoiceService()

        mock_provider = MagicMock()
        mock_provider.provider = VoiceProvider.OPENAI
        mock_provider.transcribe = AsyncMock(return_value=STTResult(text="Hello"))
        service.register_stt(mock_provider)

        result = await service.speech_to_text(b"audio data", provider=VoiceProvider.OPENAI)

        assert result.text == "Hello"


class TestVoiceServiceStream:
    """Test streaming operations"""

    @pytest.mark.asyncio
    async def test_stream_tts_no_provider(self):
        """Test streaming TTS with no provider"""
        from app.voice import VoiceService

        service = VoiceService()

        with pytest.raises(ValueError, match="not available"):
            async for chunk in service.stream_tts("Hello"):
                pass


class TestListVoices:
    """Test voice listing"""

    def test_list_openai_voices(self):
        """Test listing OpenAI voices"""
        from app.voice import VoiceProvider, VoiceService

        service = VoiceService()
        voices = service.list_voices(VoiceProvider.OPENAI)

        assert len(voices) > 0
        assert voices[0]["id"] == "alloy"

    def test_list_voices_default(self):
        """Test listing voices with default"""
        from app.voice import VoiceService

        service = VoiceService()
        voices = service.list_voices()

        assert len(voices) > 0


class TestGlobalVoiceService:
    """Test global voice service"""

    def test_get_voice_service(self):
        """Test getting voice service"""
        # Reset global
        import app.voice as voice_module
        from app.voice import get_voice_service
        voice_module._voice_service = None

        service = get_voice_service()

        assert service is not None

    def test_singleton(self):
        """Test singleton behavior"""
        # Reset global
        import app.voice as voice_module
        from app.voice import get_voice_service
        voice_module._voice_service = None

        service1 = get_voice_service()
        service2 = get_voice_service()

        assert service1 is service2

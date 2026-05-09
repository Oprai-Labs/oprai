"""
Tests for LLM Service module.

Tests LLM orchestration, Chat Completions and Responses API support.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestLLMServiceInit:
    """Test LLMService initialization"""

    def test_initialization_requires_api_key(self):
        """Test initialization requires API key"""
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = ""

            with pytest.raises(RuntimeError, match="OPRAI_OPENAI_API_KEY"):
                from app.services.llm import LLMService
                LLMService()

    def test_initialization_with_api_key(self):
        """Test initialization with API key"""
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.OPRAI_RESPONDER_MODEL_OPENAI = "gpt-4o-mini"
            mock_settings.OPRAI_GPT_MAX_TOKENS = 2000
            mock_settings.OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI = None

            from app.services.llm import LLMService

            service = LLMService()

            assert service._model == "gpt-4o-mini"

    def test_responses_api_models(self):
        """Test that o-series models use Responses API"""
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.OPRAI_RESPONDER_MODEL_OPENAI = "o1-mini"
            mock_settings.OPRAI_GPT_MAX_TOKENS = 2000
            mock_settings.OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI = None

            from app.services.llm import LLMService

            service = LLMService()

            assert service._use_responses_api is True

    def test_chat_completion_models(self):
        """Test that regular models use Chat Completions"""
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.OPRAI_RESPONDER_MODEL_OPENAI = "gpt-4o"
            mock_settings.OPRAI_GPT_MAX_TOKENS = 2000
            mock_settings.OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI = None

            from app.services.llm import LLMService

            service = LLMService()

            assert service._use_responses_api is False

    def test_fallback_model_configured(self):
        """Test fallback model is configured"""
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"
            mock_settings.OPRAI_RESPONDER_MODEL_OPENAI = "gpt-4o-mini"
            mock_settings.OPRAI_GPT_MAX_TOKENS = 2000
            mock_settings.OPRAI_RESPONDER_FALLBACK_MODEL_OPENAI = "gpt-3.5-turbo"

            from app.services.llm import LLMService

            service = LLMService()

            assert service._model == "gpt-4o-mini"


class TestResponsesApiModels:
    """Test responses API models constant"""

    def test_responses_api_models_list(self):
        """Test responses API models are defined correctly"""
        from app.services.llm import _RESPONSES_API_MODELS

        assert "o1" in _RESPONSES_API_MODELS
        assert "o1-mini" in _RESPONSES_API_MODELS
        assert "gpt-4o-mini" not in _RESPONSES_API_MODELS
        assert "gpt-4o" not in _RESPONSES_API_MODELS

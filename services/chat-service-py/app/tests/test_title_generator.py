"""
Tests for Title Generator module.

Tests auto-generation of chat session titles.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTitleGenerator:
    """Test generate_title function"""

    @pytest.mark.asyncio
    async def test_generate_title_returns_string(self):
        """Test generate_title returns a string"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(return_value="SOL Swap Session")
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            result = await generate_title("I want to swap my SOL for USDC")

            assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_generate_title_truncates_long(self):
        """Test generate_title truncates long titles"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            long_title = "A" * 100
            mock_llm.acomplete = AsyncMock(return_value=long_title)
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            result = await generate_title("Test message")

            # Should be truncated to 60 chars
            assert len(result) <= 60

    @pytest.mark.asyncio
    async def test_generate_title_strips_quotes(self):
        """Test generate_title strips quotes from LLM output"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(return_value='"SOL Swap Session"')
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            result = await generate_title("Test")

            assert not result.startswith('"')

    @pytest.mark.asyncio
    async def test_generate_title_strips_single_quotes(self):
        """Test generate_title strips single quotes"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(return_value="'SOL Swap'")
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            result = await generate_title("Test")

            assert not result.startswith("'")
            assert not result.endswith("'")

    @pytest.mark.asyncio
    async def test_generate_title_empty_response_uses_fallback(self):
        """Test generate_title uses fallback for empty response"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(return_value="")
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            user_message = "I want to swap SOL for USDC on Jupiter"
            result = await generate_title(user_message)

            # Should use truncated user message as fallback
            assert len(result) <= 40

    @pytest.mark.asyncio
    async def test_generate_title_llm_error_uses_fallback(self):
        """Test generate_title uses fallback when LLM fails"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(side_effect=Exception("API Error"))
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            user_message = "I want to swap SOL for USDC"
            result = await generate_title(user_message)

            # Should use truncated user message as fallback
            assert len(result) <= 40

    @pytest.mark.asyncio
    async def test_generate_title_fallback_truncates(self):
        """Test fallback uses first 40 chars"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(side_effect=Exception("Error"))
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            long_message = "A" * 100
            result = await generate_title(long_message)

            assert len(result) == 40

    @pytest.mark.asyncio
    async def test_generate_title_calls_llm(self):
        """Test generate_title calls LLM with correct messages"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(return_value="Test Title")
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            await generate_title("User message content")

            # Verify LLM was called
            assert mock_llm.acomplete.called


class TestTitlePrompt:
    """Test title prompt loading"""

    def test_get_title_prompt_returns_string(self):
        """Test _get_title_prompt returns a string"""
        from app.services.title_generator import _get_title_prompt

        result = _get_title_prompt()

        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_title_prompt_cached(self):
        """Test _get_title_prompt uses caching"""
        from app.services.title_generator import _TITLE_PROMPT, _get_title_prompt

        # Call twice - should use cached value
        result1 = _get_title_prompt()
        result2 = _get_title_prompt()

        assert result1 == result2


class TestTitleGeneratorEdgeCases:
    """Test edge cases for title generation"""

    @pytest.mark.asyncio
    async def test_generate_title_with_short_message(self):
        """Test title generation with short user message"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.acomplete = AsyncMock(return_value="Hi")
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            result = await generate_title("Hi")

            assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_generate_title_preserves_short_titles(self):
        """Test short titles are preserved"""
        with patch("app.services.title_generator.LLMService") as mock_llm_class:
            mock_llm = MagicMock()
            short_title = "Swap"
            mock_llm.acomplete = AsyncMock(return_value=short_title)
            mock_llm_class.return_value = mock_llm

            from app.services.title_generator import generate_title

            result = await generate_title("I want to swap")

            assert result == "Swap"

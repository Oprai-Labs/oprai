"""
Tests for Summary Service module.

Tests conversation summarization using OpenAI.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSummaryServiceInit:
    """Test SummaryService initialization"""

    def test_initialization_without_api_key(self):
        """Test service initializes without API key"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = ""

            from app.services.summary import SummaryService

            service = SummaryService()
            assert service._client is None

    def test_initialization_with_api_key(self):
        """Test service initializes with API key"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            assert service._client is not None


class TestSummarize:
    """Test summarize method"""

    @pytest.mark.asyncio
    async def test_summarize_without_client_returns_fallback(self):
        """Test summarize returns fallback when no client"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = ""

            from app.services.summary import SummaryService

            service = SummaryService()

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Hello, I want to swap SOL for USDC",
                token_count=20
            )

            assert "title" in result
            assert "summary" in result
            assert "tokenCount" in result
            assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_summarize_with_api_call(self):
        """Test summarize makes API call when client exists"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            # Mock API response
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: SOL Swap | Date: 2025-01-01 | Summary: * User wants to swap SOL for USDC"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Hello, I want to swap SOL for USDC",
                token_count=20
            )

            assert service._client.chat.completions.create.called

    @pytest.mark.asyncio
    async def test_summarize_empty_response_uses_fallback(self):
        """Test summarize uses fallback when API returns empty"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            # Mock API response with empty content
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = ""

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Some conversation text",
                token_count=50
            )

            # Should return fallback
            assert "title" in result

    @pytest.mark.asyncio
    async def test_summarize_api_error_uses_fallback(self):
        """Test summarize uses fallback when API raises exception"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            service._client.chat.completions.create = AsyncMock(
                side_effect=Exception("API Error")
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Some conversation text",
                token_count=50
            )

            # Should return fallback
            assert "title" in result
            assert "summary" in result

    @pytest.mark.asyncio
    async def test_summarize_extracts_title(self):
        """Test summarize extracts title from response"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: My Custom Title | Date: 2025-01-01 | Summary: * Summary text"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Test chunk",
                token_count=10
            )

            assert result["title"] == "My Custom Title"

    @pytest.mark.asyncio
    async def test_summarize_default_title(self):
        """Test summarize uses default title when not in format"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Just some summary text without title format"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Test chunk",
                token_count=10
            )

            assert result["title"] == "Session summary"

    @pytest.mark.asyncio
    async def test_summarize_preserves_token_count(self):
        """Test summarize preserves token count in response"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: Test | Date: 2025-01-01 | Summary: * Summary"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Test chunk",
                token_count=123
            )

            assert result["tokenCount"] == 123

    @pytest.mark.asyncio
    async def test_summarize_has_timestamp(self):
        """Test summarize includes timestamp in response"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: Test | Date: 2025-01-01 | Summary: * Summary"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Test chunk",
                token_count=10
            )

            assert "timestamp" in result
            assert isinstance(result["timestamp"], str)

    @pytest.mark.asyncio
    async def test_summarize_uses_correct_model(self):
        """Test summarize uses gpt-4o-mini model"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: Test | Summary: * Test"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            await service.summarize("conv-123", "chunk", 10)

            call_kwargs = service._client.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_summarize_uses_correct_temperature(self):
        """Test summarize uses temperature 0.3"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: Test | Summary: * Test"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            await service.summarize("conv-123", "chunk", 10)

            call_kwargs = service._client.chat.completions.create.call_args.kwargs
            assert call_kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_summarize_max_tokens(self):
        """Test summarize uses max_tokens 300"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: Test | Summary: * Test"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            await service.summarize("conv-123", "chunk", 10)

            call_kwargs = service._client.chat.completions.create.call_args.kwargs
            assert call_kwargs["max_tokens"] == 300


class TestFallback:
    """Test fallback summary method"""

    def test_fallback_returns_dict(self):
        """Test _fallback returns dict"""
        from app.services.summary import SummaryService

        result = SummaryService._fallback(
            chunk="Test conversation chunk",
            token_count=100,
            timestamp="2025-01-01T00:00:00"
        )

        assert isinstance(result, dict)
        assert "title" in result
        assert "summary" in result
        assert "tokenCount" in result
        assert "timestamp" in result

    def test_fallback_title(self):
        """Test _fallback returns default title"""
        from app.services.summary import SummaryService

        result = SummaryService._fallback(
            chunk="Test",
            token_count=10,
            timestamp="2025-01-01T00:00:00"
        )

        assert result["title"] == "Session summary"

    def test_fallback_summary_format(self):
        """Test _fallback summary format"""
        from app.services.summary import SummaryService

        result = SummaryService._fallback(
            chunk="This is a test conversation about Solana DeFi",
            token_count=50,
            timestamp="2025-01-01T00:00:00+00:00"
        )

        assert "Title:" in result["summary"]
        assert "Date:" in result["summary"]
        assert "Summary:" in result["summary"]

    def test_fallback_truncates_long_chunks(self):
        """Test _fallback truncates long chunks"""
        from app.services.summary import SummaryService

        long_chunk = "A" * 500
        result = SummaryService._fallback(
            chunk=long_chunk,
            token_count=100,
            timestamp="2025-01-01T00:00:00"
        )

        # Should be truncated to 200 chars
        assert len(result["summary"]) < len(long_chunk)

    def test_fallback_preserves_token_count(self):
        """Test _fallback preserves token count"""
        from app.services.summary import SummaryService

        result = SummaryService._fallback(
            chunk="Test",
            token_count=999,
            timestamp="2025-01-01T00:00:00"
        )

        assert result["tokenCount"] == 999

    def test_fallback_preserves_timestamp(self):
        """Test _fallback preserves timestamp"""
        from app.services.summary import SummaryService

        timestamp = "2025-06-15T14:30:00+00:00"
        result = SummaryService._fallback(
            chunk="Test",
            token_count=10,
            timestamp=timestamp
        )

        assert result["timestamp"] == timestamp


class TestPrompts:
    """Test prompt constants"""

    def test_summary_format_hint_defined(self):
        """Test SUMMARY_FORMAT_HINT is defined"""
        from app.services.summary import SUMMARY_FORMAT_HINT

        assert isinstance(SUMMARY_FORMAT_HINT, str)
        assert "Title:" in SUMMARY_FORMAT_HINT
        assert "Date:" in SUMMARY_FORMAT_HINT
        assert "Summary:" in SUMMARY_FORMAT_HINT

    def test_system_prompt_defined(self):
        """Test SUMMARIZATION_SYSTEM_PROMPT is defined"""
        from app.services.summary import SUMMARIZATION_SYSTEM_PROMPT

        assert isinstance(SUMMARIZATION_SYSTEM_PROMPT, str)
        assert "DeFi" in SUMMARIZATION_SYSTEM_PROMPT
        assert "Solana" in SUMMARIZATION_SYSTEM_PROMPT
        assert len(SUMMARIZATION_SYSTEM_PROMPT) > 0


class TestSummarizeEdgeCases:
    """Test edge cases for summarize"""

    @pytest.mark.asyncio
    async def test_summarize_empty_chunk(self):
        """Test summarize with empty chunk"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: Empty | Summary: * Empty conversation"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="",
                token_count=0
            )

            assert "title" in result

    @pytest.mark.asyncio
    async def test_summarize_large_token_count(self):
        """Test summarize with large token count"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "Title: Large | Summary: * Large conversation"

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Chunk",
                token_count=100000
            )

            assert result["tokenCount"] == 100000

    @pytest.mark.asyncio
    async def test_summarize_none_content(self):
        """Test summarize handles None content from API"""
        with patch("app.services.summary.settings") as mock_settings:
            mock_settings.OPRAI_OPENAI_API_KEY = "test-key"

            from app.services.summary import SummaryService

            service = SummaryService()
            service._client = MagicMock()

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = None

            service._client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            result = await service.summarize(
                conversation_id="conv-123",
                chunk="Test",
                token_count=10
            )

            # Should use fallback
            assert "title" in result

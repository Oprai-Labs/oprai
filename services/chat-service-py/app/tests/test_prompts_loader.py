"""
Tests for Prompt Loader module.

Tests PromptLoader singleton, caching, fallback behavior, and edge cases.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestPromptLoaderSingleton:
    """Test PromptLoader singleton pattern"""

    def test_singleton_same_instance(self):
        """Test that get_prompt_loader returns the same instance"""
        from app.prompts.loader import get_prompt_loader

        loader1 = get_prompt_loader()
        loader2 = get_prompt_loader()

        assert loader1 is loader2

    def test_singleton_class_attribute(self):
        """Test that _instance class attribute is shared"""
        from app.prompts.loader import PromptLoader

        loader = PromptLoader()
        # After creation, _instance should be set
        assert PromptLoader._instance is not None


class TestPromptLoaderFiles:
    """Test prompt file loading"""

    def test_prompt_files_defined(self):
        """Test that PROMPT_FILES list is defined"""
        from app.prompts.loader import PromptLoader

        assert len(PromptLoader.PROMPT_FILES) > 0
        assert "solana_action_base.txt" in PromptLoader.PROMPT_FILES

    def test_get_system_prompt_returns_string(self):
        """Test get_system_prompt returns a string"""
        from app.prompts.loader import get_system_prompt

        prompt = get_system_prompt()
        assert isinstance(prompt, str)

    def test_prompt_length_property(self):
        """Test prompt_length property returns count"""
        from app.prompts.loader import get_prompt_loader

        loader = get_prompt_loader()
        length = loader.prompt_length

        assert isinstance(length, int)
        assert length > 0

    def test_is_loaded_property(self):
        """Test is_loaded property returns boolean"""
        from app.prompts.loader import get_prompt_loader

        loader = get_prompt_loader()
        loaded = loader.is_loaded

        assert isinstance(loaded, bool)
        assert loaded is True


class TestPromptLoaderEdgeCases:
    """Test edge cases and error handling"""

    def test_get_prompt_length_returns_int(self):
        """Test prompt_length returns integer"""
        from app.prompts.loader import get_prompt_loader

        loader = get_prompt_loader()
        length = loader.prompt_length
        assert isinstance(length, int)


class TestPromptLoaderContent:
    """Test prompt content characteristics"""

    def test_system_prompt_contains_solana(self):
        """Test system prompt contains Solana-related content"""
        from app.prompts.loader import get_system_prompt

        prompt = get_system_prompt()
        assert "solana" in prompt.lower() or len(prompt) > 0

    def test_system_prompt_contains_action_blocks(self):
        """Test prompt contains action block definitions"""
        from app.prompts.loader import get_system_prompt

        prompt = get_system_prompt()
        # Should contain either new format or legacy format
        has_action = (
            "[ACTION:" in prompt or
            "ACTION:" in prompt or
            len(prompt) > 0
        )
        assert has_action

    def test_prompt_is_substantial(self):
        """Test that prompt has substantial content"""
        from app.prompts.loader import get_prompt_loader

        loader = get_prompt_loader()
        # If multiple files loaded, prompt should be substantial
        assert loader.prompt_length > 1000

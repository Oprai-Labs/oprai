"""
Tests for Prompt Loader module.

Tests modular prompt loading with singleton caching.
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestPromptLoader:
    """Test PromptLoader singleton"""

    def test_singleton_pattern(self):
        """Test that PromptLoader is a singleton"""
        from app.prompts.loader import PromptLoader

        # Reset for testing
        PromptLoader._instance = None
        PromptLoader._cached_prompt = None
        PromptLoader._initialized = False

        loader1 = PromptLoader()
        loader2 = PromptLoader()

        assert loader1 is loader2

    def test_prompt_files_list(self):
        """Test that all expected prompt files are defined"""
        from app.prompts.loader import PromptLoader

        expected_files = [
            "solana_action_base.txt",
            "solana_action_queries.txt",
            "solana_action_core.txt",
            "solana_action_dex.txt",
            "solana_action_lending.txt",
            "solana_action_staking.txt",
            "solana_action_nft.txt",
            "solana_action_crosschain.txt",
            "solana_action_streamflow.txt",
        ]

        assert PromptLoader.PROMPT_FILES == expected_files

    def test_get_system_prompt_returns_string(self):
        """Test get_system_prompt returns a string"""
        from app.prompts.loader import get_system_prompt

        prompt = get_system_prompt()

        assert isinstance(prompt, str)

    def test_prompt_length_property(self):
        """Test prompt_length property"""
        from app.prompts.loader import get_prompt_loader

        loader = get_prompt_loader()

        assert hasattr(loader, "prompt_length")
        assert loader.prompt_length >= 0

    def test_is_loaded_property(self):
        """Test is_loaded property"""
        from app.prompts.loader import get_prompt_loader

        loader = get_prompt_loader()

        assert hasattr(loader, "is_loaded")
        assert loader.is_loaded is True

    def test_get_prompt_loader_returns_singleton(self):
        """Test get_prompt_loader returns singleton"""
        from app.prompts.loader import get_prompt_loader

        loader1 = get_prompt_loader()
        loader2 = get_prompt_loader()

        assert loader1 is loader2


class TestPromptLoaderInit:
    """Test PromptLoader initialization"""

    def test_initialization_sets_flag(self):
        """Test that initialization sets the _initialized flag"""
        from app.prompts.loader import PromptLoader

        # Store original state
        original_instance = PromptLoader._instance
        original_cached = PromptLoader._cached_prompt
        original_init = PromptLoader._initialized

        try:
            # Reset
            PromptLoader._instance = None
            PromptLoader._cached_prompt = None
            PromptLoader._initialized = False

            loader = PromptLoader()

            assert PromptLoader._initialized is True
        finally:
            # Restore
            PromptLoader._instance = original_instance
            PromptLoader._cached_prompt = original_cached
            PromptLoader._initialized = original_init


class TestPromptLoading:
    """Test actual prompt loading"""

    def test_base_prompt_loaded(self):
        """Test base prompt is loaded"""
        from app.prompts import get_system_prompt

        prompt = get_system_prompt()

        # Should contain base prompt content
        assert len(prompt) > 0

    def test_prompt_not_empty(self):
        """Test prompt is not empty"""
        from app.prompts import get_system_prompt

        prompt = get_system_prompt()

        assert prompt.strip() != ""


class TestPromptModule:
    """Test prompt module interface"""

    def test_module_exports(self):
        """Test module exports"""
        from app.prompts import get_prompt_loader, get_system_prompt

        assert callable(get_prompt_loader)
        assert callable(get_system_prompt)

    def test_get_system_prompt_function(self):
        """Test convenience function works"""
        from app.prompts import get_system_prompt

        result = get_system_prompt()

        assert isinstance(result, str)
        assert len(result) > 0


class TestPromptPerformance:
    """Test prompt loading performance"""

    def test_multiple_calls_return_same_instance(self):
        """Test multiple calls don't reload prompts"""
        from app.prompts import get_prompt_loader

        loader = get_prompt_loader()
        first_length = loader.prompt_length

        # Call again - should be cached
        loader2 = get_prompt_loader()
        second_length = loader2.prompt_length

        assert first_length == second_length

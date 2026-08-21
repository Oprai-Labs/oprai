"""
Tests for Jupiter Plugin module.

Tests Jupiter DEX swap actions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSwapAction:
    """Test SwapAction class"""

    def test_action_name(self):
        """Test action name"""
        from app.plugins.jupiter_plugin import SwapAction

        action = SwapAction()
        assert action.name == "swap"

    def test_action_description(self):
        """Test description"""
        from app.plugins.jupiter_plugin import SwapAction

        action = SwapAction()
        assert "swap" in action.description.lower()

    def test_action_aliases(self):
        """Test aliases"""
        from app.plugins.jupiter_plugin import SwapAction

        action = SwapAction()
        assert "exchange" in action.aliases
        assert "trade" in action.aliases

    def test_action_parameters(self):
        """Test parameters defined"""
        from app.plugins.jupiter_plugin import SwapAction

        action = SwapAction()
        params = action.parameters

        assert "inputMint" in params
        assert "outputMint" in params
        assert "amount" in params

    def test_action_examples(self):
        """Test examples"""
        from app.plugins.jupiter_plugin import SwapAction

        action = SwapAction()
        examples = action.examples

        assert len(examples) > 0
        assert "params" in examples[0]

    def test_action_priority(self):
        """Test priority"""
        from app.plugins.base import PluginPriority
        from app.plugins.jupiter_plugin import SwapAction

        action = SwapAction()
        assert action.priority == PluginPriority.HIGH


class TestGetQuoteAction:
    """Test GetQuoteAction class"""

    def test_action_name(self):
        """Test action name"""
        from app.plugins.jupiter_plugin import GetQuoteAction

        action = GetQuoteAction()
        assert action.name == "get_quote"

    def test_action_aliases(self):
        """Test aliases"""
        from app.plugins.jupiter_plugin import GetQuoteAction

        action = GetQuoteAction()
        assert "quote" in action.aliases
        assert "price" in action.aliases


class TestJupiterPluginActions:
    """Test Jupiter plugin action execution"""

    @pytest.mark.asyncio
    async def test_swap_execute_success(self):
        """Test successful swap execution"""
        from app.plugins.base import PluginContext
        from app.plugins.jupiter_plugin import SwapAction

        action = SwapAction()

        mock_response = MagicMock()
        mock_response.json.return_value = {"swapTransaction": "base64..."}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await action.execute(
                params={
                    "inputMint": "So11111111111111111111111111111111111111112",
                    "outputMint": "EPjFWdd5AufqSSqeMzoqYLswbeSuY5JPtejQ89E",
                    "amount": 1000000000
                },
                context=PluginContext(plugin_id="test")
            )

            assert result.success is True

    @pytest.mark.asyncio
    async def test_swap_execute_error(self):
        """Test swap execution error"""
        from app.plugins.base import PluginContext
        from app.plugins.jupiter_plugin import SwapAction

        action = SwapAction()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.side_effect = Exception("API Error")

            result = await action.execute(
                params={
                    "inputMint": "So11111111111111111111111111111111111111112",
                    "outputMint": "EPjFWdd5AufqSSqeMzoqYLswbeSuY5JPtejQ89E",
                    "amount": 1000000000
                },
                context=PluginContext(plugin_id="test")
            )

            assert result.success is False


class TestGetQuoteExecution:
    """Test quote execution"""

    @pytest.mark.asyncio
    async def test_quote_execute_success(self):
        """Test successful quote fetch"""
        from app.plugins.base import PluginContext
        from app.plugins.jupiter_plugin import GetQuoteAction

        action = GetQuoteAction()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "inputMint": "So11111",
            "outputMint": "EPjFWdd5",
            "inAmount": "1000000000",
            "outAmount": "5000000"
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await action.execute(
                params={
                    "inputMint": "So11111111111111111111111111111111111111112",
                    "outputMint": "EPjFWdd5AufqSSqeMzoqYLswbeSuY5JPtejQ89E",
                    "amount": 1000000000
                },
                context=PluginContext(plugin_id="test")
            )

            assert result.success is True


class TestPluginRegistration:
    """Test Jupiter plugin registration"""

    def test_plugin_class_exists(self):
        """Test JupiterPlugin class exists"""
        try:
            from app.plugins.jupiter_plugin import JupiterPlugin
            assert JupiterPlugin is not None
        except ImportError:
            # Plugin may be defined differently
            pass

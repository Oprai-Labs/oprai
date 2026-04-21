"""
Tests for DeFi Plugins module.

Tests various DeFi protocol actions.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestBuildAction:
    """Test _build_action helper"""

    @pytest.mark.asyncio
    async def test_build_action_success(self):
        """Test successful action build"""
        from app.plugins.defi_plugins import _build_action
        from app.plugins.base import PluginResult

        mock_response = MagicMock()
        mock_response.json.return_value = {"transaction": "base64..."}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await _build_action("swap", {"amount": 100})

            assert result.success is True

    @pytest.mark.asyncio
    async def test_build_action_error(self):
        """Test action build error"""
        from app.plugins.defi_plugins import _build_action

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.side_effect = Exception("API Error")

            result = await _build_action("swap", {"amount": 100})

            assert result.success is False


class TestMeteoraActions:
    """Test Meteora actions"""

    def test_meteora_swap_name(self):
        """Test Meteora swap action name"""
        from app.plugins.defi_plugins import MeteoraSwapAction

        action = MeteoraSwapAction()
        assert action.name == "meteora_swap"

    def test_meteora_swap_aliases(self):
        """Test aliases"""
        from app.plugins.defi_plugins import MeteoraSwapAction

        action = MeteoraSwapAction()
        assert "swap_meteora" in action.aliases

    def test_meteora_swap_parameters(self):
        """Test parameters"""
        from app.plugins.defi_plugins import MeteoraSwapAction

        action = MeteoraSwapAction()
        params = action.parameters

        assert "inputMint" in params
        assert "outputMint" in params
        assert "amount" in params


class TestMeteoraOpenPosition:
    """Test Meteora open position"""

    def test_action_name(self):
        """Test action name"""
        from app.plugins.defi_plugins import MeteoraOpenPositionAction

        action = MeteoraOpenPositionAction()
        assert action.name == "meteora_open_position"

    def test_action_parameters(self):
        """Test parameters"""
        from app.plugins.defi_plugins import MeteoraOpenPositionAction

        action = MeteoraOpenPositionAction()
        params = action.parameters

        assert "pool" in params
        assert "amountX" in params
        assert "amountY" in params


class TestMarginfiActions:
    """Test Marginfi actions"""

    def test_marginfi_deposit_name(self):
        """Test deposit action name"""
        from app.plugins.defi_plugins import MarginfiDepositAction

        action = MarginfiDepositAction()
        assert action.name == "marginfi_deposit"

    def test_marginfi_borrow_name(self):
        """Test borrow action name"""
        from app.plugins.defi_plugins import MarginfiBorrowAction

        action = MarginfiBorrowAction()
        assert action.name == "marginfi_borrow"

    def test_marginfi_parameters(self):
        """Test deposit parameters"""
        from app.plugins.defi_plugins import MarginfiDepositAction

        action = MarginfiDepositAction()
        params = action.parameters

        assert "amount" in params
        assert "bank" in params


class TestRaydiumActions:
    """Test Raydium actions"""

    def test_raydium_swap_name(self):
        """Test swap action name"""
        from app.plugins.defi_plugins import RaydiumSwapAction

        action = RaydiumSwapAction()
        assert action.name == "raydium_swap"

    def test_raydium_add_liquidity_name(self):
        """Test add liquidity action name"""
        from app.plugins.defi_plugins import RaydiumAddLiquidityAction

        action = RaydiumAddLiquidityAction()
        assert action.name == "raydium_add_liquidity"


class TestOrcaActions:
    """Test Orca actions"""

    def test_orca_swap_name(self):
        """Test swap action name"""
        from app.plugins.defi_plugins import OrcaSwapAction

        action = OrcaSwapAction()
        assert action.name == "orca_swap"


class TestJitoActions:
    """Test Jito actions"""

    def test_jito_stake_name(self):
        """Test stake action name"""
        from app.plugins.defi_plugins import JitoStakeAction

        action = JitoStakeAction()
        assert action.name == "jito_stake"


class TestMarinadeActions:
    """Test Marinade actions"""

    def test_marinade_stake_name(self):
        """Test stake action name"""
        from app.plugins.defi_plugins import MarinadeStakeAction

        action = MarinadeStakeAction()
        assert action.name == "marinade_stake"


class TestSolendActions:
    """Test Solend actions"""

    def test_solend_deposit_name(self):
        """Test deposit action name"""
        from app.plugins.defi_plugins import SolendDepositAction

        action = SolendDepositAction()
        assert action.name == "solend_deposit"

    def test_solend_borrow_name(self):
        """Test borrow action name"""
        from app.plugins.defi_plugins import SolendBorrowAction

        action = SolendBorrowAction()
        assert action.name == "solend_borrow"


class TestKaminoActions:
    """Test Kamino actions"""

    def test_kamino_deposit_name(self):
        """Test deposit action name"""
        from app.plugins.defi_plugins import KaminoDepositAction

        action = KaminoDepositAction()
        assert action.name == "kamino_deposit"

    def test_kamino_borrow_name(self):
        """Test borrow action name"""
        from app.plugins.defi_plugins import KaminoBorrowAction

        action = KaminoBorrowAction()
        assert action.name == "kamino_borrow"


class TestDefiActionsExecution:
    """Test executing DeFi actions"""

    @pytest.mark.asyncio
    async def test_meteora_swap_execute(self):
        """Test executing Meteora swap"""
        from app.plugins.defi_plugins import MeteoraSwapAction
        from app.plugins.base import PluginContext

        action = MeteoraSwapAction()

        mock_response = MagicMock()
        mock_response.json.return_value = {"tx": "base64"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await action.execute(
                params={"inputMint": "SOL", "outputMint": "USDC", "amount": 100},
                context=PluginContext(plugin_id="test")
            )

            assert result.success is True

    @pytest.mark.asyncio
    async def test_marginfi_deposit_execute(self):
        """Test executing Marginfi deposit"""
        from app.plugins.defi_plugins import MarginfiDepositAction
        from app.plugins.base import PluginContext

        action = MarginfiDepositAction()

        mock_response = MagicMock()
        mock_response.json.return_value = {"tx": "base64"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = await action.execute(
                params={"amount": 1000, "token": "SOL"},
                context=PluginContext(plugin_id="test")
            )

            assert result.success is True


class TestActionAliases:
    """Test action aliases"""

    def test_meteora_has_alias(self):
        """Test Meteora has swap alias"""
        from app.plugins.defi_plugins import MeteoraSwapAction

        action = MeteoraSwapAction()
        assert len(action.aliases) > 0

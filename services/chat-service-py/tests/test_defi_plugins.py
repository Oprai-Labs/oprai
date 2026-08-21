"""
Comprehensive test suite for DeFi plugins.

Tests cover:
- Plugin instantiation and metadata
- Action names, aliases, and parameter schemas
- validate_params(): missing required fields, wrong types, valid input
- execute(): correct action_type string sent to gateway (mocked)
- Special routing: Squid provider injection, Jupiter action type mapping
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Allow importing from app/ without full service setup
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.plugins.base import PluginContext, PluginResult
from app.plugins.defi_plugins import (
    ALL_PLUGINS,
    # Jito
    JitoPlugin,
    JitoStakeAction,
    JitoTipAction,
    JupiterBorrowAction,
    JupiterCancelDCAAction,
    JupiterCancelLimitOrderAction,
    JupiterDCAAction,
    JupiterJlpAddAction,
    JupiterJlpRemoveAction,
    JupiterLendDepositAction,
    # Jupiter Lend
    JupiterLendPlugin,
    JupiterLendWithdrawAction,
    JupiterLimitOrderAction,
    JupiterPerpCloseAction,
    JupiterPerpOpenAction,
    # Jupiter Perp
    JupiterPerpPlugin,
    # Jupiter DCA/Limit
    JupiterPlugin,
    JupiterRepayAction,
    # JupSOL
    JupSolPlugin,
    JupSolStakeAction,
    JupSolUnstakeAction,
    KaminoBorrowAction,
    KaminoDepositAction,
    KaminoLongOpenAction,
    KaminoMultiplyAddAction,
    KaminoMultiplyCloseAction,
    KaminoMultiplyOpenAction,
    KaminoMultiplyWithdrawAction,
    # Kamino
    KaminoPlugin,
    KaminoPositionCloseAction,
    KaminoShortOpenAction,
    KaminoStakeAction,
    KaminoUnstakeAction,
    MagicEdenAcceptOfferAction,
    MagicEdenBuyAction,
    MagicEdenCancelListingAction,
    MagicEdenCancelOfferAction,
    MagicEdenListAction,
    MagicEdenMakeOfferAction,
    # Magic Eden
    MagicEdenPlugin,
    MarinadeDelayedUnstakeAction,
    # Marinade
    MarinadePlugin,
    MarinadeStakeAction,
    MarinadeUnstakeAction,
    MeteoraAddLiquidityAction,
    MeteoraAddToPositionAction,
    MeteoraClaimFeesAction,
    MeteoraClaimRewardsAction,
    MeteoraClosePositionAction,
    MeteoraCreatePoolAction,
    MeteoraHarvestAction,
    MeteoraOpenPositionAction,
    # Meteora
    MeteoraPlugin,
    MeteoraRemoveLiquidityAction,
    MeteoraStakeAction,
    MeteoraSwapAction,
    MeteoraUnstakeAction,
    OrcaLiquidityAction,
    # Orca
    OrcaPlugin,
    OrcaSwapAction,
    PumpFunBuyAction,
    PumpFunLaunchAction,
    # PumpFun
    PumpFunPlugin,
    PumpFunSellAction,
    RaydiumAddLiquidityAction,
    RaydiumClosePositionAction,
    RaydiumCreatePoolAction,
    RaydiumDecreasePositionAction,
    RaydiumIncreasePositionAction,
    RaydiumOpenPositionAction,
    # Raydium
    RaydiumPlugin,
    RaydiumRemoveLiquidityAction,
    RaydiumSwapAction,
    SquidBridgeAction,
    # Squid
    SquidPlugin,
    StreamflowCancelStreamAction,
    StreamflowCreateMultipleAction,
    StreamflowCreateStreamAction,
    StreamflowGetOneAction,
    StreamflowListAction,
    # Streamflow
    StreamflowPlugin,
    StreamflowTopupAction,
    StreamflowTransferStreamAction,
    StreamflowUpdateAction,
    StreamflowWithdrawAction,
    TensorBuyAction,
    TensorCancelListingAction,
    TensorCancelOfferAction,
    TensorListAction,
    TensorMakeOfferAction,
    # Tensor
    TensorPlugin,
    TokenBurnAction,
    TokenTransferAction,
    # Token Utility
    TokenUtilityPlugin,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx() -> PluginContext:
    return PluginContext(
        plugin_id="test",
        user_wallet="HwMBvLQKr1uqHNZ9v6bRX5GsKBLfNbpTDFTRDMkqmHa",
        session_id="sess-001",
    )


def ok_result() -> PluginResult:
    return PluginResult(success=True, data={"transaction": "base64tx=="})


async def _mock_execute(action_type: str, params: dict) -> PluginResult:
    return ok_result()


# ---------------------------------------------------------------------------
# 1. ALL_PLUGINS registry
# ---------------------------------------------------------------------------

class TestAllPluginsRegistry:
    def test_count(self):
        assert len(ALL_PLUGINS) == 21

    def test_all_are_classes(self):
        from app.plugins.base import BasePlugin
        for cls in ALL_PLUGINS:
            assert issubclass(cls, BasePlugin), f"{cls} is not a BasePlugin subclass"

    def test_unique_ids(self):
        ids = [cls().id for cls in ALL_PLUGINS]
        assert len(ids) == len(set(ids)), "Duplicate plugin IDs found"

    def test_each_has_actions(self):
        for cls in ALL_PLUGINS:
            plugin = cls()
            assert len(plugin.actions) > 0, f"{plugin.name} has no actions"

    def test_plugin_names_nonempty(self):
        for cls in ALL_PLUGINS:
            plugin = cls()
            assert plugin.name, f"{cls} has empty name"
            assert plugin.description, f"{cls} has empty description"


# ---------------------------------------------------------------------------
# 2. Meteora Plugin
# ---------------------------------------------------------------------------

class TestMeteoraPlugin:
    def setup_method(self):
        self.plugin = MeteoraPlugin()

    def test_id_and_name(self):
        assert self.plugin.id == "meteora"
        assert self.plugin.name == "Meteora"

    def test_action_count(self):
        assert len(self.plugin.actions) == 12

    def test_action_names(self):
        names = [a.name for a in self.plugin.actions]
        assert "meteora_swap" in names
        assert "meteora_open_position" in names
        assert "meteora_close_position" in names
        assert "meteora_add_to_position" in names
        assert "meteora_add_liquidity" in names
        assert "meteora_remove_liquidity" in names
        assert "meteora_create_pool" in names
        assert "meteora_claim_fees" in names
        assert "meteora_claim_rewards" in names
        assert "meteora_stake" in names
        assert "meteora_unstake" in names
        assert "meteora_harvest" in names

    # --- meteora_swap ---
    def test_swap_missing_inputMint(self):
        action = MeteoraSwapAction()
        valid, err = action.validate_params({"outputMint": "So111", "amount": 1.0})
        assert not valid
        assert "inputMint" in err

    def test_swap_missing_outputMint(self):
        action = MeteoraSwapAction()
        valid, err = action.validate_params({"inputMint": "USDC", "amount": 1.0})
        assert not valid
        assert "outputMint" in err

    def test_swap_missing_amount(self):
        action = MeteoraSwapAction()
        valid, err = action.validate_params({"inputMint": "USDC", "outputMint": "SOL"})
        assert not valid
        assert "amount" in err

    def test_swap_wrong_type_amount(self):
        action = MeteoraSwapAction()
        valid, err = action.validate_params({"inputMint": "USDC", "outputMint": "SOL", "amount": "1.0"})
        assert not valid
        assert "number" in err

    def test_swap_valid(self):
        action = MeteoraSwapAction()
        valid, err = action.validate_params({"inputMint": "USDC", "outputMint": "SOL", "amount": 100.0})
        assert valid
        assert err is None

    def test_swap_alias(self):
        action = MeteoraSwapAction()
        assert "swap_meteora" in action.aliases

    @pytest.mark.asyncio
    async def test_swap_execute_action_type(self):
        action = MeteoraSwapAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"inputMint": "USDC", "outputMint": "SOL", "amount": 100.0}, make_ctx())
            mock_build.assert_called_once_with("meteora_swap", {"inputMint": "USDC", "outputMint": "SOL", "amount": 100.0})

    # --- meteora_open_position ---
    def test_open_position_required_params(self):
        action = MeteoraOpenPositionAction()
        required = ["pool", "amountX", "amountY", "minBinId", "maxBinId"]
        for param in required:
            p = {"pool": "poolAddr", "amountX": 1.0, "amountY": 1.0, "minBinId": -10, "maxBinId": 10}
            del p[param]
            valid, err = action.validate_params(p)
            assert not valid, f"Should fail without {param}"

    def test_open_position_valid(self):
        action = MeteoraOpenPositionAction()
        valid, _ = action.validate_params({
            "pool": "ABC", "amountX": 1.0, "amountY": 1.0, "minBinId": -10, "maxBinId": 10
        })
        assert valid

    # --- meteora_close_position ---
    def test_close_position_missing_position(self):
        action = MeteoraClosePositionAction()
        valid, err = action.validate_params({})
        assert not valid
        assert "position" in err

    # --- meteora_create_pool ---
    def test_create_pool_required_params(self):
        action = MeteoraCreatePoolAction()
        required_full = {"tokenXMint": "AAAA", "tokenYMint": "BBBB", "binStep": 25, "initialPrice": 1.0}
        for param in required_full:
            p = dict(required_full)
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    # --- meteora_stake ---
    def test_stake_required_params(self):
        action = MeteoraStakeAction()
        valid, _ = action.validate_params({"farm": "farmAddr", "amount": 100.0})
        assert valid
        valid2, _ = action.validate_params({"amount": 100.0})
        assert not valid2

    @pytest.mark.asyncio
    async def test_harvest_execute_type(self):
        action = MeteoraHarvestAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"farm": "farmAddr"}, make_ctx())
            mock_build.assert_called_once_with("meteora_harvest", {"farm": "farmAddr"})

    def test_get_action_by_alias(self):
        action = self.plugin.get_action("swap_meteora")
        assert action is not None
        assert action.name == "meteora_swap"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# 4. Orca Plugin
# ---------------------------------------------------------------------------

class TestOrcaPlugin:
    def setup_method(self):
        self.plugin = OrcaPlugin()

    def test_id(self):
        assert self.plugin.id == "orca"

    def test_action_count(self):
        assert len(self.plugin.actions) == 2

    def test_swap_required_params(self):
        action = OrcaSwapAction()
        for param in ["whirlpoolAddress", "inputMint", "outputMint", "amount"]:
            p = {"whirlpoolAddress": "wp", "inputMint": "A", "outputMint": "B", "amount": 1.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_swap_alias(self):
        action = OrcaSwapAction()
        assert "whirlpool_swap" in action.aliases

    def test_add_liquidity_required(self):
        action = OrcaLiquidityAction()
        valid, _ = action.validate_params({
            "whirlpoolAddress": "wp", "tokenAAmount": 1.0, "tokenBAmount": 1.0
        })
        assert valid

    def test_add_liquidity_alias(self):
        action = OrcaLiquidityAction()
        assert "add_liquidity_orca" in action.aliases

    @pytest.mark.asyncio
    async def test_swap_execute_type(self):
        action = OrcaSwapAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"whirlpoolAddress": "wp", "inputMint": "A", "outputMint": "B", "amount": 1.0}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("orca_swap", p)


# ---------------------------------------------------------------------------
# 6. Kamino Plugin
# ---------------------------------------------------------------------------

class TestKaminoPlugin:
    def setup_method(self):
        self.plugin = KaminoPlugin()

    def test_id(self):
        assert self.plugin.id == "kamino"

    def test_action_count(self):
        assert len(self.plugin.actions) == 11

    def test_action_names(self):
        names = {a.name for a in self.plugin.actions}
        expected = {
            "kamino_deposit", "kamino_borrow",
            "kamino_multiply_open", "kamino_multiply_add",
            "kamino_multiply_withdraw", "kamino_multiply_close",
            "kamino_long_open", "kamino_short_open",
            "kamino_position_close", "kamino_stake", "kamino_unstake",
        }
        assert expected == names

    def test_deposit_required(self):
        action = KaminoDepositAction()
        for param in ["market", "reserve", "amount"]:
            p = {"market": "mkt", "reserve": "rsv", "amount": 1.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_multiply_open_required(self):
        action = KaminoMultiplyOpenAction()
        for param in ["strategy", "amount", "leverage"]:
            p = {"strategy": "strat", "amount": 100.0, "leverage": 2.5}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_long_open_required(self):
        action = KaminoLongOpenAction()
        for param in ["collateralToken", "debtToken", "amount", "leverage"]:
            p = {"collateralToken": "SOL", "debtToken": "USDC", "amount": 1.0, "leverage": 2.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_long_aliases(self):
        action = KaminoLongOpenAction()
        assert "kamino_long" in action.aliases

    def test_short_aliases(self):
        action = KaminoShortOpenAction()
        assert "kamino_short" in action.aliases

    def test_stake_amount_only(self):
        action = KaminoStakeAction()
        valid, _ = action.validate_params({"amount": 500.0})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    @pytest.mark.asyncio
    async def test_multiply_close_execute_type(self):
        action = KaminoMultiplyCloseAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"position": "posAddr"}, make_ctx())
            mock_build.assert_called_once_with("kamino_multiply_close", {"position": "posAddr"})

    @pytest.mark.asyncio
    async def test_position_close_execute_type(self):
        action = KaminoPositionCloseAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"position": "posAddr"}, make_ctx())
            mock_build.assert_called_once_with("kamino_position_close", {"position": "posAddr"})


# ---------------------------------------------------------------------------
# 7. Jito Plugin
# ---------------------------------------------------------------------------

class TestJitoPlugin:
    def setup_method(self):
        self.plugin = JitoPlugin()

    def test_id(self):
        assert self.plugin.id == "jito"

    def test_action_count(self):
        assert len(self.plugin.actions) == 2

    def test_stake_valid(self):
        action = JitoStakeAction()
        valid, _ = action.validate_params({"amount": 1.0})
        assert valid

    def test_stake_missing_amount(self):
        action = JitoStakeAction()
        valid, err = action.validate_params({})
        assert not valid
        assert "amount" in err

    def test_tip_required(self):
        action = JitoTipAction()
        valid, _ = action.validate_params({"tipLamports": 10000})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    def test_tip_aliases(self):
        action = JitoTipAction()
        assert "jito_priority_tip" in action.aliases
        assert "jito_mev_tip" in action.aliases

    def test_tip_wrong_type(self):
        action = JitoTipAction()
        valid, err = action.validate_params({"tipLamports": "10000"})
        assert not valid

    @pytest.mark.asyncio
    async def test_tip_execute_type(self):
        action = JitoTipAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"tipLamports": 10000}, make_ctx())
            mock_build.assert_called_once_with("jito_tip", {"tipLamports": 10000})

    @pytest.mark.asyncio
    async def test_stake_execute_type(self):
        action = JitoStakeAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"amount": 2.5}, make_ctx())
            mock_build.assert_called_once_with("jito_stake", {"amount": 2.5})



# ---------------------------------------------------------------------------
# 10. Raydium Plugin
# ---------------------------------------------------------------------------

class TestRaydiumPlugin:
    def setup_method(self):
        self.plugin = RaydiumPlugin()

    def test_id(self):
        assert self.plugin.id == "raydium"

    def test_action_count(self):
        assert len(self.plugin.actions) == 8

    def test_swap_required(self):
        action = RaydiumSwapAction()
        for param in ["inputMint", "outputMint", "amount"]:
            p = {"inputMint": "USDC", "outputMint": "SOL", "amount": 100.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_swap_aliases(self):
        action = RaydiumSwapAction()
        assert "swap_raydium" in action.aliases
        assert "raydium_trade" in action.aliases

    def test_create_pool_required(self):
        action = RaydiumCreatePoolAction()
        for param in ["mintA", "mintB", "amountA", "amountB"]:
            p = {"mintA": "A", "mintB": "B", "amountA": 100.0, "amountB": 100.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_open_position_required(self):
        action = RaydiumOpenPositionAction()
        for param in ["poolId", "inputMint", "inputAmount", "tickLower", "tickUpper"]:
            p = {"poolId": "pid", "inputMint": "A", "inputAmount": 1.0, "tickLower": -100, "tickUpper": 100}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_open_position_aliases(self):
        action = RaydiumOpenPositionAction()
        assert "raydium_clmm_open" in action.aliases

    def test_decrease_position_liquidity_string(self):
        action = RaydiumDecreasePositionAction()
        valid, _ = action.validate_params({"positionId": "pid", "liquidity": "1000000"})
        assert valid
        valid2, _ = action.validate_params({"positionId": "pid", "liquidity": 1000000})
        assert not valid2  # liquidity must be string

    @pytest.mark.asyncio
    async def test_remove_liquidity_execute_type(self):
        action = RaydiumRemoveLiquidityAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"poolId": "pool1", "lpAmount": 500.0}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("raydium_remove_liquidity", p)


# ---------------------------------------------------------------------------
# 11. Marinade Plugin
# ---------------------------------------------------------------------------

class TestMarinadePlugin:
    def setup_method(self):
        self.plugin = MarinadePlugin()

    def test_id(self):
        assert self.plugin.id == "marinade"

    def test_action_count(self):
        assert len(self.plugin.actions) == 3

    def test_stake_valid(self):
        action = MarinadeStakeAction()
        valid, _ = action.validate_params({"amount": 1.0})
        assert valid

    def test_unstake_valid(self):
        action = MarinadeUnstakeAction()
        valid, _ = action.validate_params({"amount": 1.0})
        assert valid

    def test_delayed_unstake_aliases(self):
        action = MarinadeDelayedUnstakeAction()
        assert "marinade_delayed_withdrawal" in action.aliases
        assert "marinade_order_unstake" in action.aliases

    def test_delayed_unstake_missing_amount(self):
        action = MarinadeDelayedUnstakeAction()
        valid, _ = action.validate_params({})
        assert not valid

    @pytest.mark.asyncio
    async def test_delayed_unstake_execute_type(self):
        action = MarinadeDelayedUnstakeAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"amount": 5.0}, make_ctx())
            mock_build.assert_called_once_with("marinade_delayed_unstake", {"amount": 5.0})


# ---------------------------------------------------------------------------
# 12. Magic Eden Plugin
# ---------------------------------------------------------------------------

class TestMagicEdenPlugin:
    def setup_method(self):
        self.plugin = MagicEdenPlugin()

    def test_id(self):
        assert self.plugin.id == "magic_eden"

    def test_action_count(self):
        assert len(self.plugin.actions) == 6

    def test_list_required(self):
        action = MagicEdenListAction()
        for param in ["mintAddress", "price"]:
            p = {"mintAddress": "nftMint", "price": 1.5}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_accept_offer_required(self):
        action = MagicEdenAcceptOfferAction()
        for param in ["mintAddress", "buyer"]:
            p = {"mintAddress": "nft", "buyer": "buyerAddr"}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_cancel_listing_required(self):
        action = MagicEdenCancelListingAction()
        valid, _ = action.validate_params({"mintAddress": "nft"})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    @pytest.mark.asyncio
    async def test_buy_execute_type(self):
        action = MagicEdenBuyAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"mintAddress": "nft", "price": 2.0}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("me_buy", p)


# ---------------------------------------------------------------------------
# 16. Tensor Plugin
# ---------------------------------------------------------------------------

class TestTensorPlugin:
    def setup_method(self):
        self.plugin = TensorPlugin()

    def test_id(self):
        assert self.plugin.id == "tensor"

    def test_action_count(self):
        assert len(self.plugin.actions) == 5

    def test_buy_required(self):
        action = TensorBuyAction()
        for param in ["mint", "maxPrice"]:
            p = {"mint": "nft", "maxPrice": 1.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_buy_alias(self):
        action = TensorBuyAction()
        assert "tensor_purchase" in action.aliases

    def test_make_offer_aliases(self):
        action = TensorMakeOfferAction()
        assert "tensor_bid" in action.aliases
        assert "tensor_offer" in action.aliases

    def test_list_required(self):
        action = TensorListAction()
        for param in ["mint", "price"]:
            p = {"mint": "nft", "price": 1.5}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    @pytest.mark.asyncio
    async def test_buy_execute_type(self):
        action = TensorBuyAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"mint": "nft", "maxPrice": 5.0}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("tensor_buy", p)

    @pytest.mark.asyncio
    async def test_cancel_listing_execute_type(self):
        action = TensorCancelListingAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"mint": "nft"}, make_ctx())
            mock_build.assert_called_once_with("tensor_cancel_listing", {"mint": "nft"})

    @pytest.mark.asyncio
    async def test_cancel_offer_execute_type(self):
        action = TensorCancelOfferAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"mint": "nft"}, make_ctx())
            mock_build.assert_called_once_with("tensor_cancel_offer", {"mint": "nft"})


# ---------------------------------------------------------------------------
# 17. Jupiter Plugin (DCA + Limit Orders)
# CRITICAL: action names in plugin differ from action_type sent to builder.rs
# ---------------------------------------------------------------------------

class TestJupiterPlugin:
    def setup_method(self):
        self.plugin = JupiterPlugin()

    def test_id(self):
        assert self.plugin.id == "jupiter"

    def test_action_count(self):
        assert len(self.plugin.actions) == 4

    def test_dca_plugin_name_vs_builder_type(self):
        # Plugin-facing name is "jupiter_dca" but builder receives "dca"
        action = JupiterDCAAction()
        assert action.name == "jupiter_dca"

    def test_dca_required_params(self):
        action = JupiterDCAAction()
        for param in ["inputMint", "outputMint", "totalAmount", "numberOfOrders", "intervalSeconds"]:
            p = {
                "inputMint": "USDC", "outputMint": "SOL",
                "totalAmount": 1000.0, "numberOfOrders": 10, "intervalSeconds": 86400
            }
            del p[param]
            valid, err = action.validate_params(p)
            assert not valid, f"Should fail without {param}"

    def test_dca_aliases(self):
        action = JupiterDCAAction()
        assert "dca" in action.aliases
        assert "jup_dca" in action.aliases
        assert "dca_order" in action.aliases

    @pytest.mark.asyncio
    async def test_dca_sends_dca_not_jupiter_dca(self):
        """CRITICAL: builder.rs expects 'dca', not 'jupiter_dca'"""
        action = JupiterDCAAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {
                "inputMint": "USDC", "outputMint": "SOL",
                "totalAmount": 1000.0, "numberOfOrders": 10, "intervalSeconds": 86400
            }
            await action.execute(p, make_ctx())
            # Must send "dca" not "jupiter_dca"
            assert mock_build.call_args[0][0] == "dca"

    def test_cancel_dca_required(self):
        action = JupiterCancelDCAAction()
        valid, _ = action.validate_params({"order": "dcaAcctAddr"})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    @pytest.mark.asyncio
    async def test_cancel_dca_sends_cancel_dca(self):
        """CRITICAL: builder.rs expects 'cancel_dca'"""
        action = JupiterCancelDCAAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"order": "dcaAddr"}, make_ctx())
            assert mock_build.call_args[0][0] == "cancel_dca"

    def test_limit_order_required(self):
        action = JupiterLimitOrderAction()
        for param in ["inputMint", "outputMint", "amount", "targetPrice"]:
            p = {"inputMint": "SOL", "outputMint": "USDC", "amount": 1.0, "targetPrice": 200.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_limit_order_aliases(self):
        action = JupiterLimitOrderAction()
        assert "limit_order" in action.aliases

    @pytest.mark.asyncio
    async def test_limit_order_sends_limit_order(self):
        """CRITICAL: builder.rs expects 'limit_order'"""
        action = JupiterLimitOrderAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"inputMint": "SOL", "outputMint": "USDC", "amount": 1.0, "targetPrice": 200.0}
            await action.execute(p, make_ctx())
            assert mock_build.call_args[0][0] == "limit_order"

    def test_cancel_limit_order_no_required(self):
        action = JupiterCancelLimitOrderAction()
        valid, _ = action.validate_params({})  # order is optional
        assert valid

    @pytest.mark.asyncio
    async def test_cancel_limit_order_sends_cancel_limit_order(self):
        """CRITICAL: builder.rs expects 'cancel_limit_order'"""
        action = JupiterCancelLimitOrderAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({}, make_ctx())
            assert mock_build.call_args[0][0] == "cancel_limit_order"


# ---------------------------------------------------------------------------
# 18. PumpFun Plugin
# ---------------------------------------------------------------------------

class TestPumpFunPlugin:
    def setup_method(self):
        self.plugin = PumpFunPlugin()

    def test_id(self):
        assert self.plugin.id == "pumpfun"

    def test_action_count(self):
        assert len(self.plugin.actions) == 3

    def test_launch_required(self):
        action = PumpFunLaunchAction()
        for param in ["name", "symbol", "description", "imageUrl"]:
            p = {"name": "Token", "symbol": "TKN", "description": "desc", "imageUrl": "url"}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_launch_aliases(self):
        action = PumpFunLaunchAction()
        assert "launch_token" in action.aliases
        assert "create_token" in action.aliases

    @pytest.mark.asyncio
    async def test_launch_sends_launch_token(self):
        """PumpFun launch sends 'launch_token', not 'pumpfun_launch'"""
        action = PumpFunLaunchAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"name": "Token", "symbol": "TKN", "description": "desc", "imageUrl": "http://img"}
            await action.execute(p, make_ctx())
            assert mock_build.call_args[0][0] == "launch_token"

    def test_buy_required(self):
        action = PumpFunBuyAction()
        valid, _ = action.validate_params({"mint": "mnt", "solAmount": 0.1})
        assert valid
        valid2, _ = action.validate_params({"mint": "mnt"})
        assert not valid2

    @pytest.mark.asyncio
    async def test_buy_execute_type(self):
        action = PumpFunBuyAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"mint": "mnt", "solAmount": 0.5}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("pumpfun_buy", p)

    @pytest.mark.asyncio
    async def test_sell_execute_type(self):
        action = PumpFunSellAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"mint": "mnt", "tokenAmount": 1000.0}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("pumpfun_sell", p)


# ---------------------------------------------------------------------------
# 19. JupSOL Plugin
# ---------------------------------------------------------------------------

class TestJupSolPlugin:
    def setup_method(self):
        self.plugin = JupSolPlugin()

    def test_id(self):
        assert self.plugin.id == "jupsol"

    def test_action_count(self):
        assert len(self.plugin.actions) == 2

    def test_stake_aliases(self):
        action = JupSolStakeAction()
        assert "stake_jupsol" in action.aliases
        assert "jup_stake" in action.aliases

    def test_unstake_aliases(self):
        action = JupSolUnstakeAction()
        assert "unstake_jupsol" in action.aliases

    def test_stake_required(self):
        action = JupSolStakeAction()
        valid, _ = action.validate_params({"amount": 1.0})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    @pytest.mark.asyncio
    async def test_stake_execute_type(self):
        action = JupSolStakeAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"amount": 3.0}, make_ctx())
            mock_build.assert_called_once_with("jupsol_stake", {"amount": 3.0})

    @pytest.mark.asyncio
    async def test_unstake_execute_type(self):
        action = JupSolUnstakeAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"amount": 2.0}, make_ctx())
            mock_build.assert_called_once_with("jupsol_unstake", {"amount": 2.0})


# ---------------------------------------------------------------------------
# 20. Token Utility Plugin
# ---------------------------------------------------------------------------

class TestTokenUtilityPlugin:
    def setup_method(self):
        self.plugin = TokenUtilityPlugin()

    def test_id(self):
        assert self.plugin.id == "token_utility"

    def test_action_count(self):
        assert len(self.plugin.actions) == 2

    def test_burn_required(self):
        action = TokenBurnAction()
        for param in ["mint", "amount"]:
            p = {"mint": "BONK", "amount": "all"}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_burn_amount_is_string(self):
        action = TokenBurnAction()
        # amount is typed "string" — can be "all" or numeric string
        valid, _ = action.validate_params({"mint": "BONK", "amount": "all"})
        assert valid
        # passing a number should fail type check
        valid2, err = action.validate_params({"mint": "BONK", "amount": 1000})
        assert not valid2

    def test_burn_aliases(self):
        action = TokenBurnAction()
        assert "burn_token" in action.aliases
        assert "token_burn" in action.aliases

    def test_transfer_required(self):
        action = TokenTransferAction()
        for param in ["to", "amount", "token"]:
            p = {"to": "walletAddr", "amount": 1.0, "token": "SOL"}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_transfer_aliases(self):
        action = TokenTransferAction()
        assert "send" in action.aliases
        assert "send_token" in action.aliases
        assert "send_sol" in action.aliases

    @pytest.mark.asyncio
    async def test_burn_execute_type(self):
        action = TokenBurnAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"mint": "BONK", "amount": "all"}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("burn", p)

    @pytest.mark.asyncio
    async def test_transfer_execute_type(self):
        action = TokenTransferAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"to": "wallet", "amount": 1.0, "token": "SOL"}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("transfer", p)


# ---------------------------------------------------------------------------
# 21. Jupiter Lend Plugin
# ---------------------------------------------------------------------------

class TestJupiterLendPlugin:
    def setup_method(self):
        self.plugin = JupiterLendPlugin()

    def test_id(self):
        assert self.plugin.id == "jupiter_lend"

    def test_action_count(self):
        assert len(self.plugin.actions) == 4

    def test_deposit_required(self):
        action = JupiterLendDepositAction()
        for param in ["token", "amount"]:
            p = {"token": "USDC", "amount": 100.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_deposit_aliases(self):
        action = JupiterLendDepositAction()
        assert "jup_lend" in action.aliases
        assert "jup_earn" in action.aliases

    @pytest.mark.asyncio
    async def test_deposit_sends_lend(self):
        action = JupiterLendDepositAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"token": "USDC", "amount": 100.0}
            await action.execute(dict(p), make_ctx())
            assert mock_build.call_args[0][0] == "lend"

    @pytest.mark.asyncio
    async def test_withdraw_sends_withdraw_lend(self):
        action = JupiterLendWithdrawAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"token": "USDC", "amount": 50.0}, make_ctx())
            assert mock_build.call_args[0][0] == "withdraw_lend"

    @pytest.mark.asyncio
    async def test_borrow_sends_borrow(self):
        action = JupiterBorrowAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"token": "USDC", "amount": 50.0}, make_ctx())
            assert mock_build.call_args[0][0] == "borrow"

    @pytest.mark.asyncio
    async def test_repay_sends_repay(self):
        action = JupiterRepayAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"token": "USDC", "amount": 50.0}, make_ctx())
            assert mock_build.call_args[0][0] == "repay"


# ---------------------------------------------------------------------------
# 22. Jupiter Perp Plugin
# ---------------------------------------------------------------------------

class TestJupiterPerpPlugin:
    def setup_method(self):
        self.plugin = JupiterPerpPlugin()

    def test_id(self):
        assert self.plugin.id == "jupiter_perp"

    def test_action_count(self):
        assert len(self.plugin.actions) == 4

    def test_open_required(self):
        action = JupiterPerpOpenAction()
        for param in ["market", "side", "collateralAmount"]:
            p = {"market": "SOL", "side": "long", "collateralAmount": 100.0}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    def test_open_aliases(self):
        action = JupiterPerpOpenAction()
        assert "jup_perp_long" in action.aliases
        assert "jup_perp_short" in action.aliases

    def test_jlp_add_required(self):
        action = JupiterJlpAddAction()
        valid, _ = action.validate_params({"amount": 1000.0})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    def test_jlp_aliases(self):
        action = JupiterJlpAddAction()
        assert "jlp_add" in action.aliases

    @pytest.mark.asyncio
    async def test_open_sends_perp_open(self):
        action = JupiterPerpOpenAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"market": "SOL", "side": "long", "collateralAmount": 100.0}
            await action.execute(dict(p), make_ctx())
            assert mock_build.call_args[0][0] == "perp_open"

    @pytest.mark.asyncio
    async def test_close_sends_perp_close(self):
        action = JupiterPerpCloseAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"market": "SOL", "side": "long", "collateralAmount": 100.0}
            await action.execute(dict(p), make_ctx())
            assert mock_build.call_args[0][0] == "perp_close"

    @pytest.mark.asyncio
    async def test_jlp_add_sends_jlp_add(self):
        action = JupiterJlpAddAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"amount": 1000.0}, make_ctx())
            assert mock_build.call_args[0][0] == "jlp_add"

    @pytest.mark.asyncio
    async def test_jlp_remove_sends_jlp_remove(self):
        action = JupiterJlpRemoveAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"amount": 500.0}, make_ctx())
            assert mock_build.call_args[0][0] == "jlp_remove"


# ---------------------------------------------------------------------------
# 23. Squid Plugin — Cross-Chain Routing
# CRITICAL: sends action_type="cross_chain_swap" + provider="squid"
# ---------------------------------------------------------------------------

class TestSquidPlugin:
    def setup_method(self):
        self.plugin = SquidPlugin()

    def test_id(self):
        assert self.plugin.id == "squid"

    def test_action_count(self):
        assert len(self.plugin.actions) == 1

    def test_bridge_required_params(self):
        action = SquidBridgeAction()
        required = {
            "originChainId": 7565164,
            "destinationChainId": 1,
            "originToken": "SOL",
            "destinationToken": "ETH",
            "amount": 1.0,
        }
        for param in required:
            p = dict(required)
            del p[param]
            valid, err = action.validate_params(p)
            assert not valid, f"Should fail without {param}"

    def test_bridge_chain_ids_are_numbers(self):
        action = SquidBridgeAction()
        valid, err = action.validate_params({
            "originChainId": "7565164",  # wrong type
            "destinationChainId": 1,
            "originToken": "SOL",
            "destinationToken": "ETH",
            "amount": 1.0,
        })
        assert not valid
        assert "number" in err

    def test_bridge_aliases(self):
        action = SquidBridgeAction()
        assert "squid_swap" in action.aliases
        assert "squid_cross_chain" in action.aliases

    def test_bridge_valid_with_optional(self):
        action = SquidBridgeAction()
        valid, _ = action.validate_params({
            "originChainId": 7565164,
            "destinationChainId": 1,
            "originToken": "SOL",
            "destinationToken": "ETH",
            "amount": 1.5,
            "recipient": "0xABC",
            "slippage": 0.5,
        })
        assert valid

    @pytest.mark.asyncio
    async def test_bridge_sends_cross_chain_swap_with_provider_squid(self):
        """CRITICAL: action_type must be 'cross_chain_swap' and params must include provider='squid'"""
        action = SquidBridgeAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {
                "originChainId": 7565164,
                "destinationChainId": 1,
                "originToken": "SOL",
                "destinationToken": "ETH",
                "amount": 1.0,
            }
            await action.execute(dict(p), make_ctx())
            # action_type must be "cross_chain_swap"
            assert mock_build.call_args[0][0] == "cross_chain_swap"
            # params must have provider="squid"
            sent_params = mock_build.call_args[0][1]
            assert sent_params.get("provider") == "squid"

    @pytest.mark.asyncio
    async def test_bridge_original_params_preserved(self):
        """All original params must be forwarded alongside provider"""
        action = SquidBridgeAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {
                "originChainId": 42161,  # Arbitrum
                "destinationChainId": 7565164,  # Solana
                "originToken": "USDC",
                "destinationToken": "USDC",
                "amount": 500.0,
                "slippage": 1.0,
            }
            await action.execute(dict(p), make_ctx())
            sent_params = mock_build.call_args[0][1]
            assert sent_params["originChainId"] == 42161
            assert sent_params["destinationChainId"] == 7565164
            assert sent_params["originToken"] == "USDC"
            assert sent_params["amount"] == 500.0
            assert sent_params["provider"] == "squid"


# ---------------------------------------------------------------------------
# 24. Streamflow Plugin
# ---------------------------------------------------------------------------

class TestStreamflowPlugin:
    def setup_method(self):
        self.plugin = StreamflowPlugin()

    def test_id(self):
        assert self.plugin.id == "streamflow"

    def test_action_count(self):
        assert len(self.plugin.actions) == 9

    def test_action_names(self):
        names = {a.name for a in self.plugin.actions}
        assert names == {
            "streamflow_create", "streamflow_create_multiple", "streamflow_cancel",
            "streamflow_withdraw", "streamflow_transfer", "streamflow_topup",
            "streamflow_update", "streamflow_get_one", "streamflow_list",
        }

    # ── Create ────────────────────────────────────────────────────────────────

    def test_create_required_params(self):
        action = StreamflowCreateStreamAction()
        required = {
            "recipient": "walletAddr",
            "mint": "USDC",
            "amount": 1000.0,
            "period": 86400,
            "amountPerPeriod": 100.0,
        }
        for param in required:
            p = dict(required)
            del p[param]
            valid, err = action.validate_params(p)
            assert not valid, f"Should fail without {param}"

    def test_create_aliases(self):
        action = StreamflowCreateStreamAction()
        assert "stream_create" in action.aliases
        assert "create_vesting" in action.aliases
        assert "vesting_create" in action.aliases

    def test_create_with_all_optional(self):
        action = StreamflowCreateStreamAction()
        valid, _ = action.validate_params({
            "recipient": "walletAddr",
            "mint": "USDC",
            "amount": 1000.0,
            "period": 86400,
            "amountPerPeriod": 100.0,
            "start": 1700000000,
            "cliff": 1700000000,
            "cliffAmount": 200.0,
            "name": "Employee Vesting",
            "canTopup": True,
            "cancelableBySender": True,
            "cancelableByRecipient": False,
            "transferableBySender": True,
            "transferableByRecipient": False,
            "automaticWithdrawal": True,
            "withdrawalFrequency": 86400,
            "partner": "partnerWallet",
            "isNative": False,
        })
        assert valid

    # ── Create Multiple ───────────────────────────────────────────────────────

    def test_create_multiple_required_params(self):
        action = StreamflowCreateMultipleAction()
        base = {
            "recipients": [{"recipient": "w1", "amount": "1000", "amountPerPeriod": "100"}],
            "mint": "USDC",
            "period": 86400,
        }
        for param in ["recipients", "mint", "period"]:
            p = dict(base)
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid, f"Should fail without {param}"

    def test_create_multiple_aliases(self):
        action = StreamflowCreateMultipleAction()
        assert "batch_stream" in action.aliases
        assert "bulk_vesting" in action.aliases

    # ── Cancel ────────────────────────────────────────────────────────────────

    def test_cancel_required(self):
        action = StreamflowCancelStreamAction()
        valid, _ = action.validate_params({"streamId": "streamAcct"})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    def test_cancel_aliases(self):
        action = StreamflowCancelStreamAction()
        assert "stream_cancel" in action.aliases
        assert "cancel_stream" in action.aliases

    # ── Withdraw ──────────────────────────────────────────────────────────────

    def test_withdraw_stream_id_required(self):
        action = StreamflowWithdrawAction()
        valid, _ = action.validate_params({"streamId": "strmAddr"})
        assert valid  # amount is optional
        valid2, _ = action.validate_params({})
        assert not valid2

    def test_withdraw_aliases(self):
        action = StreamflowWithdrawAction()
        assert "claim_stream" in action.aliases

    # ── Transfer ──────────────────────────────────────────────────────────────

    def test_transfer_required(self):
        action = StreamflowTransferStreamAction()
        for param in ["streamId", "newRecipient"]:
            p = {"streamId": "strmAddr", "newRecipient": "newWallet"}
            del p[param]
            valid, _ = action.validate_params(p)
            assert not valid

    # ── Topup ─────────────────────────────────────────────────────────────────

    def test_topup_required(self):
        action = StreamflowTopupAction()
        valid, _ = action.validate_params({"streamId": "str", "amount": 500.0})
        assert valid
        valid2, _ = action.validate_params({"streamId": "str"})
        assert not valid2  # amount required

    def test_topup_aliases(self):
        action = StreamflowTopupAction()
        assert "extend_stream" in action.aliases

    # ── Update ────────────────────────────────────────────────────────────────

    def test_update_stream_id_required(self):
        action = StreamflowUpdateAction()
        valid, _ = action.validate_params({"streamId": "str", "automaticWithdrawal": True})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    def test_update_all_optional_params(self):
        action = StreamflowUpdateAction()
        valid, _ = action.validate_params({
            "streamId": "str",
            "automaticWithdrawal": True,
            "withdrawalFrequency": 604800,
            "transferableBySender": False,
            "transferableByRecipient": True,
        })
        assert valid

    # ── Get One ───────────────────────────────────────────────────────────────

    def test_get_one_required(self):
        action = StreamflowGetOneAction()
        valid, _ = action.validate_params({"streamId": "str"})
        assert valid
        valid2, _ = action.validate_params({})
        assert not valid2

    def test_get_one_aliases(self):
        action = StreamflowGetOneAction()
        assert "stream_info" in action.aliases
        assert "stream_details" in action.aliases

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_no_params_needed(self):
        action = StreamflowListAction()
        valid, _ = action.validate_params({})
        assert valid

    def test_list_aliases(self):
        action = StreamflowListAction()
        assert "streamflow_streams" in action.aliases
        assert "my_streams" in action.aliases

    # ── Execute dispatch ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_execute_type(self):
        action = StreamflowCreateStreamAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"recipient": "w", "mint": "USDC", "amount": 100.0, "period": 86400, "amountPerPeriod": 10.0}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("streamflow_create", p)

    @pytest.mark.asyncio
    async def test_create_multiple_execute_type(self):
        action = StreamflowCreateMultipleAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"recipients": [{"recipient": "w1", "amount": "100", "amountPerPeriod": "10"}], "mint": "USDC", "period": 86400}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("streamflow_create_multiple", p)

    @pytest.mark.asyncio
    async def test_cancel_execute_type(self):
        action = StreamflowCancelStreamAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"streamId": "str"}, make_ctx())
            mock_build.assert_called_once_with("streamflow_cancel", {"streamId": "str"})

    @pytest.mark.asyncio
    async def test_withdraw_execute_type(self):
        action = StreamflowWithdrawAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"streamId": "str"}, make_ctx())
            mock_build.assert_called_once_with("streamflow_withdraw", {"streamId": "str"})

    @pytest.mark.asyncio
    async def test_transfer_execute_type(self):
        action = StreamflowTransferStreamAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"streamId": "str", "newRecipient": "newW"}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("streamflow_transfer", p)

    @pytest.mark.asyncio
    async def test_topup_execute_type(self):
        action = StreamflowTopupAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"streamId": "str", "amount": 500.0}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("streamflow_topup", p)

    @pytest.mark.asyncio
    async def test_update_execute_type(self):
        action = StreamflowUpdateAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            p = {"streamId": "str", "automaticWithdrawal": True}
            await action.execute(p, make_ctx())
            mock_build.assert_called_once_with("streamflow_update", p)

    @pytest.mark.asyncio
    async def test_get_one_execute_type(self):
        action = StreamflowGetOneAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({"streamId": "str"}, make_ctx())
            mock_build.assert_called_once_with("streamflow_get_one", {"streamId": "str"})

    @pytest.mark.asyncio
    async def test_list_execute_type(self):
        action = StreamflowListAction()
        with patch("app.plugins.defi_plugins._build_action", new_callable=AsyncMock) as mock_build:
            mock_build.return_value = ok_result()
            await action.execute({}, make_ctx())
            mock_build.assert_called_once_with("streamflow_list", {})


# ---------------------------------------------------------------------------
# 25. Cross-cutting concerns
# ---------------------------------------------------------------------------

class TestCrossCutting:
    """Tests that validate behaviour across all plugins"""

    def test_all_action_names_unique_per_plugin(self):
        for cls in ALL_PLUGINS:
            plugin = cls()
            names = [a.name for a in plugin.actions]
            assert len(names) == len(set(names)), \
                f"{plugin.name} has duplicate action names: {names}"

    def test_all_actions_have_descriptions(self):
        for cls in ALL_PLUGINS:
            plugin = cls()
            for action in plugin.actions:
                assert action.description, \
                    f"{plugin.name}.{action.name} has empty description"

    def test_validate_params_accepts_extra_keys(self):
        """Extra params not in schema should not cause validation to fail"""
        action = JitoStakeAction()
        valid, _ = action.validate_params({"amount": 1.0, "extraParam": "ignored"})
        assert valid

    def test_plugin_get_action_not_found(self):
        plugin = JitoPlugin()
        action = plugin.get_action("nonexistent")
        assert action is None

    def test_plugin_get_action_by_name(self):
        plugin = MarinadePlugin()
        action = plugin.get_action("marinade_stake")
        assert action is not None

    def test_all_plugins_have_ids_without_spaces(self):
        for cls in ALL_PLUGINS:
            plugin = cls()
            assert " " not in plugin.id, f"Plugin id '{plugin.id}' contains spaces"

    def test_required_params_all_string_or_number(self):
        """All required param types must be valid schema types"""
        valid_types = {"string", "number", "boolean", "array", "object"}
        for cls in ALL_PLUGINS:
            plugin = cls()
            for action in plugin.actions:
                for pname, schema in action.parameters.items():
                    ptype = schema.get("type")
                    assert ptype in valid_types, \
                        f"{plugin.name}.{action.name}.{pname} has invalid type '{ptype}'"

    def test_validate_params_returns_tuple(self):
        action = JitoStakeAction()
        result = action.validate_params({"amount": 1.0})
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)

    def test_squid_is_in_all_plugins(self):
        ids = [cls().id for cls in ALL_PLUGINS]
        assert "squid" in ids

    def test_streamflow_is_in_all_plugins(self):
        ids = [cls().id for cls in ALL_PLUGINS]
        assert "streamflow" in ids

    def test_mayan_not_in_all_plugins(self):
        ids = [cls().id for cls in ALL_PLUGINS]
        assert "mayan" not in ids

    def test_total_action_count(self):
        """Sanity check: total actions across all plugins"""
        total = sum(len(cls().actions) for cls in ALL_PLUGINS)
        # 12+8+2+11+2+15+4+8+3+6+3+2+5+4+3+2+2+4+4+1+4 = 105 (Solend, BonkFun removed)
        assert total >= 101, f"Expected at least 101 actions, got {total}"

    @pytest.mark.asyncio
    async def test_build_action_failure_returns_plugin_result_false(self):
        """When gateway call fails, PluginResult.success should be False"""
        from app.plugins.defi_plugins import _build_action
        with patch("app.plugins.defi_plugins.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post.side_effect = Exception("Connection refused")
            mock_client_cls.return_value = mock_client
            result = await _build_action("jito_stake", {"amount": "1.0"})
            assert result.success is False
            assert "Connection refused" in result.error

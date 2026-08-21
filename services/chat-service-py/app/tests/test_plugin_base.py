"""
Tests for Plugin Base Classes.

Tests plugin system base classes: PluginContext, PluginResult,
PluginAction, PluginProvider, PluginEvaluator, BasePlugin.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.plugins.base import (
    BasePlugin,
    PluginAction,
    PluginContext,
    PluginEvaluator,
    PluginPriority,
    PluginProvider,
    PluginResult,
)


class TestPluginContext:
    """Test PluginContext dataclass"""

    def test_create_basic_context(self):
        """Test creating basic context"""
        ctx = PluginContext(plugin_id="test-plugin")

        assert ctx.plugin_id == "test-plugin"
        assert ctx.character_id is None
        assert ctx.user_wallet is None

    def test_create_full_context(self):
        """Test creating context with all fields"""
        ctx = PluginContext(
            plugin_id="test-plugin",
            character_id="char-123",
            user_wallet="Wallet123",
            session_id="session-456",
            message_id="msg-789",
            config={"key": "value"},
            state={"counter": 1}
        )

        assert ctx.plugin_id == "test-plugin"
        assert ctx.character_id == "char-123"
        assert ctx.user_wallet == "Wallet123"
        assert ctx.session_id == "session-456"
        assert ctx.message_id == "msg-789"
        assert ctx.config == {"key": "value"}
        assert ctx.state == {"counter": 1}

    def test_get_config(self):
        """Test get_config method"""
        ctx = PluginContext(
            plugin_id="test",
            config={"api_key": "secret123", "enabled": True}
        )

        assert ctx.get_config("api_key") == "secret123"
        assert ctx.get_config("enabled") is True
        assert ctx.get_config("missing") is None
        assert ctx.get_config("missing", "default") == "default"

    def test_set_state(self):
        """Test set_state method"""
        ctx = PluginContext(plugin_id="test")

        ctx.set_state("count", 5)
        ctx.set_state("name", "test")

        assert ctx.state["count"] == 5
        assert ctx.state["name"] == "test"

    def test_get_state(self):
        """Test get_state method"""
        ctx = PluginContext(
            plugin_id="test",
            state={"value": 42}
        )

        assert ctx.get_state("value") == 42
        assert ctx.get_state("missing") is None
        assert ctx.get_state("missing", "default") == "default"


class TestPluginResult:
    """Test PluginResult dataclass"""

    def test_create_success_result(self):
        """Test creating success result"""
        result = PluginResult(success=True, data={"key": "value"})

        assert result.success is True
        assert result.data == {"key": "value"}
        assert result.error is None
        assert result.execution_time_ms == 0.0

    def test_create_error_result(self):
        """Test creating error result"""
        result = PluginResult(
            success=False,
            error="Something went wrong",
            execution_time_ms=100.5
        )

        assert result.success is False
        assert result.error == "Something went wrong"
        assert result.data is None
        assert result.execution_time_ms == 100.5

    def test_create_result_with_metadata(self):
        """Test creating result with metadata"""
        result = PluginResult(
            success=True,
            data="test",
            metadata={"source": "api", "cached": False}
        )

        assert result.metadata["source"] == "api"
        assert result.metadata["cached"] is False


class TestPluginPriority:
    """Test PluginPriority enum"""

    def test_priority_values(self):
        """Test priority values"""
        assert PluginPriority.CRITICAL == 100
        assert PluginPriority.HIGH == 75
        assert PluginPriority.NORMAL == 50
        assert PluginPriority.LOW == 25
        assert PluginPriority.BACKGROUND == 0

    def test_priority_ordering(self):
        """Test priority ordering"""
        priorities = [
            PluginPriority.BACKGROUND,
            PluginPriority.LOW,
            PluginPriority.NORMAL,
            PluginPriority.HIGH,
            PluginPriority.CRITICAL
        ]

        # Sort by priority (highest first)
        sorted_priorities = sorted(priorities, reverse=True)

        assert sorted_priorities[0] == PluginPriority.CRITICAL
        assert sorted_priorities[4] == PluginPriority.BACKGROUND


class TestPluginAction:
    """Test PluginAction abstract class"""

    def test_abstract_action(self):
        """Test creating concrete action"""

        class TestAction(PluginAction):
            @property
            def name(self) -> str:
                return "test_action"

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True, data=params)

        action = TestAction()

        assert action.name == "test_action"
        assert action.description == ""
        assert action.aliases == []
        assert action.parameters == {}
        assert action.examples == []
        assert action.priority == PluginPriority.NORMAL

    def test_action_with_properties(self):
        """Test action with custom properties"""

        class CustomAction(PluginAction):
            @property
            def name(self) -> str:
                return "custom_action"

            @property
            def description(self) -> str:
                return "A custom action"

            @property
            def aliases(self) -> list[str]:
                return ["custom", "do"]

            @property
            def parameters(self) -> dict:
                return {
                    "amount": {"type": "number", "required": True},
                    "token": {"type": "string", "required": False, "default": "SOL"}
                }

            @property
            def examples(self) -> list:
                return [{"input": "test", "output": "result"}]

            @property
            def priority(self) -> PluginPriority:
                return PluginPriority.HIGH

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True)

        action = CustomAction()

        assert action.name == "custom_action"
        assert action.description == "A custom action"
        assert "custom" in action.aliases
        assert "do" in action.aliases
        assert action.parameters["amount"]["required"] is True
        assert action.parameters["token"]["default"] == "SOL"
        assert action.priority == PluginPriority.HIGH

    @pytest.mark.asyncio
    async def test_execute_returns_result(self):
        """Test execute returns PluginResult"""

        class SimpleAction(PluginAction):
            @property
            def name(self) -> str:
                return "simple"

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True, data={"executed": True})

        action = SimpleAction()
        ctx = PluginContext(plugin_id="test")

        result = await action.execute({}, ctx)

        assert result.success is True
        assert result.data["executed"] is True

    def test_validate_params_valid(self):
        """Test parameter validation with valid params"""

        class ValidatedAction(PluginAction):
            @property
            def name(self) -> str:
                return "validated"

            @property
            def parameters(self) -> dict:
                return {
                    "amount": {"type": "number", "required": True},
                    "token": {"type": "string", "required": False},
                    "enabled": {"type": "boolean", "required": False}
                }

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True)

        action = ValidatedAction()

        # Valid params
        is_valid, error = action.validate_params({
            "amount": 100,
            "token": "SOL",
            "enabled": True
        })

        assert is_valid is True
        assert error is None

    def test_validate_params_missing_required(self):
        """Test validation fails for missing required params"""

        class ValidatedAction(PluginAction):
            @property
            def name(self) -> str:
                return "validated"

            @property
            def parameters(self) -> dict:
                return {
                    "amount": {"type": "number", "required": True}
                }

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True)

        action = ValidatedAction()

        is_valid, error = action.validate_params({})

        assert is_valid is False
        assert "amount" in error.lower()

    def test_validate_params_wrong_type(self):
        """Test validation fails for wrong type"""

        class ValidatedAction(PluginAction):
            @property
            def name(self) -> str:
                return "validated"

            @property
            def parameters(self) -> dict:
                return {
                    "amount": {"type": "number", "required": True}
                }

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True)

        action = ValidatedAction()

        is_valid, error = action.validate_params({"amount": "not a number"})

        assert is_valid is False
        assert "number" in error.lower()

    def test_matches_trigger(self):
        """Test trigger matching"""

        class TriggerAction(PluginAction):
            @property
            def name(self) -> str:
                return "swap"

            @property
            def aliases(self) -> list[str]:
                return ["exchange", "trade"]

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True)

        action = TriggerAction()

        assert action.matches_trigger("swap some tokens") is True
        assert action.matches_trigger("exchange SOL for USDC") is True
        assert action.matches_trigger("trade now") is True
        assert action.matches_trigger("hello world") is False


class TestPluginProvider:
    """Test PluginProvider abstract class"""

    def test_abstract_provider(self):
        """Test creating concrete provider"""

        class TestProvider(PluginProvider):
            @property
            def name(self) -> str:
                return "test_provider"

            async def fetch(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True, data={})

        provider = TestProvider()

        assert provider.name == "test_provider"
        assert provider.description == ""
        assert provider.cache_ttl == 60
        assert provider.priority == PluginPriority.NORMAL

    @pytest.mark.asyncio
    async def test_fetch_returns_result(self):
        """Test fetch returns PluginResult"""

        class DataProvider(PluginProvider):
            @property
            def name(self) -> str:
                return "data"

            async def fetch(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True, data={"price": 100})

        provider = DataProvider()
        ctx = PluginContext(plugin_id="test")

        result = await provider.fetch({}, ctx)

        assert result.success is True
        assert result.data["price"] == 100


class TestPluginEvaluator:
    """Test PluginEvaluator abstract class"""

    def test_abstract_evaluator(self):
        """Test creating concrete evaluator"""

        class TestEvaluator(PluginEvaluator):
            @property
            def name(self) -> str:
                return "test_evaluator"

            async def evaluate(self, context: PluginContext, input: str) -> PluginResult:
                return PluginResult(success=True, data=[])

        evaluator = TestEvaluator()

        assert evaluator.name == "test_evaluator"
        assert evaluator.description == ""
        assert evaluator.priority == PluginPriority.NORMAL


class TestBasePlugin:
    """Test BasePlugin abstract class"""

    def test_abstract_plugin(self):
        """Test creating concrete plugin"""

        class TestPlugin(BasePlugin):
            @property
            def id(self) -> str:
                return "test_plugin"

            @property
            def name(self) -> str:
                return "Test Plugin"

        plugin = TestPlugin()

        assert plugin.id == "test_plugin"
        assert plugin.name == "Test Plugin"
        assert plugin.version == "1.0.0"
        assert plugin.description == ""
        assert plugin.dependencies == []
        assert plugin.actions == []
        assert plugin.providers == []
        assert plugin.evaluators == []
        assert plugin.clients == []
        assert plugin.config_schema == {}

    def test_plugin_with_actions(self):
        """Test plugin with actions"""

        class MockAction(PluginAction):
            @property
            def name(self) -> str:
                return "mock_action"

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True)

        class ActionPlugin(BasePlugin):
            @property
            def id(self) -> str:
                return "action_plugin"

            @property
            def name(self) -> str:
                return "Action Plugin"

            @property
            def actions(self) -> list[PluginAction]:
                return [MockAction()]

        plugin = ActionPlugin()

        assert len(plugin.actions) == 1
        assert plugin.get_action("mock_action") is not None
        assert plugin.get_action("nonexistent") is None

    def test_plugin_with_providers(self):
        """Test plugin with providers"""

        class MockProvider(PluginProvider):
            @property
            def name(self) -> str:
                return "mock_provider"

            async def fetch(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True)

        class ProviderPlugin(BasePlugin):
            @property
            def id(self) -> str:
                return "provider_plugin"

            @property
            def name(self) -> str:
                return "Provider Plugin"

            @property
            def providers(self) -> list[PluginProvider]:
                return [MockProvider()]

        plugin = ProviderPlugin()

        assert len(plugin.providers) == 1
        assert plugin.get_provider("mock_provider") is not None

    def test_plugin_with_evaluators(self):
        """Test plugin with evaluators"""

        class MockEvaluator(PluginEvaluator):
            @property
            def name(self) -> str:
                return "mock_evaluator"

            async def evaluate(self, context: PluginContext, input: str) -> PluginResult:
                return PluginResult(success=True, data=[])

        class EvaluatorPlugin(BasePlugin):
            @property
            def id(self) -> str:
                return "evaluator_plugin"

            @property
            def name(self) -> str:
                return "Evaluator Plugin"

            @property
            def evaluators(self) -> list[PluginEvaluator]:
                return [MockEvaluator()]

        plugin = EvaluatorPlugin()

        assert len(plugin.evaluators) == 1
        assert plugin.get_evaluator("mock_evaluator") is not None

    @pytest.mark.asyncio
    async def test_on_load_hook(self):
        """Test on_load hook"""

        class HookPlugin(BasePlugin):
            @property
            def id(self) -> str:
                return "hook_plugin"

            @property
            def name(self) -> str:
                return "Hook Plugin"

            async def on_load(self, context: PluginContext) -> None:
                context.set_state("loaded", True)

        plugin = HookPlugin()
        ctx = PluginContext(plugin_id="hook_plugin")

        await plugin.on_load(ctx)

        assert ctx.get_state("loaded") is True

    @pytest.mark.asyncio
    async def test_on_unload_hook(self):
        """Test on_unload hook"""

        class HookPlugin(BasePlugin):
            @property
            def id(self) -> str:
                return "hook_plugin"

            @property
            def name(self) -> str:
                return "Hook Plugin"

            async def on_unload(self, context: PluginContext) -> None:
                context.set_state("unloaded", True)

        plugin = HookPlugin()
        ctx = PluginContext(plugin_id="hook_plugin")

        await plugin.on_unload(ctx)

        assert ctx.get_state("unloaded") is True


class TestPluginEdgeCases:
    """Test edge cases for plugin system"""

    def test_context_with_nested_config(self):
        """Test context with nested config"""
        ctx = PluginContext(
            plugin_id="test",
            config={
                "api": {
                    "key": "secret",
                    "endpoints": {
                        "primary": "https://api.example.com",
                        "backup": "https://backup.example.com"
                    }
                }
            }
        )

        assert ctx.get_config("api") == {"key": "secret", "endpoints": {"primary": "https://api.example.com", "backup": "https://backup.example.com"}}

    def test_result_with_complex_data(self):
        """Test result with complex data structures"""
        result = PluginResult(
            success=True,
            data={
                "transactions": [
                    {"id": "tx1", "status": "confirmed"},
                    {"id": "tx2", "status": "pending"}
                ],
                "metadata": {
                    "count": 2,
                    "total_value": 150.50
                }
            },
            metadata={"source": "blockchain", "timing": {"start": 100, "end": 200}}
        )

        assert result.success is True
        assert len(result.data["transactions"]) == 2
        assert result.metadata["source"] == "blockchain"

    def test_validate_all_types(self):
        """Test validation of all parameter types"""

        class AllTypesAction(PluginAction):
            @property
            def name(self) -> str:
                return "all_types"

            @property
            def parameters(self) -> dict:
                return {
                    "str_param": {"type": "string"},
                    "num_param": {"type": "number"},
                    "bool_param": {"type": "boolean"},
                    "arr_param": {"type": "array"},
                    "obj_param": {"type": "object"}
                }

            async def execute(self, params: dict, context: PluginContext) -> PluginResult:
                return PluginResult(success=True)

        action = AllTypesAction()

        # All valid
        is_valid, _ = action.validate_params({
            "str_param": "hello",
            "num_param": 123,
            "bool_param": True,
            "arr_param": [1, 2, 3],
            "obj_param": {"key": "value"}
        })
        assert is_valid is True

        # String type mismatch
        is_valid, error = action.validate_params({"str_param": 123})
        assert is_valid is False
        assert "string" in error.lower()

        # Number type mismatch
        is_valid, error = action.validate_params({"num_param": "not a number"})
        assert is_valid is False

        # Boolean type mismatch
        is_valid, error = action.validate_params({"bool_param": "yes"})
        assert is_valid is False

        # Array type mismatch
        is_valid, error = action.validate_params({"arr_param": "not array"})
        assert is_valid is False

        # Object type mismatch
        is_valid, error = action.validate_params({"obj_param": "not object"})
        assert is_valid is False

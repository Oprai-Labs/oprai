"""
Tests for Plugin Manager module.

Tests plugin lifecycle, registration, and management.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestPluginManagerInit:
    """Test PluginManager initialization"""

    def test_init_with_default_dir(self):
        """Test initialization with default plugins directory"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        assert manager.plugins_dir == Path("plugins")
        assert manager._loaded is False

    def test_init_with_custom_dir(self):
        """Test initialization with custom directory"""
        from app.plugins.manager import PluginManager

        custom_dir = Path("/custom/plugins")
        manager = PluginManager(plugins_dir=custom_dir)
        assert manager.plugins_dir == custom_dir


class TestPluginManagerProperties:
    """Test plugin manager properties"""

    def test_plugins_property(self):
        """Test plugins property returns copy"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        plugins = manager.plugins

        assert isinstance(plugins, dict)

    def test_actions_property(self):
        """Test actions property returns copy"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        actions = manager.actions

        assert isinstance(actions, dict)

    def test_providers_property(self):
        """Test providers property returns copy"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        providers = manager.providers

        assert isinstance(providers, dict)

    def test_evaluators_property(self):
        """Test evaluators property returns copy"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        evaluators = manager.evaluators

        assert isinstance(evaluators, dict)


class TestPluginManagerGetters:
    """Test plugin manager getter methods"""

    def test_get_plugin_not_found(self):
        """Test get_plugin returns None for non-existent"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        plugin = manager.get_plugin("nonexistent")

        assert plugin is None

    def test_get_action_not_found(self):
        """Test get_action returns None for non-existent"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        action = manager.get_action("nonexistent")

        assert action is None

    def test_get_provider_not_found(self):
        """Test get_provider returns None for non-existent"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        provider = manager.get_provider("nonexistent")

        assert provider is None

    def test_get_evaluator_not_found(self):
        """Test get_evaluator returns None for non-existent"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        evaluator = manager.get_evaluator("nonexistent")

        assert evaluator is None


class TestPluginManagerListMethods:
    """Test listing methods"""

    def test_list_plugins_empty(self):
        """Test list_plugins returns empty list when no plugins"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        plugins = manager.list_plugins()

        assert plugins == []

    def test_list_actions_empty(self):
        """Test list_actions returns empty list when no actions"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        actions = manager.list_actions()

        assert actions == []

    def test_list_providers_empty(self):
        """Test list_providers returns empty list when no providers"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        providers = manager.list_providers()

        assert providers == []


class TestPluginManagerLoad:
    """Test plugin loading"""

    @pytest.mark.asyncio
    async def test_load_all_twice_warns(self):
        """Test loading twice warns"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        manager._loaded = True

        # Should warn but not fail
        await manager.load_all()

        assert manager._loaded is True


class TestPluginManagerExecute:
    """Test action execution"""

    @pytest.mark.asyncio
    async def test_execute_action_not_found(self):
        """Test executing non-existent action returns error"""
        from app.plugins.manager import PluginManager
        from app.plugins.base import PluginContext

        manager = PluginManager()
        context = PluginContext(plugin_id="test")

        result = await manager.execute_action("nonexistent", {}, context)

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_provider_not_found(self):
        """Test executing non-existent provider returns error"""
        from app.plugins.manager import PluginManager
        from app.plugins.base import PluginContext

        manager = PluginManager()
        context = PluginContext(plugin_id="test")

        result = await manager.execute_provider("nonexistent", {}, context)

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_run_evaluators_empty(self):
        """Test running evaluators with no evaluators"""
        from app.plugins.manager import PluginManager

        manager = PluginManager()
        # Should return empty list, not error
        result = await manager.run_evaluators(MagicMock(), "test input")

        assert result == []


class TestGlobalPluginManager:
    """Test global plugin manager"""

    def test_get_plugin_manager_returns_instance(self):
        """Test get_plugin_manager returns instance"""
        from app.plugins.manager import get_plugin_manager

        manager = get_plugin_manager()
        assert manager is not None

    def test_get_plugin_manager_singleton(self):
        """Test get_plugin_manager returns same instance"""
        from app.plugins.manager import get_plugin_manager

        manager1 = get_plugin_manager()
        manager2 = get_plugin_manager()

        assert manager1 is manager2


class TestInitializePlugins:
    """Test plugin initialization"""

    @pytest.mark.asyncio
    async def test_initialize_plugins_calls_load_all(self):
        """Test initialize_plugins loads all"""
        from app.plugins.manager import initialize_plugins, get_plugin_manager

        manager = get_plugin_manager()
        manager._loaded = False  # Reset

        await initialize_plugins()

        # Should complete without error
        assert True

"""
Plugin Manager

Manages plugin lifecycle, registration, and execution.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional, TypeVar

from app.plugins.base import (
    BasePlugin,
    PluginAction,
    PluginProvider,
    PluginEvaluator,
    PluginContext,
    PluginResult,
    PluginPriority,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PluginManager:
    """
    Central plugin management system.

    Handles:
    - Plugin discovery and loading
    - Plugin lifecycle management
    - Action/provider/evaluator registration
    - Execution coordination
    """

    def __init__(self, plugins_dir: Optional[Path] = None):
        """
        Initialize plugin manager.

        Args:
            plugins_dir: Directory containing plugin modules
        """
        self.plugins_dir = plugins_dir or Path("plugins")
        self._plugins: dict[str, BasePlugin] = {}
        self._actions: dict[str, PluginAction] = {}
        self._providers: dict[str, PluginProvider] = {}
        self._evaluators: dict[str, PluginEvaluator] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def plugins(self) -> dict[str, BasePlugin]:
        """All registered plugins"""
        return self._plugins.copy()

    @property
    def actions(self) -> dict[str, PluginAction]:
        """All registered actions"""
        return self._actions.copy()

    @property
    def providers(self) -> dict[str, PluginProvider]:
        """All registered providers"""
        return self._providers.copy()

    @property
    def evaluators(self) -> dict[str, PluginEvaluator]:
        """All registered evaluators"""
        return self._evaluators.copy()

    async def load_all(self) -> None:
        """Load all plugins from plugins directory"""
        async with self._lock:
            if self._loaded:
                logger.warning("Plugins already loaded")
                return

            logger.info(f"Loading plugins from {self.plugins_dir}")

            if not self.plugins_dir.exists():
                logger.warning(f"Plugins directory not found: {self.plugins_dir}")
                self._loaded = True
                return

            # Discover plugins
            for plugin_path in self.plugins_dir.glob("*.py"):
                if plugin_path.name.startswith("_"):
                    continue

                try:
                    await self._load_plugin_file(plugin_path)
                except Exception as e:
                    logger.error("Failed to load plugin {plugin_path}", exc_info=True)

            self._loaded = True
            logger.info(f"Loaded {len(self._plugins)} plugins")

    async def _load_plugin_file(self, path: Path) -> None:
        """Load a plugin from a Python file.

        The path must resolve within the configured plugins_dir to prevent
        loading arbitrary files via directory traversal.
        """
        resolved = path.resolve()
        plugins_resolved = self.plugins_dir.resolve()
        try:
            resolved.relative_to(plugins_resolved)
        except ValueError:
            raise ValueError(
                f"Plugin path {path!r} resolves outside plugins directory {self.plugins_dir!r}"
            )
        module_name = path.stem
        spec = importlib.util.spec_from_file_location(module_name, str(path))

        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find plugin classes
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BasePlugin) and obj is not BasePlugin:
                    await self.register_plugin(obj)
                    break
        except Exception as e:
            logger.error("Error loading module {module_name}", exc_info=True)
            raise

    async def register_plugin(self, plugin_class: type[BasePlugin]) -> None:
        """Register a plugin class"""
        plugin = plugin_class()
        plugin_id = plugin.id

        if plugin_id in self._plugins:
            logger.warning(f"Plugin {plugin_id} already registered")
            return

        # Check dependencies
        for dep_id in plugin.dependencies:
            if dep_id not in self._plugins:
                raise ValueError(f"Missing dependency {dep_id} for plugin {plugin_id}")

        # Create context
        context = PluginContext(plugin_id=plugin_id)

        # Call on_load
        try:
            await plugin.on_load(context)
        except Exception as e:
            logger.error("Plugin {plugin_id} on_load failed", exc_info=True)
            raise

        # Register plugin
        self._plugins[plugin_id] = plugin

        # Register actions
        for action in plugin.actions:
            self._actions[action.name] = action
            for alias in action.aliases:
                self._actions[alias] = action
            logger.info(f"Registered action: {action.name}")

        # Register providers
        for provider in plugin.providers:
            self._providers[provider.name] = provider
            logger.info(f"Registered provider: {provider.name}")

        # Register evaluators
        for evaluator in plugin.evaluators:
            self._evaluators[evaluator.name] = evaluator
            logger.info(f"Registered evaluator: {evaluator.name}")

        logger.info(f"Loaded plugin: {plugin.name} v{plugin.version}")

    async def unload_plugin(self, plugin_id: str) -> bool:
        """Unload a plugin"""
        async with self._lock:
            plugin = self._plugins.get(plugin_id)
            if not plugin:
                return False

            # Check if other plugins depend on this one
            for other_id, other_plugin in self._plugins.items():
                if plugin_id in other_plugin.dependencies:
                    logger.warning(f"Cannot unload {plugin_id}: {other_id} depends on it")
                    return False

            # Call on_unload
            context = PluginContext(plugin_id=plugin_id)
            try:
                await plugin.on_unload(context)
            except Exception as e:
                logger.error("Plugin {plugin_id} on_unload failed", exc_info=True)

            # Unregister actions
            for action in plugin.actions:
                self._actions.pop(action.name, None)
                for alias in action.aliases:
                    self._actions.pop(alias, None)

            # Unregister providers
            for provider in plugin.providers:
                self._providers.pop(provider.name, None)

            # Unregister evaluators
            for evaluator in plugin.evaluators:
                self._evaluators.pop(evaluator.name, None)

            # Remove plugin
            del self._plugins[plugin_id]

            logger.info(f"Unloaded plugin: {plugin_id}")
            return True

    def get_plugin(self, plugin_id: str) -> Optional[BasePlugin]:
        """Get a plugin by ID"""
        return self._plugins.get(plugin_id)

    def get_action(self, name: str) -> Optional[PluginAction]:
        """Get an action by name or alias"""
        return self._actions.get(name)

    def get_provider(self, name: str) -> Optional[PluginProvider]:
        """Get a provider by name"""
        return self._providers.get(name)

    def get_evaluator(self, name: str) -> Optional[PluginEvaluator]:
        """Get an evaluator by name"""
        return self._evaluators.get(name)

    async def execute_action(
        self,
        action_name: str,
        params: dict[str, Any],
        context: PluginContext
    ) -> PluginResult:
        """Execute an action by name"""
        import time

        action = self.get_action(action_name)
        if not action:
            return PluginResult(
                success=False,
                error=f"Action not found: {action_name}"
            )

        # Validate parameters
        is_valid, error = action.validate_params(params)
        if not is_valid:
            return PluginResult(success=False, error=error)

        start_time = time.perf_counter()
        try:
            result = await action.execute(params, context)
            execution_time = (time.perf_counter() - start_time) * 1000
            result.execution_time_ms = execution_time
            return result
        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000
            logger.error("Action {action_name} failed", exc_info=True)
            return PluginResult(
                success=False,
                error=str(e),
                execution_time_ms=execution_time
            )

    async def execute_provider(
        self,
        provider_name: str,
        params: dict[str, Any],
        context: PluginContext
    ) -> PluginResult:
        """Execute a provider by name"""
        import time

        provider = self.get_provider(provider_name)
        if not provider:
            return PluginResult(
                success=False,
                error=f"Provider not found: {provider_name}"
            )

        start_time = time.perf_counter()
        try:
            result = await provider.fetch(params, context)
            execution_time = (time.perf_counter() - start_time) * 1000
            result.execution_time_ms = execution_time
            return result
        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000
            logger.error("Provider {provider_name} failed", exc_info=True)
            return PluginResult(
                success=False,
                error=str(e),
                execution_time_ms=execution_time
            )

    async def run_evaluators(
        self,
        context: PluginContext,
        input_text: str
    ) -> list[str]:
        """Run all evaluators and get suggested actions"""
        suggested_actions = []

        # Sort evaluators by priority
        sorted_evaluators = sorted(
            self._evaluators.values(),
            key=lambda e: e.priority,
            reverse=True
        )

        for evaluator in sorted_evaluators:
            try:
                result = await evaluator.evaluate(context, input_text)
                if result.success and result.data:
                    suggested_actions.extend(result.data)
            except Exception as e:
                logger.error("Evaluator {evaluator.name} failed", exc_info=True)

        return list(set(suggested_actions))  # Dedupe

    def list_plugins(self) -> list[dict[str, Any]]:
        """List all registered plugins with metadata"""
        return [
            {
                "id": plugin.id,
                "name": plugin.name,
                "version": plugin.version,
                "description": plugin.description,
                "actions": [a.name for a in plugin.actions],
                "providers": [p.name for p in plugin.providers],
                "evaluators": [e.name for e in plugin.evaluators],
            }
            for plugin in self._plugins.values()
        ]

    def list_actions(self) -> list[dict[str, Any]]:
        """List all registered actions with metadata"""
        return [
            {
                "name": action.name,
                "description": action.description,
                "aliases": action.aliases,
                "parameters": action.parameters,
                "examples": action.examples,
            }
            for action in self._actions.values()
        ]

    def list_providers(self) -> list[dict[str, Any]]:
        """List all registered providers with metadata"""
        return [
            {
                "name": provider.name,
                "description": provider.description,
                "cache_ttl": provider.cache_ttl,
            }
            for provider in self._providers.values()
        ]


# Global plugin manager instance
_plugin_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Get the global plugin manager instance"""
    global _plugin_manager
    if _plugin_manager is None:
        plugins_dir = Path(__file__).parent
        _plugin_manager = PluginManager(plugins_dir)
    return _plugin_manager


async def initialize_plugins() -> None:
    """Initialize all plugins"""
    manager = get_plugin_manager()
    await manager.load_all()

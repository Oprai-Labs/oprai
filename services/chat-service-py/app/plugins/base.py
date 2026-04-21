"""
Plugin System Base Classes

Based on elizaOS plugin architecture.
Provides base classes for creating extensible plugins.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional, TypeVar, Generic

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PluginPriority(int, Enum):
    """Plugin priority levels"""
    CRITICAL = 100  # Always run first
    HIGH = 75
    NORMAL = 50
    LOW = 25
    BACKGROUND = 0   # Run last


@dataclass
class PluginContext:
    """Context passed to plugin methods"""
    plugin_id: str
    character_id: Optional[str] = None
    user_wallet: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    config: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value"""
        return self.config.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Set state value"""
        self.state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get state value"""
        return self.state.get(key, default)


@dataclass
class PluginResult(Generic[T]):
    """Result from plugin execution"""
    success: bool
    data: Optional[T] = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginAction(ABC):
    """
    Base class for plugin actions.
    Actions are discrete operations that can be triggered by the agent.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique action name"""
        pass

    @property
    def description(self) -> str:
        """Action description"""
        return ""

    @property
    def aliases(self) -> list[str]:
        """Alternative names for this action"""
        return []

    @property
    def parameters(self) -> dict[str, dict[str, Any]]:
        """
        Action parameters schema.
        Returns dict of parameter_name -> {type, required, description, default}
        """
        return {}

    @property
    def examples(self) -> list[dict[str, Any]]:
        """Example usages"""
        return []

    @property
    def priority(self) -> PluginPriority:
        """Action priority"""
        return PluginPriority.NORMAL

    @abstractmethod
    async def execute(
        self,
        params: dict[str, Any],
        context: PluginContext
    ) -> PluginResult:
        """Execute the action"""
        pass

    def validate_params(self, params: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate action parameters.

        Returns:
            (is_valid, error_message)
        """
        for param_name, schema in self.parameters.items():
            if schema.get("required", False) and param_name not in params:
                return False, f"Missing required parameter: {param_name}"

            param_type = schema.get("type")
            if param_name in params and param_type:
                value = params[param_name]
                # Basic type checking
                if param_type == "string" and not isinstance(value, str):
                    return False, f"Parameter {param_name} must be a string"
                elif param_type == "number" and not isinstance(value, (int, float)):
                    return False, f"Parameter {param_name} must be a number"
                elif param_type == "boolean" and not isinstance(value, bool):
                    return False, f"Parameter {param_name} must be a boolean"
                elif param_type == "array" and not isinstance(value, list):
                    return False, f"Parameter {param_name} must be an array"
                elif param_type == "object" and not isinstance(value, dict):
                    return False, f"Parameter {param_name} must be an object"

        return True, None

    def matches_trigger(self, text: str) -> bool:
        """
        Check if this action should be triggered by text.
        Override for custom trigger logic.
        """
        text_lower = text.lower()
        return any(alias in text_lower for alias in [self.name] + self.aliases)


class PluginProvider(ABC):
    """
    Base class for plugin providers.
    Providers fetch data from external sources.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider name"""
        pass

    @property
    def description(self) -> str:
        """Provider description"""
        return ""

    @property
    def cache_ttl(self) -> int:
        """Cache time-to-live in seconds"""
        return 60

    @property
    def priority(self) -> PluginPriority:
        """Provider priority"""
        return PluginPriority.NORMAL

    @abstractmethod
    async def fetch(
        self,
        params: dict[str, Any],
        context: PluginContext
    ) -> PluginResult:
        """Fetch data from the provider"""
        pass

    async def get(self, key: str, context: PluginContext) -> Any:
        """
        Get cached data or fetch fresh.
        Override for custom caching logic.
        """
        return (await self.fetch({key}, context)).data


class PluginEvaluator(ABC):
    """
    Base class for plugin evaluators.
    Evaluators assess situations and decide what to do.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique evaluator name"""
        pass

    @property
    def description(self) -> str:
        """Evaluator description"""
        return ""

    @property
    def priority(self) -> PluginPriority:
        """Evaluator priority"""
        return PluginPriority.NORMAL

    @abstractmethod
    async def evaluate(
        self,
        context: PluginContext,
        input: str
    ) -> PluginResult[list[str]]:
        """
        Evaluate input and return list of suggested actions.

        Returns:
            PluginResult with list of action names to consider
        """
        pass


class BasePlugin(ABC):
    """
    Base class for all plugins.
    Plugins can provide actions, providers, and evaluators.
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique plugin identifier"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin display name"""
        pass

    @property
    def version(self) -> str:
        """Plugin version"""
        return "1.0.0"

    @property
    def description(self) -> str:
        """Plugin description"""
        return ""

    @property
    def dependencies(self) -> list[str]:
        """Required plugin dependencies"""
        return []

    @property
    def actions(self) -> list[PluginAction]:
        """Actions provided by this plugin"""
        return []

    @property
    def providers(self) -> list[PluginProvider]:
        """Providers provided by this plugin"""
        return []

    @property
    def evaluators(self) -> list[PluginEvaluator]:
        """Evaluators provided by this plugin"""
        return []

    @property
    def clients(self) -> list[str]:
        """Supported client types"""
        return []

    @property
    def config_schema(self) -> dict[str, dict[str, Any]]:
        """Configuration schema"""
        return {}

    async def on_load(self, context: PluginContext) -> None:
        """Called when plugin is loaded"""
        pass

    async def on_unload(self, context: PluginContext) -> None:
        """Called when plugin is unloaded"""
        pass

    async def on_message(self, message: str, context: PluginContext) -> None:
        """Called for each message (optional)"""
        pass

    async def on_action_complete(
        self,
        action: str,
        result: PluginResult,
        context: PluginContext
    ) -> None:
        """Called after an action completes"""
        pass

    def get_action(self, name: str) -> Optional[PluginAction]:
        """Get action by name"""
        for action in self.actions:
            if action.name == name or name in action.aliases:
                return action
        return None

    def get_provider(self, name: str) -> Optional[PluginProvider]:
        """Get provider by name"""
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None

    def get_evaluator(self, name: str) -> Optional[PluginEvaluator]:
        """Get evaluator by name"""
        for evaluator in self.evaluators:
            if evaluator.name == name:
                return evaluator
        return None

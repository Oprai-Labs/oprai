"""
Plugin System for OPRAI

Based on elizaOS plugin architecture.
Provides extensible plugin system for adding custom actions, providers, and evaluators.
"""

from app.plugins.base import (
    BasePlugin,
    PluginAction,
    PluginProvider,
    PluginEvaluator,
    PluginContext,
    PluginResult,
)

__all__ = [
    "BasePlugin",
    "PluginAction",
    "PluginProvider",
    "PluginEvaluator",
    "PluginContext",
    "PluginResult",
]

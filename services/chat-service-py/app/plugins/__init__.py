"""
Plugin System for OPRAI

Based on elizaOS plugin architecture.
Provides extensible plugin system for adding custom actions, providers, and evaluators.
"""

from app.plugins.base import (
    BasePlugin,
    PluginAction,
    PluginContext,
    PluginEvaluator,
    PluginProvider,
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

"""
Template System

Based on elizaOS template architecture.
Provides customizable prompt templates for various tasks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class TemplateContext:
    """Context for template rendering"""
    character_name: str = ""
    bio: str = ""
    lore: str = ""
    topics: str = ""
    adjectives: str = ""
    style: str = ""
    knowledge: str = ""
    conversation: str = ""
    user_facts: str = ""
    goals: str = ""
    message: str = ""
    response: str = ""
    twitter_handle: str = ""
    topic: str = ""
    adjective: str = ""
    max_tweet_length: int = 280
    providers: str = ""
    post_examples: str = ""
    post_directions: str = ""
    custom: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a context value"""
        if hasattr(self, key):
            return getattr(self, key)
        return self.custom.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a context value"""
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            self.custom[key] = value


class PromptTemplate:
    """
    Advanced prompt template with variable interpolation.
    Supports {{variable}} syntax and conditionals.
    """

    def __init__(self, template: str, name: str = ""):
        self.template = template
        self.name = name
        self._compiled = None

    def render(self, context: TemplateContext | dict[str, Any]) -> str:
        """
        Render the template with context.

        Args:
            context: Template context

        Returns:
            Rendered string
        """
        if isinstance(context, dict):
            ctx = TemplateContext()
            for key, value in context.items():
                ctx.set(key, value)
        else:
            ctx = context

        result = self.template

        # Handle each blocks FIRST (before variable replacement)
        each_pattern = r'\{#each\s+(\w+)\s+as\s+(\w+)\}(.*?)\{/each\}'

        def each_replace(match):
            list_key = match.group(1)
            item_name = match.group(2)
            content = match.group(3)
            items = ctx.get(list_key, [])

            if not isinstance(items, (list, tuple)):
                return ""

            results = []
            for item in items:
                item_content = content.replace(f"{{{{{item_name}}}}}", str(item))
                results.append(item_content)

            return "".join(results)

        result = re.sub(each_pattern, each_replace, result, flags=re.DOTALL)

        # Handle conditional blocks
        condition_pattern = r'\{#if\s+(\w+)\}(.*?)\{/if\}'

        def condition_replace(match):
            condition = match.group(1)
            content = match.group(2)
            value = ctx.get(condition)
            if value and value not in (False, "", None, [], {}):
                return content
            return ""

        result = re.sub(condition_pattern, condition_replace, result, flags=re.DOTALL)

        # Replace {{variable}} patterns LAST
        pattern = r'\{\{(\w+)\}\}'

        def replace(match):
            key = match.group(1)
            value = ctx.get(key, "")
            return str(value) if value is not None else ""

        result = re.sub(pattern, replace, result)

        # Clean up extra whitespace
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()


class TemplateManager:
    """
    Manages prompt templates.
    Loads from files and provides default templates.
    """

    def __init__(self, templates_dir: Optional[Path] = None):
        self.templates_dir = templates_dir
        self._templates: dict[str, PromptTemplate] = {}
        self._load_defaults()

        if templates_dir and templates_dir.exists():
            self._load_from_dir(templates_dir)

    def _load_defaults(self) -> None:
        """Load default templates"""
        defaults = {
            "system": DEFAULT_SYSTEM_TEMPLATE,
            "message_handler": DEFAULT_MESSAGE_HANDLER_TEMPLATE,
            "twitter_post": DEFAULT_TWITTER_POST_TEMPLATE,
            "twitter_search": DEFAULT_TWITTER_SEARCH_TEMPLATE,
            "should_respond": DEFAULT_SHOULD_RESPOND_TEMPLATE,
            "evaluation": DEFAULT_EVALUATION_TEMPLATE,
            "goals": DEFAULT_GOALS_TEMPLATE,
            "facts": DEFAULT_FACTS_TEMPLATE,
            "summary": DEFAULT_SUMMARY_TEMPLATE,
        }

        for name, template in defaults.items():
            self._templates[name] = PromptTemplate(template, name)

    def _load_from_dir(self, directory: Path) -> None:
        """Load templates from directory"""
        for file_path in directory.glob("*.yaml"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)

                if isinstance(data, dict):
                    for name, template in data.items():
                        self._templates[name] = PromptTemplate(
                            template,
                            name=f"{file_path.stem}/{name}"
                        )
                        logger.info(f"Loaded template: {name}")

            except Exception as e:
                logger.error("Failed to load template file {file_path}", exc_info=True)

    def get_template(self, name: str) -> Optional[PromptTemplate]:
        """Get a template by name"""
        return self._templates.get(name)

    def register_template(self, name: str, template: str) -> None:
        """Register a new template"""
        self._templates[name] = PromptTemplate(template, name)

    def render(
        self,
        template_name: str,
        context: TemplateContext | dict[str, Any],
    ) -> str:
        """Render a template by name"""
        template = self.get_template(template_name)
        if not template:
            raise ValueError(f"Template not found: {template_name}")

        return template.render(context)

    def list_templates(self) -> list[str]:
        """List all template names"""
        return list(self._templates.keys())


# Default Templates

DEFAULT_SYSTEM_TEMPLATE = """You are {{character_name}}, an AI assistant with the following characteristics:

## About You
{{bio}}

## Background
{{lore}}

## Areas of Expertise
{{topics}}

## Personality Traits
{{adjectives}}

## Communication Style
{{style}}

## Knowledge Base
{{knowledge}}

Remember to stay in character and maintain consistency with your defined personality and expertise.
"""

DEFAULT_MESSAGE_HANDLER_TEMPLATE = """# Character: {{character_name}}

## Bio
{{bio}}

## Lore
{{lore}}

## Topics
{{topics}}

## Style Guidelines
{{style}}

## Recent Conversation
{{conversation}}

## User Information
{{user_facts}}

## Current Goals
{{goals}}

## Task
Respond to the user's message in the voice and style of {{character_name}}. Be helpful, accurate, and maintain your character's personality.
"""

DEFAULT_TWITTER_POST_TEMPLATE = """# Areas of Expertise
{{knowledge}}

# About {{character_name}} (@{{twitter_handle}}):
{{bio}}
{{lore}}
{{topics}}

{{providers}}

{{post_examples}}

{{post_directions}}

# Task: Generate a post in the voice and style and perspective of {{character_name}} @{{twitter_handle}}.
Write a 1-3 sentence post that is {{adjective}} about {{topic}} (without mentioning {{topic}} directly), from the perspective of {{character_name}}. Do not add commentary or acknowledge this request, just write the post.
Your response should not contain any questions. Brief, concise statements only. The total character count MUST be less than {{max_tweet_length}}. No emojis. Use \\n\\n (double spaces) between statements.
"""

DEFAULT_TWITTER_SEARCH_TEMPLATE = """# Character: {{character_name}}

## Bio
{{bio}}

## Search Query
{{search_query}}

## Task
Search for relevant tweets and determine which ones {{character_name}} should engage with.
Consider:
1. Relevance to character's topics of expertise
2. Engagement potential
3. Character alignment

Return a list of tweet IDs to engage with and brief reasoning.
"""

DEFAULT_SHOULD_RESPOND_TEMPLATE = """# Character: {{character_name}}
{{bio}}

# Task
Decide if you should respond to the following message. Consider:
1. Is the message directed at you or relevant to your expertise?
2. Would responding add value to the conversation?
3. Are you being mentioned or asked a question?

# Context
{{conversation}}

# Message
{{message}}

# Response
Reply with only "true" if you should respond, or "false" if you should not.
"""

DEFAULT_EVALUATION_TEMPLATE = """# Character: {{character_name}}

# Response to Evaluate
{{response}}

# Evaluation Criteria
{{criteria}}

# Task
Evaluate the response on a scale of 1-10 for each criterion.
Provide brief feedback for improvement.

# Format
Return JSON: {"scores": {"criterion": score}, "feedback": "string"}
"""

DEFAULT_GOALS_TEMPLATE = """# Current Goals
{{goals}}

# Instructions
Keep these goals in mind while responding. Work towards achieving them when appropriate.
"""

DEFAULT_FACTS_TEMPLATE = """# Known Facts
{{facts}}

# Instructions
Use these facts to provide contextually relevant responses.
"""

DEFAULT_SUMMARY_TEMPLATE = """# Conversation Summary

The following is a summary of a long conversation. Use this context to continue the conversation naturally.

## Summary
{{summary}}

## Key Points
{{key_points}}

## Last Messages
{{last_messages}}

Continue the conversation as {{character_name}}, maintaining context from the summary above.
"""


# Global template manager
_template_manager: Optional[TemplateManager] = None


def get_template_manager() -> TemplateManager:
    """Get the global template manager"""
    global _template_manager
    if _template_manager is None:
        _template_manager = TemplateManager()
    return _template_manager

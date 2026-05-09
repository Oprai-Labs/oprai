"""
Prompt Builder Service

Builds dynamic prompts from character configurations.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Optional

from app.models.character import Character, CharacterStyle

logger = logging.getLogger(__name__)


# Default prompt templates
DEFAULT_SYSTEM_TEMPLATE = """You are {name}, an AI assistant with the following characteristics:

## About You
{bio}

## Background
{lore}

## Areas of Expertise
{topics}

## Personality Traits
{adjectives}

## Communication Style
{style}

## Knowledge Base
{knowledge}

{system_prompt}

Remember to stay in character and maintain consistency with your defined personality and expertise.
"""

DEFAULT_MESSAGE_HANDLER_TEMPLATE = """# Character: {name}

## Bio
{bio}

## Lore
{lore}

## Topics
{topics}

## Style Guidelines
{style}

## Recent Conversation
{conversation}

## User Information
{user_facts}

## Current Goals
{goals}

## Task
Respond to the user's message in the voice and style of {name}. Be helpful, accurate, and maintain your character's personality.
"""

DEFAULT_TWITTER_POST_TEMPLATE = """# Areas of Expertise
{knowledge}

# About {name} (@{twitter_handle}):
{bio}
{lore}
{topics}

{providers}

{character_post_examples}

{post_directions}

# Task: Generate a post in the voice and style and perspective of {name} @{twitter_handle}.
Write a 1-3 sentence post that is {adjective} about {topic} (without mentioning {topic} directly), from the perspective of {name}. Do not add commentary or acknowledge this request, just write the post.
Your response should not contain any questions. Brief, concise statements only. The total character count MUST be less than {max_tweet_length}. No emojis. Use \\n\\n (double spaces) between statements.
"""


class PromptBuilder:
    """Service for building prompts from character configurations"""

    def __init__(self, character: Character):
        """
        Initialize prompt builder.

        Args:
            character: Character configuration
        """
        self.character = character

    def _get_bio(self) -> str:
        """Get bio as string"""
        if isinstance(self.character.bio, list):
            return random.choice(self.character.bio)
        return self.character.bio

    def _get_lore(self) -> str:
        """Get lore as formatted string"""
        if not self.character.lore:
            return ""
        return "\n".join(f"- {item}" for item in self.character.lore)

    def _get_topics(self) -> str:
        """Get topics as formatted string"""
        if not self.character.topics:
            return ""
        return ", ".join(self.character.topics)

    def _get_adjectives(self) -> str:
        """Get adjectives as formatted string"""
        if not self.character.adjectives:
            return ""
        return ", ".join(self.character.adjectives)

    def _get_style(self, context: str = "all") -> str:
        """Get style instructions for context"""
        if not self.character.style:
            return ""

        instructions = list(self.character.style.all)

        if context == "chat" and self.character.style.chat:
            instructions.extend(self.character.style.chat)
        elif context == "post" and self.character.style.post:
            instructions.extend(self.character.style.post)

        return "\n".join(f"- {i}" for i in instructions)

    def _get_knowledge(self) -> str:
        """Get knowledge as formatted string"""
        if not self.character.knowledge:
            return ""
        return "\n\n".join(self.character.knowledge)

    def build_system_prompt(self, **kwargs: Any) -> str:
        """
        Build the system prompt for the character.

        Args:
            **kwargs: Additional template variables

        Returns:
            System prompt string
        """
        template = self.character.system_prompt or DEFAULT_SYSTEM_TEMPLATE

        # If custom template in character.templates, use it
        if self.character.templates and self.character.templates.message_handler_template:
            template = self.character.templates.message_handler_template

        variables = {
            "name": self.character.name,
            "bio": self._get_bio(),
            "lore": self._get_lore(),
            "topics": self._get_topics(),
            "adjectives": self._get_adjectives(),
            "style": self._get_style("all"),
            "knowledge": self._get_knowledge(),
            "system_prompt": self.character.system_prompt or "",
            **kwargs,
        }

        return template.format(**variables)

    def build_message_handler_prompt(
        self,
        conversation: str = "",
        user_facts: str = "",
        goals: str = "",
        **kwargs: Any,
    ) -> str:
        """
        Build the message handler prompt.

        Args:
            conversation: Recent conversation history
            user_facts: Known facts about the user
            goals: Current goals
            **kwargs: Additional template variables

        Returns:
            Message handler prompt string
        """
        template = DEFAULT_MESSAGE_HANDLER_TEMPLATE

        if self.character.templates and self.character.templates.message_handler_template:
            template = self.character.templates.message_handler_template

        variables = {
            "name": self.character.name,
            "bio": self._get_bio(),
            "lore": self._get_lore(),
            "topics": self._get_topics(),
            "style": self._get_style("chat"),
            "conversation": conversation,
            "user_facts": user_facts,
            "goals": goals,
            **kwargs,
        }

        return template.format(**variables)

    def build_twitter_post_prompt(
        self,
        topic: str = "",
        adjective: Optional[str] = None,
        max_tweet_length: int = 280,
        **kwargs: Any,
    ) -> str:
        """
        Build a prompt for generating Twitter posts.

        Args:
            topic: Topic to post about
            adjective: Tone adjective
            max_tweet_length: Maximum tweet length
            **kwargs: Additional template variables

        Returns:
            Twitter post prompt string
        """
        template = DEFAULT_TWITTER_POST_TEMPLATE

        if self.character.templates and self.character.templates.twitter_post_template:
            template = self.character.templates.twitter_post_template

        # Get random post example
        post_examples = ""
        if self.character.post_examples:
            examples = random.sample(
                self.character.post_examples,
                min(3, len(self.character.post_examples))
            )
            post_examples = "\n".join(f"- {ex}" for ex in examples)

        # Get random adjective if not provided
        if not adjective and self.character.adjectives:
            adjective = random.choice(self.character.adjectives)
        adjective = adjective or "engaging"

        # Get post directions from style
        post_directions = ""
        if self.character.style and self.character.style.post:
            post_directions = "\n".join(f"- {d}" for d in self.character.style.post)

        variables = {
            "name": self.character.name,
            "twitter_handle": kwargs.get("twitter_handle", self.character.name.lower().replace(" ", "")),
            "bio": self._get_bio(),
            "lore": self._get_lore(),
            "topics": self._get_topics(),
            "knowledge": self._get_knowledge(),
            "character_post_examples": post_examples,
            "post_directions": post_directions,
            "adjective": adjective,
            "topic": topic or random.choice(self.character.topics) if self.character.topics else "DeFi",
            "max_tweet_length": max_tweet_length,
            "providers": "",  # Placeholder for dynamic providers
            **kwargs,
        }

        return template.format(**variables)

    def build_should_respond_prompt(
        self,
        message: str,
        context: str = "",
        **kwargs: Any,
    ) -> str:
        """
        Build a prompt to decide if character should respond.

        Args:
            message: Incoming message
            context: Conversation context
            **kwargs: Additional template variables

        Returns:
            Should respond prompt string
        """
        template = """# Character: {name}
{bio}

# Task
Decide if you should respond to the following message. Consider:
1. Is the message directed at you or relevant to your expertise?
2. Would responding add value to the conversation?
3. Are you being mentioned or asked a question?

# Context
{context}

# Message
{message}

# Response
Reply with only "true" if you should respond, or "false" if you should not.
"""

        variables = {
            "name": self.character.name,
            "bio": self._get_bio(),
            "message": message,
            "context": context,
            **kwargs,
        }

        return template.format(**variables)

    def build_evaluation_prompt(
        self,
        response: str,
        criteria: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> str:
        """
        Build a prompt to evaluate a response.

        Args:
            response: Response to evaluate
            criteria: Evaluation criteria
            **kwargs: Additional template variables

        Returns:
            Evaluation prompt string
        """
        template = """# Character: {name}

# Response to Evaluate
{response}

# Evaluation Criteria
{criteria}

# Task
Evaluate the response on a scale of 1-10 for each criterion.
Provide brief feedback for improvement.

# Format
Return JSON: {{"scores": {{"criterion": score}}, "feedback": "string"}}
"""

        default_criteria = [
            "Stays in character",
            "Provides accurate information",
            "Is helpful and relevant",
            "Maintains appropriate tone",
        ]

        variables = {
            "name": self.character.name,
            "response": response,
            "criteria": "\n".join(f"- {c}" for c in (criteria or default_criteria)),
            **kwargs,
        }

        return template.format(**variables)

    def build_goals_prompt(self, goals: list[str], **kwargs: Any) -> str:
        """
        Build a prompt that includes current goals.

        Args:
            goals: List of current goals
            **kwargs: Additional template variables

        Returns:
            Goals prompt string
        """
        template = """# Current Goals
{goals}

# Instructions
Keep these goals in mind while responding. Work towards achieving them when appropriate.
"""

        variables = {
            "goals": "\n".join(f"- {goal}" for goal in goals),
            **kwargs,
        }

        return template.format(**variables)

    def build_facts_prompt(self, facts: list[dict[str, Any]], **kwargs: Any) -> str:
        """
        Build a prompt that includes known facts.

        Args:
            facts: List of known facts
            **kwargs: Additional template variables

        Returns:
            Facts prompt string
        """
        template = """# Known Facts
{facts}

# Instructions
Use these facts to provide contextually relevant responses.
"""

        facts_str = ""
        for fact in facts:
            key = fact.get("key", "")
            value = fact.get("value", "")
            facts_str += f"- {key}: {value}\n"

        variables = {
            "facts": facts_str,
            **kwargs,
        }

        return template.format(**variables)

    def get_message_examples_for_context(self, count: int = 3) -> list[dict[str, Any]]:
        """
        Get random message examples for context.

        Args:
            count: Number of examples to return

        Returns:
            List of message examples
        """
        if not self.character.message_examples:
            return []

        flat_examples = []
        for example_group in self.character.message_examples:
            for example in example_group:
                flat_examples.append({
                    "user": example.user,
                    "content": example.content.model_dump(),
                })

        return random.sample(flat_examples, min(count, len(flat_examples)))

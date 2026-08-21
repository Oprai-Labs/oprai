"""
Tests for Character Prompt Builder module.

Tests dynamic prompt generation from character configurations.
"""

from unittest.mock import MagicMock

import pytest

from app.models.character import Character, CharacterStyle, CharacterTemplates


class TestPromptBuilderInit:
    """Test PromptBuilder initialization"""

    def test_initialization_with_character(self):
        """Test initialization stores character"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock(spec=Character)
        builder = PromptBuilder(character)

        assert builder.character == character


class TestGetBio:
    """Test _get_bio method"""

    def test_bio_as_string(self):
        """Test bio as string"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "I am a DeFi assistant"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None
        character.knowledge = None

        builder = PromptBuilder(character)
        result = builder._get_bio()

        assert result == "I am a DeFi assistant"

    def test_bio_as_list(self):
        """Test bio as list returns random choice"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = ["First bio", "Second bio", "Third bio"]
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None
        character.knowledge = None

        builder = PromptBuilder(character)
        result = builder._get_bio()

        assert result in ["First bio", "Second bio", "Third bio"]


class TestGetLore:
    """Test _get_lore method"""

    def test_lore_none(self):
        """Test lore None returns empty string"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None

        builder = PromptBuilder(character)
        result = builder._get_lore()

        assert result == ""

    def test_lore_list(self):
        """Test lore list returns formatted string"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = ["Lore item 1", "Lore item 2", "Lore item 3"]
        character.topics = None
        character.adjectives = None
        character.style = None

        builder = PromptBuilder(character)
        result = builder._get_lore()

        assert "- Lore item 1" in result
        assert "- Lore item 2" in result
        assert "- Lore item 3" in result


class TestGetTopics:
    """Test _get_topics method"""

    def test_topics_none(self):
        """Test topics None returns empty string"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None

        builder = PromptBuilder(character)
        result = builder._get_topics()

        assert result == ""

    def test_topics_list(self):
        """Test topics list returns comma-separated string"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = None
        character.topics = ["DeFi", "Solana", "Trading"]
        character.adjectives = None
        character.style = None

        builder = PromptBuilder(character)
        result = builder._get_topics()

        assert "DeFi" in result
        assert "Solana" in result
        assert "Trading" in result


class TestGetAdjectives:
    """Test _get_adjectives method"""

    def test_adjectives_none(self):
        """Test adjectives None returns empty string"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None

        builder = PromptBuilder(character)
        result = builder._get_adjectives()

        assert result == ""

    def test_adjectives_list(self):
        """Test adjectives list returns comma-separated string"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = None
        character.topics = None
        character.adjectives = ["helpful", "friendly", "professional"]
        character.style = None

        builder = PromptBuilder(character)
        result = builder._get_adjectives()

        assert "helpful" in result
        assert "friendly" in result
        assert "professional" in result


class TestGetStyle:
    """Test _get_style method"""

    def test_style_none(self):
        """Test style None returns empty string"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None

        builder = PromptBuilder(character)
        result = builder._get_style()

        assert result == ""

    def test_style_all_context(self):
        """Test style with all context"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = CharacterStyle(
            all=["Be concise", "Be friendly"],
            chat=["Use emojis"],
            post=["Keep it short"]
        )

        builder = PromptBuilder(character)
        result = builder._get_style("all")

        assert "Be concise" in result
        assert "Be friendly" in result

    def test_style_chat_context(self):
        """Test style with chat context"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.bio = "Test"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = CharacterStyle(
            all=["Be concise"],
            chat=["Use emojis", "Add warmth"],
            post=["Keep it short"]
        )

        builder = PromptBuilder(character)
        result = builder._get_style("chat")

        assert "Use emojis" in result
        assert "Add warmth" in result


class TestBuildSystemPrompt:
    """Test build_system_prompt method"""

    def test_build_system_prompt_basic(self):
        """Test basic system prompt generation"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "TestBot"
        character.bio = "A test bot"
        character.lore = ["Lore 1"]
        character.topics = ["DeFi"]
        character.adjectives = ["helpful"]
        character.style = CharacterStyle(all=["Be concise"])
        character.knowledge = ["Knowledge base"]
        character.system_prompt = None
        character.templates = None

        builder = PromptBuilder(character)
        result = builder.build_system_prompt()

        assert "TestBot" in result
        assert "A test bot" in result
        assert "DeFi" in result
        assert "helpful" in result

    def test_build_system_prompt_with_custom_template(self):
        """Test system prompt with custom template"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "CustomBot"
        character.bio = "Bio"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None
        character.knowledge = None
        character.system_prompt = None
        character.templates = MagicMock()
        character.templates.message_handler_template = "Custom: {name} - {bio}"

        builder = PromptBuilder(character)
        result = builder.build_system_prompt()

        # An author-supplied template no longer REPLACES the scaffold — it is
        # persona material inside it. A character can be made public and
        # duplicated by another wallet, so letting its author own the whole
        # system prompt handed them the assistant.
        assert result.startswith("You are CustomBot")
        assert "<persona>" in result and "</persona>" in result
        assert "Custom: {name} - {bio}" in result          # text survives, verbatim
        assert "Custom: CustomBot" not in result           # but is not the template


class TestBuildMessageHandlerPrompt:
    """Test build_message_handler_prompt method"""

    def test_build_message_handler_basic(self):
        """Test basic message handler prompt"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "ChatBot"
        character.bio = ["Bio 1", "Bio 2"]
        character.lore = ["Lore item"]
        character.topics = ["Solana"]
        character.style = CharacterStyle(all=["Be helpful"], chat=["Add examples"])
        character.templates = None
        character.post_examples = None
        character.adjectives = None
        character.knowledge = None

        builder = PromptBuilder(character)
        result = builder.build_message_handler_prompt(
            conversation="Hello there",
            user_facts="User is a trader",
            goals="Help with DeFi"
        )

        assert "ChatBot" in result
        assert "Hello there" in result
        assert "User is a trader" in result

    def test_build_message_handler_empty_params(self):
        """Test message handler with empty params"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "Bot"
        character.bio = "Bio"
        character.lore = None
        character.topics = None
        character.style = None
        character.templates = None
        character.post_examples = None
        character.adjectives = None
        character.knowledge = None

        builder = PromptBuilder(character)
        result = builder.build_message_handler_prompt()

        assert "Bot" in result


class TestBuildTwitterPostPrompt:
    """Test build_twitter_post_prompt method"""

    def test_build_twitter_post_basic(self):
        """Test basic twitter post prompt"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "TwitterBot"
        character.bio = "A Twitter bot"
        character.lore = ["Lore"]
        character.topics = ["DeFi", "Crypto"]
        character.adjectives = ["funny", "informative"]
        character.style = CharacterStyle(all=["Be brief"], post=["No emojis"])
        character.post_examples = ["Post 1", "Post 2", "Post 3", "Post 4"]
        character.knowledge = ["Info"]
        character.templates = None

        builder = PromptBuilder(character)
        result = builder.build_twitter_post_prompt(
            topic="Solana",
            adjective="funny"
        )

        assert "TwitterBot" in result
        assert "Solana" in result
        assert "funny" in result

    def test_build_twitter_post_default_topic(self):
        """Test twitter post with default topic"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "Bot"
        character.bio = "Bio"
        character.lore = None
        character.topics = ["Trading", "Staking"]
        character.adjectives = ["cool"]
        character.style = None
        character.post_examples = None
        character.knowledge = None
        character.templates = None

        builder = PromptBuilder(character)
        result = builder.build_twitter_post_prompt()

        # Should pick random topic
        assert "Bot" in result

    def test_build_twitter_post_custom_length(self):
        """Test twitter post with custom length"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "Bot"
        character.bio = "Bio"
        character.lore = None
        character.topics = ["Test"]
        character.adjectives = ["test"]
        character.style = None
        character.post_examples = None
        character.knowledge = None
        character.templates = None

        builder = PromptBuilder(character)
        result = builder.build_twitter_post_prompt(max_tweet_length=140)

        assert "140" in result


class TestBuildShouldRespondPrompt:
    """Test build_should_respond_prompt method"""

    def test_build_should_respond_basic(self):
        """Test basic should respond prompt"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "Bot"
        character.bio = ["Bio"]

        builder = PromptBuilder(character)
        result = builder.build_should_respond_prompt(
            message="Hello bot",
            context="General chat"
        )

        assert "Bot" in result
        assert "Hello bot" in result
        assert "General chat" in result


class TestBuildEvaluationPrompt:
    """Test build_evaluation_prompt method"""

    def test_build_evaluation_basic(self):
        """Test basic evaluation prompt"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "Bot"

        builder = PromptBuilder(character)
        result = builder.build_evaluation_prompt(
            response="This is a response"
        )

        assert "Bot" in result
        assert "This is a response" in result

    def test_build_evaluation_custom_criteria(self):
        """Test evaluation with custom criteria"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "Bot"

        builder = PromptBuilder(character)
        result = builder.build_evaluation_prompt(
            response="Response",
            criteria=["Accuracy", "Helpfulness"]
        )

        assert "Accuracy" in result
        assert "Helpfulness" in result


class TestBuildGoalsPrompt:
    """Test build_goals_prompt method"""

    def test_build_goals_basic(self):
        """Test basic goals prompt"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        builder = PromptBuilder(character)

        result = builder.build_goals_prompt(
            goals=["Goal 1", "Goal 2", "Goal 3"]
        )

        assert "Goal 1" in result
        assert "Goal 2" in result
        assert "Goal 3" in result

    def test_build_goals_empty(self):
        """Test goals with empty list"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        builder = PromptBuilder(character)

        result = builder.build_goals_prompt(goals=[])

        assert isinstance(result, str)


class TestBuildFactsPrompt:
    """Test build_facts_prompt method"""

    def test_build_facts_basic(self):
        """Test basic facts prompt"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        builder = PromptBuilder(character)

        facts = [
            {"key": "wallet", "value": "Wallet123"},
            {"key": "tier", "value": "Premium"}
        ]

        result = builder.build_facts_prompt(facts=facts)

        assert "wallet" in result
        assert "Wallet123" in result
        assert "tier" in result

    def test_build_facts_partial(self):
        """Test facts with partial data"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        builder = PromptBuilder(character)

        facts = [
            {"key": "name"},
            {"value": "only value"}
        ]

        result = builder.build_facts_prompt(facts=facts)

        assert isinstance(result, str)


class TestGetMessageExamples:
    """Test get_message_examples_for_context method"""

    def test_get_examples_no_examples(self):
        """Test with no message examples"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.message_examples = None

        builder = PromptBuilder(character)
        result = builder.get_message_examples_for_context()

        assert result == []

    def test_get_examples_with_count(self):
        """Test getting examples with count"""
        from app.models.character import MessageContent, MessageExample
        from app.services.character.prompt_builder import PromptBuilder

        content1 = MessageExample(
            user="User1",
            content=MessageContent(text="Hello")
        )
        content2 = MessageExample(
            user="User2",
            content=MessageContent(text="Hi there")
        )
        content3 = MessageExample(
            user="User3",
            content=MessageContent(text="Hey")
        )
        content4 = MessageExample(
            user="User4",
            content=MessageContent(text="Greetings")
        )

        character = MagicMock()
        character.message_examples = [
            [content1, content2],
            [content3, content4]
        ]

        builder = PromptBuilder(character)
        result = builder.get_message_examples_for_context(count=2)

        assert len(result) <= 2
        assert isinstance(result, list)


class TestPromptBuilderEdgeCases:
    """Test edge cases for PromptBuilder"""

    def test_all_fields_none(self):
        """Test with all optional fields None"""
        from app.services.character.prompt_builder import PromptBuilder

        character = MagicMock()
        character.name = "MinimalBot"
        character.bio = "Minimal bio"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None
        character.knowledge = None
        character.system_prompt = None
        character.templates = None
        character.post_examples = None

        builder = PromptBuilder(character)
        result = builder.build_system_prompt()

        assert "MinimalBot" in result
        assert "Minimal bio" in result

    def test_with_extra_kwargs(self):
        """Test with extra kwargs passed to prompt"""
        from app.services.character.prompt_builder import (
            DEFAULT_SYSTEM_TEMPLATE,
            PromptBuilder,
        )

        # Create a custom template that uses extra kwargs
        character = MagicMock()
        character.name = "Bot"
        character.bio = "Bio"
        character.lore = None
        character.topics = None
        character.adjectives = None
        character.style = None
        character.knowledge = None
        character.system_prompt = "Custom: {custom_field}"
        character.templates = None
        character.post_examples = None

        builder = PromptBuilder(character)
        result = builder.build_system_prompt(custom_field="custom_value")

        # kwargs fill placeholders in OUR template. A placeholder written inside
        # author text stays literal: substituting there would let a persona pull
        # arbitrary variables into itself, which is the hazard _safe_format is
        # documented to guard against.
        assert "Custom: {custom_field}" in result
        assert "custom_value" not in result

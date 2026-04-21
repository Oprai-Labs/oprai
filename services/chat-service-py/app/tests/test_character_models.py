"""
Tests for Character Models.

Tests Pydantic models for character system.
"""

import pytest
from datetime import datetime


class TestModelProvider:
    """Test ModelProvider enum"""

    def test_model_provider_values(self):
        """Test all model provider values"""
        from app.models.character import ModelProvider

        assert ModelProvider.OPENAI.value == "openai"
        assert ModelProvider.ANTHROPIC.value == "anthropic"
        assert ModelProvider.OLLAMA.value == "ollama"
        assert ModelProvider.GROK.value == "grok"
        assert ModelProvider.GEMINI.value == "gemini"


class TestClientType:
    """Test ClientType enum"""

    def test_client_type_values(self):
        """Test all client type values"""
        from app.models.character import ClientType

        assert ClientType.DISCORD.value == "discord"
        assert ClientType.TWITTER.value == "twitter"
        assert ClientType.TELEGRAM.value == "telegram"
        assert ClientType.FARCASTER.value == "farcaster"


class TestCharacterStyle:
    """Test CharacterStyle model"""

    def test_style_defaults(self):
        """Test style has empty defaults"""
        from app.models.character import CharacterStyle

        style = CharacterStyle()
        assert style.all == []
        assert style.chat == []
        assert style.post == []

    def test_style_with_values(self):
        """Test style with custom values"""
        from app.models.character import CharacterStyle

        style = CharacterStyle(
            all=["Be concise"],
            chat=["Be friendly"],
            post=["Be engaging"]
        )
        assert len(style.all) == 1
        assert len(style.chat) == 1


class TestCharacterSettings:
    """Test CharacterSettings model"""

    def test_settings_empty(self):
        """Test empty settings"""
        from app.models.character import CharacterSettings

        settings = CharacterSettings()
        assert settings.secrets is None
        assert settings.voice is None

    def test_settings_with_secrets(self):
        """Test settings with secrets"""
        from app.models.character import CharacterSettings

        settings = CharacterSettings(
            secrets={"api_key": "secret123"},
            model="gpt-4"
        )
        assert settings.secrets["api_key"] == "secret123"
        assert settings.model == "gpt-4"


class TestCharacterVoice:
    """Test CharacterVoice model"""

    def test_voice_required_fields(self):
        """Test voice requires model"""
        from app.models.character import CharacterVoice

        voice = CharacterVoice(model="elevenlabs")
        assert voice.model == "elevenlabs"


class TestMessageExample:
    """Test MessageExample model"""

    def test_message_example(self):
        """Test message example creation"""
        from app.models.character import MessageExample, MessageContent

        content = MessageContent(text="Hello!")
        example = MessageExample(
            user="User1",
            content=content
        )
        assert example.user == "User1"
        assert example.content.text == "Hello!"


class TestCharacterModel:
    """Test Character model"""

    def test_character_required_fields(self):
        """Test character requires name, modelProvider, clients"""
        from app.models.character import Character, ModelProvider, ClientType

        char = Character(
            name="TestBot",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="A test bot"
        )
        assert char.name == "TestBot"
        assert char.model_provider == ModelProvider.OPENAI

    def test_character_with_bio_list(self):
        """Test character accepts bio as list"""
        from app.models.character import Character, ModelProvider, ClientType

        char = Character(
            name="TestBot",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio=["Bio line 1", "Bio line 2"]
        )
        assert isinstance(char.bio, list)
        assert len(char.bio) == 2

    def test_character_alias_mapping(self):
        """Test alias mapping works"""
        from app.models.character import Character, ModelProvider, ClientType

        char = Character(
            name="TestBot",
            modelProvider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="Test bio"
        )
        assert char.model_provider == ModelProvider.OPENAI


class TestCreateCharacterRequest:
    """Test CreateCharacterRequest model"""

    def test_create_request_required(self):
        """Test create request requires fields"""
        from app.models.character import CreateCharacterRequest, ModelProvider, ClientType

        req = CreateCharacterRequest(
            name="NewBot",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="New bot bio"
        )
        assert req.name == "NewBot"

    def test_create_request_optional_fields(self):
        """Test create request optional fields"""
        from app.models.character import CreateCharacterRequest, ModelProvider, ClientType

        req = CreateCharacterRequest(
            name="NewBot",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="Bio",
            topics=["Solana", "DeFi"],
            tags=["ai", "assistant"]
        )
        assert req.topics == ["Solana", "DeFi"]
        assert req.tags == ["ai", "assistant"]


class TestUpdateCharacterRequest:
    """Test UpdateCharacterRequest model"""

    def test_update_request_all_optional(self):
        """Test all fields are optional in update"""
        from app.models.character import UpdateCharacterRequest

        req = UpdateCharacterRequest(name="NewName")
        assert req.name == "NewName"  # Name is set, others are None
        assert req.model_provider is None


class TestCharacterFilters:
    """Test CharacterFilters model"""

    def test_filters_defaults(self):
        """Test filters have None defaults"""
        from app.models.character import CharacterFilters

        filters = CharacterFilters()
        assert filters.owner_wallet is None
        assert filters.is_public is None


class TestCharacterWithRuntime:
    """Test CharacterWithRuntime model"""

    def test_runtime_defaults(self):
        """Test runtime has default values"""
        from app.models.character import CharacterWithRuntime, Character, ModelProvider, ClientType

        char = Character(
            name="TestBot",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="Test"
        )
        runtime = CharacterWithRuntime(**char.model_dump())
        assert runtime.status == "idle"
        assert runtime.message_count is None


class TestBuiltinCharacters:
    """Test built-in character templates"""

    def test_builtin_characters_exist(self):
        """Test built-in characters are defined"""
        from app.models.character import BUILTIN_CHARACTERS

        assert len(BUILTIN_CHARACTERS) > 0

    def test_first_builtin_character(self):
        """Test first character has required fields"""
        from app.models.character import BUILTIN_CHARACTERS

        first = BUILTIN_CHARACTERS[0]
        assert "name" in first
        assert "modelProvider" in first
        assert "clients" in first
        assert "bio" in first

    def test_defi_analyst_character(self):
        """Test DeFi Analyst character"""
        from app.models.character import BUILTIN_CHARACTERS

        analyst = next((c for c in BUILTIN_CHARACTERS if c["name"] == "DeFi Analyst"), None)
        assert analyst is not None
        assert "yield farming" in analyst["topics"]

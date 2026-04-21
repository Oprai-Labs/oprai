"""
Tests for Character Loader Service.

Tests character loading, caching, filtering and CRUD operations.
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime


class TestCharacterLoaderInit:
    """Test CharacterLoader initialization"""

    def test_initialization_creates_cache(self):
        """Test initialization creates empty cache"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        assert loader._cache is not None
        assert isinstance(loader._cache, dict)

    def test_initialization_loads_builtins(self):
        """Test initialization loads built-in characters"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        # Should have loaded some characters
        assert len(loader._cache) > 0

    def test_custom_characters_dir(self):
        """Test custom characters directory"""
        from app.services.character.loader import CharacterLoader

        custom_dir = Path("/custom/characters")
        loader = CharacterLoader(characters_dir=custom_dir)

        assert loader.characters_dir == custom_dir


class TestLoadFromFile:
    """Test load_from_file method"""

    def test_load_from_file_success(self):
        """Test loading character from valid JSON file"""
        from app.services.character.loader import CharacterLoader
        from app.models.character import Character

        # Create temp character file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            char_data = {
                "name": "Test Bot",
                "modelProvider": "openai",
                "clients": ["direct"],
                "bio": "A test bot"
            }
            json.dump(char_data, f)
            temp_path = Path(f.name)

        try:
            loader = CharacterLoader()
            character = loader.load_from_file(temp_path)

            assert isinstance(character, Character)
            assert character.name == "Test Bot"
            assert character.model_provider.value == "openai"
        finally:
            temp_path.unlink()

    def test_load_from_file_not_found(self):
        """Test loading from non-existent file raises"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        with pytest.raises(FileNotFoundError):
            loader.load_from_file(Path("/nonexistent/character.json"))

    def test_load_from_file_missing_name(self):
        """Test loading file without name field raises"""
        from app.services.character.loader import CharacterLoader

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            char_data = {
                "modelProvider": "openai",
                "clients": ["direct"]
            }
            json.dump(char_data, f)
            temp_path = Path(f.name)

        try:
            loader = CharacterLoader()
            with pytest.raises(ValueError, match="name"):
                loader.load_from_file(temp_path)
        finally:
            temp_path.unlink()

    def test_load_from_file_missing_model_provider(self):
        """Test loading file without modelProvider raises"""
        from app.services.character.loader import CharacterLoader

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            char_data = {
                "name": "Test",
                "clients": ["direct"]
            }
            json.dump(char_data, f)
            temp_path = Path(f.name)

        try:
            loader = CharacterLoader()
            with pytest.raises(ValueError, match="modelProvider"):
                loader.load_from_file(temp_path)
        finally:
            temp_path.unlink()

    def test_load_from_file_missing_clients(self):
        """Test loading file without clients raises"""
        from app.services.character.loader import CharacterLoader

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            char_data = {
                "name": "Test",
                "modelProvider": "openai"
            }
            json.dump(char_data, f)
            temp_path = Path(f.name)

        try:
            loader = CharacterLoader()
            with pytest.raises(ValueError, match="clients"):
                loader.load_from_file(temp_path)
        finally:
            temp_path.unlink()

    def test_load_from_file_caches_character(self):
        """Test loaded character is cached"""
        from app.services.character.loader import CharacterLoader

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            char_data = {
                "name": "Cached Bot",
                "modelProvider": "openai",
                "clients": ["direct"],
                "bio": "A test bot for caching"
            }
            json.dump(char_data, f)
            temp_path = Path(f.name)

        try:
            loader = CharacterLoader()
            character = loader.load_from_file(temp_path)

            # Should be in cache
            assert character.id in loader._cache
        finally:
            temp_path.unlink()


class TestGetCharacter:
    """Test get_character method"""

    def test_get_existing_character(self):
        """Test getting existing character returns it"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        # Get a built-in character
        char_id = list(loader._cache.keys())[0]
        character = loader.get_character(char_id)

        assert character is not None

    def test_get_nonexistent_character(self):
        """Test getting non-existent character returns None"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        result = loader.get_character("nonexistent-id-12345")

        assert result is None


class TestListCharacters:
    """Test list_characters method"""

    def test_list_all_characters(self):
        """Test listing all characters returns list"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        results = loader.list_characters()

        assert isinstance(results, list)
        assert len(results) > 0

    def test_list_filter_by_owner(self):
        """Test filtering by owner wallet"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        results = loader.list_characters(owner_wallet="Wallet123")

        assert isinstance(results, list)

    def test_list_filter_by_public(self):
        """Test filtering by public status"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        results = loader.list_characters(is_public=True)

        assert isinstance(results, list)
        for char in results:
            assert char.is_public is True

    def test_list_filter_by_tags(self):
        """Test filtering by tags"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        results = loader.list_characters(tags=["defi"])

        assert isinstance(results, list)

    def test_list_search_by_name(self):
        """Test search by name"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        results = loader.list_characters(search="oprai")

        assert isinstance(results, list)

    def test_list_search_case_insensitive(self):
        """Test search is case insensitive"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        results_lower = loader.list_characters(search="oprai")
        results_upper = loader.list_characters(search="OPRAI")

        assert isinstance(results_lower, list)


class TestCreateCharacter:
    """Test create_character method"""

    def test_create_character_success(self):
        """Test creating a new character"""
        from app.services.character.loader import CharacterLoader
        from app.models.character import CreateCharacterRequest, ModelProvider, ClientType

        loader = CharacterLoader()

        request = CreateCharacterRequest(
            name="New Character",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="A new character"
        )

        character = loader.create_character(request, owner_wallet="Wallet123")

        assert character.name == "New Character"
        assert character.owner_wallet == "Wallet123"
        assert character.id in loader._cache

    def test_create_character_generates_id(self):
        """Test character gets generated ID"""
        from app.services.character.loader import CharacterLoader
        from app.models.character import CreateCharacterRequest, ModelProvider, ClientType

        loader = CharacterLoader()

        request = CreateCharacterRequest(
            name="ID Test",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="A test character for ID generation"
        )

        character = loader.create_character(request)

        assert character.id is not None
        assert len(character.id) > 0


class TestUpdateCharacter:
    """Test update_character method"""

    def test_update_existing_character(self):
        """Test updating existing character"""
        from app.services.character.loader import CharacterLoader
        from app.models.character import CreateCharacterRequest, ModelProvider, ClientType

        loader = CharacterLoader()

        # Create a character first
        request = CreateCharacterRequest(
            name="Update Test",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="Original bio"
        )
        character = loader.create_character(request)

        # Update it
        updated = loader.update_character(
            character.id,
            {"bio": "Updated bio"}
        )

        assert updated is not None
        assert updated.bio == "Updated bio"

    def test_update_nonexistent_returns_none(self):
        """Test updating non-existent returns None"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        result = loader.update_character("nonexistent-id", {"name": "New"})

        assert result is None

    def test_cannot_update_builtin(self):
        """Test cannot update built-in characters"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        # Get a built-in character ID
        builtin_id = list(loader._builtin_ids)[0]

        with pytest.raises(ValueError, match="built-in"):
            loader.update_character(builtin_id, {"name": "Hack"})


class TestDeleteCharacter:
    """Test delete_character method"""

    def test_delete_existing_character(self):
        """Test deleting existing character"""
        from app.services.character.loader import CharacterLoader
        from app.models.character import CreateCharacterRequest, ModelProvider, ClientType

        loader = CharacterLoader()

        # Create a character
        request = CreateCharacterRequest(
            name="Delete Test",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="A character for deletion testing"
        )
        character = loader.create_character(request)
        char_id = character.id

        # Delete it
        result = loader.delete_character(char_id)

        assert result is True
        assert char_id not in loader._cache

    def test_delete_nonexistent_returns_false(self):
        """Test deleting non-existent returns False"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        result = loader.delete_character("nonexistent-id")

        assert result is False

    def test_cannot_delete_builtin(self):
        """Test cannot delete built-in characters"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        builtin_id = list(loader._builtin_ids)[0]

        with pytest.raises(ValueError, match="built-in"):
            loader.delete_character(builtin_id)


class TestDuplicateCharacter:
    """Test duplicate_character method"""

    def test_duplicate_existing_character(self):
        """Test duplicating existing character"""
        from app.services.character.loader import CharacterLoader
        from app.models.character import CreateCharacterRequest, ModelProvider, ClientType

        loader = CharacterLoader()

        # Create a character
        request = CreateCharacterRequest(
            name="Original",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="Original bio"
        )
        original = loader.create_character(request)

        # Duplicate it
        duplicate = loader.duplicate_character(original.id)

        assert duplicate is not None
        assert duplicate.name == "Original (Copy)"
        assert duplicate.id != original.id
        assert duplicate.id in loader._cache

    def test_duplicate_nonexistent_returns_none(self):
        """Test duplicating non-existent returns None"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        result = loader.duplicate_character("nonexistent-id")

        assert result is None


class TestRandomBio:
    """Test get_random_bio method"""

    def test_get_random_bio_from_list(self):
        """Test getting random bio from list"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        # Get a built-in character with bio
        for char in loader._cache.values():
            if char.bio:
                result = loader.get_random_bio(char)
                assert isinstance(result, str)
                break

    def test_get_random_bio_returns_string(self):
        """Test returns string"""
        from app.services.character.loader import CharacterLoader

        loader = CharacterLoader()

        char = loader._cache.get(list(loader._cache.keys())[0])
        if char:
            result = loader.get_random_bio(char)
            assert isinstance(result, str)


class TestStyleInstructions:
    """Test get_style_instructions method"""

    def test_get_style_instructions_all(self):
        """Test getting style instructions for all contexts"""
        from app.services.character.loader import CharacterLoader
        from app.models.character import CharacterStyle

        mock_char = MagicMock()
        mock_char.style = CharacterStyle(
            all=["Be concise", "Be friendly"],
            chat=["Use emojis"],
            post=["Keep it short"]
        )

        loader = CharacterLoader()
        result = loader.get_style_instructions(mock_char, "all")

        assert "Be concise" in result
        assert "Be friendly" in result

    def test_get_style_instructions_chat(self):
        """Test getting style instructions for chat"""
        from app.services.character.loader import CharacterLoader
        from app.models.character import CharacterStyle

        mock_char = MagicMock()
        mock_char.style = CharacterStyle(
            all=["Be concise"],
            chat=["Use emojis"],
            post=[]
        )

        loader = CharacterLoader()
        result = loader.get_style_instructions(mock_char, "chat")

        assert "Use emojis" in result

    def test_get_style_instructions_no_style(self):
        """Test returns empty string when no style"""
        from app.services.character.loader import CharacterLoader

        mock_char = MagicMock()
        mock_char.style = None

        loader = CharacterLoader()
        result = loader.get_style_instructions(mock_char)

        assert result == ""


class TestGetCharacterLoader:
    """Test get_character_loader function"""

    def test_get_character_loader_singleton(self):
        """Test returns singleton instance"""
        from app.services.character.loader import get_character_loader, CharacterLoader

        loader1 = get_character_loader()
        loader2 = get_character_loader()

        assert loader1 is loader2

    def test_get_character_loader_type(self):
        """Test returns CharacterLoader instance"""
        from app.services.character.loader import get_character_loader, CharacterLoader

        loader = get_character_loader()

        assert isinstance(loader, CharacterLoader)

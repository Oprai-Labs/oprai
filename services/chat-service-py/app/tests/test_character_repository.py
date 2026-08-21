"""
Tests for Character Repository module.

Tests database operations for characters.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCharacterModel:
    """Test CharacterModel SQLAlchemy model"""

    def test_tablename(self):
        """Test table name"""
        from app.db.character_repository import CharacterModel

        assert CharacterModel.__tablename__ == "characters"

    def test_schema(self):
        """Test schema is chat_schema"""
        from app.config import settings
        from app.db.character_repository import CharacterModel

        table_args = CharacterModel.__table_args__
        assert table_args[2]["schema"] == "chat_schema"

    def test_columns_exist(self):
        """Test required columns exist"""
        from app.db.character_repository import CharacterModel

        columns = [c.name for c in CharacterModel.__table__.columns]

        assert "id" in columns
        assert "name" in columns
        assert "model_provider" in columns
        assert "clients" in columns
        assert "bio" in columns
        assert "owner_wallet" in columns
        assert "is_public" in columns

    def test_indexes_defined(self):
        """Test indexes are defined"""
        from app.db.character_repository import CharacterModel

        indexes = CharacterModel.__table_args__
        index_names = [idx.name for idx in indexes if hasattr(idx, 'name')]

        assert "ix_characters_owner_wallet" in index_names
        assert "ix_characters_is_public" in index_names


class TestCharacterRepositoryInit:
    """Test CharacterRepository initialization"""

    def test_init(self):
        """Test repository initialization"""
        from app.db.character_repository import CharacterRepository

        mock_session = MagicMock()
        repo = CharacterRepository(mock_session)

        assert repo.session is mock_session


class TestCharacterRepositoryCreate:
    """Test character creation"""

    @pytest.mark.asyncio
    async def test_create_character(self):
        """Test creating a character"""
        from app.db.character_repository import CharacterRepository
        from app.models.character import (
            ClientType,
            CreateCharacterRequest,
            ModelProvider,
        )

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        request = CreateCharacterRequest(
            name="TestBot",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="A test bot"
        )

        repo = CharacterRepository(mock_session)

        # Mock UUID
        with patch("uuid.uuid4", return_value="test-uuid-123"):
            result = await repo.create(request, owner_wallet="wallet123")

        # Should have added model
        assert mock_session.add.called


class TestCharacterRepositoryGet:
    """Test getting characters"""

    @pytest.mark.asyncio
    async def test_get_character_found(self):
        """Test getting existing character"""
        from app.db.character_repository import CharacterRepository

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_model = MagicMock()
        mock_model.id = "char-123"
        mock_model.name = "TestBot"
        mock_model.model_provider = "openai"
        mock_model.clients = ["direct"]
        mock_model.bio = ["Test bio"]
        mock_model.lore = None
        mock_model.knowledge = None
        mock_model.message_examples = None
        mock_model.post_examples = None
        mock_model.topics = None
        mock_model.adjectives = None
        mock_model.style = None
        mock_model.settings = None
        mock_model.templates = None
        mock_model.system_prompt = None
        mock_model.active = True
        mock_model.owner_wallet = "wallet123"
        mock_model.is_public = False
        mock_model.tags = None
        mock_model.created_at = datetime.utcnow()
        mock_model.updated_at = datetime.utcnow()

        mock_result.scalar_one_or_none.return_value = mock_model
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = CharacterRepository(mock_session)
        result = await repo.get("char-123")

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_character_not_found(self):
        """Test getting non-existent character"""
        from app.db.character_repository import CharacterRepository

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = CharacterRepository(mock_session)
        result = await repo.get("nonexistent")

        assert result is None


class TestCharacterRepositoryList:
    """Test listing characters"""

    @pytest.mark.asyncio
    async def test_list_characters(self):
        """Test listing characters"""
        from app.db.character_repository import CharacterRepository

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        # `list()` returns (rows, total) — the count comes from a second query
        # via scalar_one(). Without stubbing it the mock hands back a MagicMock
        # and the tuple's second element is not a number.
        mock_result.scalar_one.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = CharacterRepository(mock_session)
        chars, total = await repo.list()

        assert chars == []
        assert total == 0


class TestCharacterRepositoryUpdate:
    """Test updating characters"""

    @pytest.mark.asyncio
    async def test_update_character_not_found(self):
        """Test updating non-existent character"""
        from app.db.character_repository import CharacterRepository
        from app.models.character import UpdateCharacterRequest

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = CharacterRepository(mock_session)

        request = UpdateCharacterRequest(name="NewName")
        result = await repo.update("nonexistent", request)

        assert result is None


class TestCharacterRepositoryDelete:
    """Test deleting characters"""

    @pytest.mark.asyncio
    async def test_delete_character(self):
        """Test deleting character"""
        from app.db.character_repository import CharacterRepository

        mock_session = AsyncMock()
        # Setup to return rowcount for delete query
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        repo = CharacterRepository(mock_session)
        # Test without owner_wallet to skip ownership check
        result = await repo.delete("char-123", owner_wallet=None)

        assert result is True


class TestCharacterRepositoryDuplicate:
    """Test duplicating characters"""

    @pytest.mark.asyncio
    async def test_duplicate_not_found(self):
        """Test duplicating non-existent character"""
        from app.db.character_repository import CharacterRepository

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = CharacterRepository(mock_session)
        result = await repo.duplicate("nonexistent")

        assert result is None


class TestCharacterRepositorySerialize:
    """Test serialization helpers"""

    def test_serialize_message_examples_empty(self):
        """Test serializing empty message examples"""
        from app.db.character_repository import CharacterRepository

        mock_session = MagicMock()
        repo = CharacterRepository(mock_session)

        result = repo._serialize_message_examples(None)

        assert result is None

    def test_serialize_message_examples_with_data(self):
        """Test serializing message examples"""
        from app.db.character_repository import CharacterRepository
        from app.models.character import MessageContent, MessageExample

        mock_session = MagicMock()
        repo = CharacterRepository(mock_session)

        examples = [
            [
                MessageExample(
                    user="User1",
                    content=MessageContent(text="Hello")
                )
            ]
        ]

        result = repo._serialize_message_examples(examples)

        assert result is not None


class TestCharacterMigration:
    """Test migration SQL"""

    def test_migration_sql_exists(self):
        """Test migration SQL is defined"""
        from app.db.character_repository import CHARACTER_MIGRATION

        assert "CREATE SCHEMA IF NOT EXISTS chat_schema" in CHARACTER_MIGRATION
        assert "CREATE TABLE IF NOT EXISTS chat_schema.characters" in CHARACTER_MIGRATION
        assert "ix_characters_owner_wallet" in CHARACTER_MIGRATION

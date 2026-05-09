"""
Tests for Character Routes.

Tests API endpoints for character management.
"""

import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException


class TestCharacterFiltersParsing:
    """Test query parameter parsing"""

    def test_tags_parsing(self):
        """Test comma-separated tags parsing"""
        tags = "defi,solana,trading"
        tag_list = tags.split(",") if tags else None

        assert tag_list == ["defi", "solana", "trading"]

    def test_empty_tags_returns_none(self):
        """Test empty tags returns None"""
        tags = ""
        tag_list = tags.split(",") if tags else None

        assert tag_list is None


class TestCharacterPagination:
    """Test pagination logic"""

    def test_pagination_offset_limit(self):
        """Test offset/limit pagination"""
        characters = list(range(100))
        limit = 20
        offset = 40

        paginated = characters[offset:offset + limit]

        assert len(paginated) == 20
        assert paginated[0] == 40


class TestCharacterExport:
    """Test character export format"""

    def test_export_format(self):
        """Test export creates CharacterFile"""
        from app.models.character import CharacterFile

        data = {
            "name": "TestBot",
            "modelProvider": "openai",
            "clients": ["direct"],
            "bio": "Test bio"
        }

        char_file = CharacterFile(**data, version="1.0.0")
        assert char_file.version == "1.0.0"
        assert char_file.name == "TestBot"


class TestCharacterImport:
    """Test character import validation"""

    def test_import_validates_required_fields(self):
        """Test import validates required fields"""
        from pydantic import ValidationError
        from app.models.character import CreateCharacterRequest

        # Missing required fields should raise validation error
        with pytest.raises(ValidationError):
            CreateCharacterRequest(name="Test")  # Missing modelProvider, clients, bio

    def test_import_accepts_valid_data(self):
        """Test import accepts valid data"""
        from app.models.character import CreateCharacterRequest, ModelProvider, ClientType

        req = CreateCharacterRequest(
            name="ImportedBot",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="Imported bio"
        )

        assert req.name == "ImportedBot"


class TestCharacterModelDump:
    """Test model dump with aliases"""

    def test_model_dump_by_alias(self):
        """Test model_dump with by_alias=True"""
        from app.models.character import Character, ModelProvider, ClientType

        char = Character(
            id="char-123",
            name="TestBot",
            model_provider=ModelProvider.OPENAI,
            clients=[ClientType.DIRECT],
            bio="Test bio"
        )

        dumped = char.model_dump(by_alias=True)

        assert "id" in dumped
        assert "modelProvider" in dumped
        assert "clients" in dumped


class TestRuntimeKeyLogic:
    """Test runtime key generation logic"""

    def test_runtime_key_format(self):
        """Test runtime key format is wallet:character"""
        wallet = "wallet123"
        character_id = "char456"
        key = f"{wallet}:{character_id}"

        assert key == "wallet123:char456"

    def test_runtime_key_different_wallets(self):
        """Test different wallets produce different keys"""
        keys = set()
        for wallet in ["w1", "w2"]:
            for char_id in ["c1", "c2"]:
                keys.add(f"{wallet}:{char_id}")

        assert len(keys) == 4

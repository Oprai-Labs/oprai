"""
Tests for ChatMessage SQLAlchemy model.

Tests database schema and column definitions.
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestChatMessageModel:
    """Test ChatMessage SQLAlchemy model"""

    def test_tablename(self):
        """Test table name"""
        from app.models.message import ChatMessage

        assert ChatMessage.__tablename__ == "chat_messages"

    def test_schema(self):
        """Test schema is configured"""
        from app.config import settings
        from app.models.message import ChatMessage

        table_args = ChatMessage.__table_args__
        # Check schema is in table args
        schema_found = False
        for arg in table_args:
            if isinstance(arg, dict) and arg.get("schema"):
                assert arg["schema"] == settings.DB_SCHEMA
                schema_found = True
        assert schema_found

    def test_columns_exist(self):
        """Test required columns exist"""
        from app.models.message import ChatMessage

        columns = [c.name for c in ChatMessage.__table__.columns]

        assert "id" in columns
        assert "session_id" in columns
        assert "wallet_address" in columns
        assert "role" in columns
        assert "content" in columns
        assert "metadata" in columns
        assert "created_at" in columns

    def test_id_column_type(self):
        """Test id column is UUID"""
        from app.models.message import ChatMessage

        id_col = ChatMessage.__table__.columns["id"]
        assert "UUID" in str(id_col.type) or "uuid" in str(id_col.type).lower()

    def test_session_id_foreign_key(self):
        """Test session_id has foreign key"""
        from app.models.message import ChatMessage

        session_col = ChatMessage.__table__.columns["session_id"]
        assert session_col.foreign_keys

    def test_metadata_is_jsonb(self):
        """Test metadata column is JSONB"""
        from app.models.message import ChatMessage

        metadata_col = ChatMessage.__table__.columns["metadata"]
        # JSONB type
        assert "json" in str(metadata_col.type).lower() or "JSONB" in str(metadata_col.type)

    def test_indexes_defined(self):
        """Test indexes are defined"""
        from app.models.message import ChatMessage

        table_args = ChatMessage.__table_args__
        index_names = [idx.name for idx in table_args if hasattr(idx, 'name')]

        assert "ix_chat_messages_session_created" in index_names
        assert "ix_chat_messages_wallet_address" in index_names

    def test_role_comment(self):
        """Test role column has comment"""
        from app.models.message import ChatMessage

        role_col = ChatMessage.__table__.columns["role"]
        # Check comment
        assert role_col.comment is not None
        assert "user" in role_col.comment
        assert "assistant" in role_col.comment
        assert "system" in role_col.comment


class TestChatMessageModelCreation:
    """Test ChatMessage model creation"""

    def test_create_message_instance(self):
        """Test creating a ChatMessage instance"""
        from app.models.message import ChatMessage

        message = ChatMessage(
            session_id=uuid.uuid4(),
            wallet_address="Wallet123",
            role="user",
            content="Hello world"
        )

        assert message.role == "user"
        assert message.content == "Hello world"
        assert message.wallet_address == "Wallet123"

    def test_message_with_metadata(self):
        """Test message with metadata"""
        from app.models.message import ChatMessage

        message = ChatMessage(
            session_id=uuid.uuid4(),
            wallet_address="Wallet123",
            role="assistant",
            content="Response",
            metadata_={"intent": "greeting"}
        )

        assert message.metadata_["intent"] == "greeting"

    def test_message_default_metadata(self):
        """Test message default metadata"""
        from app.models.message import ChatMessage

        message = ChatMessage(
            session_id=uuid.uuid4(),
            wallet_address="Wallet123",
            role="user",
            content="Test"
        )

        assert message.metadata_ is None or message.metadata_ == {}


class TestChatMessageModelRoles:
    """Test message role validation"""

    def test_valid_roles(self):
        """Test all valid role values"""
        from app.models.message import ChatMessage

        valid_roles = ["user", "assistant", "system"]

        for role in valid_roles:
            message = ChatMessage(
                session_id=uuid.uuid4(),
                wallet_address="Wallet123",
                role=role,
                content="Test"
            )
            assert message.role == role

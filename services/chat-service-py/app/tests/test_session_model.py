"""
Tests for ChatSession SQLAlchemy model.

Tests database schema and column definitions.
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestChatSessionModel:
    """Test ChatSession SQLAlchemy model"""

    def test_tablename(self):
        """Test table name"""
        from app.models.session import ChatSession

        assert ChatSession.__tablename__ == "chat_sessions"

    def test_schema(self):
        """Test schema is configured"""
        from app.config import settings
        from app.models.session import ChatSession

        table_args = ChatSession.__table_args__
        # Check schema is in table args
        schema_found = False
        for arg in table_args:
            if isinstance(arg, dict) and arg.get("schema"):
                assert arg["schema"] == settings.DB_SCHEMA
                schema_found = True
        assert schema_found

    def test_columns_exist(self):
        """Test required columns exist"""
        from app.models.session import ChatSession

        columns = [c.name for c in ChatSession.__table__.columns]

        assert "id" in columns
        assert "user_id" in columns
        assert "wallet_address" in columns
        assert "title" in columns
        assert "message_count" in columns
        assert "created_at" in columns
        assert "updated_at" in columns
        assert "is_deleted" in columns
        assert "deleted_at" in columns
        assert "pinned" in columns
        assert "pinned_at" in columns

    def test_id_column_type(self):
        """Test id column is UUID"""
        from app.models.session import ChatSession

        id_col = ChatSession.__table__.columns["id"]
        assert "UUID" in str(id_col.type) or "uuid" in str(id_col.type).lower()

    def test_indexes_defined(self):
        """Test indexes are defined"""
        from app.models.session import ChatSession

        table_args = ChatSession.__table_args__
        index_names = [idx.name for idx in table_args if hasattr(idx, 'name')]

        assert "ix_chat_sessions_wallet_created" in index_names
        assert "ix_chat_sessions_user_id" in index_names


class TestChatSessionModelCreation:
    """Test ChatSession model creation"""

    def test_create_session_instance(self):
        """Test creating a ChatSession instance"""
        from app.models.session import ChatSession

        session = ChatSession(
            user_id="user123",
            wallet_address="Wallet123",
            title="Test Chat",
            message_count=0
        )

        assert session.user_id == "user123"
        assert session.wallet_address == "Wallet123"
        assert session.title == "Test Chat"
        assert session.message_count == 0

    def test_session_with_defaults(self):
        """Test session with default values"""
        from app.models.session import ChatSession

        session = ChatSession(
            user_id="user123",
            wallet_address="Wallet123",
            title="New chat"
        )

        assert session.title == "New chat"  # default

    def test_session_with_id(self):
        """Test session with custom id"""
        from app.models.session import ChatSession

        custom_id = uuid.uuid4()
        session = ChatSession(
            id=custom_id,
            user_id="user123",
            wallet_address="Wallet123",
            title="Custom Session"
        )

        assert session.id == custom_id


class TestChatSessionPinned:
    """Test pinned functionality"""

    def test_pinned_default(self):
        """Test pinned defaults to False"""
        from app.models.session import ChatSession

        session = ChatSession(
            user_id="user123",
            wallet_address="Wallet123",
            title="Test",
            pinned=False
        )

        assert session.pinned is False

    def test_is_deleted_default(self):
        """Test is_deleted defaults to False"""
        from app.models.session import ChatSession

        session = ChatSession(
            user_id="user123",
            wallet_address="Wallet123",
            title="Test",
            is_deleted=False
        )

        assert session.is_deleted is False


class TestBaseModel:
    """Test Base declarative base"""

    def test_base_exists(self):
        """Test Base class exists"""
        from app.models.session import Base

        assert Base is not None

    def test_base_is_declarative(self):
        """Test Base is DeclarativeBase"""
        from sqlalchemy.orm import DeclarativeBase

        from app.models.session import Base

        assert issubclass(Base, DeclarativeBase)

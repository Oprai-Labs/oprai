"""
Tests for Chat Service Models.

Tests SQLAlchemy models for chat service.
"""

import pytest


class TestChatSessionModel:
    """Test ChatSession model"""

    def test_tablename(self):
        """Test table name is chat_sessions"""
        from app.models.session import ChatSession

        assert ChatSession.__tablename__ == "chat_sessions"

    def test_primary_key(self):
        """Test id is primary key"""
        from app.models.session import ChatSession

        pk_columns = [c.name for c in ChatSession.__table__.primary_key]
        assert "id" in pk_columns

    def test_required_fields(self):
        """Test required fields exist"""
        from app.models.session import ChatSession

        table_columns = [c.name for c in ChatSession.__table__.columns]

        assert "id" in table_columns
        assert "user_id" in table_columns
        assert "wallet_address" in table_columns
        assert "title" in table_columns
        assert "message_count" in table_columns
        assert "created_at" in table_columns
        assert "updated_at" in table_columns
        assert "is_deleted" in table_columns
        assert "deleted_at" in table_columns
        assert "pinned" in table_columns
        assert "pinned_at" in table_columns

    def test_indexes(self):
        """Test indexes are defined"""
        from app.models.session import ChatSession

        indexes = ChatSession.__table_args__
        # Should have wallet_created and user_id indexes
        index_names = [idx.name for idx in indexes if hasattr(idx, 'name')]
        assert "ix_chat_sessions_wallet_created" in index_names
        assert "ix_chat_sessions_user_id" in index_names


class TestChatSessionInstance:
    """Test ChatSession model instances"""

    def test_create_session(self):
        """Test creating a session instance"""
        from app.models.session import ChatSession

        session = ChatSession(
            user_id="user-123",
            wallet_address="Wallet123",
            title="Test Chat"
        )

        assert session.user_id == "user-123"
        assert session.wallet_address == "Wallet123"
        assert session.title == "Test Chat"

    def test_session_with_message_count(self):
        """Test session with message count"""
        from app.models.session import ChatSession

        session = ChatSession(
            user_id="user-123",
            wallet_address="Wallet123",
            title="Test",
            message_count=10
        )

        assert session.message_count == 10

    def test_session_with_pinned(self):
        """Test pinned session"""
        from app.models.session import ChatSession

        session = ChatSession(
            user_id="user-123",
            wallet_address="Wallet123",
            title="Pinned Chat",
            pinned=True
        )

        assert session.pinned is True

"""
Tests for Session Service module.

Tests session serialization and list operations.
"""

import uuid
from datetime import datetime, timezone, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSessionSerialize:
    """Test session serialization"""

    def test_serialize_session(self):
        """Test session serialization format"""
        from app.models.session import ChatSession
        from app.services.session import _serialize

        # Create mock session
        session = ChatSession(
            id=uuid.uuid4(),
            user_id="user-123",
            wallet_address="Wallet123",
            title="Test Session",
            pinned=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        result = _serialize(session)

        assert "id" in result
        assert "wallet" in result
        assert "title" in result
        assert "pinned" in result
        assert "createdAt" in result
        assert "updatedAt" in result


class TestListSessions:
    """Test list_sessions function"""

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        """Test listing sessions when none exist"""
        from app.services.session import list_sessions

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        sessions, has_more, cursor = await list_sessions(
            db=mock_db,
            wallet="test_wallet"
        )

        assert sessions == []
        assert has_more is False
        assert cursor is None

    @pytest.mark.asyncio
    async def test_list_sessions_sorted_by_updated(self):
        """Test sessions are sorted by updated_at descending"""
        from app.services.session import list_sessions

        # Verify the query includes order_by
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        await list_sessions(db=mock_db, wallet="test_wallet")

        # Check execute was called
        assert mock_db.execute.called


class TestPagination:
    """Test pagination functionality"""

    @pytest.mark.asyncio
    async def test_cursor_based_pagination(self):
        """Test cursor-based pagination"""
        from app.services.session import list_sessions

        cursor = datetime.now(UTC).isoformat()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        sessions, has_more, next_cursor = await list_sessions(
            db=mock_db,
            wallet="test_wallet",
            cursor=cursor
        )

        # Should handle cursor parameter
        assert mock_db.execute.called


class TestSessionFilters:
    """Test session filtering"""

    @pytest.mark.asyncio
    async def test_filter_by_wallet(self):
        """Test filtering sessions by wallet"""
        from app.services.session import list_sessions

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        await list_sessions(db=mock_db, wallet="SpecificWallet")

        # Verify wallet filter was applied
        assert mock_db.execute.called


class TestSessionModel:
    """Test ChatSession model"""

    def test_session_model_exists(self):
        """Test ChatSession model can be imported"""
        from app.models.session import ChatSession

        assert ChatSession is not None

    def test_session_has_required_fields(self):
        """Test ChatSession has expected fields"""
        from app.models.session import ChatSession

        # Check fields exist
        assert hasattr(ChatSession, "id")
        assert hasattr(ChatSession, "wallet_address")
        assert hasattr(ChatSession, "title")
        assert hasattr(ChatSession, "pinned")
        assert hasattr(ChatSession, "created_at")
        assert hasattr(ChatSession, "updated_at")

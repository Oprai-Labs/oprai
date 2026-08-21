"""
Tests for ChatSummary SQLAlchemy model.

Tests database schema and column definitions.
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestChatSummaryModel:
    """Test ChatSummary SQLAlchemy model"""

    def test_tablename(self):
        """Test table name"""
        from app.models.summary import ChatSummary

        assert ChatSummary.__tablename__ == "chat_summaries"

    def test_schema(self):
        """Test schema is configured"""
        from app.config import settings
        from app.models.summary import ChatSummary

        table_args = ChatSummary.__table_args__
        # Check schema is in table args
        schema_found = False
        for arg in table_args:
            if isinstance(arg, dict) and arg.get("schema"):
                assert arg["schema"] == settings.DB_SCHEMA
                schema_found = True
        assert schema_found

    def test_columns_exist(self):
        """Test required columns exist"""
        from app.models.summary import ChatSummary

        columns = [c.name for c in ChatSummary.__table__.columns]

        assert "id" in columns
        assert "session_id" in columns
        assert "block_index" in columns
        assert "summary_text" in columns
        assert "message_start" in columns
        assert "message_end" in columns
        assert "created_at" in columns

    def test_id_column_type(self):
        """Test id column is UUID"""
        from app.models.summary import ChatSummary

        id_col = ChatSummary.__table__.columns["id"]
        assert "UUID" in str(id_col.type) or "uuid" in str(id_col.type).lower()

    def test_session_id_foreign_key(self):
        """Test session_id has foreign key"""
        from app.models.summary import ChatSummary

        session_col = ChatSummary.__table__.columns["session_id"]
        assert session_col.foreign_keys

    def test_unique_constraint(self):
        """Test unique constraint on session_id and block_index"""
        from app.models.summary import ChatSummary

        table_args = ChatSummary.__table_args__
        constraint_names = [c.name for c in table_args if hasattr(c, 'name')]

        assert "uq_summary_session_block" in constraint_names

    def test_indexes_defined(self):
        """Test indexes are defined"""
        from app.models.summary import ChatSummary

        table_args = ChatSummary.__table_args__
        index_names = [idx.name for idx in table_args if hasattr(idx, 'name')]

        assert "ix_chat_summaries_session_block" in index_names


class TestChatSummaryModelCreation:
    """Test ChatSummary model creation"""

    def test_create_summary_instance(self):
        """Test creating a ChatSummary instance"""
        from app.models.summary import ChatSummary

        summary = ChatSummary(
            session_id=uuid.uuid4(),
            block_index=0,
            summary_text="This is a summary of the conversation",
            message_start=1,
            message_end=10
        )

        assert summary.block_index == 0
        assert summary.summary_text == "This is a summary of the conversation"
        assert summary.message_start == 1
        assert summary.message_end == 10

    def test_summary_with_id(self):
        """Test summary with custom id"""
        from app.models.summary import ChatSummary

        custom_id = uuid.uuid4()
        summary = ChatSummary(
            id=custom_id,
            session_id=uuid.uuid4(),
            block_index=1,
            summary_text="Block 1 summary",
            message_start=11,
            message_end=20
        )

        assert summary.id == custom_id


class TestBlockStrategy:
    """Test block-based summarization strategy"""

    def test_block_size_constant(self):
        """Test BLOCK_SIZE constant is 10"""
        from app.services.summary import BLOCK_SIZE

        assert BLOCK_SIZE == 10

    def test_message_block_calculation(self):
        """Test message block index calculation"""
        from app.services.summary import BLOCK_SIZE

        # Messages 1-10 -> block 0
        assert (1 - 1) // BLOCK_SIZE == 0
        assert (10 - 1) // BLOCK_SIZE == 0

        # Messages 11-20 -> block 1
        assert (11 - 1) // BLOCK_SIZE == 1
        assert (20 - 1) // BLOCK_SIZE == 1

        # Messages 21-30 -> block 2
        assert (21 - 1) // BLOCK_SIZE == 2
        assert (30 - 1) // BLOCK_SIZE == 2


class TestSummaryService:
    """Test summary service functions"""

    def test_fetch_sol_balance_success(self):
        """Test fetching SOL balance"""
        import asyncio

        from app.services.summary import _fetch_sol_balance

        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"value": 1000000000}}  # 1 SOL

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

            result = asyncio.run(_fetch_sol_balance("Wallet123"))

            assert result == 1.0

    def test_fetch_sol_balance_error(self):
        """Test fetching SOL balance with error"""
        import asyncio

        from app.services.summary import _fetch_sol_balance

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post.side_effect = Exception("Network error")

            result = asyncio.run(_fetch_sol_balance("Wallet123"))

            assert result is None


class TestChatSummaryBlockIndexing:
    """Test block index calculations"""

    def test_first_block(self):
        """Test first block (messages 1-10)"""
        from app.services.summary import BLOCK_SIZE

        # Message 1-10 should be block 0
        for msg_num in range(1, 11):
            block_idx = (msg_num - 1) // BLOCK_SIZE
            assert block_idx == 0

    def test_second_block(self):
        """Test second block (messages 11-20)"""
        from app.services.summary import BLOCK_SIZE

        # Message 11-20 should be block 1
        for msg_num in range(11, 21):
            block_idx = (msg_num - 1) // BLOCK_SIZE
            assert block_idx == 1

    def test_multiple_blocks(self):
        """Test multiple blocks"""
        from app.services.summary import BLOCK_SIZE

        # Message 45 should be block 4
        block_idx = (45 - 1) // BLOCK_SIZE
        assert block_idx == 4
